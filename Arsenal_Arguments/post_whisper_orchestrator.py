"""Orchestrateur post-Whisper : OCR carrousels + audit final.

S'attend à ce que whisper_supervisor.py soit en cours.
1. Attend que whisper_supervisor.log contienne "[done]" (= Whisper terminé proprement).
2. Stoppe le progress_monitor Whisper (par lookup PID via cmdline).
3. Lance ocr_carousels.py (foreground).
4. Lance arsenal_audit.py.
5. Poste un récap final sur Discord.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT.parent / ".env")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
CHANNEL_ID = "1493760267300110466"  # Migration 2026-04-29 → ISTIC L1 G2
API_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
USER_AGENT = "BotGSTAR-PostWhisperOrch/1.0"

SUPERVISOR_LOG = "_claude_logs/tasks/whisper_supervisor.log"
ORCHESTRATOR_LOG = "_claude_logs/tasks/post_whisper_orchestrator.log"
OCR_LOG = "_claude_logs/tasks/ocr_run.log"
AUDIT_LOG = "_claude_logs/tasks/audit_final.log"

POLL_INTERVAL = 60


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(msg: str) -> None:
    line = f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    Path(ORCHESTRATOR_LOG).parent.mkdir(parents=True, exist_ok=True)
    with open(ORCHESTRATOR_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def post_embed(title: str, description: str, color: int = 0x3498DB, fields: list[tuple[str, str, bool]] | None = None) -> None:
    if not TOKEN:
        return
    embed = {
        "title": title,
        "description": description[:4096],
        "color": color,
        "footer": {"text": "Arsenal Post-Whisper Orchestrator"},
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
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            return
        except Exception:
            time.sleep(2)


def wait_for_whisper_done() -> None:
    log("[wait] en attente de [done] dans le supervisor log...")
    while True:
        if Path(SUPERVISOR_LOG).exists():
            with open(SUPERVISOR_LOG, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if "[done]" in content:
                log("[wait] Whisper terminé.")
                return
            if "[max-restarts]" in content:
                log("[wait] Whisper a atteint max-restarts. On continue quand même.")
                return
        time.sleep(POLL_INTERVAL)


def stop_whisper_monitor() -> None:
    """Tue les process progress_monitor.py qui surveillent le whisper.
    Cherche sur python.exe ET pythonw.exe (cas process détaché via Start-Process)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | "
             "Where-Object {($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') "
             "-and $_.CommandLine -like '*progress_monitor*'} | "
             "ForEach-Object {Stop-Process -Id $_.ProcessId -Force; $_.ProcessId}"],
            capture_output=True, text=True, timeout=15,
        )
        log(f"[stop-monitor] killed PIDs: {out.stdout.strip() or 'none'}")
    except Exception as e:
        log(f"[stop-monitor-err] {e}")


def run_ocr() -> int:
    log("[ocr] lancement de ocr_carousels.py")
    post_embed(
        "🟢 Pipeline | OCR carrousels — démarrage",
        "Lancement de `python ocr_carousels.py` (easyocr fr+en, GPU).",
        color=0x3498DB,
    )
    Path(OCR_LOG).parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    with open(OCR_LOG, "w", encoding="utf-8") as f:
        proc = subprocess.run(
            [sys.executable, "ocr_carousels.py"],
            stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT),
            timeout=4 * 3600,
        )
    elapsed = time.time() - start
    rc = proc.returncode
    log(f"[ocr] terminé rc={rc} duration={elapsed:.0f}s")
    color = 0x2ECC71 if rc == 0 else 0xE74C3C
    title = "✅ Pipeline | OCR carrousels — terminé" if rc == 0 else "❌ Pipeline | OCR carrousels — erreur"
    post_embed(
        title,
        f"Exit code : `{rc}` · Durée : `{elapsed:.0f}s`\nLog : `{OCR_LOG}`",
        color=color,
    )
    return rc


def run_audit() -> int:
    log("[audit] lancement de arsenal_audit.py")
    post_embed(
        "🟢 Pipeline | Audit final — démarrage",
        "Lancement de `python arsenal_audit.py`.",
        color=0x3498DB,
    )
    Path(AUDIT_LOG).parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    with open(AUDIT_LOG, "w", encoding="utf-8") as f:
        proc = subprocess.run(
            [sys.executable, "arsenal_audit.py"],
            stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT),
            timeout=600,
        )
    elapsed = time.time() - start
    rc = proc.returncode

    # Lire le log pour extraire les chiffres clés
    summary_lines = []
    try:
        with open(AUDIT_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if any(k in line for k in ["Total lignes", "transcrip", "Vid", "SUCCESS", "FAILED", "PENDING"]):
                    summary_lines.append(line.strip())
    except Exception:
        pass
    summary = "\n".join(summary_lines[:12]) or "(audit log vide)"
    log(f"[audit] terminé rc={rc} duration={elapsed:.0f}s")
    color = 0x2ECC71 if rc == 0 else 0xE74C3C
    title = "📊 Pipeline | Audit final" if rc == 0 else "❌ Pipeline | Audit final — erreur"
    post_embed(
        title,
        f"Exit code : `{rc}` · Durée : `{elapsed:.0f}s`",
        color=color,
        fields=[("Résumé", f"```\n{summary[:1000]}\n```", False)],
    )
    return rc


def main() -> int:
    Path("_claude_logs/tasks").mkdir(parents=True, exist_ok=True)
    log("[orchestrator] démarrage post-Whisper")
    post_embed(
        "🟢 Orchestrateur | Démarrage",
        "Surveillance de Whisper. Quand il sera terminé, OCR carrousels + audit final lanceront automatiquement.",
        color=0x3498DB,
    )

    wait_for_whisper_done()
    stop_whisper_monitor()

    rc_ocr = run_ocr()
    rc_audit = run_audit()

    overall_ok = rc_ocr == 0 and rc_audit == 0
    color = 0x9B59B6 if overall_ok else 0xE67E22
    log(f"[orchestrator] terminé. OCR={rc_ocr}, audit={rc_audit}")
    post_embed(
        "🏁 Orchestrateur | Pipeline complet terminé" if overall_ok else "⚠️ Orchestrateur | Pipeline terminé avec erreurs",
        "Tous les jobs post-Whisper sont terminés. Vois le récap audit ci-dessus pour les chiffres finaux.",
        color=color,
        fields=[
            ("OCR", "✅ OK" if rc_ocr == 0 else f"❌ rc={rc_ocr}", True),
            ("Audit", "✅ OK" if rc_audit == 0 else f"❌ rc={rc_audit}", True),
        ],
    )
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
