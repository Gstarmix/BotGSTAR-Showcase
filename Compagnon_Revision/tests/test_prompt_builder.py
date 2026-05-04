"""
test_prompt_builder.py — Couverture des comportements clés du PromptBuilder.

Lance avec :
    python -m unittest tests.test_prompt_builder

(depuis la racine de Compagnon_Revision).
"""

import csv
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

# Path setup
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "_scripts"
DIALOGUE = SCRIPTS / "dialogue"
for _p in (str(ROOT), str(SCRIPTS), str(DIALOGUE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from prompt_builder import (  # noqa: E402
    CM_TRANSCRIPTION_WORD_CAP,
    PREVIOUS_WEAK_POINTS_TOP_N,
    PromptBuilder,
    SessionContext,
)


def make_blank_pdf(path: Path) -> None:
    """Crée un PDF d'une page blanche (extract_text retournera '')."""
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    with path.open("wb") as f:
        w.write(f)


class TestPromptBuilder(unittest.TestCase):

    def setUp(self):
        self._tmpobj = TemporaryDirectory()
        self.tmp = Path(self._tmpobj.name)
        self.system_prompt = self.tmp / "PROMPT_SYSTEME.md"
        self.system_prompt.write_text(
            "# Prompt système (test fixture)\nTu es prof particulier exigeant.",
            encoding="utf-8",
        )
        self.cours_root = self.tmp / "COURS"
        self.cours_root.mkdir()
        self.builder = PromptBuilder(self.system_prompt, self.cours_root)

        # Énoncé PDF blanc (extract_text -> "" -> message de fallback explicite)
        self.enonce = self.tmp / "AN1_TD5_enonce.pdf"
        make_blank_pdf(self.enonce)

    def tearDown(self):
        self._tmpobj.cleanup()

    def _ctx(self, **overrides) -> SessionContext:
        defaults = dict(
            matiere="AN1",
            type="TD",
            num="5",
            exo="3",
            enonce_path=self.enonce,
        )
        defaults.update(overrides)
        return SessionContext(**defaults)

    # ---------------------------------------------------------------- system_prompt

    def test_system_prompt_property_loads_file(self):
        self.assertIn("prof particulier", self.builder.system_prompt)
        # Immutable côté API : property non-settable
        with self.assertRaises(AttributeError):
            self.builder.system_prompt = "autre chose"  # type: ignore[misc]

    # ---------------------------------------------------------------- minimal

    def test_minimal_message_only_enonce(self):
        msg = self.builder.build_initial_context_message(self._ctx())
        self.assertIn("=== CONTEXTE DE LA SÉANCE ===", msg)
        self.assertIn("Matière : AN1", msg)
        self.assertIn("Type : TD 5", msg)
        self.assertIn("Exercice ciblé : exercice 3", msg)
        self.assertIn("=== ÉNONCÉ DE L'EXERCICE ===", msg)
        # PDF blanc -> message scanné
        self.assertIn("PDF probablement scanné", msg)
        # Sections optionnelles absentes
        self.assertNotIn("=== TRANSCRIPTION CM PERTINENTE ===", msg)
        self.assertNotIn("=== POLY DU PROF", msg)
        self.assertNotIn("POINTS FAIBLES HISTORIQUES", msg)
        # Instructions présentes
        self.assertIn("=== INSTRUCTIONS ===", msg)
        self.assertIn("Démarre la séance", msg)
        self.assertNotIn("[RESUME_SESSION]", msg)

    def test_exo_full_label(self):
        msg = self.builder.build_initial_context_message(self._ctx(exo="full"))
        self.assertIn("Exercice ciblé : tout le TD/TP", msg)
        self.assertNotIn("exercice full", msg)

    # ---------------------------------------------------------------- resume

    def test_resume_message(self):
        msg = self.builder.build_initial_context_message(
            self._ctx(), is_resume=True
        )
        self.assertTrue(msg.startswith("[RESUME_SESSION]"))
        self.assertIn("Reprends la séance interrompue", msg)
        self.assertNotIn("Démarre la séance", msg)

    # ---------------------------------------------------------------- CM transcription cap

    def test_cm_transcription_under_cap(self):
        cm = self.tmp / "cm.txt"
        cm.write_text("alpha bêta gamma delta", encoding="utf-8")
        msg = self.builder.build_initial_context_message(
            self._ctx(cm_transcription_path=cm)
        )
        self.assertIn("=== TRANSCRIPTION CM PERTINENTE ===", msg)
        self.assertIn("alpha bêta gamma delta", msg)
        self.assertNotIn("tronqué", msg)

    def test_cm_transcription_over_cap_is_truncated(self):
        cm = self.tmp / "cm_long.txt"
        cm.write_text(" ".join(f"mot{i}" for i in range(CM_TRANSCRIPTION_WORD_CAP + 50)),
                      encoding="utf-8")
        msg = self.builder.build_initial_context_message(
            self._ctx(cm_transcription_path=cm)
        )
        self.assertIn(f"tronqué à {CM_TRANSCRIPTION_WORD_CAP} mots", msg)
        # Le dernier mot capé doit être présent, le 1er au-delà ne doit pas l'être
        self.assertIn(f"mot{CM_TRANSCRIPTION_WORD_CAP - 1}", msg)
        self.assertNotIn(
            f" mot{CM_TRANSCRIPTION_WORD_CAP + 10} ", msg + " "
        )

    # ---------------------------------------------------------------- weak points

    def _write_weak_points_csv(self, path: Path, count: int) -> None:
        # Génère un CSV avec count rows, captured_at strictement croissant
        # (le plus récent en dernier).
        base = datetime.now(tz=timezone.utc) - timedelta(days=count)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "id", "captured_at", "concept", "what_failed", "score",
                    "exercise_context",
                ],
            )
            w.writeheader()
            for i in range(count):
                ts = (base + timedelta(days=i)).isoformat(timespec="seconds")
                w.writerow({
                    "id": f"wp_{i}",
                    "captured_at": ts,
                    "concept": f"concept_{i}",
                    "what_failed": f"raison_{i}",
                    "score": str(i % 5),
                    "exercise_context": f"ex{i}",
                })

    def test_previous_weak_points_top_n_sorted_desc(self):
        csv_path = self.tmp / "AN1_points_faibles.csv"
        self._write_weak_points_csv(csv_path, count=PREVIOUS_WEAK_POINTS_TOP_N + 3)
        msg = self.builder.build_initial_context_message(
            self._ctx(previous_weak_points_path=csv_path)
        )
        self.assertIn("POINTS FAIBLES HISTORIQUES SUR AN1", msg)
        # Les 5 plus récents = indices count-1, count-2, ..., count-5
        # avec count = 8 ici => 7, 6, 5, 4, 3
        for kept in range(7, 2, -1):
            self.assertIn(f"concept_{kept}", msg)
        # Les autres doivent être absents
        for dropped in range(0, 3):
            self.assertNotIn(f"concept_{dropped}", msg)

    def test_previous_weak_points_missing_file_graceful(self):
        ghost = self.tmp / "ghost.csv"
        msg = self.builder.build_initial_context_message(
            self._ctx(previous_weak_points_path=ghost)
        )
        self.assertIn("aucun point faible historique enregistré", msg)

    def test_previous_weak_points_empty_csv(self):
        empty = self.tmp / "empty.csv"
        empty.write_text(
            "id,captured_at,concept,what_failed,score,exercise_context\n",
            encoding="utf-8",
        )
        msg = self.builder.build_initial_context_message(
            self._ctx(previous_weak_points_path=empty)
        )
        self.assertIn("CSV présent mais vide", msg)

    # ---------------------------------------------------------------- PDF errors

    def test_pdf_missing_file_graceful(self):
        ctx = self._ctx(enonce_path=self.tmp / "ghost.pdf")
        msg = self.builder.build_initial_context_message(ctx)
        self.assertIn("PDF introuvable", msg)

    def test_pdf_corrupt_file_graceful(self):
        bogus = self.tmp / "bogus.pdf"
        bogus.write_text("ceci n'est pas un PDF", encoding="utf-8")
        ctx = self._ctx(enonce_path=bogus)
        msg = self.builder.build_initial_context_message(ctx)
        self.assertIn("Extraction PDF échouée", msg)

    # ---------------------------------------------------------------- session_context re-export

    def test_session_context_reexport_from_session_state(self):
        # Importable des deux côtés, même classe
        from session_state import SessionContext as ContextFromState
        self.assertIs(ContextFromState, SessionContext)


if __name__ == "__main__":
    unittest.main(verbosity=2)
