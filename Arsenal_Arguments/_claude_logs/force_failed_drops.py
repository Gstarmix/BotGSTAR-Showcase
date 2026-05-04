"""
force_failed_drops.py — One-shot : force le summarize des messages ❌ du salon
🔗・liens malgré le quota Pro Max.

Le bot Discord est en `weekly_throttled=true` (91% > seuil 80%) → pipeline a
posté ❌ sur les drops récents au lieu de générer le résumé. Ce script :

  1. Charge la liste de messages ❌ déjà scannée (failed_reactions.json).
  2. Pour chaque message, résout (plateforme, content_id) via les helpers du
     cog (extract_urls_all_platforms + resolve_tiktok_short_url).
  3. Lit suivi_global.csv et catégorise chaque ID :
       - TO_SUMMARIZE : DL=SUCCESS, summary != SUCCESS
       - DL_FAILED    : DL!=SUCCESS, on ne peut pas résumer
       - ALREADY_OK   : DL+summary+sync = SUCCESS
       - NOT_IN_CSV   : orphan, skip
  4. Pour chaque TO_SUMMARIZE, lance `summarize.py --use-claude-code --id <X>`
     en subprocess séquentiel (lock-safe avec le lock du summarizer). Comme on
     appelle le script directement (pas via le wrapper bot `step_summarize`),
     le check quota du bot est by-passé.
  5. Attend ~90 s pour laisser l'auto-sync du publisher (loop 15 s) ramasser
     les nouveaux summary=SUCCESS et publier sur les forums.
  6. Re-lit le CSV et, pour chaque message dont toutes les URLs sont
     full-SUCCESS (DL+summary+sync), supprime ❌ et ajoute ✅ via REST.

Lancer une seule fois :
    python Arsenal_Arguments/_claude_logs/force_failed_drops.py --apply

Sans --apply : dry-run (montre ce qui serait fait, ne touche rien).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# --- Path setup so we can import the cog helpers ---
ROOT = Path(__file__).resolve().parents[2]
ARSENAL = ROOT / "Arsenal_Arguments"
EXTENSIONS = ROOT / "extensions"
sys.path.insert(0, str(ARSENAL))
sys.path.insert(0, str(ROOT))

# Import URL extraction helpers from the cog (single source of truth)
from extensions.arsenal_pipeline import (  # noqa: E402
    extract_urls_all_platforms,
    extract_id_for_platform,
    resolve_tiktok_short_url,
)

# Discord token via .env
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
TOKEN = os.environ["DISCORD_BOT_TOKEN"]

CSV_PATH = ARSENAL / "suivi_global.csv"
SUMMARIZE_PY = ARSENAL / "summarize.py"
PYTHON = sys.executable
LIST_FILE = ARSENAL / "_claude_logs" / "failed_reactions.json"
CHANNEL = 1498918445763268658  # 🔗・liens
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "BotGSTAR/force-failed-drops",
    "Content-Type": "application/json",
}
EMOJI_FAIL = "❌"
EMOJI_OK = "✅"


def load_csv() -> dict[tuple[str, str], dict]:
    """Index CSV rows by (plateforme.lower(), id). If multiple rows share a key,
    keep the most recent download_timestamp (latest state)."""
    if not CSV_PATH.is_file():
        sys.exit(f"CSV introuvable : {CSV_PATH}")
    latest: dict[tuple[str, str], dict] = {}
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            plat = (row.get("plateforme") or "").strip().lower()
            rid = (row.get("id") or "").strip()
            if not plat or not rid:
                continue
            key = (plat, rid)
            prev = latest.get(key)
            if prev is None or (row.get("download_timestamp") or "") > (prev.get("download_timestamp") or ""):
                latest[key] = row
    return latest


def resolve_message_urls(content: str) -> list[tuple[str, str]]:
    """Returns list of (platform_lower, content_id) extracted from the message
    body, with TikTok short URLs resolved to their long form."""
    out = []
    for u in extract_urls_all_platforms(content):
        url = u["url"]
        plat = u["platform"]
        if plat == "TikTok":
            url = resolve_tiktok_short_url(url)
        cid = extract_id_for_platform(url, plat)
        if cid:
            out.append((plat.lower(), cid))
    return out


def categorize(messages: list[dict], csv_index: dict) -> tuple[list, list, list, list]:
    """Returns (to_summarize, dl_failed, already_ok, not_in_csv).
    Each item in to_summarize / dl_failed is dict
        {message_id, content, urls=[(plat, id, row)]}"""
    to_summarize, dl_failed, already_ok, not_in_csv = [], [], [], []
    for m in messages:
        urls = resolve_message_urls(m.get("content") or "")
        if not urls:
            not_in_csv.append({**m, "urls": []})
            continue
        rows = []
        for plat, cid in urls:
            row = csv_index.get((plat, cid))
            rows.append((plat, cid, row))
        # Decision per message
        any_dl_failed = False
        any_summary_pending = False
        all_full_success = True
        any_in_csv = False
        for plat, cid, row in rows:
            if row is None:
                all_full_success = False
                continue
            any_in_csv = True
            dl = (row.get("download_status") or "").upper().strip()
            su = (row.get("summary_status") or "").upper().strip()
            sy = (row.get("sync_status") or "").upper().strip()
            if dl != "SUCCESS":
                any_dl_failed = True
                all_full_success = False
            if su != "SUCCESS":
                if dl == "SUCCESS":
                    any_summary_pending = True
                all_full_success = False
            if sy != "SUCCESS":
                all_full_success = False
        if not any_in_csv:
            not_in_csv.append({**m, "urls": rows})
        elif all_full_success:
            already_ok.append({**m, "urls": rows})
        elif any_summary_pending:
            to_summarize.append({**m, "urls": rows})
        elif any_dl_failed:
            dl_failed.append({**m, "urls": rows})
        else:
            # Edge case : DL ok, summary ok, sync pending → just need sync
            already_ok.append({**m, "urls": rows})
    return to_summarize, dl_failed, already_ok, not_in_csv


def run_summarize(content_id: str) -> tuple[bool, str]:
    """Lance `summarize.py --use-claude-code --id <X>` en subprocess séquentiel.
    Bypass : on appelle summarize.py directement, pas le wrapper bot, donc
    pas de check quota Pro Max."""
    cmd = [
        PYTHON, str(SUMMARIZE_PY),
        "--base-dir", str(ARSENAL),
        "--use-claude-code",
        "--id", content_id,
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=900,  # 15 min cap par item
        )
    except subprocess.TimeoutExpired:
        return False, "timeout_900s"
    if r.returncode != 0:
        return False, f"rc={r.returncode}: {(r.stderr or '')[-300:]}"
    return True, "ok"


def discord_request(method: str, path: str, **kw):
    """Wrapper minimal pour l'API Discord REST."""
    url = f"https://discord.com/api/v10{path}"
    req = urllib.request.Request(url, method=method, headers=HEADERS)
    body = kw.get("json")
    if body is not None:
        req.data = json.dumps(body).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def flip_reaction(message_id: str) -> tuple[bool, str]:
    """Remove ❌ then add ✅ on the bot's behalf."""
    e_fail = urllib.parse.quote(EMOJI_FAIL)
    e_ok = urllib.parse.quote(EMOJI_OK)
    s1, _ = discord_request("DELETE", f"/channels/{CHANNEL}/messages/{message_id}/reactions/{e_fail}/@me")
    s2, _ = discord_request("PUT", f"/channels/{CHANNEL}/messages/{message_id}/reactions/{e_ok}/@me")
    return (s1 in (200, 204) and s2 in (200, 204)), f"del={s1} put={s2}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Exécute (sinon dry-run)")
    p.add_argument("--limit", type=int, default=0, help="Limite (0 = pas de limite)")
    p.add_argument("--sync-wait", type=int, default=90,
                    help="Secondes à attendre après le batch summarize avant de re-lire le CSV")
    args = p.parse_args()

    if not LIST_FILE.is_file():
        sys.exit(f"Liste introuvable : {LIST_FILE} (relance le scan d'abord)")
    messages = json.loads(LIST_FILE.read_text(encoding="utf-8"))
    print(f"[load] {len(messages)} messages avec ❌ chargés depuis {LIST_FILE.name}")

    csv_index = load_csv()
    print(f"[load] {len(csv_index)} (plateforme, id) uniques au CSV")

    to_summarize, dl_failed, already_ok, not_in_csv = categorize(messages, csv_index)
    print(f"[cat]  TO_SUMMARIZE: {len(to_summarize)}")
    print(f"[cat]  DL_FAILED   : {len(dl_failed)}")
    print(f"[cat]  ALREADY_OK  : {len(already_ok)} (juste à flipper la reaction)")
    print(f"[cat]  NOT_IN_CSV  : {len(not_in_csv)}")

    if args.limit and len(to_summarize) > args.limit:
        print(f"[limit] limite à {args.limit} sur {len(to_summarize)} TO_SUMMARIZE")
        to_summarize = to_summarize[:args.limit]

    if not args.apply:
        print()
        print("DRY-RUN — rien n'est exécuté. Echantillon TO_SUMMARIZE :")
        for m in to_summarize[:10]:
            ids = [f"{p}:{c}" for p, c, _ in m["urls"] if c]
            print(f"  · msg={m['id']}  ids={','.join(ids)}")
        if len(to_summarize) > 10:
            print(f"  ... et {len(to_summarize) - 10} autres")
        print()
        print("Echantillon DL_FAILED (sera skip) :")
        for m in dl_failed[:5]:
            ids = [f"{p}:{c}" for p, c, _ in m["urls"] if c]
            print(f"  · msg={m['id']}  ids={','.join(ids)}")
        print()
        print("Echantillon ALREADY_OK (juste flip ❌→✅) :")
        for m in already_ok[:5]:
            ids = [f"{p}:{c}" for p, c, _ in m["urls"] if c]
            print(f"  · msg={m['id']}  ids={','.join(ids)}")
        print()
        print("Pour exécuter : python force_failed_drops.py --apply")
        return 0

    # Phase 1 : flip ALREADY_OK (zero LLM cost)
    print()
    print(f"[phase1] Flip immédiat ❌→✅ pour {len(already_ok)} messages déjà SUCCESS au CSV")
    flipped_immediate = 0
    for m in already_ok:
        ok, info = flip_reaction(m["id"])
        if ok:
            flipped_immediate += 1
        else:
            print(f"  WARN flip failed msg={m['id']}: {info}")
    print(f"[phase1] flip immédiat : {flipped_immediate}/{len(already_ok)} OK")

    # Phase 2 : summarize batch
    summarize_ids: list[str] = []
    seen = set()
    for m in to_summarize:
        for plat, cid, row in m["urls"]:
            if row is None:
                continue
            su = (row.get("summary_status") or "").upper().strip()
            dl = (row.get("download_status") or "").upper().strip()
            if dl == "SUCCESS" and su != "SUCCESS" and cid not in seen:
                summarize_ids.append(cid)
                seen.add(cid)
    print()
    print(f"[phase2] Summarize batch : {len(summarize_ids)} content_ids uniques")
    summarized_ok = 0
    summarize_fail: list[tuple[str, str]] = []
    t0 = time.time()
    for i, cid in enumerate(summarize_ids, 1):
        elapsed = time.time() - t0
        eta = (elapsed / i * (len(summarize_ids) - i)) if i > 0 else 0
        print(f"  [{i}/{len(summarize_ids)}] summarize id={cid} (elapsed={elapsed:.0f}s, eta={eta:.0f}s)")
        ok, info = run_summarize(cid)
        if ok:
            summarized_ok += 1
        else:
            summarize_fail.append((cid, info))
            print(f"    FAIL: {info}")
    print(f"[phase2] summarize : {summarized_ok}/{len(summarize_ids)} OK")
    if summarize_fail:
        print(f"[phase2] échecs ({len(summarize_fail)}) :")
        for cid, info in summarize_fail[:10]:
            print(f"    · {cid}: {info[:200]}")

    # Phase 3 : wait for auto-sync
    if summarized_ok > 0 and args.sync_wait > 0:
        print()
        print(f"[phase3] Attente {args.sync_wait}s pour laisser l'auto-sync du publisher tourner…")
        time.sleep(args.sync_wait)

    # Phase 4 : re-read CSV, flip reactions for newly-success messages
    print()
    print("[phase4] Re-lecture CSV + flip ❌→✅ des messages devenus SUCCESS")
    csv_index2 = load_csv()
    to_check = to_summarize + dl_failed  # dl_failed could have been retried (it wasn't, but stay safe)
    flipped_after = 0
    still_failed = 0
    for m in to_check:
        all_ok = True
        for plat, cid, _row in m["urls"]:
            if not cid:
                all_ok = False
                break
            row2 = csv_index2.get((plat, cid))
            if not row2:
                all_ok = False
                break
            dl = (row2.get("download_status") or "").upper().strip()
            su = (row2.get("summary_status") or "").upper().strip()
            sy = (row2.get("sync_status") or "").upper().strip()
            if dl != "SUCCESS" or su != "SUCCESS" or sy != "SUCCESS":
                all_ok = False
                break
        if all_ok:
            ok, info = flip_reaction(m["id"])
            if ok:
                flipped_after += 1
            else:
                print(f"  WARN flip failed msg={m['id']}: {info}")
        else:
            still_failed += 1
    print(f"[phase4] flip après summarize : {flipped_after} OK, {still_failed} encore en ❌")

    print()
    print("=== Récap ===")
    print(f"  Messages traités     : {len(messages)}")
    print(f"  Flip immédiat (déjà OK) : {flipped_immediate}")
    print(f"  Summarize lancés     : {len(summarize_ids)}")
    print(f"  Summarize OK         : {summarized_ok}")
    print(f"  Flip après summarize : {flipped_after}")
    print(f"  Encore en ❌         : {still_failed} (probablement DL FAILED)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompu.")
        sys.exit(130)
