"""
app.py — Front Flask du compagnon de révision.

Sert l'UI (index.html), expose une API JSON pour démarrer/terminer une
session, envoyer un message user, streamer la réponse Claude en SSE et
exposer le snapshot de quota.

L'état de la session vit en mémoire process — singleton sous lock car
Flask tourne en threaded mode. À redémarrer si bug, pas de persistance
au-delà du JSON de session écrit par ``SessionState`` (et restauré via
``find_resumable``).

Cf. ARCHITECTURE.md §8.
"""

import json
import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from claude_client import (
    DEFAULT_MODEL,
    ClaudeClient,
    ClaudeClientError,
    ClaudeQuotaExhaustedError,
)
from config import (
    COURS_ROOT,
    DEFAULT_ENGINE,
    ENGINE_PREF_PATH,
    PROJECT_ROOT,
    PROMPT_SYSTEME_PATH,
    SCHEMA_VERSION_ENGINE_PREF,
    SESSIONS_DIR,
)
from parser import ParserEvent, ParserEventType
from prompt_builder import PromptBuilder, SessionContext
from quota_check import get_usage_snapshot
from session_state import SessionState

logger = logging.getLogger(__name__)

# ============================================================ Flask app

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)

DEFAULT_PORT = 5680


# ============================================================ État de session (singleton)

class CompanionSession:
    """Container des objets vivants d'une séance."""

    def __init__(
        self,
        session_state: SessionState,
        client: ClaudeClient,
        prompt_builder: PromptBuilder,
    ):
        self.session_state = session_state
        self.client = client
        self.prompt_builder = prompt_builder
        self.event_queue: queue.Queue = queue.Queue()
        self.streaming_thread: Optional[threading.Thread] = None
        self.pending_user_text: Optional[str] = None
        self.lock = threading.Lock()


_state: Optional[CompanionSession] = None
_state_lock = threading.Lock()


# ============================================================ Endpoints

@app.route("/")
def index():
    """Sert la page principale."""
    if not (TEMPLATES_DIR / "index.html").exists():
        return ("index.html absent (sera codé en §14).", 404)
    return send_from_directory(str(TEMPLATES_DIR), "index.html")


@app.route("/api/quota", methods=["GET"])
def api_quota():
    """Snapshot quota Pro Max — poll côté front toutes les 60s."""
    return jsonify(get_usage_snapshot())


@app.route("/api/start_session", methods=["POST"])
def api_start_session():
    """Démarre une session. Body JSON : matiere, type, num, exo, +chemins."""
    global _state
    body = request.get_json(silent=True) or {}
    required = ("matiere", "type", "num", "exo")
    missing = [k for k in required if k not in body]
    if missing:
        return jsonify({"error": f"champs manquants : {missing}"}), 400

    try:
        ctx = _build_session_context(body)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400

    engine = _read_engine_pref()
    try:
        builder = PromptBuilder(PROMPT_SYSTEME_PATH, COURS_ROOT)
    except OSError as e:
        return jsonify({"error": f"PROMPT_SYSTEME absent : {e}"}), 500

    session_id = _build_session_id(ctx)
    session_state = SessionState(
        session_id=session_id,
        sessions_dir=SESSIONS_DIR,
        context=ctx,
        engine=engine,
        model=DEFAULT_MODEL,
    )
    session_state.start()

    client = ClaudeClient(engine=engine, system_prompt=builder.system_prompt)
    initial = builder.build_initial_context_message(ctx)
    client.append_user_message(initial)

    with _state_lock:
        if _state is not None:
            try:
                _state.session_state.finalize(interrupted=True)
            except Exception:
                logger.exception("Cleanup ancien state a leve")
        _state = CompanionSession(session_state, client, builder)

    logger.info("Session demarree : %s (engine=%s)", session_id, engine)
    return jsonify({
        "ok": True,
        "session_id": session_id,
        "engine": engine,
    })


@app.route("/api/send_message", methods=["POST"])
def api_send_message():
    """Stocke le message user. Le streaming démarre au prochain GET /api/stream_response."""
    global _state
    body = request.get_json(silent=True) or {}
    text = body.get("text") or ""
    if not text.strip():
        return jsonify({"error": "text vide"}), 400
    with _state_lock:
        if _state is None:
            return jsonify({"error": "pas de session active"}), 409
        _state.pending_user_text = text
    return ("", 202)


