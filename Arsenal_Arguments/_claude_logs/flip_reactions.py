"""
flip_reactions.py — Phase 4 standalone : flip ❌→✅ pour les messages dont la
ligne CSV est full-SUCCESS (DL+summary+sync). Resilient aux timeouts SSL via
retry exponentiel et sleep entre calls.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARSENAL = ROOT / "Arsenal_Arguments"
sys.path.insert(0, str(ARSENAL))
sys.path.insert(0, str(ROOT))

from extensions.arsenal_pipeline import (  # noqa: E402
    extract_urls_all_platforms,
    extract_id_for_platform,
    resolve_tiktok_short_url,
)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
TOKEN = os.environ["DISCORD_BOT_TOKEN"]

CSV_PATH = ARSENAL / "suivi_global.csv"
LIST_FILE = ARSENAL / "_claude_logs" / "failed_reactions.json"
CHANNEL = 1498918445763268658
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "BotGSTAR/flip-reactions",
    "Content-Type": "application/json",
}
EMOJI_FAIL = "❌"
EMOJI_OK = "✅"


def load_csv():
    latest = {}
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


def discord_request_retry(method: str, path: str, max_attempts: int = 4):
    url = f"https://discord.com/api/v10{path}"
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(url, method=method, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited
                try:
                    body = json.loads(e.read())
                    retry_after = float(body.get("retry_after", 1.0))
                except Exception:
                    retry_after = 1.0
                time.sleep(retry_after + 0.2)
                continue
            return e.code, b""
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == max_attempts:
                return 0, str(e).encode()
            time.sleep(delay)
            delay *= 1.5
    return 0, b"all_attempts_failed"


def flip(message_id: str):
    e_fail = urllib.parse.quote(EMOJI_FAIL)
    e_ok = urllib.parse.quote(EMOJI_OK)
    s1, b1 = discord_request_retry("PUT", f"/channels/{CHANNEL}/messages/{message_id}/reactions/{e_ok}/@me")
    if s1 not in (200, 204):
        return False, f"put_ok={s1}:{b1[:80]}"
    s2, b2 = discord_request_retry("DELETE", f"/channels/{CHANNEL}/messages/{message_id}/reactions/{e_fail}/@me")
    if s2 not in (200, 204):
        return False, f"del_fail={s2}:{b2[:80]}"
    return True, "ok"


def resolve(content):
    out = []
    for u in extract_urls_all_platforms(content or ""):
        url = u["url"]
        plat = u["platform"]
        if plat == "TikTok":
            url = resolve_tiktok_short_url(url)
        cid = extract_id_for_platform(url, plat)
        if cid:
            out.append((plat.lower(), cid))
    return out


def main():
    messages = json.loads(LIST_FILE.read_text(encoding="utf-8"))
    csv_index = load_csv()
    print(f"[flip] {len(messages)} messages, {len(csv_index)} CSV rows")

    flipped, still_failed, errors = 0, 0, []
    for i, m in enumerate(messages, 1):
        urls = resolve(m.get("content") or "")
        if not urls:
            still_failed += 1
            continue
        all_ok = True
        for plat, cid in urls:
            row = csv_index.get((plat, cid))
            if not row:
                all_ok = False
                break
            dl = (row.get("download_status") or "").upper().strip()
            su = (row.get("summary_status") or "").upper().strip()
            sy = (row.get("sync_status") or "").upper().strip()
            if dl != "SUCCESS" or su != "SUCCESS" or sy != "SUCCESS":
                all_ok = False
                break
        if all_ok:
            ok, info = flip(m["id"])
            if ok:
                flipped += 1
                print(f"  [{i}/{len(messages)}] flip OK msg={m['id']}")
            else:
                errors.append((m["id"], info))
                print(f"  [{i}/{len(messages)}] flip FAIL msg={m['id']} : {info}")
            time.sleep(0.5)  # rate-limit friendly
        else:
            still_failed += 1

    print()
    print("=== Récap flip ===")
    print(f"  Flipped         : {flipped}")
    print(f"  Encore en ❌    : {still_failed}")
    print(f"  Erreurs flip    : {len(errors)}")
    for mid, info in errors[:10]:
        print(f"    · {mid}: {info}")


if __name__ == "__main__":
    sys.exit(main())
