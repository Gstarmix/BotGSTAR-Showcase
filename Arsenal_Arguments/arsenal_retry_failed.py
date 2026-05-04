"""
arsenal_retry_failed.py — Rattrapage des downloads FAILED dans suivi_global.csv.

Profite des fixes 2026-04-29 :
- yt-dlp 2026.03.17 (à jour)
- gallery-dl fallback dans dl_instagram (sauve les carrousels d'images IG)
- 6 plateformes supportées (TikTok/IG/YouTube/X/Reddit/Threads via dl_generic)

Pour chaque ligne FAILED, lance le bon downloader avec --url. Les lignes
SUCCESS résultantes sont ajoutées au CSV ; après la passe complète,
csv_normalize.py est appelé pour dédupliquer (garde la version la plus récente).

Usage:
    python arsenal_retry_failed.py                          # dry-run
    python arsenal_retry_failed.py --apply                  # exécute
    python arsenal_retry_failed.py --apply --limit 10       # max 10 retry
    python arsenal_retry_failed.py --apply --platform Instagram
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

from arsenal_config import cfg, CSV_ENCODING, get_logger

log = get_logger("arsenal_retry_failed")

PLATFORM_TO_SCRIPT = {
    "tiktok":   "dl_tiktok.py",
    "instagram":"dl_instagram.py",
    "youtube":  "dl_generic.py",
    "x":        "dl_generic.py",
    "twitter":  "dl_generic.py",
    "reddit":   "dl_generic.py",
    "threads":  "dl_generic.py",
}


def load_failed_rows(platform_filter: str | None = None) -> list[dict]:
    """Lit le CSV et retourne les lignes download_status=FAILED.

    Si plusieurs lignes pour le même (plateforme, id), on ne garde que la PLUS RÉCENTE
    (celle qui reflète l'état actuel du download). Si la dernière est SUCCESS, on
    skippe la ligne (elle a déjà réussi en retry précédent).
    """
    if not os.path.isfile(cfg.CSV_PATH):
        log.error(f"CSV introuvable : {cfg.CSV_PATH}")
        return []
    with open(cfg.CSV_PATH, encoding=CSV_ENCODING) as f:
        all_rows = list(csv.DictReader(f))

    # Indexer par (plateforme.lower(), id) pour garder la dernière version
    latest: dict[tuple, dict] = {}
    for row in all_rows:
        plat = (row.get("plateforme") or "").strip().lower()
        rid = (row.get("id") or "").strip()
        if not plat or not rid:
            continue
        key = (plat, rid)
        # `download_timestamp` est le tie-breaker : on garde le plus récent
        prev = latest.get(key)
        if prev is None or row.get("download_timestamp", "") > prev.get("download_timestamp", ""):
            latest[key] = row

    failed = [r for r in latest.values()
              if (r.get("download_status") or "").strip().upper() == "FAILED"]

    if platform_filter:
        pf = platform_filter.lower()
        failed = [r for r in failed
                  if (r.get("plateforme") or "").strip().lower() == pf]

    # Trie par timestamp pour déterminisme
    failed.sort(key=lambda r: r.get("download_timestamp", ""))
    return failed


def retry_one(row: dict) -> tuple[bool, str]:
    plat = (row.get("plateforme") or "").strip().lower()
    url = (row.get("url") or "").strip()
    rid = (row.get("id") or "").strip()
    if not url:
        return False, "url_missing"

    script_name = PLATFORM_TO_SCRIPT.get(plat)
    if not script_name:
        return False, f"unknown_platform:{plat}"

    # cfg.base_path est un str (cf ArsenalPaths), pas un Path. Wrappe avant /.
    base = Path(cfg.base_path) if hasattr(cfg, "base_path") else Path(cfg.BASE_PATH)
    script_path = base / script_name
    # Fallback robuste
    if not Path(script_path).is_file():
        script_path = Path(__file__).resolve().parent / script_name

    cmd = [sys.executable, str(script_path), "--url", url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return False, "timeout_600s"

    if res.returncode != 0:
        return False, f"rc={res.returncode}:{(res.stderr or '')[-200:]}"
    return True, "ok"


def normalize_csv():
    """Appelle csv_normalize.py pour dédupliquer (garde dernière entry par id)."""
    script_path = Path(__file__).resolve().parent / "csv_normalize.py"
    cmd = [sys.executable, str(script_path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        log.info(f"csv_normalize : rc={res.returncode}")
        if res.returncode != 0:
            log.warning((res.stderr or "")[-300:])
    except subprocess.TimeoutExpired:
        log.error("csv_normalize timeout")


def main():
    parser = argparse.ArgumentParser(description="Retry FAILED downloads from CSV")
    parser.add_argument("--apply", action="store_true",
                        help="Exécute (sinon dry-run)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limite le nombre de retry (0 = pas de limite)")
    parser.add_argument("--platform", type=str, default=None,
                        help="Filtrer par plateforme (Instagram, TikTok, …)")
    parser.add_argument("--no-normalize", action="store_true",
                        help="Ne pas appeler csv_normalize.py à la fin")
    args = parser.parse_args()

    log.info(f"Mode : {'APPLY' if args.apply else 'DRY-RUN'}")
    log.info(f"CSV  : {cfg.CSV_PATH}")
    failed = load_failed_rows(platform_filter=args.platform)
    log.info(f"FAILED uniques : {len(failed)}")

    if args.platform:
        log.info(f"Filtre plateforme : {args.platform}")
    if args.limit > 0 and len(failed) > args.limit:
        log.info(f"Limite : {args.limit} (sur {len(failed)})")
        failed = failed[:args.limit]

    by_platform: dict[str, int] = {}
    for r in failed:
        p = (r.get("plateforme") or "").strip()
        by_platform[p] = by_platform.get(p, 0) + 1
    log.info(f"Répartition : {by_platform}")

    if not args.apply:
        log.info("Dry-run : rien n'est exécuté. Utiliser --apply pour relancer.")
        for r in failed[:10]:
            log.info(f"  would retry : {r.get('plateforme')} {r.get('id')}  url={r.get('url')[:80]}")
        if len(failed) > 10:
            log.info(f"  … et {len(failed) - 10} autres")
        return 0

    success = fail = 0
    fail_reasons: dict[str, int] = {}
    start = time.time()
    for i, r in enumerate(failed, 1):
        plat = r.get("plateforme")
        rid = r.get("id")
        log.info(f"[{i}/{len(failed)}] {plat} {rid} …")
        ok, msg = retry_one(r)
        if ok:
            success += 1
            log.info(f"  ✅ OK")
        else:
            fail += 1
            reason = msg.split(":")[0] if ":" in msg else msg
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
            log.warning(f"  ❌ {msg[:200]}")

    duration = time.time() - start
    log.info(f"\nTerminé en {duration/60:.1f}m. ✅ {success}  ❌ {fail}")
    if fail_reasons:
        log.info(f"Causes d'échec : {fail_reasons}")

    if not args.no_normalize:
        log.info("Normalisation du CSV…")
        normalize_csv()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.warning("Interrompu par l'utilisateur")
        sys.exit(130)
