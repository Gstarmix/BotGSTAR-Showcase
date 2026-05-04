"""
session_state.py — Gestion du JSON de session.

Crée et maintient le fichier ``_sessions/<session_id>.json``, append les
échanges et les weak_points en atomic write, fait tourner un thread daemon
de heartbeat (last_alive toutes les 30s), gère la finalisation propre ou
interrompue, charge une session existante pour la reprise.

Cf. ARCHITECTURE.md §6, §2 (schéma JSON), §1.3 (heartbeat / reprise).
"""

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Optional

from config import COURS_ROOT, PROJECT_ROOT, SCHEMA_VERSION_SESSION
from utils import atomic_write_json, now_iso, parse_iso, seconds_since

# SessionContext vit officiellement dans prompt_builder (cf. ARCHITECTURE.md §5.3).
# On le re-importe + ré-expose ici pour la rétrocompatibilité des callers
# qui font ``from session_state import SessionContext``.
from prompt_builder import SessionContext  # noqa: F401  (ré-export)

logger = logging.getLogger(__name__)


# ============================================================ Constantes

# Cf. ARCHITECTURE.md §1.3 : `last_alive < maintenant - 5 min` => reprenable.
RESUMABLE_LAST_ALIVE_THRESHOLD_SECONDS = 5 * 60


# ============================================================ SessionState

