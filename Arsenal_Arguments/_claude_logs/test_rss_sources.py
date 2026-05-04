"""Test santé de toutes les sources RSS définies dans rss_sources_politique.yaml.

Lit le YAML de référence (toujours en sync), teste chaque source active,
et reporte OK/FAIL + nombre d'articles. Recommande la désactivation des
sources qui ne répondent plus.

Usage :
  python _claude_logs/test_rss_sources.py
  python _claude_logs/test_rss_sources.py --include-inactive
"""
from __future__ import annotations

import argparse
import io
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import feedparser
import requests
import yaml

UA = "BotGSTAR-VeillePolitique-HealthCheck/1.0"
TIMEOUT = 12

# Lit le YAML de référence (root BotGSTAR/datas/)
SOURCES_YAML = Path(__file__).resolve().parents[2] / "datas" / "rss_sources_politique.yaml"


def load_sources(include_inactive: bool = False) -> list[dict]:
    if not SOURCES_YAML.exists():
        print(f"[ERR] YAML introuvable : {SOURCES_YAML}", file=sys.stderr)
        sys.exit(1)
    with SOURCES_YAML.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        print("[ERR] YAML doit contenir une liste à la racine", file=sys.stderr)
        sys.exit(1)
    if include_inactive:
        return raw
    return [s for s in raw if s.get("active", True)]


def test_one(source: dict) -> dict:
    out = {
        "id": source["id"],
        "category": source.get("category", "?"),
        "priority": source.get("priority", "?"),
        "url": source["url"],
        "ok": False,
        "n_entries": 0,
        "title": "",
        "error": "",
    }
    try:
        r = requests.get(
            source["url"],
            timeout=TIMEOUT,
            headers={"User-Agent": UA},
            allow_redirects=True,
        )
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}"
            return out
        feed = feedparser.parse(r.content)
        if feed.bozo and not feed.entries:
            out["error"] = f"bozo: {str(feed.bozo_exception)[:100]}"
            return out
        if not feed.entries:
            out["error"] = "0 articles (flux vide)"
            return out
        out["ok"] = True
        out["n_entries"] = len(feed.entries)
        out["title"] = (feed.feed.get("title") or "")[:60]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Test santé des sources RSS politique")
    ap.add_argument("--include-inactive", action="store_true",
                    help="Tester aussi les sources marquées active: false")
    args = ap.parse_args()

    sources = load_sources(include_inactive=args.include_inactive)
    if not sources:
        print("Aucune source à tester.")
        return 0

    print(f"Test de {len(sources)} sources depuis {SOURCES_YAML.name}...")
    print("=" * 88)

    by_category: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(test_one, s): s for s in sources}
        for fut in as_completed(futures):
            r = fut.result()
            by_category.setdefault(r["category"], []).append(r)

    # Affichage par catégorie (ordre défini, sinon alphabétique)
    CATEGORY_ORDER = [
        "actu-chaude", "arsenal-eco", "arsenal-ecologie", "arsenal-international",
        "arsenal-social", "arsenal-attaques", "arsenal-medias",
    ]
    cats = [c for c in CATEGORY_ORDER if c in by_category]
    cats.extend(sorted(c for c in by_category if c not in CATEGORY_ORDER))

    total_ok = total_fail = 0
    for cat in cats:
        items = sorted(by_category[cat], key=lambda x: (not x["ok"], x["priority"], x["id"]))
        print(f"\n=== {cat.upper()} ({len(items)} sources) ===")
        for r in items:
            tag = "OK  " if r["ok"] else "FAIL"
            n = f"({r['n_entries']:>4} articles)" if r["ok"] else ""
            err_or_title = r["title"] if r["ok"] else r["error"]
            print(f"  [{tag}] prio={r['priority']} {r['id']:<28} {n:<18} {err_or_title[:60]}")
            if r["ok"]:
                total_ok += 1
            else:
                total_fail += 1

    print("\n" + "=" * 88)
    print(f"RÉSUMÉ : {total_ok} OK · {total_fail} FAIL · sur {total_ok + total_fail} sources testées")
    print()
    print("Cible : 5 sources OK par catégorie minimum.")
    for cat in cats:
        ok = sum(1 for r in by_category[cat] if r["ok"])
        target = 5
        status = "OK" if ok >= target else "ATTENTION"
        print(f"  {cat:<25} : {ok:>2} OK / {target} cible  [{status}]")

    if total_fail > 0:
        print()
        print("Sources en erreur :")
        for cat in cats:
            for r in by_category[cat]:
                if not r["ok"]:
                    print(f"  - {r['id']:<28} ({cat}) : {r['error']}")
        print()
        print("Pour désactiver une source défaillante :")
        print("  Discord : !veille_pol sources toggle <id>")
        print("  YAML manuel : passer `active: false` puis !veille_pol reload")

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
