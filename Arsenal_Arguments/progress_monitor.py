"""Surveille un dossier et poste les fichiers traites en temps reel via embed
Discord dans le salon de logs (1475955504332411187).

Usage:
  python progress_monitor.py --watch "02_whisper_transcripts/*.txt" \
                             --label "Whisper" --kind whisper \
                             [--total 543] [--interval 15]

Modes (--kind):
  - whisper : parse le session.log associe pour extraire audio_duration,
              transcribe_time, ratio, segments_count
  - ocr     : OCR carrousel — pas de session.log, juste taille fichier
  - generic : minimaliste

Lit DISCORD_BOT_TOKEN depuis ../.env (racine BotGSTAR).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT.parent / ".env")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    print("[ERR] DISCORD_BOT_TOKEN manquant", file=sys.stderr)
    sys.exit(1)

CHANNEL_ID = "1493760267300110466"  # Migration 2026-04-29 → ISTIC L1 G2
API_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
USER_AGENT = "BotGSTAR-Arsenal-ProgressMonitor/2.0"

# Couleurs Discord (decimal)
COLOR_BLUE = 0x3498DB     # info / running
COLOR_GREEN = 0x2ECC71    # success
COLOR_ORANGE = 0xE67E22   # warning / stall
COLOR_RED = 0xE74C3C      # error
COLOR_PURPLE = 0x9B59B6   # completion / milestone


def post_embed(embed: dict, content: str | None = None) -> bool:
    """Poste un embed Discord. Retourne True si OK."""
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content[:1990]
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bot {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"[ERR] HTTP {e.code}: {body[:200]}", file=sys.stderr, flush=True)
            if e.code == 429:
                # Rate limit — read retry_after if possible
                try:
                    j = json.loads(body)
                    time.sleep(float(j.get("retry_after", 2)))
                except Exception:
                    time.sleep(2)
                continue
            return False
        except Exception as e:
            print(f"[ERR] post attempt {attempt+1}: {e}", file=sys.stderr, flush=True)
            time.sleep(2)
    return False


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def snapshot(pattern: str) -> set[str]:
    return {Path(p).name for p in glob.glob(pattern)}


def fmt_duration(secs: float) -> str:
    if secs < 0:
        return "—"
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def fmt_eta(remaining: int, rate_per_sec: float) -> str:
    if rate_per_sec <= 0 or remaining <= 0:
        return "—"
    return fmt_duration(remaining / rate_per_sec)


def parse_whisper_session(transcript_filename: str, log_root: str) -> dict:
    """Extrait les metadatas du fichier session.log Whisper."""
    base = Path(transcript_filename).stem
    log_path = Path(log_root) / f"{base}.session.log"
    if not log_path.exists():
        return {}
    try:
        with log_path.open("r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("RESULT_JSON:"):
                    return json.loads(line[len("RESULT_JSON:"):])
    except Exception as e:
        print(f"[WARN] parse session log {log_path}: {e}", file=sys.stderr, flush=True)
    return {}


def file_size_human(path: str) -> str:
    try:
        n = os.path.getsize(path)
        for unit in ("o", "Ko", "Mo", "Go"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} To"
    except Exception:
        return "?"


def build_file_embed(filename: str, args, cumul: int, done: int, rate: float, kind_meta: dict) -> dict:
    """Construit l'embed pour un fichier termine."""
    if args.total:
        remaining = args.total - done
        pct = 100.0 * done / args.total
        progress_line = f"`{done}/{args.total}` ({pct:.1f}%) · ETA **{fmt_eta(remaining, rate)}** · `{rate*60:.1f}/min`"
    else:
        progress_line = f"`+{cumul}` nouveaux · `{rate*60:.1f}/min`"

    embed = {
        "title": f"✅ {args.label} | {filename}",
        "color": COLOR_GREEN,
        "description": progress_line,
        "footer": {"text": f"Arsenal {args.label}"},
        "timestamp": now_iso(),
        "fields": [],
    }

    if args.kind == "whisper" and kind_meta:
        if "audio_duration" in kind_meta:
            embed["fields"].append({
                "name": "Audio",
                "value": fmt_duration(kind_meta["audio_duration"]),
                "inline": True,
            })
        if "transcribe_time" in kind_meta:
            embed["fields"].append({
                "name": "Transcription",
                "value": fmt_duration(kind_meta["transcribe_time"]),
                "inline": True,
            })
        if "ratio" in kind_meta:
            embed["fields"].append({
                "name": "Ratio",
                "value": f"x{kind_meta['ratio']:.1f}",
                "inline": True,
            })
        if "segments_count" in kind_meta:
            embed["fields"].append({
                "name": "Segments",
                "value": str(kind_meta["segments_count"]),
                "inline": True,
            })
        if "language" in kind_meta:
            embed["fields"].append({
                "name": "Langue",
                "value": f"{kind_meta['language']} ({int(kind_meta.get('language_prob', 0)*100)}%)" if kind_meta.get('language_prob', 0) <= 1 else kind_meta['language'],
                "inline": True,
            })
    elif args.kind == "ocr":
        out_path = Path(args.watch).parent.parent / filename
        # Approximation : taille du txt comme indicateur
        target = next(iter(glob.glob(str(Path(args.watch).parent / "**" / filename), recursive=True)), None) or filename
        embed["fields"].append({
            "name": "Taille",
            "value": file_size_human(target) if target else "?",
            "inline": True,
        })

    return embed


