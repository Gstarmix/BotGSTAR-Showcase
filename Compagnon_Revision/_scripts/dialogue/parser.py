"""
parser.py — Machine à états SSE pour Claude streaming.

Consomme un flux SSE caractère par caractère et émet des événements typés
(``TEXT_CHUNK``, ``TTS``, ``WEAK_POINT``, ``END_SESSION``). Le contenu d'une
balise spéciale n'est jamais visible côté front Flask : il est buffé puis
extrait pour être routé vers le moteur TTS, le tracker de points faibles, ou
la finalisation de session.

Cf. ARCHITECTURE.md §3 et CLAUDE.md §4.
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)


# ============================================================ États

class ParserState(Enum):
    OUTSIDE = "outside"                  # texte normal, accumulé pour batch flush
    PROBE_OPENING = "probe_opening"      # vu '<', on accumule pour matcher une balise
    INSIDE_TTS = "inside_tts"            # entre <<<TTS>>> et <<<END>>>
    INSIDE_WEAK_POINT = "inside_weak_point"  # entre <<<WEAK_POINT>>> et <<<END>>>
    INSIDE_END_SESSION = "inside_end_session"  # réservé, non utilisé en pratique
    PROBE_CLOSING = "probe_closing"      # à l'intérieur d'une balise, vu '<' candidat


# ============================================================ Événements

class ParserEventType(Enum):
    TEXT_CHUNK = "text_chunk"
    TTS = "tts"
    WEAK_POINT = "weak_point"
    END_SESSION = "end_session"


@dataclass
class ParserEvent:
    type: ParserEventType
    payload: Union[str, dict]


# ============================================================ Balises

_TAG_TTS_OPEN = "<<<TTS>>>"
_TAG_WP_OPEN = "<<<WEAK_POINT>>>"
_TAG_END_SESSION = "<<<END_SESSION>>>"
_TAG_CLOSE = "<<<END>>>"

_OPENING_PATTERNS = [_TAG_TTS_OPEN, _TAG_WP_OPEN, _TAG_END_SESSION]


# ============================================================ Parser

class StreamParser:
    """Machine à états qui consomme un stream SSE caractère par caractère
    et émet des événements pour le front et la couche dialogue.

    Tolérante aux malformations : un WEAK_POINT au JSON cassé est loggué
    en warning et l'événement n'est pas émis (la session continue).
    """

    def __init__(self, on_event: Callable[[ParserEvent], None]):
        self._on_event = on_event
        self._state: ParserState = ParserState.OUTSIDE
        self._probe_buffer: str = ""        # depuis '<' en attente de match
        self._inner_buffer: str = ""        # contenu entre balises (TTS / WP json)
        self._text_buffer: str = ""         # texte accumulé en OUTSIDE pour batch
        self._return_state: Optional[ParserState] = None  # parent depuis PROBE_CLOSING

    # ---------------------------------------------------------------- API publique

    def feed(self, chunk: str) -> None:
        """Consomme un chunk de stream et émet les événements appropriés."""
        for char in chunk:
            self._step(char)

    def flush(self) -> None:
        """Vide ce qui reste à la fin du stream.

        - Le ``_text_buffer`` accumulé en OUTSIDE part en TEXT_CHUNK final.
        - Le ``_probe_buffer`` (PROBE_OPENING en cours) part aussi en TEXT_CHUNK :
          un fragment de balise tronqué vaut mieux que rien côté affichage.
        - Si on est dans un INSIDE_*, le contenu accumulé est perdu avec un
          warning : on n'invente pas un event TTS/WEAK_POINT depuis un fragment.
        """
        self._flush_text_buffer()
        if self._probe_buffer:
            self._emit(ParserEventType.TEXT_CHUNK, self._probe_buffer)
            self._probe_buffer = ""
        if self._state != ParserState.OUTSIDE:
            logger.warning(
                "Stream tronque dans etat %s, contenu inner=%r perdu",
                self._state.value, self._inner_buffer
            )
            self._inner_buffer = ""
            self._return_state = None
            self._state = ParserState.OUTSIDE

    # ---------------------------------------------------------------- step interne

    def _step(self, char: str) -> None:
        s = self._state
        if s == ParserState.OUTSIDE:
            self._step_outside(char)
        elif s == ParserState.PROBE_OPENING:
            self._step_probe_opening(char)
        elif s in (ParserState.INSIDE_TTS, ParserState.INSIDE_WEAK_POINT):
            self._step_inside_content(char)
        elif s == ParserState.PROBE_CLOSING:
            self._step_probe_closing(char)

    def _step_outside(self, char: str) -> None:
        if char == "<":
            self._flush_text_buffer()
            self._probe_buffer = char
            self._state = ParserState.PROBE_OPENING
        else:
            self._text_buffer += char

    def _step_probe_opening(self, char: str) -> None:
        self._probe_buffer += char
        if self._probe_buffer == _TAG_TTS_OPEN:
            self._probe_buffer = ""
            self._state = ParserState.INSIDE_TTS
        elif self._probe_buffer == _TAG_WP_OPEN:
            self._probe_buffer = ""
            self._state = ParserState.INSIDE_WEAK_POINT
        elif self._probe_buffer == _TAG_END_SESSION:
            self._probe_buffer = ""
            self._state = ParserState.OUTSIDE
            self._emit(ParserEventType.END_SESSION, "")
        elif not self._is_opening_prefix(self._probe_buffer):
            # Faux positif — flush comme texte, retour OUTSIDE.
            self._emit(ParserEventType.TEXT_CHUNK, self._probe_buffer)
            self._probe_buffer = ""
            self._state = ParserState.OUTSIDE
        # sinon (préfixe valide pas encore complet), on attend

    def _step_inside_content(self, char: str) -> None:
        if char == "<":
            self._return_state = self._state
            self._probe_buffer = char
            self._state = ParserState.PROBE_CLOSING
        else:
            self._inner_buffer += char

    def _step_probe_closing(self, char: str) -> None:
        self._probe_buffer += char
        if self._probe_buffer == _TAG_CLOSE:
            if self._return_state == ParserState.INSIDE_TTS:
                self._emit(ParserEventType.TTS, self._inner_buffer)
            elif self._return_state == ParserState.INSIDE_WEAK_POINT:
                parsed = self._try_parse_weak_point(self._inner_buffer)
                if parsed is not None:
                    self._emit(ParserEventType.WEAK_POINT, parsed)
                # sinon : warning loggué dans _try_parse, pas d'event émis
            self._probe_buffer = ""
            self._inner_buffer = ""
            self._return_state = None
            self._state = ParserState.OUTSIDE
        elif not _TAG_CLOSE.startswith(self._probe_buffer):
            # Pas un close — réintégrer le probe au inner_buffer et retour parent.
            self._inner_buffer += self._probe_buffer
            self._probe_buffer = ""
            self._state = self._return_state
            self._return_state = None
        # sinon (préfixe valide), on attend

    # ---------------------------------------------------------------- helpers

    def _is_opening_prefix(self, buf: str) -> bool:
        return any(p.startswith(buf) for p in _OPENING_PATTERNS)

    def _flush_text_buffer(self) -> None:
        if self._text_buffer:
            self._emit(ParserEventType.TEXT_CHUNK, self._text_buffer)
            self._text_buffer = ""

    def _emit(self, event_type: ParserEventType, payload) -> None:
        self._on_event(ParserEvent(type=event_type, payload=payload))

    def _try_parse_weak_point(self, raw_json: str) -> Optional[dict]:
        """Parse + valide le JSON d'un weak_point. Retourne None si malformé."""
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.warning("WEAK_POINT JSON invalide: %s - raw=%r", e, raw_json)
            return None
        if not isinstance(data, dict):
            logger.warning("WEAK_POINT pas un dict: raw=%r", raw_json)
            return None
        required = {"concept", "what_failed", "score"}
        if not required.issubset(data.keys()):
            missing = required - data.keys()
            logger.warning("WEAK_POINT champs manquants: %s - raw=%r", missing, raw_json)
            return None
        if not isinstance(data["score"], int) or not 0 <= data["score"] <= 4:
            logger.warning("WEAK_POINT score invalide: %r", data.get("score"))
            return None
        data["cm_anchor"] = self._normalize_cm_anchor(data.get("cm_anchor"))
        return data

    def _normalize_cm_anchor(self, anchor) -> Optional[dict]:
        """Normalise cm_anchor (cf. _prompts/PROMPT_SYSTEME_COMPAGNON.md §5.2)."""
        if anchor is None:
            return None
        if not isinstance(anchor, dict):
            logger.warning("cm_anchor pas un dict: %r", anchor)
            return None
        if not any(k in anchor for k in ("transcription", "poly", "section")):
            logger.warning("cm_anchor vide: %r", anchor)
            return None
        for key in ("transcription", "poly"):
            if key in anchor and isinstance(anchor[key], str):
                path = anchor[key]
                if path.startswith(("C:", "/", "\\")):
                    logger.warning(
                        "cm_anchor.%s chemin absolu detecte: %r - mise a null",
                        key, path
                    )
                    anchor[key] = None
        if not any(anchor.get(k) for k in ("transcription", "poly", "section")):
            return None
        return anchor
