"""
test_parser.py — Couverture des 9 cas de ARCHITECTURE.md §3.5.

Lance avec :
    python -m unittest tests.test_parser

(depuis la racine de Compagnon_Revision).
"""

import json
import logging
import sys
import unittest
from pathlib import Path

# Path setup : permet l'import direct de parser.py depuis _scripts/dialogue/
ROOT = Path(__file__).resolve().parent.parent
DIALOGUE_DIR = ROOT / "_scripts" / "dialogue"
for _p in (str(ROOT), str(DIALOGUE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from parser import (  # noqa: E402
    ParserEvent,
    ParserEventType,
    ParserState,
    StreamParser,
)


class TestStreamParser(unittest.TestCase):
    """Cas 1-9 de ARCHITECTURE.md §3.5."""

    def setUp(self):
        self.events: list[ParserEvent] = []
        self.parser = StreamParser(self.events.append)

    # Helper
    def _types(self) -> list[ParserEventType]:
        return [e.type for e in self.events]

    def _texts(self) -> str:
        return "".join(
            e.payload for e in self.events if e.type == ParserEventType.TEXT_CHUNK
        )

    # ---------------------------------------------------------------- cas 1
    def test_01_simple_text(self):
        """Texte simple sans balise -> tout flushé en TEXT_CHUNK."""
        self.parser.feed("Bonjour le monde")
        self.parser.flush()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].type, ParserEventType.TEXT_CHUNK)
        self.assertEqual(self.events[0].payload, "Bonjour le monde")

    # ---------------------------------------------------------------- cas 2
    def test_02_tts_single_chunk(self):
        """<<<TTS>>>Bonjour<<<END>>> en un chunk -> 1 event TTS."""
        self.parser.feed("<<<TTS>>>Bonjour<<<END>>>")
        self.parser.flush()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].type, ParserEventType.TTS)
        self.assertEqual(self.events[0].payload, "Bonjour")

    # ---------------------------------------------------------------- cas 3
    def test_03_tts_split_in_5_chunks(self):
        """<<<TTS>>>Bonjour<<<END>>> coupé en 5 chunks -> 1 event TTS."""
        chunks = ["<<<", "TTS>>>B", "onjou", "r<<<E", "ND>>>"]
        for c in chunks:
            self.parser.feed(c)
        self.parser.flush()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].type, ParserEventType.TTS)
        self.assertEqual(self.events[0].payload, "Bonjour")

    # ---------------------------------------------------------------- cas 4
    def test_04_tts_surrounded_by_text(self):
        """Salut <<<TTS>>>OK<<<END>>> suite -> 3 events (TEXT, TTS, TEXT)."""
        self.parser.feed("Salut <<<TTS>>>OK<<<END>>> suite")
        self.parser.flush()
        self.assertEqual(self._types(), [
            ParserEventType.TEXT_CHUNK,
            ParserEventType.TTS,
            ParserEventType.TEXT_CHUNK,
        ])
        self.assertEqual(self.events[0].payload, "Salut ")
        self.assertEqual(self.events[1].payload, "OK")
        self.assertEqual(self.events[2].payload, " suite")

    # ---------------------------------------------------------------- cas 5
    def test_05_weak_point_valid(self):
        """WEAK_POINT JSON valide -> 1 event WEAK_POINT avec dict parsé."""
        wp = {
            "concept": "theoreme des accroissements finis",
            "what_failed": "hypothese de continuite non enoncee",
            "score": 1,
            "cm_anchor": {
                "transcription": "AN1/CM/CM6.txt",
                "section": "Accroissements finis",
            },
        }
        self.parser.feed("<<<WEAK_POINT>>>" + json.dumps(wp) + "<<<END>>>")
        self.parser.flush()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].type, ParserEventType.WEAK_POINT)
        payload = self.events[0].payload
        self.assertEqual(payload["concept"], "theoreme des accroissements finis")
        self.assertEqual(payload["score"], 1)
        self.assertEqual(payload["cm_anchor"]["section"], "Accroissements finis")

    # ---------------------------------------------------------------- cas 6
    def test_06_weak_point_malformed(self):
        """WEAK_POINT JSON malformé -> 0 event, warning logué."""
        with self.assertLogs(level=logging.WARNING) as cm:
            self.parser.feed('<<<WEAK_POINT>>>{"concept": "X", oops<<<END>>>')
            self.parser.flush()
        self.assertEqual(len(self.events), 0)
        self.assertTrue(
            any("WEAK_POINT" in line for line in cm.output),
            f"Aucun warning WEAK_POINT trouvé. Logs: {cm.output}",
        )

    # ---------------------------------------------------------------- cas 7
    def test_07_end_session(self):
        """<<<END_SESSION>>> seul -> 1 event END_SESSION."""
        self.parser.feed("<<<END_SESSION>>>")
        self.parser.flush()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].type, ParserEventType.END_SESSION)

    # ---------------------------------------------------------------- cas 8
    def test_08_false_positive_tag(self):
        """Faux positif <<<X>>> -> flush comme texte, pas d'event spécial."""
        self.parser.feed("<<<X>>>")
        self.parser.flush()
        # Aucun event TTS / WEAK_POINT / END_SESSION
        for e in self.events:
            self.assertEqual(
                e.type, ParserEventType.TEXT_CHUNK,
                f"Event inattendu: {e.type}",
            )
        # Le contenu original doit être recomposable depuis les TEXT_CHUNK
        self.assertEqual(self._texts(), "<<<X>>>")

    # ---------------------------------------------------------------- cas 9
    def test_09_truncated_during_probe(self):
        """Stream tronqué pendant <<<TT... -> état OUTSIDE après flush, warning."""
        with self.assertLogs(level=logging.WARNING) as cm:
            self.parser.feed("<<<TT")
            self.parser.flush()
        # Pas d'event TTS / WEAK_POINT / END_SESSION
        for e in self.events:
            self.assertEqual(
                e.type, ParserEventType.TEXT_CHUNK,
                f"Event inattendu: {e.type}",
            )
        # État ramené à OUTSIDE
        self.assertEqual(self.parser._state, ParserState.OUTSIDE)
        # Au moins un warning émis (stream tronqué)
        self.assertTrue(
            any("tronque" in line.lower() for line in cm.output),
            f"Warning de troncation introuvable. Logs: {cm.output}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
