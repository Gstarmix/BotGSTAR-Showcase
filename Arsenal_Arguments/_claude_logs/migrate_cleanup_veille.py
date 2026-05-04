"""Phase E — Cleanup côté serveur Veille (1475846763909873727).

Supprime les éléments désormais migrés vers ISTIC L1 G2 :
- Catégorie ARSENAL (1493709215494181047) + ses 6 forums (avec posts)
- Catégorie 📡 VEILLE POLITIQUE (1498693097939271770) + ses 7 salons texte
- Salon #liens (1493701174656766122) dans la catégorie Salons textuels

Préservé sur Veille :
- Catégories TRAVAUX, LOGICIELS, PROMPTS (usages annexes hors migration)
- Salons #général, #logs, #blabla, #n8n, #whisper, #test, #inspirations, #rules, #moderator-only
- Catégorie Salons textuels (parent)

Usage :
    python migrate_cleanup_veille.py            # dry-run
    python migrate_cleanup_veille.py --apply    # supprime
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")
TOKEN = os.environ["DISCORD_BOT_TOKEN"]

VEILLE = "1475846763909873727"

CATEGORY_ARSENAL_ID = "1493709215494181047"
CATEGORY_VEILLE_POL_ID = "1498693097939271770"
LIENS_CHANNEL_ID = "1493701174656766122"

API = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "BotGSTAR-Cleanup/1.0",
}


def http(method: str, path: str) -> dict:
    req = urllib.request.Request(f"{API}/{path}", headers=HEADERS, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                try:
                    j = json.loads(body)
                    wait = float(j.get("retry_after", 2))
                except Exception:
                    wait = 2
                print(f"    [429] retry dans {wait}s")
                time.sleep(wait)
                continue
            print(f"    [ERR HTTP {e.code}] {body[:300]}")
            raise
    raise RuntimeError(f"Failed {method} {path}")


def delete_channel(cid: str, name: str, apply: bool):
    label = "DELETE" if apply else "would DELETE"
    print(f"  {label}  #{name}  ({cid})")
    if apply:
        http("DELETE", f"channels/{cid}")
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Exécute les DELETE")
    args = parser.parse_args()

    print(f"Mode : {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"\nFetch structure du serveur Veille…")
    chans = http("GET", f"guilds/{VEILLE}/channels")
    by_id = {c["id"]: c for c in chans}
    by_parent = {}
    for c in chans:
        pid = c.get("parent_id")
        if pid:
            by_parent.setdefault(pid, []).append(c)

    # Phase 1 : enfants de catégorie ARSENAL
    print(f"\n══ Phase 1 : suppression contenu de catégorie ARSENAL ({CATEGORY_ARSENAL_ID})")
    for child in by_parent.get(CATEGORY_ARSENAL_ID, []):
        delete_channel(child["id"], child["name"], args.apply)

    # Phase 2 : catégorie ARSENAL elle-même
    print(f"\n══ Phase 2 : suppression catégorie ARSENAL")
    if CATEGORY_ARSENAL_ID in by_id:
        delete_channel(CATEGORY_ARSENAL_ID, by_id[CATEGORY_ARSENAL_ID]["name"], args.apply)
    else:
        print("  (déjà absent)")

    # Phase 3 : enfants de catégorie 📡 VEILLE POLITIQUE
    print(f"\n══ Phase 3 : suppression contenu de catégorie 📡 VEILLE POLITIQUE ({CATEGORY_VEILLE_POL_ID})")
    for child in by_parent.get(CATEGORY_VEILLE_POL_ID, []):
        delete_channel(child["id"], child["name"], args.apply)

    # Phase 4 : catégorie 📡 VEILLE POLITIQUE elle-même
    print(f"\n══ Phase 4 : suppression catégorie 📡 VEILLE POLITIQUE")
    if CATEGORY_VEILLE_POL_ID in by_id:
        delete_channel(CATEGORY_VEILLE_POL_ID, by_id[CATEGORY_VEILLE_POL_ID]["name"], args.apply)
    else:
        print("  (déjà absent)")

    # Phase 5 : salon #liens
    print(f"\n══ Phase 5 : suppression salon #liens ({LIENS_CHANNEL_ID})")
    if LIENS_CHANNEL_ID in by_id:
        delete_channel(LIENS_CHANNEL_ID, by_id[LIENS_CHANNEL_ID]["name"], args.apply)
    else:
        print("  (déjà absent)")

    print(f"\nTerminé.{'' if args.apply else '  Relancer avec --apply pour supprimer.'}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[ERR] HTTP {e.code} : {body[:500]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERR] {type(e).__name__} : {e}", file=sys.stderr)
        sys.exit(1)
