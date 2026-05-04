# ARCHITECTURE.md — Compagnon_Revision

> **Spec technique détaillée pour Claude Code.**
> Lu en complément de `CLAUDE.md` au début de chaque session de développement.
> Ne touche pas à ce fichier sans validation explicite de Gstar.

---

## 0. À LIRE AVANT TOUTE CHOSE

Ce document spécifie **comment** coder le compagnon de révision. `CLAUDE.md` dit *quoi* coder et *avec quelles règles*. Ce document dit *comment* assembler les briques, quels schémas JSON utiliser, quelles signatures de fonctions exposer, comment gérer le streaming SSE, etc.

L'ordre de lecture recommandé :
1. `CLAUDE.md` (manuel d'instructions générales)
2. `_prompts/PROMPT_SYSTEME_COMPAGNON.md` (spec pédagogique du compagnon)
3. Ce fichier (spec technique)

Les sections suivent l'ordre de construction Phase A de `CLAUDE.md` §9.

---

## 1. VUE D'ENSEMBLE — FLUX DE DONNÉES

### 1.1 Boucle de session — schéma général

```
┌─────────────────────────────────────────────────────────────────────┐
│                       FRONT FLASK (navigateur)                      │
│  ┌─────────────────┐    ┌──────────────┐    ┌──────────────────┐    │
│  │ Push-to-talk    │    │ Zone dialogue│    │ Sidebar quota    │    │
│  │ (espace global) │    │ (SSE stream) │    │ (poll 60s)       │    │
│  └────────┬────────┘    └──────▲───────┘    └────────▲─────────┘    │
└───────────┼────────────────────┼─────────────────────┼──────────────┘
            │ keyboard event     │ SSE events          │ HTTP poll
            ▼                    │                     │
  ┌─────────────────────────────────────────────────────────────────┐
  │                     FLASK APP (port 5680)                       │
  │  /api/start_recording  /api/stop_recording  /api/quota          │
  │  /api/stream_response  /api/upload_photo                        │
  └────┬────────────────────────────────┬───────────────┬───────────┘
       │                                │               │
       ▼                                ▼               │
  ┌──────────────┐                 ┌──────────────┐    │
  │ AUDIO        │                 │ DIALOGUE     │    │
  │ listener.py  │ ──[wav bytes]──▶│ claude_client│    │
  │ (sounddevice)│                 │              │    │
  └──────┬───────┘                 │ ┌──────────┐ │    │
         │                         │ │parser.py │ │    │
         ▼                         │ │ (states) │ │    │
  ┌──────────────┐                 │ └──────────┘ │    │
  │ transcribe_  │                 │              │    │
  │ stream.py    │ ──[texte]──────▶│              │    │
  │ (faster-     │                 │              │    │
  │  whisper GPU)│                 │              │    │
  └──────────────┘                 └──────┬───────┘    │
                                          │            │
                                          ▼            │
                                   ┌──────────────┐    │
                                   │session_state │    │
                                   │ (JSON atomic)│    │
                                   └──────────────┘    │
                                                       │
                                          ┌────────────┘
                                          ▼
                                   ┌──────────────┐
                                   │quota_check.py│
                                   │ (Arsenal)    │
                                   └──────────────┘
```

### 1.2 Phases d'une session
1. **Démarrage** : check quota → charge contexte (énoncé + transcription CM + points faibles passés) → instancie `SessionState` → ouvre conversation Claude avec system prompt + contexte initial → Claude pose la première question.
2. **Boucle dialogue** : push-to-talk → enregistrement WAV → Whisper → texte étudiant → envoi à Claude (avec historique conversation) → streaming SSE de la réponse → parser extrait balises → affichage front.
3. **Capture** : à chaque `<<<WEAK_POINT>>>` détecté par le parser, écriture atomique dans le JSON de session.
4. **Fin** : `<<<END_SESSION>>>` détecté → finalisation JSON (`ended_at`, `interrupted: false`) → réponse front avec récap → fermeture front.

### 1.3 Heartbeat (pour reprise de session interrompue)
En parallèle de la boucle dialogue, un thread daemon écrit `last_alive: ISO timestamp` toutes les 30 secondes dans le JSON de session via atomic write.

Au démarrage suivant du compagnon, scan de `_sessions/*.json` :
- Si une session a `interrupted: true` ou (`ended_at` absent ET `last_alive` < maintenant - 5 min), elle est marquée comme reprenable.
- Le front propose à Gstar : "Session AN1 TD5 ex3 du 02/05 interrompue, reprendre ?" (oui = `[RESUME_SESSION]` envoyé au prompt, non = nouvelle session).

---

## 2. SCHÉMA JSON D'UNE SESSION (DÉTAILLÉ)

### 2.1 Format complet
Fichier `_sessions/YYYY-MM-DD_{MAT}_{TYPE}{N}_ex{n}.json` :

```json
{
  "schema_version": 1,
  "session_id": "2026-05-02_AN1_TD5_ex3",
  "matiere": "AN1",
  "type": "TD",
  "num": "5",
  "exo": "3",

  "started_at": "2026-05-02T19:30:00+02:00",
  "ended_at": "2026-05-02T20:18:42+02:00",
  "last_alive": "2026-05-02T20:18:42+02:00",
  "interrupted": false,
  "interrupted_at": null,
  "resumed_at": null,
  "duration_seconds": 2922,

  "engine": "cli_subscription",
  "model": "claude-opus-4-7",

  "context_files": {
    "enonce": "AN1/TD/AN1_TD5_enonce.pdf",
    "transcription_cm": "AN1/CM/CM6_AN1_dérivation.txt",
    "poly_cm": "AN1/CM/poly_AN1_ISTIC_Etude_fonction.pdf",
    "previous_weak_points": "_points_faibles/AN1_points_faibles.csv"
  },

  "weak_points": [
    {
      "id": "wp_a3f81e2c-7b42-11ee-b962-0242ac120002",
      "captured_at": "2026-05-02T19:48:13+02:00",
      "concept": "théorème des accroissements finis",
      "what_failed": "hypothèses non énoncées spontanément, application après indice 2",
      "score": 1,
      "cm_anchor": {
        "transcription": "AN1/CM/CM6_AN1_dérivation.txt",
        "poly": "AN1/CM/poly_AN1_ISTIC_Etude_fonction.pdf",
        "section": "Théorème des accroissements finis et inégalité de Lagrange"
      },
      "cm_anchor_malformed": false,
      "exercise_context": "ex3 question 2"
    }
  ],

  "transcript": [
    {
      "role": "claude",
      "at": "2026-05-02T19:30:05+02:00",
      "text": "Exercice 3. Énoncez la première chose que vous comptez faire."
    },
    {
      "role": "student",
      "at": "2026-05-02T19:30:24+02:00",
      "text": "Heu, je vais dériver la fonction.",
      "audio_path": "_logs/audio/2026-05-02_19-30-24.wav"
    },
    {
      "role": "claude",
      "at": "2026-05-02T19:30:32+02:00",
      "text": "« Heu » n'est pas une démarche. Pourquoi dériver ?"
    }
  ],

  "stats": {
    "total_exchanges": 47,
    "weak_points_count": 1,
    "claude_tokens_input": 18432,
    "claude_tokens_output": 3214,
    "whisper_seconds": 612.4,
    "tts_calls": 3,
    "photos_received": 0,
    "silences_detected": 4
  }
}
```

### 2.2 Champs obligatoires en écriture initiale
Au démarrage, le JSON est créé avec ces champs minimum :
```json
{
  "schema_version": 1,
  "session_id": "...",
  "matiere": "...",
  "type": "...",
  "num": "...",
  "exo": "...",
  "started_at": "...",
  "last_alive": "...",
  "interrupted": false,
  "engine": "...",
  "model": "...",
  "context_files": {...},
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
    "silences_detected": 0
  }
}
```

`ended_at`, `interrupted_at`, `resumed_at` restent à `null` jusqu'à l'événement correspondant. `duration_seconds` est calculé à la fin.

### 2.3 Conventions
- **Toutes les dates en ISO 8601 avec timezone** : `2026-05-02T19:30:00+02:00`. Utiliser `zoneinfo.ZoneInfo("Europe/Paris")` (cf. pattern Arsenal).
- **`session_id`** : identique au nom du fichier sans `.json`. Utilisé pour les références croisées (export Anki, logs).
- **`weak_points[].id`** : UUID v1 ou v4, préfixe `wp_`. Utilisé pour le SRS Anki.
- **`audio_path`** dans transcript : chemin relatif au projet, peut être absent si Whisper a transcrit en streaming sans persister le WAV.
- **`exercise_context`** dans `weak_points[]` : libre, format court genre `"ex3 question 2"` ou `"ex2 calcul des dérivées partielles"`. Permet au SRS de remettre le point en contexte plus tard.

### 2.4 Migration de schéma
Pour toute évolution de schéma :
1. Incrémenter `schema_version`
2. Ajouter une fonction `_migrate_v{N-1}_to_v{N}(data: dict) -> dict` dans `session_state.py`
3. Au load d'un JSON, si `schema_version` < courant, appliquer toutes les migrations en chaîne avant validation
4. Le fichier est réécrit (atomic write) à la version courante au prochain `flush()`

---

## 3. MACHINE À ÉTATS DU PARSER SSE

### 3.1 Problème à résoudre
Le streaming SSE de Claude renvoie le texte en chunks. Une balise `<<<WEAK_POINT>>>{...}<<<END>>>` peut arriver sur 5 chunks séparés. Le front Flask ne doit jamais voir le contenu d'une balise (qui est destiné au parser, pas à Gstar).

Solution : machine à états qui buffère pendant qu'on est "à l'intérieur potentielle" d'une balise.

### 3.2 États
```python
from enum import Enum

class ParserState(Enum):
    OUTSIDE = "outside"              # texte normal, on flush vers le front
    PROBE_OPENING = "probe_opening"  # on a vu '<' ou '<<' ou '<<<', incertitude
    INSIDE_TTS = "inside_tts"        # on est entre <<<TTS>>> et <<<END>>>
    INSIDE_WEAK_POINT = "inside_weak_point"
    INSIDE_END_SESSION = "inside_end_session"  # on a vu <<<END_S, on attend la fin
    PROBE_CLOSING = "probe_closing"  # on a vu '<' à l'intérieur d'une balise
```

### 3.3 Transitions

```
État courant         Input              → Nouvel état           Action
──────────────────────────────────────────────────────────────────────────
OUTSIDE              char != '<'        OUTSIDE                 flush char
OUTSIDE              '<'                PROBE_OPENING           buffer '<'

PROBE_OPENING        complète à '<<<TTS>>>'           INSIDE_TTS              consomme la balise
PROBE_OPENING        complète à '<<<WEAK_POINT>>>'    INSIDE_WEAK_POINT       consomme la balise
PROBE_OPENING        complète à '<<<END_SESSION>>>'   END                     émet event END_SESSION
PROBE_OPENING        ne matche aucun pattern         OUTSIDE                 flush le buffer

INSIDE_TTS           char != '<'        INSIDE_TTS              accumule dans tts_buffer
INSIDE_TTS           '<'                PROBE_CLOSING (depuis INSIDE_TTS)

PROBE_CLOSING        complète à '<<<END>>>'          OUTSIDE                 émet event TTS(tts_buffer), reset
PROBE_CLOSING        ne matche pas      retour à l'état parent  buffer rejoint le contenu

INSIDE_WEAK_POINT    char != '<'        INSIDE_WEAK_POINT       accumule dans wp_buffer
INSIDE_WEAK_POINT    '<'                PROBE_CLOSING (depuis INSIDE_WEAK_POINT)

PROBE_CLOSING        complète à '<<<END>>>' (depuis WP)  OUTSIDE             émet event WEAK_POINT(parse_json(wp_buffer))
```

### 3.4 Implémentation suggérée — `_scripts/dialogue/parser.py`

```python
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ParserEventType(Enum):
    TEXT_CHUNK = "text_chunk"        # texte à afficher au front
    TTS = "tts"                       # phrase à vocaliser
    WEAK_POINT = "weak_point"         # point faible capturé
    END_SESSION = "end_session"       # fin de séance


@dataclass
class ParserEvent:
    type: ParserEventType
    payload: str | dict


# Balises supportées (ouverture)
_TAG_TTS_OPEN = "<<<TTS>>>"
_TAG_WP_OPEN = "<<<WEAK_POINT>>>"
_TAG_END_SESSION = "<<<END_SESSION>>>"
_TAG_CLOSE = "<<<END>>>"

# Pour la reconnaissance partielle pendant le buffering
_OPENING_PATTERNS = [_TAG_TTS_OPEN, _TAG_WP_OPEN, _TAG_END_SESSION]


class StreamParser:
    """Machine à états qui consomme un stream SSE caractère par caractère
    et émet des événements pour le front et la couche dialogue.
    
    Tolérant aux malformations : si une balise WEAK_POINT a un JSON invalide,
    elle est loggée comme warning et flush au front (visible mais pas exploitée).
    """

    def __init__(self, on_event: Callable[[ParserEvent], None]):
        self._on_event = on_event
        self._state: ParserState = ParserState.OUTSIDE
        self._buffer: str = ""        # buffer pendant probe
        self._inner_buffer: str = ""  # contenu entre balises (TTS text, WP json)
        self._return_state: Optional[ParserState] = None  # pour PROBE_CLOSING

    def feed(self, chunk: str) -> None:
        """Consomme un chunk de stream et émet les événements appropriés."""
        for char in chunk:
            self._step(char)

    def flush(self) -> None:
        """Vide ce qui reste à la fin du stream. Le buffer en cours, s'il existe,
        est traité comme du texte normal (cas de stream tronqué sans balise fermante).
        """
        if self._buffer:
            self._emit(ParserEventType.TEXT_CHUNK, self._buffer)
            self._buffer = ""
        if self._state != ParserState.OUTSIDE:
            logger.warning(
                "Stream tronqué dans état %s, contenu %r perdu",
                self._state, self._inner_buffer
            )
            self._inner_buffer = ""
            self._state = ParserState.OUTSIDE

    def _step(self, char: str) -> None:
        # Implémentation de la machine à états (voir §3.3)
        # ... (à coder par Claude Code en suivant la table de transitions)
        raise NotImplementedError

    def _emit(self, event_type: ParserEventType, payload) -> None:
        self._on_event(ParserEvent(type=event_type, payload=payload))

    def _try_parse_weak_point(self, raw_json: str) -> Optional[dict]:
        """Parse le JSON d'un weak_point, retourne None si malformé.
        
        En cas d'erreur : log warning, retourne None. Le caller doit alors
        écrire un weak_point avec cm_anchor_malformed=true et raw_json en
        debug, mais NE DOIT PAS faire planter la session.
        """
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.warning("WEAK_POINT JSON invalide: %s — raw=%r", e, raw_json)
            return None
        # Validation du schéma minimal
        required = {"concept", "what_failed", "score"}
        if not required.issubset(data.keys()):
            missing = required - data.keys()
            logger.warning("WEAK_POINT champs manquants: %s — raw=%r", missing, raw_json)
            return None
        if not isinstance(data["score"], int) or not 0 <= data["score"] <= 4:
            logger.warning("WEAK_POINT score invalide: %r", data.get("score"))
            return None
        # Normalisation cm_anchor
        data["cm_anchor"] = self._normalize_cm_anchor(data.get("cm_anchor"))
        return data

    def _normalize_cm_anchor(self, anchor) -> Optional[dict]:
        """Normalise le champ cm_anchor selon la spec _prompts/...md §5.2."""
        if anchor is None:
            return None
        if not isinstance(anchor, dict):
            logger.warning("cm_anchor pas un dict: %r", anchor)
            return None
        # Au moins un sous-champ présent
        if not any(k in anchor for k in ("transcription", "poly", "section")):
            logger.warning("cm_anchor vide: %r", anchor)
            return None
        # Vérification chemins relatifs
        for key in ("transcription", "poly"):
            if key in anchor:
                path = anchor[key]
                if path.startswith(("C:", "/", "\\")):
                    logger.warning(
                        "cm_anchor.%s chemin absolu détecté: %r — mise à null",
                        key, path
                    )
                    anchor[key] = None
        # Si après normalisation tout est null, on retourne None
        if not any(anchor.get(k) for k in ("transcription", "poly", "section")):
            return None
        return anchor
```

### 3.5 Tests Phase A — `tests/test_parser.py`
Cas à couvrir :
1. Texte simple sans balise → tout flushé en TEXT_CHUNK
2. `<<<TTS>>>Bonjour<<<END>>>` complet en un chunk → 1 event TTS
3. `<<<TTS>>>Bonjour<<<END>>>` coupé en 5 chunks → 1 event TTS
4. `Salut <<<TTS>>>OK<<<END>>> suite` → 3 events (TEXT, TTS, TEXT)
5. WEAK_POINT JSON valide → 1 event WEAK_POINT avec dict parsé
6. WEAK_POINT JSON malformé → 0 event, warning logué
7. `<<<END_SESSION>>>` seul → 1 event END_SESSION
8. Faux positif `<<<X>>>` (pas une balise reconnue) → flush comme texte
9. Stream tronqué pendant `<<<TT...` → buffer perdu, warning logué, état revient à OUTSIDE

---

## 4. CLIENT CLAUDE — `_scripts/dialogue/claude_client.py`

### 4.1 Responsabilités
- Lit `_secrets/engine_pref.json` pour savoir s'il appelle CLI subscription ou API Anthropic
- Construit la requête (system prompt + historique + nouveau message utilisateur)
- Streame la réponse en SSE
- Délègue le parsing au `StreamParser`
- Track les tokens consommés (si dispo)

### 4.2 Interface publique
```python
from typing import Iterator, Callable
from pathlib import Path
from .parser import StreamParser, ParserEvent

class ClaudeClient:
    """Wrapper unique pour les deux moteurs (CLI subscription / API Anthropic)."""

    def __init__(
        self,
        engine: str,                          # "cli_subscription" | "api_anthropic"
        system_prompt: str,                    # contenu de PROMPT_SYSTEME_COMPAGNON.md
        model: str = "claude-opus-4-7",
    ):
        self._engine = engine
        self._system_prompt = system_prompt
        self._model = model
        self._history: list[dict] = []  # [{"role": "user"|"assistant", "content": "..."}]

    def append_user_message(self, text: str) -> None:
        """Ajoute un message utilisateur à l'historique sans appeler Claude."""
        self._history.append({"role": "user", "content": text})

    def stream_response(
        self,
        on_event: Callable[[ParserEvent], None],
    ) -> dict:
        """Appelle Claude avec l'historique courant, streame la réponse,
        et délègue le parsing à StreamParser.
        
        Returns: dict avec stats {"input_tokens": int, "output_tokens": int}
        Note: la réponse complète de Claude est ajoutée à l'historique.
        """
        if self._engine == "cli_subscription":
            return self._stream_via_cli(on_event)
        elif self._engine == "api_anthropic":
            return self._stream_via_api(on_event)
        else:
            raise ValueError(f"Engine inconnu : {self._engine}")

    def _stream_via_cli(self, on_event) -> dict:
        # subprocess.Popen sur `claude --print --output-format stream-json ...`
        # Parse les chunks JSON émis par le CLI, extrait le delta texte,
        # le passe à un StreamParser local.
        # En fin de stream : récupère les stats tokens depuis le dernier event JSON.
        ...

    def _stream_via_api(self, on_event) -> dict:
        # Utilise le SDK anthropic en mode streaming
        # client.messages.stream(...) avec system=self._system_prompt
        ...
```

### 4.3 CLI subscription — détails techniques
La CLI Claude expose un mode JSON streaming :
```
claude --print --output-format stream-json --input-format text "<message>"
```
Avec system prompt via fichier :
```
claude --print --output-format stream-json --append-system-prompt "$(cat _prompts/PROMPT_SYSTEME_COMPAGNON.md)" "<message>"
```

Les flags exacts peuvent varier selon version. À tester en Phase A et ajuster.

L'env doit avoir `ANTHROPIC_API_KEY` unset pour forcer OAuth/keychain (cf. `start_claude_code_session.ps1` §6).

### 4.4 API Anthropic — détails techniques
```python
import anthropic
client = anthropic.Anthropic()  # lit ANTHROPIC_API_KEY depuis env
with client.messages.stream(
    model="claude-opus-4-7",
    max_tokens=4096,
    system=self._system_prompt,
    messages=self._history,
) as stream:
    for text in stream.text_stream:
        parser.feed(text)
    parser.flush()
    final_message = stream.get_final_message()
    return {
        "input_tokens": final_message.usage.input_tokens,
        "output_tokens": final_message.usage.output_tokens,
    }
```

### 4.5 Gestion d'erreurs
- **CLI quota épuisé** : la CLI retourne un code erreur ou un message JSON spécifique. Catch, log, propose à Gstar via le front de switcher en API Anthropic.
- **API rate limit** : retry exponential backoff (max 3 tentatives, 1s/2s/4s).
- **Network error** : log + retry x1, sinon erreur remontée au front avec message clair.

---

## 5. PROMPT BUILDER — `_scripts/dialogue/prompt_builder.py`

### 5.1 Responsabilités
- Charge le prompt système (`_prompts/PROMPT_SYSTEME_COMPAGNON.md`) — invariant
- Assemble le **contexte initial** de la session (variable selon TD/CC/exo)
- Compose le premier message utilisateur qui démarre la conversation

### 5.2 Format du contexte initial
Le contexte initial est envoyé comme **premier message utilisateur** (role=user) à Claude. Il inclut :

```
=== CONTEXTE DE LA SÉANCE ===

Matière : AN1 (Analyse 1)
Type : TD 5
Exercice ciblé : exercice 3
Date : 2026-05-02
Heure de début : 19:30
Durée prévue : 45-60 minutes

=== ÉNONCÉ DE L'EXERCICE ===

[contenu extrait du PDF AN1_TD5_enonce.pdf, exercice 3 uniquement
 si extractible — sinon TD entier avec mention "ciblez ex3"]

=== TRANSCRIPTION CM PERTINENTE ===

[contenu de AN1/CM/CM6_AN1_dérivation.txt, sections relatives au sujet
 de l'exercice si identifiables — sinon CM entier avec un cap à ~4000 mots]

=== POLY DU PROF (extraits) ===

[si disponible : extraits OCR/lecture du PDF poly relevant
 — sinon section omise]

=== POINTS FAIBLES HISTORIQUES SUR AN1 ===

[lecture de _points_faibles/AN1_points_faibles.csv si existe,
 5 points faibles les plus récents avec scores]

=== INSTRUCTIONS ===

Démarrez la séance. Posez la première question selon §2.2 du prompt système.
```

### 5.3 Interface publique
```python
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

@dataclass
class SessionContext:
    matiere: str
    type: str           # "TD" | "CC" | "Examen"
    num: str
    exo: str            # "3" ou "full"
    enonce_path: Path   # absolu
    cm_transcription_path: Optional[Path] = None
    cm_poly_path: Optional[Path] = None
    previous_weak_points_path: Optional[Path] = None


class PromptBuilder:
    def __init__(self, system_prompt_path: Path, cours_root: Path):
        self._system_prompt = system_prompt_path.read_text(encoding="utf-8")
        self._cours_root = cours_root

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def build_initial_context_message(
        self,
        ctx: SessionContext,
        is_resume: bool = False,
    ) -> str:
        """Construit le premier message user à envoyer à Claude.
        
        Si is_resume=True, ajoute le marker [RESUME_SESSION] et un récap court
        des derniers échanges de la session interrompue.
        """
        ...
```

### 5.4 Extraction de l'énoncé d'un PDF
Phase A : extraction texte via `pypdf2` ou `pdfplumber`. Si l'extraction est de mauvaise qualité (PDF scanné), fallback : injection du PDF entier en multimodal Claude (mais ça consomme plus de tokens).

Pour Phase A, on accepte l'extraction texte simple. La qualité sera évaluée à l'usage.

---

## 6. SESSION STATE — `_scripts/dialogue/session_state.py`

### 6.1 Responsabilités
- Crée et maintient le JSON de session
- Atomic write à chaque modification structurelle
- Heartbeat thread qui met à jour `last_alive` toutes les 30s
- Gère la finalisation (calcul `duration_seconds`, écriture `ended_at`, etc.)

### 6.2 Interface publique
```python
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

class SessionState:
    HEARTBEAT_INTERVAL_SECONDS = 30

    def __init__(
        self,
        session_id: str,
        sessions_dir: Path,
        context: SessionContext,
        engine: str,
        model: str,
    ):
        self._path = sessions_dir / f"{session_id}.json"
        self._data: dict = self._build_initial_data(...)
        self._lock = threading.Lock()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()

    def start(self) -> None:
        """Crée le fichier JSON et démarre le heartbeat thread."""
        ...

    def append_exchange(self, role: str, text: str, audio_path: Optional[Path] = None) -> None:
        """Ajoute un échange au transcript, atomic write."""
        ...

    def add_weak_point(self, wp: dict) -> None:
        """Ajoute un weak_point (déjà validé/normalisé par le parser), atomic write."""
        ...

    def increment_stat(self, key: str, delta: float = 1) -> None:
        """Incrémente une stat (tokens, photos, etc.)."""
        ...

    def finalize(self, interrupted: bool = False) -> None:
        """Stoppe le heartbeat, écrit ended_at et duration_seconds, atomic write final."""
        ...

    @classmethod
    def load(cls, path: Path) -> "SessionState":
        """Charge une session existante (pour reprise)."""
        ...

    @classmethod
    def find_resumable(cls, sessions_dir: Path) -> list[Path]:
        """Liste les sessions reprenables (interrupted=true ou last_alive ancien)."""
        ...
```

### 6.3 Atomic write helper
À placer dans un module partagé (`_scripts/utils.py` à créer en Phase A) :
```python
import json
import os
from pathlib import Path

def atomic_write_json(path: Path, data: dict) -> None:
    """Écrit data en JSON dans path de façon atomique (.tmp + os.replace)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
```

### 6.4 Heartbeat thread
```python
def _heartbeat_loop(self) -> None:
    while not self._stop_heartbeat.is_set():
        with self._lock:
            self._data["last_alive"] = _now_iso()
            atomic_write_json(self._path, self._data)
        self._stop_heartbeat.wait(self.HEARTBEAT_INTERVAL_SECONDS)
```

Daemon=True pour qu'il meure automatiquement avec le process principal en cas de crash brutal. Le `last_alive` ancien permettra alors la détection à la reprise.

---

## 7. AUDIO — PUSH-TO-TALK ET WHISPER

### 7.1 `_scripts/audio/listener.py` — push-to-talk
```python
import sounddevice as sd
import numpy as np
import keyboard  # global hotkey, Windows-friendly
from pathlib import Path
from datetime import datetime

class PushToTalkListener:
    SAMPLE_RATE = 16000  # Whisper natif
    CHANNELS = 1
    HOTKEY = "space"

    def __init__(self, on_recording_complete):
        self._on_complete = on_recording_complete  # callback(wav_path: Path)
        self._is_recording = False
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None

    def start(self) -> None:
        keyboard.on_press_key(self.HOTKEY, self._on_press)
        keyboard.on_release_key(self.HOTKEY, self._on_release)

    def stop(self) -> None:
        keyboard.unhook_all()

    def _on_press(self, e) -> None:
        if self._is_recording:
            return
        self._is_recording = True
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            callback=self._audio_callback,
        )
        self._stream.start()

    def _on_release(self, e) -> None:
        if not self._is_recording:
            return
        self._is_recording = False
        self._stream.stop()
        self._stream.close()
        wav_path = self._save_wav()
        self._on_complete(wav_path)

    def _audio_callback(self, indata, frames, time, status):
        if status:
            logger.warning("Audio status: %s", status)
        self._frames.append(indata.copy())

    def _save_wav(self) -> Path:
        ...
```

### 7.2 `_scripts/audio/transcribe_stream.py` — wrapper Whisper
Phase A : version simple, **non-streaming** (transcription complète après que le WAV est sauvé). Le streaming Whisper viendra en Phase B si besoin.

```python
from faster_whisper import WhisperModel
from pathlib import Path

class WhisperTranscriber:
    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "int8_float16",
    ):
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, wav_path: Path, language: str = "fr") -> tuple[str, float]:
        """Retourne (texte_concaténé, durée_audio_secondes)."""
        segments, info = self._model.transcribe(
            str(wav_path),
            language=language,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text, info.duration
```

### 7.3 Détection de silence (pour `[SILENCE_10S]`)
Phase A : détection côté front Flask via le hotkey. Si aucune frappe espace dans les 10 secondes après une réponse Claude, le front envoie un message synthétique `[SILENCE_10S]` au backend, qui l'injecte comme un message utilisateur dans la conversation Claude.

---

## 8. FRONT FLASK — `_scripts/web/app.py`

### 8.1 Endpoints

| Méthode | Path | Description |
|---------|------|-------------|
| GET | `/` | Page principale, sert `index.html` |
| GET | `/api/quota` | Snapshot quota (JSON), poll côté client |
| POST | `/api/start_session` | Démarre une session avec body `{matiere, type, num, exo}` |
| GET | `/api/stream_response` | SSE qui streame la réponse Claude après envoi user |
| POST | `/api/send_message` | Envoie un message user, retourne 202 puis SSE prend le relai |
| POST | `/api/upload_photo` | Upload manuel d'une photo (Phase B, stub en Phase A) |
| POST | `/api/end_session` | Force la fin propre |

### 8.2 SSE streaming
```python
from flask import Response, stream_with_context

@app.route("/api/stream_response")
def stream_response():
    def generate():
        # Le ClaudeClient.stream_response émet des events au parser,
        # le parser émet des ParserEvent qu'on transforme en SSE
        for event in get_pending_events():
            if event.type == ParserEventType.TEXT_CHUNK:
                yield f"event: text\ndata: {json.dumps(event.payload)}\n\n"
            elif event.type == ParserEventType.TTS:
                yield f"event: tts\ndata: {json.dumps(event.payload)}\n\n"
            elif event.type == ParserEventType.WEAK_POINT:
                # Pas envoyé au front (interne)
                continue
            elif event.type == ParserEventType.END_SESSION:
                yield f"event: end\ndata: {{}}\n\n"
                break
    return Response(stream_with_context(generate()), mimetype="text/event-stream")
```

### 8.3 Front HTML — squelette minimal Phase A

```html
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <title>Compagnon de révision</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div class="layout">
    <main id="dialogue">
      <!-- les échanges Claude/étudiant s'ajoutent ici en temps réel -->
    </main>
    <aside id="sidebar">
      <div id="quota-panel"></div>
      <div id="record-indicator">Maintenir [Espace] pour parler</div>
      <button id="end-session">Terminer la séance</button>
    </aside>
  </div>
  <script src="/static/app.js"></script>
</body>
</html>
```

Pas de framework JS Phase A. Vanilla JS suffit pour SSE + fetch.

### 8.4 Lancement
```python
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5680, debug=False, threaded=True)
```

Port 5680 pour ne pas conflicter avec `arsenal_agent.py` (5679).

---

## 9. QUOTA — `_scripts/quota/quota_check.py`

### 9.1 Wrapper minimal
```python
import sys
from pathlib import Path

# Ajout au path d'Arsenal_Arguments (Phase A, à supprimer en Phase B)
ARSENAL_PATH = Path(__file__).resolve().parents[2] / "Arsenal_Arguments"
if str(ARSENAL_PATH) not in sys.path:
    sys.path.insert(0, str(ARSENAL_PATH))

from claude_usage import fetch_usage  # noqa: E402

# Seuils Compagnon (différents de la GUI Arsenal qui est à 70/80)
THRESHOLD_5H_BLOCK_SESSION = 85
THRESHOLD_7D_BLOCK_SESSION = 90
THRESHOLD_5H_WARN_INSESSION = 90


def can_start_session() -> tuple[bool, str]:
    """Retourne (autorisé, raison_si_non)."""
    try:
        usage = fetch_usage()
    except Exception as e:
        # Mode tolérant : si le check échoue, on laisse passer avec warning
        logger.warning("Quota check échoué : %s — autorisation par défaut", e)
        return True, ""

    if usage["five_hour"]["utilization"] > THRESHOLD_5H_BLOCK_SESSION:
        reset = usage["five_hour"]["resets_at"]
        return False, f"Quota 5h à {usage['five_hour']['utilization']}%, reset à {reset}"

    if usage["seven_day"]["utilization"] > THRESHOLD_7D_BLOCK_SESSION:
        reset = usage["seven_day"]["resets_at"]
        return False, f"Quota hebdo à {usage['seven_day']['utilization']}%, reset à {reset}"

    return True, ""


def get_usage_snapshot() -> dict:
    """Snapshot pour affichage front (sidebar)."""
    try:
        return fetch_usage()
    except Exception:
        return {"error": "unavailable"}
```

---

## 10. ENTRY POINT — `compagnon.py`

### 10.1 Mode CLI (Phase A)
```python
import argparse
import logging
from pathlib import Path

import config
from _scripts.dialogue.prompt_builder import PromptBuilder, SessionContext
from _scripts.dialogue.claude_client import ClaudeClient
from _scripts.dialogue.session_state import SessionState
from _scripts.dialogue.parser import StreamParser
from _scripts.audio.listener import PushToTalkListener
from _scripts.audio.transcribe_stream import WhisperTranscriber
from _scripts.quota.quota_check import can_start_session
from _scripts.web.app import run_flask_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("matiere", help="AN1, EN1, PSI, ...")
    parser.add_argument("type", help="TD, CC, Examen")
    parser.add_argument("num", help="Numéro du TD/CC")
    parser.add_argument("exo", help="Numéro de l'exercice ou 'full'")
    parser.add_argument("--resume", action="store_true", help="Reprendre une session interrompue")
    args = parser.parse_args()

    # 1. Check quota
    ok, reason = can_start_session()
    if not ok:
        print(f"❌ Impossible de démarrer : {reason}")
        return 1

    # 2. Construit le contexte
    ctx = SessionContext(...)
    
    # 3. Lance Flask en thread + ouvre navigateur
    run_flask_app(ctx, args.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### 10.2 Lancement type
```powershell
cd C:\Users\Gstar\OneDrive\Documents\BotGSTAR\Compagnon_Revision
python compagnon.py AN1 TD 5 3
```

Ouvre le navigateur sur `http://127.0.0.1:5680/`, démarre Claude qui pose la première question, push-to-talk actif.

---

## 11. PLAN DE TESTS PHASE A

### 11.1 Tests automatiques (`tests/`)
Couverture minimale obligatoire avant validation Phase A :
- `test_parser.py` : 9 cas listés en §3.5
- `test_session_state.py` : create / append_exchange / add_weak_point / finalize / load / atomic write integrity (kill au milieu)
- `test_prompt_builder.py` : assemblage du contexte initial avec et sans points faibles, avec et sans poly

### 11.2 Tests manuels (à faire par Gstar)
1. **Smoke test** : lancer `python compagnon.py AN1 TD 5 3` avec quota OK, vérifier que Claude pose la première question.
2. **Push-to-talk** : maintenir espace, parler 3 secondes, relâcher, vérifier que Whisper transcrit et que Claude répond.
3. **Capture weak point** : provoquer un blocage (refuser de répondre 3 fois), vérifier que le JSON contient un weak_point bien formé.
4. **Heartbeat** : démarrer une session, attendre 60s, killer brutalement le process, redémarrer, vérifier que la session est listée comme reprenable.
5. **Quota tendu** : simuler quota >85% (cookie modifié manuellement), vérifier que `compagnon.py` refuse de démarrer.
6. **End session** : balise `<<<END_SESSION>>>` reçue, vérifier que `ended_at` et `duration_seconds` sont bien écrits.

### 11.3 Critère de validation Phase A (cf. CLAUDE.md §9)
> Gstar peut faire une session de révision de 30 min, AN1 TD5, dialogue texte propre, points faibles capturés en JSON, quota tracké en live dans la sidebar. Pas de TTS, pas de photo, pas de SRS — juste la boucle.

Si ce critère est validé sur 3 sessions réelles consécutives sans bug bloquant, on passe en Phase B.

---

## 12. ORDRE DE CODAGE RECOMMANDÉ — PHASE A

Pour Claude Code, ordre suggéré pour minimiser les blocages mutuels :

1. **`config.py`** — constantes de chemins. ~30 lignes.
2. **`_scripts/utils.py`** — `atomic_write_json`, helpers ISO timestamps. ~50 lignes.
3. **`_scripts/dialogue/parser.py`** — la machine à états. **Le morceau central, à coder en premier après les utils.** ~250 lignes avec tests.
4. **`tests/test_parser.py`** — les 9 cas. À coder **immédiatement après** parser.py, validation indispensable avant de continuer.
5. **`_scripts/dialogue/session_state.py`** — gestion JSON + heartbeat. ~200 lignes.
6. **`tests/test_session_state.py`**.
7. **`_scripts/quota/quota_check.py`** — wrapper minimal. ~80 lignes.
8. **`_scripts/audio/transcribe_stream.py`** — wrapper Whisper. ~80 lignes.
9. **`_scripts/audio/listener.py`** — push-to-talk. ~120 lignes.
10. **`_scripts/dialogue/prompt_builder.py`** — assemblage contexte. ~150 lignes.
11. **`tests/test_prompt_builder.py`**.
12. **`_scripts/dialogue/claude_client.py`** — wrapper API/CLI. ~250 lignes.
13. **`_scripts/web/app.py`** — Flask + SSE. ~200 lignes.
14. **`_scripts/web/templates/index.html` + `static/app.js`** — front minimal. ~150 lignes total.
15. **`compagnon.py`** — entry point qui colle tout ensemble. ~80 lignes.

Total Phase A estimé : **~2000 lignes de code Python + 300 de front + 400 de tests**.

À faire par bouts (cf. CLAUDE.md §6), avec validation Gstar à chaque module avant de passer au suivant.

---

## 13. RAPPEL FINAL

Cette spec est l'état au démarrage Phase A. Elle évoluera. Toute modification structurelle (changement de schéma JSON, ajout d'un nouvel état dans le parser, refonte d'une signature publique) passe par Gstar et Claude.ai, jamais par Claude Code seul.

Si Claude Code détecte que la spec est insuffisante ou ambiguë sur un point en cours de codage, il **arrête** et pose la question. Mieux vaut une question de plus que 200 lignes de code basé sur une devinette.
