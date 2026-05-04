"""
compagnon.py — Entry point CLI du compagnon de révision.

Usage::

    python compagnon.py AN1 TD 5 3
    python compagnon.py AN1 TD 5 3 --enonce-path AN1/TD/AN1_TD5_enonce.pdf
    python compagnon.py AN1 TD 5 3 --resume
    python compagnon.py AN1 TD 5 3 --enable-audio

Pose le ``sys.path`` vers les sous-modules (``_scripts/dialogue``, ``audio``,
``quota``, ``web``) puis :

1. Vérifie le quota Pro Max via ``can_start_session()``.
2. Si ``--resume``, liste les sessions reprenables.
3. Lance Flask en thread daemon sur ``127.0.0.1:5680``.
4. Optionnel : démarre le listener push-to-talk + transcripteur Whisper.
5. Ouvre le navigateur sur l'UI avec les query params pré-remplis.
6. Bloque jusqu'à ``Ctrl+C`` ou jusqu'à la mort du thread Flask.

Cf. ARCHITECTURE.md §10.
"""

import argparse
import logging
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

# Path bootstrap : pose les chemins avant les imports internes (config.py
# est à la racine, les modules sont dans _scripts/{dialogue,audio,quota,web}/).
ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "_scripts"
for _sub in ("dialogue", "audio", "quota", "web"):
    sys.path.insert(0, str(SCRIPTS / _sub))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

from app import app, DEFAULT_PORT  # noqa: E402
from config import SESSIONS_DIR  # noqa: E402
from quota_check import can_start_session  # noqa: E402
from session_state import SessionState  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.skip_quota_check:
        logger.warning("--skip-quota-check actif : quota Claude non verifie.")
    else:
        ok, reason = can_start_session()
        if not ok:
            print(f"Impossible de demarrer : {reason}", file=sys.stderr)
            return 1
        logger.info("Quota OK.")

    if args.resume:
        _print_resumable()

    url = _build_url(args)

    flask_thread = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=DEFAULT_PORT,
            debug=False, threaded=True, use_reloader=False,
        ),
        daemon=True,
        name="flask-app",
    )
    flask_thread.start()
    time.sleep(1)  # laisse Flask binder le port avant browser/listener

    listener = None
    if args.enable_audio:
        listener = _start_audio_listener()

    logger.info("Ouverture du navigateur : %s", url)
    webbrowser.open(url)

    try:
        while flask_thread.is_alive():
            flask_thread.join(timeout=1)
    except KeyboardInterrupt:
        logger.info("Interruption clavier, arret en cours.")
    finally:
        if listener is not None:
            listener.stop()
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compagnon de révision (Phase A)")
    p.add_argument("matiere", help="AN1, EN1, PSI, ...")
    p.add_argument("type", help="TD, TP, CC, Examen, Quiz")
    p.add_argument("num", help="Numéro du TD/TP/CC")
    p.add_argument("exo", help="Numéro de l'exercice ou 'full'")
    p.add_argument("--enonce-path", help="PDF d'énoncé (absolu ou relatif à COURS_ROOT)")
    p.add_argument("--resume", action="store_true",
                   help="Lister les sessions reprenables avant de démarrer")
    p.add_argument("--enable-audio", action="store_true",
                   help="Hooker ESPACE et activer Whisper push-to-talk")
    p.add_argument("--skip-quota-check", action="store_true",
                   help="Bypass le quota_check au boot (tests d'infra qui ne hit pas Claude)")
    return p.parse_args()


def _print_resumable() -> None:
    resumable = SessionState.find_resumable(SESSIONS_DIR)
    if not resumable:
        print("Aucune session reprenable.")
        return
    print("Sessions reprenables :")
    for p in resumable:
        print(f"  - {p.name}")
    print(
        "Phase A : pas de reprise auto. Relance avec les memes arguments "
        "pour reprendre la session du jour, ou choisis-en une ci-dessus."
    )


def _build_url(args: argparse.Namespace) -> str:
    params = {
        "matiere": args.matiere,
        "type": args.type,
        "num": args.num,
        "exo": args.exo,
    }
    if args.enonce_path:
        params["enonce_path"] = args.enonce_path
    return f"http://127.0.0.1:{DEFAULT_PORT}/?{urlencode(params)}"


def _start_audio_listener():
    """Instancie WhisperTranscriber + PushToTalkListener.

    Le callback ``on_recording_complete`` transcrit le WAV puis POST le texte
    sur ``/api/send_message`` côté Flask local — le streaming ultérieur est
    géré par l'UI navigateur classique.
    """
    import requests
    from listener import PushToTalkListener
    from transcribe_stream import WhisperTranscriber

    logger.info("Chargement Whisper large-v3 (peut prendre quelques secondes)...")
    transcriber = WhisperTranscriber()

    def on_wav(wav_path: Path) -> None:
        try:
            text, dur = transcriber.transcribe(wav_path)
            logger.info("Transcription (%.2fs audio) : %s", dur, text[:120])
            r = requests.post(
                f"http://127.0.0.1:{DEFAULT_PORT}/api/send_message",
                json={"text": text}, timeout=5,
            )
            if r.status_code not in (200, 202):
                logger.warning(
                    "send_message HTTP %d : %s", r.status_code, r.text[:200],
                )
        except Exception:
            logger.exception("Echec dans on_wav (callback push-to-talk)")

    listener = PushToTalkListener(on_recording_complete=on_wav)
    listener.start()
    logger.info("Push-to-talk arme sur ESPACE. Maintenir pour parler, relacher pour envoyer.")
    return listener


if __name__ == "__main__":
    raise SystemExit(main())