class SessionState:
    """Maintient le JSON de session. Atomic write à chaque mutation.

    Le heartbeat est un thread daemon qui rafraîchit ``last_alive`` toutes
    les ``HEARTBEAT_INTERVAL_SECONDS``. Si le process crashe brutalement,
    le thread meurt aussi et ``last_alive`` reste figé sur sa dernière
    valeur — la session sera détectée comme reprenable au prochain démarrage.
    """

    HEARTBEAT_INTERVAL_SECONDS = 30

    def __init__(
        self,
        session_id: str,
        sessions_dir: Path,
        context: SessionContext,
        engine: str,
        model: str,
    ):
        self._path: Path = sessions_dir / f"{session_id}.json"
        self._lock = threading.Lock()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()
        self._data: dict = self._build_initial_data(
            session_id, context, engine, model
        )

    # ---------------------------------------------------------------- factories

    @classmethod
    def load(cls, path: Path) -> "SessionState":
        """Charge une session existante depuis disque (pour reprise).

        N'écrit rien et ne démarre pas le heartbeat — l'appelant orchestre.
        Si ``schema_version`` est inattendu, log un warning mais ne raise pas
        (la migration douce est prévue en Phase B).
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != SCHEMA_VERSION_SESSION:
            logger.warning(
                "Session %s : schema_version %r != %r attendu",
                path.name, raw.get("schema_version"), SCHEMA_VERSION_SESSION,
            )
        instance = cls.__new__(cls)
        instance._path = path
        instance._lock = threading.Lock()
        instance._heartbeat_thread = None
        instance._stop_heartbeat = threading.Event()
        instance._data = raw
        return instance

    @classmethod
    def find_resumable(cls, sessions_dir: Path) -> list[Path]:
        """Liste les sessions reprenables.

        Critères (ARCHITECTURE.md §1.3) :
        - ``interrupted: true`` => reprenable
        - sinon, ``ended_at`` null/absent ET ``last_alive`` plus ancien que
          ``RESUMABLE_LAST_ALIVE_THRESHOLD_SECONDS`` => reprenable
        - sinon, considérée terminée proprement => ignorée

        Les fichiers illisibles sont skip avec warning.
        """
        if not sessions_dir.exists():
            return []
        results: list[Path] = []
        for path in sorted(sessions_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Session illisible ignorée : %s — %s", path.name, e)
                continue
            if data.get("interrupted"):
                results.append(path)
                continue
            if data.get("ended_at"):
                continue
            elapsed = seconds_since(data.get("last_alive"))
            if elapsed is None or elapsed > RESUMABLE_LAST_ALIVE_THRESHOLD_SECONDS:
                results.append(path)
        return results

    # ---------------------------------------------------------------- start/finalize

    def start(self) -> None:
        """Écrit le JSON initial sur disque et démarre le heartbeat thread."""
        with self._lock:
            self._data["last_alive"] = now_iso()
            atomic_write_json(self._path, self._data)
        self._start_heartbeat()

    def finalize(self, interrupted: bool = False) -> None:
        """Stoppe le heartbeat, écrit ``ended_at`` + ``duration_seconds``.

        Si ``interrupted=True``, pose aussi ``interrupted_at`` et
        ``interrupted: true``. Idempotent : un second appel n'a pas d'effet
        cassant (re-pose les mêmes champs avec un nouveau timestamp).
        """
        self._stop_heartbeat.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(
                timeout=self.HEARTBEAT_INTERVAL_SECONDS + 5
            )
            self._heartbeat_thread = None
        with self._lock:
            ended = now_iso()
            self._data["ended_at"] = ended
            self._data["last_alive"] = ended
            self._data["interrupted"] = bool(interrupted)
            self._data["interrupted_at"] = ended if interrupted else None
            try:
                started = parse_iso(self._data["started_at"])
                ended_dt = parse_iso(ended)
                self._data["duration_seconds"] = int(
                    (ended_dt - started).total_seconds()
                )
            except (KeyError, ValueError, TypeError):
                self._data["duration_seconds"] = None
            atomic_write_json(self._path, self._data)

    # ---------------------------------------------------------------- mutations

    def append_exchange(
        self,
        role: str,
        text: str,
        audio_path: Optional[Path] = None,
    ) -> None:
        """Ajoute un échange au transcript et incrémente ``total_exchanges``."""
        if role not in ("claude", "student"):
            raise ValueError(
                f"role invalide : {role!r} (attendu 'claude' | 'student')"
            )
        entry: dict = {
            "role": role,
            "at": now_iso(),
            "text": text,
        }
        if audio_path is not None:
            entry["audio_path"] = self._relativize(audio_path)
        with self._lock:
            self._data["transcript"].append(entry)
            self._data["stats"]["total_exchanges"] += 1
            atomic_write_json(self._path, self._data)

    def add_weak_point(self, wp: dict) -> None:
        """Ajoute un weak_point déjà validé/normalisé par le parser.

        Complète automatiquement ``id`` (UUID v4 préfixé ``wp_``),
        ``captured_at`` (now ISO), et garantit la présence de
        ``cm_anchor_malformed`` (default False) cf. ARCHITECTURE.md §2.3.
        """
        wp = dict(wp)  # copie défensive — on ne mute pas l'argument
        wp.setdefault("id", f"wp_{uuid.uuid4()}")
        wp.setdefault("captured_at", now_iso())
        wp.setdefault("cm_anchor_malformed", False)
        with self._lock:
            self._data["weak_points"].append(wp)
            self._data["stats"]["weak_points_count"] += 1
            atomic_write_json(self._path, self._data)

    def increment_stat(self, key: str, delta: float = 1) -> None:
        """Incrémente ``stats[key]`` de ``delta``. Crée la clé si absente."""
        with self._lock:
            self._data["stats"][key] = self._data["stats"].get(key, 0) + delta
            atomic_write_json(self._path, self._data)

    # ---------------------------------------------------------------- accesseurs

    @property
    def path(self) -> Path:
        return self._path

    @property
    def data(self) -> dict:
        """Snapshot read-only du dict interne."""
        return self._data

    # ---------------------------------------------------------------- internes

    def _build_initial_data(
        self,
        session_id: str,
        context: SessionContext,
        engine: str,
        model: str,
    ) -> dict:
        """Squelette JSON initial cf. ARCHITECTURE.md §2.2."""
        now = now_iso()
        return {
            "schema_version": SCHEMA_VERSION_SESSION,
            "session_id": session_id,
            "matiere": context.matiere,
            "type": context.type,
            "num": context.num,
            "exo": context.exo,
            "started_at": now,
            "ended_at": None,
            "last_alive": now,
            "interrupted": False,
            "interrupted_at": None,
            "resumed_at": None,
            "duration_seconds": None,
            "engine": engine,
            "model": model,
            "context_files": self._build_context_files(context),
            "weak_points": [],
            "transcript": [],
            "stats": {
                "total_exchanges": 0,
                "weak_points_count": 0,
                "claude_tokens_input": 0,
                "claude_tokens_output": 0,
                "whisper_seconds": 0.0,
                "tts_calls": 0,
                "photos_received": 0,
                "silences_detected": 0,
            },
        }

    def _build_context_files(self, context: SessionContext) -> dict:
        files = {"enonce": self._relativize(context.enonce_path)}
        if context.cm_transcription_path is not None:
            files["transcription_cm"] = self._relativize(context.cm_transcription_path)
        if context.cm_poly_path is not None:
            files["poly_cm"] = self._relativize(context.cm_poly_path)
        if context.previous_weak_points_path is not None:
            files["previous_weak_points"] = self._relativize(
                context.previous_weak_points_path
            )
        return files

    def _relativize(self, path: Path) -> str:
        """Tente un chemin relatif à COURS_ROOT puis PROJECT_ROOT.

        Si ``path`` n'est sous aucun des deux, retourne le str absolu
        (POSIX-style) — meilleur que rien pour les fichiers hors arbo.
        """
        path = Path(path)
        for root in (COURS_ROOT, PROJECT_ROOT):
            try:
                rel = path.resolve().relative_to(root.resolve())
                return rel.as_posix()
            except (ValueError, OSError):
                continue
        return path.as_posix()

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._stop_heartbeat.clear()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="session-heartbeat",
        )
        self._heartbeat_thread = thread
        thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.is_set():
            with self._lock:
                self._data["last_alive"] = now_iso()
                try:
                    atomic_write_json(self._path, self._data)
                except OSError as e:
                    logger.warning("Heartbeat atomic_write a échoué : %s", e)
            self._stop_heartbeat.wait(self.HEARTBEAT_INTERVAL_SECONDS)