def build_start_embed(args, baseline: int) -> dict:
    desc = f"Surveillance demarree sur `{args.watch}`\nBaseline : **{baseline}** fichier(s)"
    if args.total:
        desc += f" / cible **{args.total}**"
    desc += f"\nIntervalle de poll : {args.interval}s"
    return {
        "title": f"🟢 {args.label} | Monitor demarre",
        "description": desc,
        "color": COLOR_BLUE,
        "footer": {"text": f"Arsenal {args.label}"},
        "timestamp": now_iso(),
    }


def build_milestone_embed(args, done: int, cumul: int, rate: float) -> dict:
    if args.total:
        pct = 100.0 * done / args.total
        title = f"🎯 {args.label} | Milestone {pct:.0f}%"
        desc = f"`{done}/{args.total}` traites — `{rate*60:.1f}/min` — ETA **{fmt_eta(args.total - done, rate)}**"
    else:
        title = f"🎯 {args.label} | +{cumul} fichiers"
        desc = f"Cumul session : **{cumul}** fichiers — `{rate*60:.1f}/min`"
    return {
        "title": title,
        "description": desc,
        "color": COLOR_PURPLE,
        "footer": {"text": f"Arsenal {args.label}"},
        "timestamp": now_iso(),
    }


def build_stall_embed(args, last_event_age_sec: float) -> dict:
    return {
        "title": f"⚠️ {args.label} | Aucune progression",
        "description": f"Aucun nouveau fichier depuis **{fmt_duration(last_event_age_sec)}**.\nLe pipeline est peut-etre bloque.",
        "color": COLOR_ORANGE,
        "footer": {"text": f"Arsenal {args.label}"},
        "timestamp": now_iso(),
    }


def build_complete_embed(args, cumul: int, elapsed: float) -> dict:
    rate = cumul / elapsed if elapsed > 0 else 0
    return {
        "title": f"🏁 {args.label} | Cible atteinte",
        "description": f"**{cumul}** fichiers traites en **{fmt_duration(elapsed)}** (rate moyen : `{rate*60:.1f}/min`)",
        "color": COLOR_PURPLE,
        "footer": {"text": f"Arsenal {args.label}"},
        "timestamp": now_iso(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", required=True, help="Glob pattern (relative au cwd)")
    ap.add_argument("--label", required=True, help="Label affiche dans Discord (ex: Whisper, OCR)")
    ap.add_argument("--kind", choices=["whisper", "ocr", "generic"], default="generic")
    ap.add_argument("--total", type=int, default=None, help="Cible totale (optionnel)")
    ap.add_argument("--interval", type=int, default=15, help="Intervalle de poll (sec)")
    ap.add_argument("--whisper-log-root", default="02_whisper_logs/videos",
                    help="Dossier des session.log Whisper (mode --kind whisper)")
    ap.add_argument("--stall-threshold", type=int, default=900,
                    help="Alerte si aucun nouveau fichier depuis N secondes (def 900s = 15min)")
    ap.add_argument("--milestone-every", type=int, default=50,
                    help="Embed milestone tous les N fichiers")
    args = ap.parse_args()

    baseline = snapshot(args.watch)
    seen = set(baseline)
    cumul = 0
    last_event_time = time.time()
    last_stall_alert = 0.0
    start_time = time.time()
    last_milestone = 0

    post_embed(build_start_embed(args, len(baseline)))
    print(f"[start] {args.label} baseline={len(baseline)} pattern={args.watch}", flush=True)

    try:
        while True:
            try:
                current = snapshot(args.watch)
                new_files = sorted(current - seen)
                now = time.time()

                if new_files:
                    seen.update(new_files)
                    last_event_time = now
                    last_stall_alert = 0.0
                    elapsed = now - start_time
                    rate = (cumul + len(new_files)) / elapsed if elapsed > 0 else 0
                    for f in new_files:
                        cumul += 1
                        kind_meta = {}
                        if args.kind == "whisper":
                            kind_meta = parse_whisper_session(f, args.whisper_log_root)
                        embed = build_file_embed(f, args, cumul, len(seen), rate, kind_meta)
                        post_embed(embed)
                        print(f"[post] {f} ({len(seen)}/{args.total or '?'})", flush=True)
                        time.sleep(0.4)  # respect Discord rate-limit (5 msg / 5s)

                        # Milestone every N files
                        if args.milestone_every and cumul - last_milestone >= args.milestone_every:
                            last_milestone = cumul
                            post_embed(build_milestone_embed(args, len(seen), cumul, rate))
                            time.sleep(0.4)

                    # Auto-complete if total reached
                    if args.total and len(seen) >= args.total:
                        post_embed(build_complete_embed(args, cumul, now - start_time))
                        print(f"[complete] {args.label} cumul={cumul}", flush=True)
                        return 0

                # Stall detection
                if (now - last_event_time) > args.stall_threshold and (now - last_stall_alert) > args.stall_threshold:
                    post_embed(build_stall_embed(args, now - last_event_time))
                    last_stall_alert = now

                time.sleep(args.interval)
            except Exception as e:
                print(f"[loop-err] {e}", file=sys.stderr, flush=True)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        post_embed({
            "title": f"🔴 {args.label} | Monitor arrete",
            "description": f"Cumul session : **{cumul}** fichier(s) traite(s)",
            "color": COLOR_RED,
            "footer": {"text": f"Arsenal {args.label}"},
            "timestamp": now_iso(),
        })
        print(f"[stop] {args.label} cumul={cumul}", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
