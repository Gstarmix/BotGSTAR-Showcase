"""
claude_client.py — Wrapper unifié pour les deux moteurs Claude.

Deux moteurs supportés :

- ``cli_subscription`` : appel via subprocess de la CLI ``claude`` avec
  ``ANTHROPIC_API_KEY`` retirée de l'env (force OAuth/keychain). Mode par
  défaut, gratuit dans le quota Max 5x.
- ``api_anthropic`` : appel via SDK ``anthropic`` Python. Facturé à la
  consommation. Mode pour quand le quota Max 5x est tendu.

Le client maintient un historique conversationnel multi-tour qui est
repassé à chaque appel pour la continuité du dialogue.

Cf. ARCHITECTURE.md §4.
"""

import json
import logging
import os
import subprocess
from typing import Callable, Optional

from parser import ParserEvent, StreamParser

logger = logging.getLogger(__name__)


# ============================================================ Exceptions

class ClaudeClientError(Exception):
    """Erreur générale du client Claude."""


class ClaudeQuotaExhaustedError(ClaudeClientError):
    """Quota CLI subscription épuisé ou rate limit API."""


class ClaudeNetworkError(ClaudeClientError):
    """Erreur réseau pendant le streaming."""


# ============================================================ Constantes

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 4096
ENGINE_CLI = "cli_subscription"
ENGINE_API = "api_anthropic"

CLI_BINARY = "claude"
CLI_WAIT_TIMEOUT_SECONDS = 60


# ============================================================ ClaudeClient