@app.route("/api/stream_response", methods=["GET"])
def api_stream_response():
    """SSE qui streame la réponse Claude au pending message user."""
    global _state
    with _state_lock:
        st = _state
    if st is None:
        return jsonify({"error": "pas de session active"}), 409

    with st.lock:
        if st.pending_user_text is None:
            return jsonify({"error": "aucun message en attente"}), 409
        user_text = st.pending_user_text
        st.pending_user_text = None
        st.client.append_user_message(user_text)
        st.session_state.append_exchange("student", user_text)
        # Vider l'éventuelle queue résiduelle
        while not st.event_queue.empty():
            try:
                st.event_queue.get_nowait()
            except queue.Empty:
                break
        st.streaming_thread = threading.Thread(
            target=_run_claude_streaming,
            args=(st,),
            daemon=True,
            name="claude-stream",
        )
        st.streaming_thread.start()

    return Response(
        stream_with_context(_sse_generator(st)),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/api/upload_photo", methods=["POST"])
def api_upload_photo():
    """Stub Phase A : photos pas encore implémentées."""
    return jsonify({"error": "non implémenté en Phase A"}), 501


@app.route("/api/end_session", methods=["POST"])
def api_end_session():
    """Finalise la session courante (ended_at, duration_seconds)."""
    global _state
    body = request.get_json(silent=True) or {}
    interrupted = bool(body.get("interrupted", False))
    with _state_lock:
        st = _state
        _state = None
    if st is None:
        return jsonify({"error": "pas de session active"}), 409
    try:
        st.session_state.finalize(interrupted=interrupted)
    except Exception as e:
        logger.exception("finalize a leve")
        return jsonify({"error": str(e)}), 500
    return jsonify({
        "ok": True,
        "session_id": st.session_state.data.get("session_id"),
        "duration_seconds": st.session_state.data.get("duration_seconds"),
        "weak_points_count": st.session_state.data.get("stats", {}).get("weak_points_count", 0),
    })


# ============================================================ Streaming Claude

def _run_claude_streaming(st: CompanionSession) -> None:
    """Tourne dans un thread daemon. Pousse les ParserEvent dans la queue."""
    full_text_chunks: list[str] = []

    def on_event(event: ParserEvent) -> None:
        if event.type == ParserEventType.TEXT_CHUNK:
            full_text_chunks.append(str(event.payload))
        st.event_queue.put(event)

    try:
        stats = st.client.stream_response(on_event=on_event)
    except ClaudeQuotaExhaustedError as e:
        logger.warning("Quota epuise : %s", e)
        st.event_queue.put(("__error__", "quota_exhausted", str(e)))
        return
    except ClaudeClientError as e:
        logger.exception("Claude client error : %s", e)
        st.event_queue.put(("__error__", "client_error", str(e)))
        return
    finally:
        st.event_queue.put(("__done__",))

    # Append à session_state pour persistance JSON
    try:
        if full_text_chunks:
            st.session_state.append_exchange("claude", "".join(full_text_chunks))
        if stats.get("input_tokens"):
            st.session_state.increment_stat("claude_tokens_input", stats["input_tokens"])
        if stats.get("output_tokens"):
            st.session_state.increment_stat("claude_tokens_output", stats["output_tokens"])
    except Exception:
        logger.exception("Persistance post-stream a leve")


def _sse_generator(st: CompanionSession):
    """Drain la queue, émet des events SSE typés. Termine sur __done__ ou __error__."""
    while True:
        item = st.event_queue.get()
        if isinstance(item, tuple):
            tag = item[0]
            if tag == "__done__":
                yield "event: done\ndata: {}\n\n"
                return
            if tag == "__error__":
                _kind, msg = item[1], item[2]
                yield f"event: error\ndata: {json.dumps({'kind': _kind, 'message': msg})}\n\n"
                return
            continue
        # ParserEvent
        if item.type == ParserEventType.TEXT_CHUNK:
            yield f"event: text\ndata: {json.dumps(item.payload)}\n\n"
        elif item.type == ParserEventType.TTS:
            yield f"event: tts\ndata: {json.dumps(item.payload)}\n\n"
        elif item.type == ParserEventType.WEAK_POINT:
            # Persiste mais ne pousse PAS au front (interne).
            try:
                st.session_state.add_weak_point(item.payload)
            except Exception:
                logger.exception("add_weak_point a leve")
        elif item.type == ParserEventType.END_SESSION:
            yield "event: end\ndata: {}\n\n"
            return


# ============================================================ Helpers

def _build_session_context(body: dict) -> SessionContext:
    """Construit un SessionContext depuis le body /api/start_session."""
    enonce = _resolve(body.get("enonce_path"), COURS_ROOT)
    if enonce is None or not enonce.exists():
        raise FileNotFoundError(f"enonce_path requis et lisible : {body.get('enonce_path')!r}")
    return SessionContext(
        matiere=body["matiere"],
        type=body["type"],
        num=str(body["num"]),
        exo=str(body["exo"]),
        enonce_path=enonce,
        cm_transcription_path=_resolve(body.get("cm_transcription_path"), COURS_ROOT),
        cm_poly_path=_resolve(body.get("cm_poly_path"), COURS_ROOT),
        previous_weak_points_path=_resolve(
            body.get("previous_weak_points_path"), PROJECT_ROOT
        ),
    )


def _resolve(value, default_root: Path) -> Optional[Path]:
    """Accepte None, path absolu, ou path relatif au default_root."""
    if not value:
        return None
    p = Path(value)
    if not p.is_absolute():
        p = default_root / p
    return p


def _build_session_id(ctx: SessionContext) -> str:
    """Format : YYYY-MM-DD_{MAT}_{TYPE}{N}_ex{n} (cf. ARCHITECTURE.md §2.1)."""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{today}_{ctx.matiere}_{ctx.type}{ctx.num}_ex{ctx.exo}"


def _read_engine_pref() -> str:
    """Lit ``_secrets/engine_pref.json``. Default cli_subscription si absent / malformé."""
    if not ENGINE_PREF_PATH.exists():
        return DEFAULT_ENGINE
    try:
        data = json.loads(ENGINE_PREF_PATH.read_text(encoding="utf-8"))
        engine = data.get("engine")
        if engine in ("cli_subscription", "api_anthropic"):
            return engine
        logger.warning("engine_pref engine inattendu : %r — fallback default", engine)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("engine_pref illisible : %s — fallback default", e)
    return DEFAULT_ENGINE


# ============================================================ Lancement

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info(
        "Compagnon Flask sur http://127.0.0.1:%d (engine pref=%s)",
        DEFAULT_PORT, _read_engine_pref(),
    )
    app.run(host="127.0.0.1", port=DEFAULT_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
