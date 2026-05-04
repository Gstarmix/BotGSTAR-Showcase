"""
test_session_state.py — Couverture du module session_state.

Lance avec :
    python -m unittest tests.test_session_state

(depuis la racine de Compagnon_Revision).
"""

import json
import sys
import time
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

from session_state import (  # noqa: E402
    RESUMABLE_LAST_ALIVE_THRESHOLD_SECONDS,
    SessionContext,
    SessionState,
)


def make_context(tmp: Path) -> SessionContext:
    return SessionContext(
        matiere="AN1",
        type="TD",
        num="5",
        exo="3",
        enonce_path=tmp / "enonce.pdf",
    )


class TestSessionState(unittest.TestCase):

    def setUp(self):
        self._tmpobj = TemporaryDirectory()
        self.tmp = Path(self._tmpobj.name)
        self.sessions_dir = self.tmp / "_sessions"
        self.sessions_dir.mkdir()
        self.ctx = make_context(self.tmp)
        self.state = SessionState(
            session_id="2026-05-01_AN1_TD5_ex3",
            sessions_dir=self.sessions_dir,
            context=self.ctx,
            engine="cli_subscription",
            model="claude-opus-4-7",
        )

    def tearDown(self):
        # Sécurité : si un test n'a pas appelé finalize, on coupe le heartbeat
        try:
            self.state._stop_heartbeat.set()
            if self.state._heartbeat_thread is not None:
                self.state._heartbeat_thread.join(timeout=2)
        except Exception:
            pass
        self._tmpobj.cleanup()

    # ---------------------------------------------------------------- skeleton

    def test_initial_data_skeleton(self):
        d = self.state.data
        self.assertEqual(d["schema_version"], 1)
        self.assertEqual(d["session_id"], "2026-05-01_AN1_TD5_ex3")
        self.assertEqual(d["matiere"], "AN1")
        self.assertEqual(d["type"], "TD")
        self.assertEqual(d["exo"], "3")
        self.assertIsNone(d["ended_at"])
        self.assertFalse(d["interrupted"])
        self.assertIsNone(d["interrupted_at"])
        self.assertIsNone(d["resumed_at"])
        self.assertEqual(d["weak_points"], [])
        self.assertEqual(d["transcript"], [])
        self.assertEqual(d["stats"]["total_exchanges"], 0)
        self.assertEqual(d["engine"], "cli_subscription")
        self.assertIn("enonce", d["context_files"])

    # ---------------------------------------------------------------- start

    def test_start_writes_file_and_starts_heartbeat(self):
        self.assertFalse(self.state.path.exists())
        self.state.start()
        self.assertTrue(self.state.path.exists())
        self.assertIsNotNone(self.state._heartbeat_thread)
        self.assertTrue(self.state._heartbeat_thread.is_alive())
        # Le fichier sur disque correspond au snapshot
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["session_id"], self.state.data["session_id"])
        self.state.finalize()

    # ---------------------------------------------------------------- append_exchange

    def test_append_exchange_two_turns(self):
        self.state.start()
        self.state.append_exchange("claude", "Bonjour")
        self.state.append_exchange(
            "student", "Salut", audio_path=self.tmp / "audio" / "y.wav"
        )
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        self.assertEqual(len(on_disk["transcript"]), 2)
        self.assertEqual(on_disk["transcript"][0]["role"], "claude")
        self.assertEqual(on_disk["transcript"][0]["text"], "Bonjour")
        self.assertEqual(on_disk["transcript"][1]["role"], "student")
        self.assertIn("audio_path", on_disk["transcript"][1])
        self.assertEqual(on_disk["stats"]["total_exchanges"], 2)
        self.state.finalize()

    def test_append_exchange_invalid_role(self):
        self.state.start()
        with self.assertRaises(ValueError):
            self.state.append_exchange("admin", "test")
        self.state.finalize()

    # ---------------------------------------------------------------- add_weak_point

    def test_add_weak_point_auto_fields(self):
        self.state.start()
        self.state.add_weak_point({
            "concept": "theoreme accroissements finis",
            "what_failed": "hypothese de continuite non enoncee",
            "score": 1,
        })
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        wp = on_disk["weak_points"][0]
        self.assertTrue(wp["id"].startswith("wp_"))
        self.assertIn("captured_at", wp)
        self.assertIn("T", wp["captured_at"])  # ISO 8601
        self.assertFalse(wp["cm_anchor_malformed"])
        self.assertEqual(wp["score"], 1)
        self.assertEqual(on_disk["stats"]["weak_points_count"], 1)
        self.state.finalize()

    def test_add_weak_point_preserves_provided_id(self):
        self.state.start()
        self.state.add_weak_point({
            "id": "wp_custom-123",
            "concept": "X",
            "what_failed": "Y",
            "score": 2,
        })
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["weak_points"][0]["id"], "wp_custom-123")
        self.state.finalize()

    # ---------------------------------------------------------------- increment_stat

    def test_increment_stat_int_and_float(self):
        self.state.start()
        self.state.increment_stat("photos_received", 1)
        self.state.increment_stat("photos_received", 2)
        self.state.increment_stat("whisper_seconds", 1.5)
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["stats"]["photos_received"], 3)
        self.assertAlmostEqual(on_disk["stats"]["whisper_seconds"], 1.5)
        self.state.finalize()

    def test_increment_stat_creates_unknown_key(self):
        self.state.start()
        self.state.increment_stat("custom_metric", 7)
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["stats"]["custom_metric"], 7)
        self.state.finalize()

    # ---------------------------------------------------------------- finalize

    def test_finalize_clean(self):
        self.state.start()
        time.sleep(0.05)
        self.state.finalize(interrupted=False)
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        self.assertIsNotNone(on_disk["ended_at"])
        self.assertFalse(on_disk["interrupted"])
        self.assertIsNone(on_disk["interrupted_at"])
        self.assertIsInstance(on_disk["duration_seconds"], int)
        self.assertGreaterEqual(on_disk["duration_seconds"], 0)
        # heartbeat arrêté
        self.assertFalse(
            self.state._heartbeat_thread is not None
            and self.state._heartbeat_thread.is_alive()
        )

    def test_finalize_interrupted(self):
        self.state.start()
        self.state.finalize(interrupted=True)
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        self.assertTrue(on_disk["interrupted"])
        self.assertIsNotNone(on_disk["interrupted_at"])
        self.assertIsNotNone(on_disk["ended_at"])

    def test_finalize_idempotent(self):
        self.state.start()
        self.state.finalize(interrupted=True)
        # Second appel ne doit pas raise
        self.state.finalize(interrupted=False)
        on_disk = json.loads(self.state.path.read_text(encoding="utf-8"))
        # Le second appel a écrasé interrupted=False
        self.assertFalse(on_disk["interrupted"])

    # ---------------------------------------------------------------- load

    def test_load_roundtrip(self):
        self.state.start()
        self.state.append_exchange("claude", "Hello")
        self.state.add_weak_point({
            "concept": "X", "what_failed": "Y", "score": 2,
        })
        self.state.finalize()
        loaded = SessionState.load(self.state.path)
        self.assertEqual(
            loaded.data["session_id"], "2026-05-01_AN1_TD5_ex3"
        )
        self.assertEqual(len(loaded.data["transcript"]), 1)
        self.assertEqual(len(loaded.data["weak_points"]), 1)

    # ---------------------------------------------------------------- find_resumable

    def test_find_resumable_detects_interrupted(self):
        self.state.start()
        self.state.finalize(interrupted=True)
        results = SessionState.find_resumable(self.sessions_dir)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], self.state.path)

    def test_find_resumable_ignores_clean_ended(self):
        self.state.start()
        self.state.finalize(interrupted=False)
        results = SessionState.find_resumable(self.sessions_dir)
        self.assertEqual(results, [])

    def test_find_resumable_detects_old_last_alive(self):
        old = (datetime.now(tz=timezone.utc) - timedelta(minutes=10))
        old_iso = old.isoformat(timespec="seconds")
        path = self.sessions_dir / "old_session.json"
        path.write_text(
            json.dumps({
                "schema_version": 1,
                "session_id": "old_session",
                "started_at": old_iso,
                "last_alive": old_iso,
                "ended_at": None,
                "interrupted": False,
            }),
            encoding="utf-8",
        )
        results = SessionState.find_resumable(self.sessions_dir)
        self.assertIn(path, results)

    def test_find_resumable_ignores_recent_alive(self):
        recent = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        path = self.sessions_dir / "recent_session.json"
        path.write_text(
            json.dumps({
                "schema_version": 1,
                "session_id": "recent_session",
                "started_at": recent,
                "last_alive": recent,
                "ended_at": None,
                "interrupted": False,
            }),
            encoding="utf-8",
        )
        results = SessionState.find_resumable(self.sessions_dir)
        self.assertNotIn(path, results)

    def test_find_resumable_empty_dir(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(SessionState.find_resumable(empty), [])

    def test_resumable_threshold_constant(self):
        # Garde-fou : si quelqu'un change la constante, on s'en aperçoit
        self.assertEqual(RESUMABLE_LAST_ALIVE_THRESHOLD_SECONDS, 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
