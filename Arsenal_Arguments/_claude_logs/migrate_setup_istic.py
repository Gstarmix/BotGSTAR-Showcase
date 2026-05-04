"""Migration veille/arsenal → ISTIC L1 G2.

⚠ DEPRECATED — script à usage unique pour la migration du 2026-04-29 (Phase W).
NE PLUS EXÉCUTER : matching par anciens noms (`📂 ARSENAL`, `🎯 ARSENAL POLITIQUE`,
`🔗・liens-arsenal`) qui ont été renommés depuis. Une re-exécution recrée
des doublons. Conservé ici pour la traçabilité de la migration uniquement
(voir CHANGELOG.md Phase W).

---

Phase 1 : crée la catégorie 🎯 ARSENAL POLITIQUE + 7 salons RSS politiques.
Phase 2 : crée la catégorie 📂 ARSENAL + 6 forums avec leurs tags (copiés du serveur Veille).
Phase 3 : crée le salon 🔗・liens-arsenal dans la catégorie 🔒 PERSONNEL existante.

Idempotent (faux — bug de matching post-renommage).
Sortie : mapping ancien_id → nouveau_id pour les 3 phases.

Usage :
    python migrate_setup_istic.py            # dry-run, affiche ce qui serait fait
    python migrate_setup_istic.py --apply    # exécute les créations
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

ISTIC = "1466806132998672466"
VEILLE = "1475846763909873727"

# IDs ISTIC déjà connus
ISTIC_PERSONNEL_CAT_ID = "1497497238350463018"

# Plan de migration ----------------------------------------------------------
ARSENAL_POLITIQUE_CAT_NAME = "🎯 ARSENAL POLITIQUE"
ARSENAL_POLITIQUE_SALONS = [
    # (name, topic — vu sur le serveur Veille via veille_rss_politique.py)
    ("🔥・actu-chaude",            "Actualité politique chaude — actu24h"),
    ("💰・arsenal-eco",            "Arsenal éco — chiffres & arguments économiques"),
    ("🌱・arsenal-ecologie",       "Arsenal écologie — données climat & écologie"),
    ("🌍・arsenal-international",  "Arsenal international — Palestine, Russie, Sahel…"),
    ("✊・arsenal-social",         "Arsenal social — luttes, syndicats, mobilisations"),
    ("🎯・arsenal-attaques",       "Arsenal attaques — répliques pro-LFI"),
    ("📺・arsenal-medias",         "Arsenal médias — fact-check, critique du récit"),
]

ARSENAL_FORUMS_CAT_NAME = "📂 ARSENAL"
# Forums à recréer côté ISTIC : on copie les noms du serveur Veille (catégorie ARSENAL)
ARSENAL_FORUMS_SOURCE_CAT_ID = "1493709215494181047"  # côté Veille

ARSENAL_LIENS_CHANNEL_NAME = "🔗・liens-arsenal"

# ---------------------------------------------------------------------------

API = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "BotGSTAR-Migration/1.0",
}


def http(method: str, path: str, body: dict | None = None, retries: int = 3) -> dict:
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(f"{API}/{path}", data=data, headers=HEADERS, method=method)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                try:
                    j = json.loads(body_err)
                    wait = float(j.get("retry_after", 2))
                except Exception:
                    wait = 2
                print(f"    [429] retry dans {wait}s")
                time.sleep(wait)
                continue
            print(f"    [ERR HTTP {e.code}] {body_err[:300]}")
            raise
    raise RuntimeError(f"Failed {method} {path} after {retries} attempts")


def find_category(chans: list, name_substr: str) -> dict | None:
    for c in chans:
        if c["type"] == 4 and name_substr.upper() in c["name"].upper():
            return c
    return None


def find_channel_by_name(chans: list, name: str, parent_id: str | None = None,
                         expected_type: int | None = None) -> dict | None:
    for c in chans:
        if c.get("name") != name:
            continue
        if expected_type is not None and c["type"] != expected_type:
            continue
        if parent_id is not None and c.get("parent_id") != parent_id:
            continue
        return c
    return None


def phase1_arsenal_politique(istic_chans: list, apply: bool) -> dict[str, str]:
    """Crée 🎯 ARSENAL POLITIQUE + 7 salons. Retourne mapping name → id."""
    print(f"\n══ Phase 1 : {ARSENAL_POLITIQUE_CAT_NAME}")
    cat = find_category(istic_chans, "ARSENAL POLITIQUE")
    if cat:
        print(f"  ✅ Catégorie existe déjà : {cat['name']} ({cat['id']})")
    else:
        print(f"  → Création catégorie : {ARSENAL_POLITIQUE_CAT_NAME}")
        if apply:
            cat = http("POST", f"guilds/{ISTIC}/channels", {
                "name": ARSENAL_POLITIQUE_CAT_NAME, "type": 4,
            })
            print(f"    ✅ {cat['id']}")
        else:
            cat = {"id": "<dry-run>", "name": ARSENAL_POLITIQUE_CAT_NAME}

    cat_id = cat["id"]
    out = {ARSENAL_POLITIQUE_CAT_NAME: cat_id}

    for name, topic in ARSENAL_POLITIQUE_SALONS:
        existing = find_channel_by_name(istic_chans, name, parent_id=cat_id, expected_type=0)
        if existing:
            print(f"  ✅ Salon existe : #{name} ({existing['id']})")
            out[name] = existing["id"]
            continue
        print(f"  → Création salon : #{name}")
        if apply:
            new_ch = http("POST", f"guilds/{ISTIC}/channels", {
                "name": name, "type": 0, "parent_id": cat_id, "topic": topic,
            })
            print(f"    ✅ {new_ch['id']}")
            out[name] = new_ch["id"]
            time.sleep(0.4)
        else:
            out[name] = "<dry-run>"
    return out


def phase2_arsenal_forums(istic_chans: list, veille_chans: list, apply: bool) -> dict[str, str]:
    """Crée 📂 ARSENAL + recopie les 6 forums (avec leurs tags) du serveur Veille."""
    print(f"\n══ Phase 2 : {ARSENAL_FORUMS_CAT_NAME}")
    cat = find_category(istic_chans, "📂 ARSENAL")
    if not cat:
        cat = find_channel_by_name(istic_chans, ARSENAL_FORUMS_CAT_NAME, expected_type=4)
    if cat:
        print(f"  ✅ Catégorie existe : {cat['name']} ({cat['id']})")
    else:
        print(f"  → Création catégorie : {ARSENAL_FORUMS_CAT_NAME}")
        if apply:
            cat = http("POST", f"guilds/{ISTIC}/channels", {
                "name": ARSENAL_FORUMS_CAT_NAME, "type": 4,
            })
            print(f"    ✅ {cat['id']}")
        else:
            cat = {"id": "<dry-run>", "name": ARSENAL_FORUMS_CAT_NAME}

    cat_id = cat["id"]
    out = {ARSENAL_FORUMS_CAT_NAME: cat_id}

    # Liste des forums sources côté Veille
    src_forums = [c for c in veille_chans
                  if c["type"] == 15 and c.get("parent_id") == ARSENAL_FORUMS_SOURCE_CAT_ID]
    print(f"  Source : {len(src_forums)} forums dans Veille/ARSENAL")

    for src in src_forums:
        name = src["name"]
        existing = find_channel_by_name(istic_chans, name, parent_id=cat_id, expected_type=15)
        if existing:
            print(f"  ✅ Forum existe : #{name} ({existing['id']})")
            out[name] = existing["id"]
            continue
        # Recopier les tags : {name, emoji_name, moderated}
        tags = []
        for t in src.get("available_tags", []):
            tag = {"name": t["name"], "moderated": t.get("moderated", False)}
            if t.get("emoji_name"):
                tag["emoji_name"] = t["emoji_name"]
            if t.get("emoji_id"):
                tag["emoji_id"] = t["emoji_id"]
            tags.append(tag)
        print(f"  → Création forum : #{name}  ({len(tags)} tags)")
        if apply:
            payload = {
                "name": name, "type": 15, "parent_id": cat_id,
                "topic": src.get("topic") or "",
                "available_tags": tags,
            }
            new_ch = http("POST", f"guilds/{ISTIC}/channels", payload)
            print(f"    ✅ {new_ch['id']}")
            out[name] = new_ch["id"]
            time.sleep(0.4)
        else:
            out[name] = "<dry-run>"
    return out


def phase3_liens_in_personnel(istic_chans: list, apply: bool) -> dict[str, str]:
    """Crée 🔗・liens-arsenal dans la catégorie 🔒 PERSONNEL existante."""
    print(f"\n══ Phase 3 : #{ARSENAL_LIENS_CHANNEL_NAME} dans 🔒 PERSONNEL")
    existing = find_channel_by_name(istic_chans, ARSENAL_LIENS_CHANNEL_NAME,
                                     parent_id=ISTIC_PERSONNEL_CAT_ID, expected_type=0)
    if existing:
        print(f"  ✅ Salon existe : #{ARSENAL_LIENS_CHANNEL_NAME} ({existing['id']})")
        return {ARSENAL_LIENS_CHANNEL_NAME: existing["id"]}
    print(f"  → Création salon : #{ARSENAL_LIENS_CHANNEL_NAME}")
    if apply:
        new_ch = http("POST", f"guilds/{ISTIC}/channels", {
            "name": ARSENAL_LIENS_CHANNEL_NAME,
            "type": 0,
            "parent_id": ISTIC_PERSONNEL_CAT_ID,
            "topic": "Drop des URLs (TikTok/IG/YT/X/Reddit/Threads) → pipeline Arsenal automatique",
        })
        print(f"    ✅ {new_ch['id']}")
        return {ARSENAL_LIENS_CHANNEL_NAME: new_ch["id"]}
    return {ARSENAL_LIENS_CHANNEL_NAME: "<dry-run>"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Exécute les créations Discord (sans : dry-run)")
    args = parser.parse_args()

    print(f"Mode : {'APPLY' if args.apply else 'DRY-RUN'}")
    print("Récupération de la structure Discord…")
    istic_chans = http("GET", f"guilds/{ISTIC}/channels")
    veille_chans = http("GET", f"guilds/{VEILLE}/channels")
    print(f"  ISTIC : {len(istic_chans)} channels")
    print(f"  Veille: {len(veille_chans)} channels")

    map1 = phase1_arsenal_politique(istic_chans, args.apply)
    map2 = phase2_arsenal_forums(istic_chans, veille_chans, args.apply)
    map3 = phase3_liens_in_personnel(istic_chans, args.apply)

    print("\n══ Mapping résultant ══")
    print(json.dumps({**map1, **map2, **map3}, indent=2, ensure_ascii=False))
    print("\nTerminé." if args.apply else "\nDry-run terminé. Relancer avec --apply pour exécuter.")
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
