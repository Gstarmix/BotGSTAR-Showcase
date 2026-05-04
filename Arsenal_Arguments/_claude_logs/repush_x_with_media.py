"""
repush_x_with_media.py — One-shot Y.23 : reset des 7 X drops historiques
qui ont été synced sans média (avant le fix Y.23 du publisher).

Pour chaque ID :
1. Supprime le thread Discord existant (via REST API Discord).
2. Retire l'entrée pmap correspondante.
3. Reset `sync_status=PENDING` + `sync_timestamp` vides dans le CSV.

L'auto-sync 15s du publisher (avec Y.23 chargé) re-publiera proprement
avec les médias (`01_raw_images/X_<id>/01.jpg, 02.mp4, …`) attachés au
nouveau thread.

Usage :
    python _claude_logs/repush_x_with_media.py --dry-run
    python _claude_logs/repush_x_with_media.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARSENAL = ROOT / "Arsenal_Arguments"
sys.path.insert(0, str(ARSENAL))

from arsenal_config import cfg, GLOBAL_CSV_COLUMNS, CSV_ENCODING  # noqa: E402

PMAP_PATH = ARSENAL / "datas" / "arsenal_published_threads.json"

TARGET_IDS = [
    # Liste historique des 8 X drops récupérés via Y.21 et qui ont été
    # synced sans média avant le fix Y.23. Tous ont été republiés au
    # 4 mai 2026 (~08:08-08:12 UTC). Adapter cette liste pour des futurs
    # one-shots du même type.
    "2048079629756387678",
    "2048088874686300431",
    "2047979906852692259",
    "2045769863172395269",
    "2042715961363484890",
    "2042151501011751073",
    "2041549588536979826",
    "2032548375015338380",
]


async def delete_thread(token: str, thread_id: str) -> tuple[bool, str]:
    import aiohttp
    url = f"https://discord.com/api/v10/channels/{thread_id}"
    headers = {"Authorization": f"Bot {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.delete(url, headers=headers) as resp:
            text = await resp.text()
            return resp.status in (200, 204, 404), f"{resp.status} {text[:120]}"


def reset_csv_sync(ids: list[str]) -> int:
    with open(cfg.CSV_PATH, encoding=CSV_ENCODING) as f:
        rows = list(csv.DictReader(f))
    n = 0
    for r in rows:
        rid = (r.get("id") or "").strip()
        plat = (r.get("plateforme") or "").strip().lower()
        if plat == "x" and rid in ids:
            r["sync_status"] = "PENDING"
            r["sync_timestamp"] = ""
            r["sync_error"] = ""
            n += 1
    tmp = cfg.CSV_PATH + ".tmp"
    with open(tmp, "w", encoding=CSV_ENCODING, newline="") as f:
        w = csv.DictWriter(f, fieldnames=GLOBAL_CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            for c in GLOBAL_CSV_COLUMNS:
                r.setdefault(c, "")
            w.writerow({c: r.get(c, "") for c in GLOBAL_CSV_COLUMNS})
    os.replace(tmp, cfg.CSV_PATH)
    return n


def remove_pmap_entries(ids: list[str]) -> tuple[int, list[str]]:
    with open(PMAP_PATH, encoding="utf-8") as f:
        pmap = json.load(f)
    removed = []
    for cid in ids:
        for prefix in ("x::", "X::", "unknown::"):
            key = f"{prefix}{cid}"
            if key in pmap:
                removed.append((key, pmap[key]["thread_id"]))
                del pmap[key]
    tmp = str(PMAP_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pmap, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PMAP_PATH)
    thread_ids = [tid for _, tid in removed]
    return len(removed), thread_ids


async def main(apply: bool) -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    # 1. Identifie les pmap entries à supprimer (et garde les thread_ids).
    with open(PMAP_PATH, encoding="utf-8") as f:
        pmap = json.load(f)

    plan: list[tuple[str, str, str]] = []  # (cid, key, thread_id)
    for cid in TARGET_IDS:
        for prefix in ("x::", "X::", "unknown::"):
            key = f"{prefix}{cid}"
            if key in pmap:
                plan.append((cid, key, pmap[key]["thread_id"]))

    print(f"Plan : {len(plan)} entries à reset")
    for cid, key, tid in plan:
        title = pmap[key].get("title", "")[:60]
        print(f"  {cid}  thread={tid}  title={title}")

    if not apply:
        print("\nDRY-RUN — utiliser --apply pour exécuter.")
        return 0

    # 2. Charge le token.
    token = ""
    env_path = ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("DISCORD_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()
                break
    if not token:
        print("ERREUR : DISCORD_BOT_TOKEN non trouvé dans .env")
        return 1

    # 3. Delete chaque thread Discord.
    print("\nSuppression des threads Discord…")
    for cid, key, tid in plan:
        ok, msg = await delete_thread(token, tid)
        marker = "✓" if ok else "✗"
        print(f"  {marker} {tid} ({cid}) — {msg}")
        await asyncio.sleep(0.5)  # rate limit

    # 4. Retire pmap entries.
    n_removed, _ = remove_pmap_entries(TARGET_IDS)
    print(f"\npmap : {n_removed} entrées retirées (backup auto via _save).")

    # 5. Reset sync_status=PENDING.
    n_csv = reset_csv_sync(TARGET_IDS)
    print(f"CSV : {n_csv} ligne(s) X reset à sync=PENDING.")

    print("\nFini. L'auto-sync 15s du publisher (avec Y.23) va re-publier "
          "ces drops avec leurs médias dans les forums politiques.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Exécute (sinon dry-run)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
