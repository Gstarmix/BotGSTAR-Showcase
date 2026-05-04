"""Supervise Whisper : relance auto en cas de stall + isole les fichiers corrompus.

Chaque cycle :
1. Lance whisper_engine.ps1 en subprocess
2. Surveille `02_whisper_transcripts/*.txt` toutes les 30s
3. Si aucun nouveau .txt pendant STALL_TIMEOUT (defaut 15 min) :
   - Kill whisper
   - Identifie le fichier coupable (session.log vide + ancien)
   - Le déplace vers _corrupted_videos/
   - Relance whisper
4. Si whisper sort proprement (exit 0) avec rien à faire : termine.
5. Logs Discord (#logs) à chaque événement (start, stall, isolement, restart, done).

Usage :
  python whisper_supervisor.py
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT.parent / ".env")

# ---------- CONFIG ----------
VIDEO_DIR = "01_raw_videos"
TRANSCRIPT_DIR = "02_whisper_transcripts"
LOG_DIR = "02_whisper_logs/videos"
CORRUPTED_DIR = "_corrupted_videos"
SUPERVISOR_LOG = "_claude_logs/tasks/whisper_supervisor.log"

POLL_INTERVAL = 30          # sec entre checks
STALL_TIMEOUT = 900         # 15 min sans nouveau .txt = stall
MAX_RESTARTS = 50           # garde-fou anti-boucle infinie

# Discord
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
CHANNEL_ID = "1493760267300110466"  # Migration 2026-04-29 → ISTIC L1 G2
API_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
USER_AGENT = "BotGSTAR-WhisperSupervisor/1.0"

COLORS = {
    "info":     0x3498DB,
    "success":  0x2ECC71,
    "warning":  0xE67E22,
    "error":    0xE74C3C,
    "purple":   0x9B59B6,
}


# ---------- DISCORD LOG ----------

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def post_embed(title: str, description: str, color: str = "info", fields: list[tuple[str, str, bool]] | None = None) -> None:
    if not TOKEN:
        return
    embed = {
        "title": title,
        "description": description[:4096],
        "color": COLORS.get(color, COLORS["info"]),
        "footer": {"text": "Arsenal Whisper Supervisor"},
        "timestamp": now_iso(),
    }
    if fields:
        embed["fields"] = [
            {"name": n[:256], "value": str(v)[:1024], "inline": inline}
            for n, v, inline in fields
        ]
    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=payload, headers={
        "Authorization": f"Bot {TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            return
        except Exception as e:
            time.sleep(2)
    log(f"[ERR] Discord post échoué : {title}")


def log(msg: str) -> None:
    line = f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    Path(SUPERVISOR_LOG).parent.mkdir(parents=True, exist_ok=True)
    with open(SUPERVISOR_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- WHISPER STATE ----------

def count_transcripts() -> int:
    return len(glob.glob(f"{TRANSCRIPT_DIR}/*.txt"))


def find_stuck_basename(min_age_sec: int = 600) -> str | None:
    """Trouve le basename le plus probable du fichier hung : session.log vide + ancien."""
    candidates = []
    cutoff = time.time() - min_age_sec
    for path in glob.glob(f"{LOG_DIR}/*.session.log"):
        if os.path.getsize(path) > 0:
            continue
        mtime = os.path.getmtime(path)
        if mtime < cutoff:
            candidates.append((mtime, path))
    if not candidates:
        return None
    # Le plus récent (= en cours d'exécution probable)
    candidates.sort(reverse=True)
    return Path(candidates[0][1]).stem.replace(".session", "")


def isolate_video(basename: str) -> Path | None:
    """Move le fichier vers _corrupted_videos. Retourne le path destination, ou None."""
    Path(CORRUPTED_DIR).mkdir(exist_ok=True)
    for ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v"):
        src = Path(VIDEO_DIR) / f"{basename}{ext}"
        if src.exists():
            dst = Path(CORRUPTED_DIR) / src.name
            shutil.move(str(src), str(dst))
            return dst
    return None


# ---------- WHISPER LIFECYCLE ----------

WHISPER_CMD = [
    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", "whisper_engine.ps1",
    "-SrcDir", VIDEO_DIR,
    "-OutRoot", TRANSCRIPT_DIR,
    "-LogRoot", LOG_DIR,
    "-Model", "large-v3",
    "-Lang", "fr",
    "-Device", "cuda",
    "-ComputeType", "int8_float16",
]


def launch_whisper(run_log_path: str) -> subprocess.Popen:
    log_file = open(run_log_path, "a", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        WHISPER_CMD,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    log(f"[launch] PID={proc.pid} log={run_log_path}")
    return proc


def kill_whisper(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            # Tuer toute l'arborescence (PowerShell + python whisper)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, text=True, timeout=10,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception as e:
        log(f"[kill-err] {e}")
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


# ---------- MAIN LOOP ----------

def main() -> int:
    Path(CORRUPTED_DIR).mkdir(exist_ok=True)
    Path("_claude_logs/tasks").mkdir(parents=True, exist_ok=True)

    initial_count = count_transcripts()
    log(f"[start] supervisor — baseline {initial_count} transcripts")
    post_embed(
        "🟢 Whisper Supervisor | Démarrage",
        f"Surveillance lancée. Baseline : **{initial_count}** transcripts.\n"
        f"Stall timeout : {STALL_TIMEOUT // 60} min · Poll : {POLL_INTERVAL}s.",
        color="info",
    )

    restart_count = 0
    isolated_files: list[str] = []

    while restart_count < MAX_RESTARTS:
        run_log = f"_claude_logs/tasks/whisper_run_{dt.datetime.now().strftime('%H%M%S')}.log"
        proc = launch_whisper(run_log)
        last_count = count_transcripts()
        last_progress_time = time.time()

        while True:
            time.sleep(POLL_INTERVAL)
            cur_count = count_transcripts()

            if cur_count > last_count:
                last_count = cur_count
                last_progress_time = time.time()

            stall_age = time.time() - last_progress_time

            # Process exited?
            if proc.poll() is not None:
                rc = proc.returncode
                # Si exit code OK et pas de stall, c'est terminé
                if rc == 0 and stall_age < STALL_TIMEOUT:
                    log(f"[done] Whisper exit clean (rc={rc}) — total transcripts: {cur_count}")
                    post_embed(
                        "🏁 Whisper Supervisor | Whisper terminé",
                        f"Whisper a terminé proprement (exit {rc}).\n"
                        f"**{cur_count - initial_count}** nouveaux transcripts produits "
                        f"(total disque : **{cur_count}**).\n"
                        f"Restarts cette session : **{restart_count}**.\n"
                        f"Fichiers isolés : **{len(isolated_files)}**.",
                        color="success",
                        fields=[("Isolés", "\n".join(isolated_files[:10]) or "—", False)] if isolated_files else None,
                    )
                    return 0
                # Sinon c'est un crash
                log(f"[crash] Whisper exit rc={rc}, restart...")
                post_embed(
                    "⚠️ Whisper Supervisor | Crash inattendu",
                    f"Whisper s'est arrêté (rc={rc}). Relance imminente.",
                    color="warning",
                )
                restart_count += 1
                break

            # Stall detection
            if stall_age > STALL_TIMEOUT:
                log(f"[stall] {stall_age:.0f}s sans progrès, kill+isolate")
                kill_whisper(proc)
                stuck = find_stuck_basename(min_age_sec=STALL_TIMEOUT - 60)
                isolated_path = None
                if stuck:
                    isolated_path = isolate_video(stuck)

                if isolated_path:
                    isolated_files.append(stuck)
                    log(f"[isolate] {stuck} → {isolated_path}")
                    post_embed(
                        "🚨 Whisper Supervisor | Stall détecté",
                        f"Aucun nouveau transcript depuis **{int(stall_age/60)} min** sur le fichier `{stuck}`.\n"
                        f"Le fichier a été déplacé vers `_corrupted_videos/` et Whisper relance.",
                        color="warning",
                        fields=[
                            ("Fichier coupable", f"`{stuck}`", True),
                            ("Stall", f"{int(stall_age/60)} min", True),
                            ("Restarts", str(restart_count + 1), True),
                            ("Total isolés", str(len(isolated_files)), True),
                        ],
                    )
                else:
                    log(f"[stall] no clear culprit, restart anyway")
                    post_embed(
                        "🚨 Whisper Supervisor | Stall sans coupable identifié",
                        f"Aucun nouveau transcript depuis **{int(stall_age/60)} min** mais aucun "
                        f"fichier session.log vide à isoler. Relance brute de Whisper.",
                        color="warning",
                    )
                restart_count += 1
                break

    log(f"[max-restarts] Atteint {MAX_RESTARTS} restarts, abandon")
    post_embed(
        "🔴 Whisper Supervisor | Trop de restarts",
        f"Atteint le plafond de **{MAX_RESTARTS}** restarts. Le supervisor s'arrête.\n"
        f"Fichiers isolés : **{len(isolated_files)}**.",
        color="error",
        fields=[("Isolés", "\n".join(isolated_files[:20]) or "—", False)] if isolated_files else None,
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("[stop] supervisor interrompu (Ctrl+C)")
        post_embed(
            "🔴 Whisper Supervisor | Arrêt manuel",
            "Le supervisor a été interrompu (Ctrl+C).",
            color="warning",
        )
        sys.exit(0)
