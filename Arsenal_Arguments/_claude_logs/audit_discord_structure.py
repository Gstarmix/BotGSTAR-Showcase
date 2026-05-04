"""Audit complet de la structure des 2 guilds Discord (ISTIC + Veille).

Liste catégories + salons par parent_id, et identifie les forums + leurs tags.
Sortie strictement informationnelle, ne modifie rien.
"""
from __future__ import annotations
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")
TOKEN = os.environ["DISCORD_BOT_TOKEN"]

API = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "BotGSTAR-Audit/1.0",
}

GUILDS = {
    "ISTIC L1 G2":  "1466806132998672466",
    "Veille":       "1475846763909873727",
}

CHANNEL_TYPES = {
    0: "text", 2: "voice", 4: "category",
    5: "announcement", 10: "thread", 11: "thread_pub",
    12: "thread_priv", 13: "stage", 14: "directory",
    15: "forum", 16: "media",
}


def http_get(path: str) -> list | dict:
    req = urllib.request.Request(f"{API}/{path}", headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def audit_guild(name: str, gid: str) -> None:
    print(f"\n{'═' * 70}")
    print(f"GUILD : {name}  ({gid})")
    print('═' * 70)

    try:
        guild = http_get(f"guilds/{gid}")
        print(f"Nom officiel : {guild.get('name')}")
        print(f"Owner        : {guild.get('owner_id')}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  [ERR] HTTP {e.code} sur /guilds/{gid} : {body[:300]}")
        return

    chans = http_get(f"guilds/{gid}/channels")
    cats = sorted([c for c in chans if c["type"] == 4],
                  key=lambda c: c.get("position", 0))
    by_parent: dict[str, list] = {}
    orphans = []
    for c in chans:
        if c["type"] == 4:
            continue
        pid = c.get("parent_id")
        if pid:
            by_parent.setdefault(pid, []).append(c)
        else:
            orphans.append(c)

    if orphans:
        print("\n  [Sans catégorie]")
        for c in sorted(orphans, key=lambda x: x.get("position", 0)):
            kind = CHANNEL_TYPES.get(c["type"], f"type_{c['type']}")
            print(f"    {kind:8} #{c['name']}  ({c['id']})")

    for cat in cats:
        cat_id = cat["id"]
        print(f"\n  [Catégorie] {cat['name']}  ({cat_id})")
        children = sorted(by_parent.get(cat_id, []),
                          key=lambda x: x.get("position", 0))
        for c in children:
            kind = CHANNEL_TYPES.get(c["type"], f"type_{c['type']}")
            tags_str = ""
            if c["type"] == 15 and c.get("available_tags"):
                tag_names = [t["name"] for t in c["available_tags"]]
                tags_str = f"  tags={tag_names}"
            print(f"    {kind:8} #{c['name']}  ({c['id']}){tags_str}")


if __name__ == "__main__":
    for name, gid in GUILDS.items():
        audit_guild(name, gid)
    print(f"\n{'─' * 70}\nAudit terminé.")