class ClaudeClient:
    """Wrapper unique pour les deux moteurs (CLI subscription / API Anthropic).

    Usage :

        client = ClaudeClient(
            engine="cli_subscription",
            system_prompt=builder.system_prompt,
        )
        client.append_user_message(builder.build_initial_context_message(ctx))
        stats = client.stream_response(on_event=lambda e: ...)
        # client._history a maintenant l'échange [user, assistant]
        client.append_user_message("réponse étudiant ...")
        stats = client.stream_response(on_event=...)
    """

    def __init__(
        self,
        engine: str,
        system_prompt: str,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        if engine not in (ENGINE_CLI, ENGINE_API):
            raise ValueError(
                f"Engine inconnu : {engine!r}. "
                f"Attendu {ENGINE_CLI!r} ou {ENGINE_API!r}."
            )
        self._engine = engine
        self._system_prompt = system_prompt
        self._model = model
        self._max_tokens = max_tokens
        self._history: list[dict] = []

    # ---------------------------------------------------------------- propriétés

    @property
    def engine(self) -> str:
        return self._engine

    @property
    def model(self) -> str:
        return self._model

    @property
    def history(self) -> list[dict]:
        """Snapshot de l'historique (read-only ; le caller ne doit pas muter)."""
        return list(self._history)

    # ---------------------------------------------------------------- API publique

    def append_user_message(self, text: str) -> None:
        """Ajoute un message utilisateur sans appeler Claude.

        Utilisé pour le contexte initial (généré par PromptBuilder) puis
        à chaque réponse étudiante. Le streaming ultérieur via
        ``stream_response()`` appellera Claude avec cet historique.
        """
        self._history.append({"role": "user", "content": text})

    def stream_response(
        self,
        on_event: Callable[[ParserEvent], None],
    ) -> dict:
        """Appelle Claude avec l'historique courant et streame la réponse.

        Délègue le parsing à un ``StreamParser`` local — chaque chunk reçu
        est ``feed`` au parser qui émet des ``ParserEvent`` typés vers
        ``on_event``. Le texte brut complet (avec balises) est ajouté à
        l'historique comme message ``assistant`` à la fin.

        Returns:
            dict avec ``input_tokens``, ``output_tokens`` (int ou None
            selon la dispo côté backend).

        Raises:
            ClaudeQuotaExhaustedError, ClaudeNetworkError, ClaudeClientError.
        """
        if self._engine == ENGINE_CLI:
            return self._stream_via_cli(on_event)
        return self._stream_via_api(on_event)

    # ---------------------------------------------------------------- API Anthropic

    def _stream_via_api(self, on_event) -> dict:
        try:
            import anthropic
        except ImportError as e:
            raise ClaudeClientError(
                f"SDK anthropic indisponible ({e}). pip install anthropic"
            ) from e

        full_raw: list[str] = []
        parser = StreamParser(on_event)

        try:
            client = anthropic.Anthropic()
            with client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system_prompt,
                messages=self._history,
            ) as stream:
                for text in stream.text_stream:
                    full_raw.append(text)
                    parser.feed(text)
                parser.flush()
                final = stream.get_final_message()
            stats = self._extract_usage_from_final(final)
        except anthropic.RateLimitError as e:
            raise ClaudeQuotaExhaustedError(
                f"API Anthropic rate limit / quota epuise : {e}"
            ) from e
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            raise ClaudeNetworkError(f"API Anthropic reseau : {e}") from e
        except anthropic.APIError as e:
            raise ClaudeClientError(f"API Anthropic erreur : {e}") from e

        self._history.append({
            "role": "assistant",
            "content": "".join(full_raw),
        })
        return stats

    @staticmethod
    def _extract_usage_from_final(final) -> dict:
        usage = getattr(final, "usage", None)
        if usage is None:
            return {"input_tokens": None, "output_tokens": None}
        return {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }

    # ---------------------------------------------------------------- CLI subscription

    def _stream_via_cli(self, on_event) -> dict:
        """Appel CLI ``claude --print --output-format stream-json``.

        Note Phase A : la CLI ``claude`` ne supporte pas nativement un
        historique multi-tour en argument. On concatène l'historique courant
        en un seul prompt rôle-balisé. C'est une approximation : Claude
        perd la structure exacte des tours, mais voit tout le contexte.
        Si ça pose problème en pratique, on basculera vers une approche
        avec sessions persistantes du CLI (option ``-r`` resume) en Phase B.

        Format des events stream-json : on tente plusieurs formes connues
        (Claude Code, Anthropic streaming) — à valider en runtime.
        """
        prompt = self._build_cli_prompt()
        env = os.environ.copy()
        # Force OAuth/keychain — clé mémoire-stockée par claude.com auth
        env.pop("ANTHROPIC_API_KEY", None)

        cmd = [
            CLI_BINARY,
            "--print",
            "--output-format", "stream-json",
            "--include-partial-messages",  # nécessaire pour avoir les deltas chunk
            "--verbose",                    # imposé par CLI quand --print + stream-json
            "--append-system-prompt", self._system_prompt,
            prompt,
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
        except FileNotFoundError as e:
            raise ClaudeClientError(
                f"Commande {CLI_BINARY!r} introuvable. Installe Claude Code "
                f"CLI ou ajoute-la au PATH."
            ) from e

        full_raw: list[str] = []
        parser = StreamParser(on_event)
        stats: dict = {"input_tokens": None, "output_tokens": None}

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Ligne non-JSON — peut arriver si la CLI mixe du log
                    logger.debug("Ligne CLI non-JSON ignoree : %r", line[:100])
                    continue
                text = self._extract_cli_delta(event)
                if text:
                    full_raw.append(text)
                    parser.feed(text)
                usage = self._extract_cli_usage(event)
                if usage:
                    stats.update({k: v for k, v in usage.items() if v is not None})
            parser.flush()
            try:
                proc.wait(timeout=CLI_WAIT_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.terminate()
                raise ClaudeNetworkError(
                    f"CLI n'a pas termine apres {CLI_WAIT_TIMEOUT_SECONDS}s"
                )
        finally:
            if proc.poll() is None:
                proc.kill()

        if proc.returncode != 0:
            stderr = (proc.stderr.read() if proc.stderr else "") or ""
            lower = stderr.lower()
            if "quota" in lower or "rate limit" in lower or "limit reached" in lower:
                raise ClaudeQuotaExhaustedError(
                    f"CLI quota / rate limit : {stderr.strip()[:300]}"
                )
            raise ClaudeClientError(
                f"CLI exit {proc.returncode} : {stderr.strip()[:300]}"
            )

        self._history.append({
            "role": "assistant",
            "content": "".join(full_raw),
        })
        return stats

    def _build_cli_prompt(self) -> str:
        """Concatène l'historique en un prompt unique rôle-balisé.

        Format simple : ``USER: ...\\n\\nASSISTANT: ...\\n\\nUSER: ...``.
        Le dernier message est toujours user (invariant : on appelle
        stream_response après append_user_message).
        """
        parts: list[str] = []
        for msg in self._history:
            role = "USER" if msg["role"] == "user" else "ASSISTANT"
            parts.append(f"{role}: {msg['content']}")
        return "\n\n".join(parts)

    @staticmethod
    def _extract_cli_delta(event: dict) -> Optional[str]:
        """Extrait le texte d'un event stream-json (format Claude Code 2.x).

        Format observé en runtime (CLI 2.1.126) :
            {"type":"stream_event",
             "event":{"type":"content_block_delta",
                      "delta":{"type":"text_delta","text":"ok"}},
             ...}

        Ne PAS lire les events ``"type":"assistant"`` (qui contiennent le
        message complet) — ça doublonnerait le texte streamé.
        """
        if not isinstance(event, dict):
            return None

        # Format Claude Code 2.x avec wrapping stream_event
        if event.get("type") == "stream_event":
            inner = event.get("event")
            if isinstance(inner, dict) and inner.get("type") == "content_block_delta":
                delta = inner.get("delta")
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    return delta.get("text") or None
            return None

        # Fallbacks pour formats simples (anciennes versions / variantes)
        if event.get("type") == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                return delta.get("text") or None
        if event.get("type") == "text" and isinstance(event.get("text"), str):
            return event["text"]
        if event.get("type") == "delta" and isinstance(event.get("text"), str):
            return event["text"]
        for key in ("text", "delta"):
            v = event.get(key)
            if isinstance(v, str):
                return v
        return None

    @staticmethod
    def _extract_cli_usage(event: dict) -> Optional[dict]:
        """Extrait ``input_tokens`` / ``output_tokens`` depuis n'importe quel event.

        Le CLI émet ``usage`` à plusieurs endroits selon le type :
        - ``stream_event > event > usage`` (message_delta intermédiaire)
        - ``stream_event > event > message > usage`` (message_start)
        - ``message > usage`` (event ``"type":"assistant"``)
        - ``usage`` au top level (event ``"type":"result"`` final)

        On les renvoie tous, le caller (boucle ``_stream_via_cli``) écrase
        sur les valeurs successives — la dernière vue (du ``result`` final)
        gagne.
        """
        if not isinstance(event, dict):
            return None

        usage = None
        # Format Claude Code 2.x avec wrapping
        if event.get("type") == "stream_event":
            inner = event.get("event")
            if isinstance(inner, dict):
                usage = inner.get("usage")
                if usage is None:
                    msg = inner.get("message")
                    if isinstance(msg, dict):
                        usage = msg.get("usage")
        # Top-level (events "result", "assistant")
        if usage is None:
            usage = event.get("usage")
        if usage is None:
            msg = event.get("message")
            if isinstance(msg, dict):
                usage = msg.get("usage")
        if not isinstance(usage, dict):
            return None
        return {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        }
