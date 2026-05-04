"""Setup unique des salons de veille politique sur le serveur Veille (1475846763909873727).

⚠ DEPRECATED — script obsolète depuis la migration du 2026-04-29 (Phase W) :
- Le serveur Veille n'héberge plus la veille politique (migrée vers ISTIC L1 G2).
- Les noms des catégories/salons ont changé.
- Conservé ici pour la traçabilité historique uniquement (voir CHANGELOG.md
  Phase V pour la mise en place initiale, Phase W pour la migration).

NE PLUS EXÉCUTER.

---

Crée :
- Catégorie "📡 VEILLE POLITIQUE"
- 5 salons texte sous cette catégorie : lfi-veille, gauche-veille, economie-veille,
  ecologie-veille, international-veille

Idempotent : si un objet existe déjà avec le bon nom, il n'est pas recréé.
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
GUILD_ID = "1475846763909873727"

CATEGORY_NAME = "📡 VEILLE POLITIQUE"
CHANNELS = [
    ("lfi-veille",           "Veille LFI / Union Populaire — sources : LFI, L'Insoumission, Frustration, LVSL, Mélenchon"),
    ("gauche-veille",        "Veille gauche — Mediapart, L'Humanité, Regards, StreetPress, Libération Politique"),
    ("economie-veille",      "Veille économie politique — Alt. Eco, Contretemps, Attac, Inégalités, RFI Eco"),
    ("ecologie-veille",      "Veille écologie / climat — Reporterre, Bon Pote, Vert, Basta, EcoloObs"),
    ("international-veille", "Veille international / géopolitique — Monde Diplo, Courrier Int., Le Monde Int., France 24, RFI"),
]

API = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "BotGSTAR-Setup/1.0",
}


def http(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(f"{API}/{path}", data=data, headers=HEADERS, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def main() -> int:
    print(f"Connexion au guild {GUILD_ID}...")
    chans = http("GET", f"guilds/{GUILD_ID}/channels")
    cats = {c["name"]: c for c in chans if c["type"] == 4}
    by_id = {c["id"]: c for c in chans}

    # 1) Catégorie
    cat = cats.get(CATEGORY_NAME)
    if not cat:
        # Si une catégorie existe deja avec un nom proche (sans emoji), ne pas la recréer
        # Recherche tolérante.
        for c in cats.values():
            if "VEILLE POLITIQUE" in c["name"].upper():
                cat = c
                break
    if not cat:
        print(f"  Création catégorie : {CATEGORY_NAME}")
        cat = http("POST", f"guilds/{GUILD_ID}/channels", {
            "name": CATEGORY_NAME,
            "type": 4,
        })
    else:
        print(f"  Catégorie déjà présente : {cat['name']} ({cat['id']})")
    cat_id = cat["id"]

    # 2) Salons
    for ch_name, topic in CHANNELS:
        # Recherche existant
        existing = next(
            (c for c in chans if c["type"] == 0 and c.get("name") == ch_name),
            None,
        )
        if existing:
            # Vérifie qu'il est bien dans la bonne catégorie (sinon move)
            if existing.get("parent_id") != cat_id:
                print(f"  Move : #{ch_name} vers la catégorie {cat['name']}")
                http("PATCH", f"channels/{existing['id']}", {"parent_id": cat_id})
            else:
                print(f"  Salon déjà OK : #{ch_name} ({existing['id']})")
            continue

        print(f"  Création salon : #{ch_name}")
        new_ch = http("POST", f"guilds/{GUILD_ID}/channels", {
            "name": ch_name,
            "type": 0,
            "parent_id": cat_id,
            "topic": topic,
        })
        print(f"    OK : {new_ch['id']}")

    print("\nSetup terminé.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[ERR] HTTP {e.code} : {body[:500]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[ERR] {type(e).__name__} : {e}", file=sys.stderr)
        sys.exit(1)
