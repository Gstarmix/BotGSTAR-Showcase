"""
Cog veille RSS pour BotGSTAR — Phases R-A + R-B.

Architecture :
- Sources définies dans datas/rss_sources.yaml (4 catégories)
- État runtime dans datas/rss_state.json (atomic write)
- Commande !veille fetch-now : cycle manuel rapide (mode 'manual', pas de récap logs)
- Commande !veille trigger-now : déclenche le cycle 'auto' (avec récap dans #logs)
- Commande !veille status : affiche l'état des sources
- Commande !veille reload : recharge la config sources
- Scheduler quotidien 8h00 Paris (R-B) avec auto-start au boot et catch-up
- Pas de scoring mots-clés (ajouté en R-D)

Décisions cadre : voir PROMPT_SYSTEME_VEILLE_RSS.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time as _time_mod
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
import feedparser
import yaml
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from discord.ext import commands, tasks

# ============================================================
# CONSTANTES
# ============================================================

ISTIC_GUILD_ID = 1466806132998672466
ADMIN_ROLE_ID = 1493905604241129592
LOG_CHANNEL_ID = 1493760267300110466

# IDs salons veille — À REMPLACER par les vrais IDs après création des
# salons côté Discord (cf. ÉTAPE 5 des consignes).
# Si un ID est à 0, le post dans cette catégorie sera skippé proprement.
VEILLE_CHANNELS: dict[str, int] = {
    "cyber": 1497581112224911462,  # 📰 cyber-veille
    "ia":    1497581185189150781,  # 🤖 ia-veille
    "dev":   1497581209927159949,  # 💻 dev-veille
    "tech":  1497581249521258737,  # 📱 tech-news
}

VALID_CATEGORIES = {"cyber", "ia", "dev", "tech"}
VALID_PRIORITIES = {1, 2, 3}
VALID_LANGUAGES = {"fr", "en"}

DIGEST_MAX_ARTICLES = 10

# Fenêtre de fraîcheur par catégorie (en heures).
# - tech : 24h (Numerama publie 5-10 articles/jour, fenêtre serrée OK)
# - cyber/ia/dev : 72h (sources spécialisées qui publient ~3-5 fois/semaine)
# Le filtre _filter_and_select utilise ces fenêtres pour rejeter les
# articles trop anciens. Le scoring (priorité + fraîcheur) trie ensuite.
DIGEST_WINDOW_HOURS_BY_CAT: dict[str, int] = {
    "cyber": 72,
    "ia":    72,
    "dev":   72,
    "tech":  24,
}
DIGEST_WINDOW_HOURS_DEFAULT = 24  # fallback si catégorie inconnue

SOURCE_ERROR_THRESHOLD = 5
HTTP_TIMEOUT_SECONDS = 15
PRUNE_DAYS = 30

# Scoring mots-clés (R-D)
KEYWORD_BOOST_POINTS = 500   # bonus par mot-clé boost matché

# Limites Discord pour les embeds (Style A — Magazine)
EMBED_DESCRIPTION_MAX = 4096
EMBED_FIELD_NAME_MAX = 256
EMBED_FIELD_VALUE_MAX = 1024
EMBED_TOTAL_MAX = 6000  # tous champs cumulés d'un embed
EMBED_FIELDS_MAX = 25   # plafond Discord par embed

# Style A : 1 article = 1 field, on plafonne à 5 articles/embed
ARTICLES_PER_EMBED = 5
DIGEST_MAX_EMBEDS_PER_CATEGORY = 2  # 5 + 5 = 10 articles max

MESSAGE_MAX_EMBEDS = 10  # plafond Discord par message

# URL d'une image transparente 730×1 pixels, hébergée publiquement.
# Discord force la largeur de l'embed à correspondre à celle de
# l'image, ce qui rend tous les embeds d'un même message uniformes.
# Cette image est invisible (transparente) mais a un effet structurel.
EMBED_SPACER_URL = "https://www.zupimages.net/up/26/17/j8a7.png"

# Locale FR pour les dates
MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
JOURS_FR = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]

PARIS_TZ = ZoneInfo("Europe/Paris")

# Heure du digest auto (Paris)
DIGEST_HOUR = 8
DIGEST_MINUTE = 0

# Skip d'une catégorie si aucun article (silencieux dans le salon,
# loggué dans #logs)
SKIP_EMPTY_CATEGORIES = True

USER_AGENT = "BotGSTAR-VeilleRSS/1.0 (+gaylordaboeka@gmail.com)"

# Chemins (relatifs au workspace BotGSTAR/)
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"
SOURCES_YAML = DATAS_DIR / "rss_sources.yaml"
KEYWORDS_YAML = DATAS_DIR / "rss_keywords.yaml"
STATE_JSON = DATAS_DIR / "rss_state.json"

# Couleurs embed par priorité (utilisées dans le digest)
PRIORITY_EMOJI = {1: "🔴", 2: "🟠", 3: "🟡"}

# Couleurs embed par catégorie
CATEGORY_COLORS = {
    "cyber": 0xC0392B,  # rouge
    "ia":    0x8E44AD,  # violet
    "dev":   0x27AE60,  # vert
    "tech":  0x2980B9,  # bleu
}

CATEGORY_TITLES = {
    "cyber": "📰 Veille cybersécurité",
    "ia":    "🤖 Veille IA",
    "dev":   "💻 Veille dev",
    "tech":  "📱 Tech news",
}

# Emoji utilisé devant le source-id dans chaque carte article.
# Correspond à l'emoji du salon Discord pour cohérence visuelle.
CATEGORY_SOURCE_EMOJI = {
    "cyber": "📰",
    "ia":    "🤖",
    "dev":   "💻",
    "tech":  "📱",
}

logger = logging.getLogger("bot.veille_rss")


# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class Source:
    id: str
    url: str
    category: str
    language: str
    priority: int
    active: bool
    notes: str = ""


@dataclass
class Article:
    guid_hash: str           # MD5 du guid (ou de l'URL si pas de guid)
    source_id: str
    title: str
    url: str
    category: str
    priority: int
    published_at: datetime   # timezone-aware UTC
    summary: str = ""
    keyword_boost: int = 0   # bonus de score depuis matching mots-clés (R-D)

    @property
    def score(self) -> float:
        """Score : priorité source + fraîcheur + bonus mots-clés (R-D)."""
        # Priorité 1 → 3000, prio 2 → 2000, prio 3 → 1000
        prio_score = (4 - self.priority) * 1000
        # Plus l'article est récent, plus son bonus est élevé.
        age_minutes = max(
            0,
            (datetime.now(timezone.utc) - self.published_at).total_seconds() / 60,
        )
        freshness = max(0, 1000 - age_minutes)  # bonus dégressif sur 1000 min
        return prio_score + freshness + self.keyword_boost


# ============================================================
# I/O ATOMIQUE
# ============================================================

def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Écriture atomique : .tmp puis os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_state() -> dict[str, Any]:
    if not STATE_JSON.exists():
        return {
            "schema_version": 1,
            "published": {},
            "fetch_state": {},
            "last_digest_at": None,
        }
    with STATE_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(state: dict[str, Any]) -> None:
    _atomic_write_json(STATE_JSON, state)


def _make_yaml() -> YAML:
    """Factory ruamel.yaml configurée pour le format BotGSTAR."""
    yaml_inst = YAML()
    yaml_inst.preserve_quotes = True
    yaml_inst.indent(mapping=2, sequence=2, offset=0)
    yaml_inst.width = 200  # ne pas wrapper les URLs longues
    return yaml_inst


def _load_sources_raw() -> Any:
    """
    Charge le YAML brut (CommentedSeq de CommentedMaps) en préservant
    structure + commentaires. Utilisé par les commandes d'écriture.
    """
    if not SOURCES_YAML.exists():
        raise FileNotFoundError(f"Fichier sources introuvable : {SOURCES_YAML}")
    yaml_inst = _make_yaml()
    with SOURCES_YAML.open("r", encoding="utf-8") as f:
        return yaml_inst.load(f)


def _save_sources_raw(data: Any) -> None:
    """
    Écriture atomique du YAML avec préservation comments/ordre via ruamel.
    """
    yaml_inst = _make_yaml()
    SOURCES_YAML.parent.mkdir(parents=True, exist_ok=True)
    tmp = SOURCES_YAML.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml_inst.dump(data, f)
    os.replace(tmp, SOURCES_YAML)


def _find_source_index(raw: Any, source_id: str) -> int:
    """Retourne l'index dans le YAML brut, ou -1 si absent."""
    for idx, item in enumerate(raw):
        if hasattr(item, "get") and item.get("id") == source_id:
            return idx
    return -1


def _is_valid_url(url: str) -> bool:
    """Validation basique : http/https + netloc non vide."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _validate_source_id(source_id: str) -> str | None:
    """
    Vérifie qu'un id est syntaxiquement valide.
    Retourne None si OK, sinon un message d'erreur.
    """
    if not source_id:
        return "id ne peut pas être vide"
    if len(source_id) > 50:
        return "id trop long (max 50 caractères)"
    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-_]*", source_id):
        return (
            "id doit contenir uniquement minuscules, chiffres, tirets, "
            "underscores ; commencer par lettre ou chiffre"
        )
    return None


def _load_keywords() -> dict[str, dict[str, list[str]]]:
    """
    Charge datas/rss_keywords.yaml.
    Retourne un dict de la forme :
    {
        "all":   {"boost": [...], "blacklist": [...]},
        "cyber": {"boost": [...], "blacklist": [...]},
        "ia":    {"boost": [...], "blacklist": [...]},
        "dev":   {"boost": [...], "blacklist": [...]},
        "tech":  {"boost": [...], "blacklist": [...]},
    }
    Si le fichier n'existe pas, retourne une structure vide cohérente
    (système R-A continue de marcher sans mots-clés).
    """
    if not KEYWORDS_YAML.exists():
        logger.info("rss_keywords.yaml absent, scoring mots-clés désactivé")
        return {cat: {"boost": [], "blacklist": []} for cat in (*VALID_CATEGORIES, "all")}

    yaml_inst = _make_yaml()
    with KEYWORDS_YAML.open("r", encoding="utf-8") as f:
        raw = yaml_inst.load(f) or {}

    result: dict[str, dict[str, list[str]]] = {}
    for cat in (*VALID_CATEGORIES, "all"):
        cat_data = raw.get(cat, {}) or {}
        result[cat] = {
            "boost": [str(k).strip() for k in (cat_data.get("boost") or []) if str(k).strip()],
            "blacklist": [str(k).strip() for k in (cat_data.get("blacklist") or []) if str(k).strip()],
        }
    return result


def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    """
    Retourne la liste des mots-clés qui matchent dans le texte.
    Matching insensible à la casse, sur substring exact.
    """
    if not text or not keywords:
        return []
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def _apply_keyword_scoring(
    article: Article,
    keywords: dict[str, dict[str, list[str]]],
) -> tuple[bool, list[str], list[str]]:
    """
    Applique le scoring mots-clés à un article.

    Modifie article.keyword_boost en place.
    Retourne (kept, boost_matches, blacklist_matches).

    - kept = False si l'article matche un mot-clé blacklist (à filtrer)
    - boost_matches = liste des mots-clés boost matchés
    - blacklist_matches = liste des mots-clés blacklist matchés
    """
    text = f"{article.title} {article.summary}"

    cat_kw = keywords.get(article.category, {"boost": [], "blacklist": []})
    all_kw = keywords.get("all", {"boost": [], "blacklist": []})

    # Blacklist d'abord (court-circuit si match)
    blacklist_words = (cat_kw.get("blacklist", []) or []) + (all_kw.get("blacklist", []) or [])
    blacklist_matches = _match_keywords(text, blacklist_words)
    if blacklist_matches:
        return False, [], blacklist_matches

    # Boost
    boost_words = (cat_kw.get("boost", []) or []) + (all_kw.get("boost", []) or [])
    boost_matches = _match_keywords(text, boost_words)
    article.keyword_boost = len(boost_matches) * KEYWORD_BOOST_POINTS

    return True, boost_matches, []


def _load_sources() -> list[Source]:
    """
    Charge et valide rss_sources.yaml. Erreur stricte si invalide.
    Convertit les CommentedMaps de ruamel en dataclasses Source.
    """
    raw = _load_sources_raw()
    if raw is None:
        raise ValueError("rss_sources.yaml est vide")
    if not isinstance(raw, list):
        raise ValueError("rss_sources.yaml doit contenir une liste à la racine")

    seen_ids: set[str] = set()
    sources: list[Source] = []

    for idx, item in enumerate(raw):
        if not hasattr(item, "get"):
            raise ValueError(f"Source #{idx} n'est pas un objet")

        try:
            sid = item["id"]
            url = item["url"]
            category = item["category"]
            language = item["language"]
            priority = item["priority"]
            active = item["active"]
        except KeyError as e:
            raise ValueError(f"Source #{idx} : champ manquant {e}") from e

        if sid in seen_ids:
            raise ValueError(f"Source id dupliqué : {sid!r}")
        seen_ids.add(sid)

        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"Source {sid!r} : category {category!r} invalide "
                f"(attendu : {VALID_CATEGORIES})"
            )
        if priority not in VALID_PRIORITIES:
            raise ValueError(
                f"Source {sid!r} : priority {priority!r} invalide "
                f"(attendu : 1, 2 ou 3)"
            )
        if language not in VALID_LANGUAGES:
            raise ValueError(
                f"Source {sid!r} : language {language!r} invalide "
                f"(attendu : fr, en)"
            )
        if not isinstance(active, bool):
            raise ValueError(f"Source {sid!r} : active doit être booléen")

        sources.append(Source(
            id=str(sid), url=str(url), category=str(category),
            language=str(language), priority=int(priority), active=bool(active),
            notes=str(item.get("notes", "")),
        ))

    return sources


# ============================================================
# FETCH RSS
# ============================================================

def _hash_guid(guid: str) -> str:
    return hashlib.md5(guid.encode("utf-8")).hexdigest()


def _entry_to_datetime(entry: Any) -> datetime:
    """Extrait la date de publication d'une entry feedparser, en UTC."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        struct = getattr(entry, key, None) or entry.get(key) if hasattr(entry, "get") else None
        if struct:
            return datetime.fromtimestamp(_time_mod.mktime(struct), tz=timezone.utc)
    # Fallback : maintenant (sera scoré comme très récent, mais on a pas mieux)
    return datetime.now(timezone.utc)


async def _fetch_one_source(
    session: aiohttp.ClientSession,
    source: Source,
    fetch_state: dict[str, Any],
) -> tuple[list[Article], str | None]:
    """
    Fetch une source RSS. Retourne (articles, error_message).
    Met à jour fetch_state[source.id] (last_etag, last_modified, errors).
    """
    state = fetch_state.setdefault(source.id, {
        "last_fetched_at": None,
        "last_etag": None,
        "last_modified": None,
        "last_error": None,
        "consecutive_errors": 0,
    })

    headers = {"User-Agent": USER_AGENT}
    if state.get("last_etag"):
        headers["If-None-Match"] = state["last_etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]

    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        async with session.get(source.url, headers=headers, timeout=timeout) as resp:
            if resp.status == 304:
                # Pas de changement
                state["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
                state["last_error"] = None
                state["consecutive_errors"] = 0
                return [], None

            if resp.status >= 400:
                err = f"HTTP {resp.status}"
                state["last_error"] = err
                state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
                return [], err

            content = await resp.read()
            state["last_etag"] = resp.headers.get("ETag")
            state["last_modified"] = resp.headers.get("Last-Modified")

    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        err = f"{type(e).__name__}: {e}"
        state["last_error"] = err
        state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
        return [], err

    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        err = f"feedparser bozo: {parsed.bozo_exception!r}"
        state["last_error"] = err
        state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
        return [], err

    articles: list[Article] = []
    for entry in parsed.entries:
        guid = (
            entry.get("id")
            or entry.get("guid")
            or entry.get("link")
            or entry.get("title", "")
        )
        if not guid:
            continue
        articles.append(Article(
            guid_hash=_hash_guid(guid),
            source_id=source.id,
            title=entry.get("title", "(sans titre)").strip(),
            url=entry.get("link", ""),
            category=source.category,
            priority=source.priority,
            published_at=_entry_to_datetime(entry),
            summary=entry.get("summary", "")[:300],
        ))

    state["last_fetched_at"] = datetime.now(timezone.utc).isoformat()
    state["last_error"] = None
    state["consecutive_errors"] = 0
    return articles, None


async def _fetch_all(sources: list[Source], state: dict[str, Any]) -> list[Article]:
    """Fetch toutes les sources actives en parallèle."""
    fetch_state = state.setdefault("fetch_state", {})
    active_sources = [s for s in sources if s.active]

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[
            _fetch_one_source(session, s, fetch_state) for s in active_sources
        ], return_exceptions=False)

    all_articles: list[Article] = []
    for (articles, _err) in results:
        all_articles.extend(articles)

    return all_articles


# ============================================================
# SÉLECTION + DÉDOUBLONNAGE
# ============================================================

def _prune_published(state: dict[str, Any]) -> None:
    """Supprime les entrées published de plus de PRUNE_DAYS jours."""
    published: dict[str, Any] = state.get("published", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    to_remove = []
    for h, meta in published.items():
        posted = meta.get("posted_at")
        if not posted:
            continue
        try:
            dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < cutoff:
            to_remove.append(h)
    for h in to_remove:
        del published[h]


def _filter_and_select(
    articles: list[Article],
    state: dict[str, Any],
) -> dict[str, list[Article]]:
    """
    Filtre les articles :
    - Pas déjà publiés (guid_hash absent de state["published"])
    - Publiés dans la fenêtre de fraîcheur de leur catégorie
    - Pas blacklistés par les mots-clés (R-D)
    Applique le boost mots-clés au score (R-D).
    Puis groupe par catégorie et trie par score décroissant, top N.
    """
    published = state.get("published", {})
    now = datetime.now(timezone.utc)
    keywords = _load_keywords()

    # Pré-calculer les cutoffs par catégorie
    cutoffs: dict[str, datetime] = {}
    for cat in VALID_CATEGORIES:
        hours = DIGEST_WINDOW_HOURS_BY_CAT.get(cat, DIGEST_WINDOW_HOURS_DEFAULT)
        cutoffs[cat] = now - timedelta(hours=hours)

    # Stats pour log
    stats_blacklisted = 0
    stats_boosted = 0

    by_category: dict[str, list[Article]] = {c: [] for c in VALID_CATEGORIES}
    for art in articles:
        if art.guid_hash in published:
            continue
        cutoff = cutoffs.get(art.category, now - timedelta(hours=DIGEST_WINDOW_HOURS_DEFAULT))
        if art.published_at < cutoff:
            continue

        # R-D : scoring mots-clés
        kept, boost_matches, blacklist_matches = _apply_keyword_scoring(art, keywords)
        if not kept:
            stats_blacklisted += 1
            logger.debug(
                "Article blacklisté: %s (mots: %s)",
                art.title[:60], blacklist_matches,
            )
            continue
        if boost_matches:
            stats_boosted += 1
            logger.debug(
                "Article boosté +%d: %s (mots: %s)",
                art.keyword_boost, art.title[:60], boost_matches,
            )

        by_category[art.category].append(art)

    if stats_blacklisted or stats_boosted:
        logger.info(
            "R-D scoring : %d articles boostés, %d articles blacklistés",
            stats_boosted, stats_blacklisted,
        )

    for cat in by_category:
        by_category[cat].sort(key=lambda a: a.score, reverse=True)
        by_category[cat] = by_category[cat][:DIGEST_MAX_ARTICLES]

    return by_category


# ============================================================
# DIGEST EMBED
# ============================================================

def _format_age(published_at: datetime) -> str:
    delta = datetime.now(timezone.utc) - published_at
    minutes = int(delta.total_seconds() / 60)
    if minutes < 60:
        return f"il y a {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"il y a {hours} h"
    days = hours // 24
    return f"il y a {days} j"


def _format_date_fr(dt: datetime) -> str:
    """Format français : 'samedi 25 avril 2026' (sans dépendance locale)."""
    jour = JOURS_FR[dt.weekday()]
    mois = MOIS_FR[dt.month - 1]
    return f"{jour} {dt.day} {mois} {dt.year}"


def _build_digest_embeds(category: str, articles: list[Article]) -> list[discord.Embed]:
    """
    Style A "Magazine" v3 (final) :
    - 1 article = 1 field, max 5 articles par embed
    - Titre uniquement sur le 1er embed (pas de "(1/2)" "(2/2)")
    - Image spacer 730×1 transparente en bas de CHAQUE embed
      (force Discord à uniformiser la largeur des embeds)
    - Footer + timestamp uniquement sur le dernier embed
    - Aération entre articles via zero-width space dans field.name
    """
    now_paris = datetime.now(PARIS_TZ)
    date_fr = _format_date_fr(now_paris)
    base_title = f"{CATEGORY_TITLES[category]} — {date_fr}"
    color = CATEGORY_COLORS[category]
    timestamp = datetime.now(timezone.utc)

    # Cas spécial : 0 article (mode manual uniquement)
    if not articles:
        embed = discord.Embed(
            title=base_title,
            description="_Aucun article dans la fenêtre de fraîcheur._",
            color=color,
            timestamp=timestamp,
        )
        embed.set_footer(text="0 article · 0 source")
        embed.set_image(url=EMBED_SPACER_URL)
        return [embed]

    # Compter ratio FR / EN pour le footer
    fr_count = sum(1 for a in articles if _detect_lang_for_article(a) == "fr")
    en_count = len(articles) - fr_count
    sources_count = len({a.source_id for a in articles})

    # Découpage en chunks de ARTICLES_PER_EMBED (atomique : jamais
    # de coupure au milieu d'un article)
    chunks: list[list[Article]] = []
    for i in range(0, len(articles), ARTICLES_PER_EMBED):
        chunks.append(articles[i : i + ARTICLES_PER_EMBED])

    # Cap dur sur le nombre d'embeds
    if len(chunks) > DIGEST_MAX_EMBEDS_PER_CATEGORY:
        chunks = chunks[:DIGEST_MAX_EMBEDS_PER_CATEGORY]

    embeds: list[discord.Embed] = []
    n_chunks = len(chunks)
    total_displayed = sum(len(c) for c in chunks)

    for idx, chunk_articles in enumerate(chunks, start=1):
        is_first = (idx == 1)
        is_last = (idx == n_chunks)

        # Titre UNIQUEMENT sur le 1er embed (pas de (1/2) (2/2))
        embed_kwargs = {"color": color}
        if is_first:
            embed_kwargs["title"] = base_title
        if is_last:
            embed_kwargs["timestamp"] = timestamp

        embed = discord.Embed(**embed_kwargs)

        # Ajouter chaque article en field
        for art in chunk_articles:
            field_name, field_value = _format_article_field(art)
            if len(field_name) > EMBED_FIELD_NAME_MAX:
                field_name = field_name[: EMBED_FIELD_NAME_MAX - 1] + "…"
            if len(field_value) > EMBED_FIELD_VALUE_MAX:
                field_value = field_value[: EMBED_FIELD_VALUE_MAX - 1] + "…"
            embed.add_field(name=field_name, value=field_value, inline=False)

        # Footer uniquement sur le dernier embed
        if is_last:
            footer_parts = [
                f"{total_displayed} article{'s' if total_displayed > 1 else ''}",
                f"{sources_count} source{'s' if sources_count > 1 else ''}",
            ]
            if fr_count and en_count:
                footer_parts.append(f"🌐 {fr_count} FR · {en_count} EN")
            elif fr_count:
                footer_parts.append(f"🌐 {fr_count} FR")
            elif en_count:
                footer_parts.append(f"🌐 {en_count} EN")
            embed.set_footer(text=" · ".join(footer_parts))

        # Image spacer sur CHAQUE embed → force largeur uniforme
        embed.set_image(url=EMBED_SPACER_URL)

        embeds.append(embed)

    return embeds


def _detect_lang_for_article(article: Article) -> str:
    """
    Retrouve la langue d'un article via sa source.
    Heuristique : utiliser la liste de sources chargée du YAML.
    Fallback 'en' si la source est introuvable.
    """
    try:
        sources = _load_sources()
        for s in sources:
            if s.id == article.source_id:
                return s.language
    except Exception:
        pass
    return "en"


def _format_article_field(article: Article) -> tuple[str, str]:
    """
    Construit le (name, value) d'un field Discord pour un article.

    name  : '​' (zero-width space) → aération entre articles
    value : "🟠 [**Titre cliquable**](url)
             📰/🤖/💻/📱 `source-id` · 🇫🇷 · _il y a 2 h_"

    L'emoji devant le source-id correspond à la catégorie de
    l'article (cohérence avec l'emoji du salon Discord).
    """
    prio_emoji = PRIORITY_EMOJI.get(article.priority, "⚪")

    title = article.title.strip()
    # Limite douce sur le titre. Le value Discord max est 1024,
    # on garde de la marge pour l'URL et la ligne méta.
    if len(title) > 200:
        title = title[:197] + "…"

    # Récupérer le drapeau langue depuis la source
    lang = _detect_lang_for_article(article)
    flag = "🇫🇷" if lang == "fr" else "🇬🇧"

    age = _format_age(article.published_at)

    # Emoji source dérivé de la catégorie (cohérence avec le salon)
    source_emoji = CATEGORY_SOURCE_EMOJI.get(article.category, "📰")

    value = (
        f"{prio_emoji} [**{title}**]({article.url})\n"
        f"{source_emoji} `{article.source_id}` · {flag} · _{age}_"
    )

    # name : zero-width space pour aérer entre articles sans header
    name = "​"

    return name, value


# ============================================================
# COG
# ============================================================

class VeilleRSS(commands.Cog):
    """Veille RSS — Phase R-A (MVP)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._sources: list[Source] = []
        self._reload_sources()

    def cog_unload(self):
        """Arrête proprement le loop si le Cog est déchargé (reload, shutdown)."""
        if self._daily_digest_loop.is_running():
            self._daily_digest_loop.cancel()
            logger.info("Loop digest quotidien arrêté (cog_unload)")

    def _reload_sources(self) -> None:
        self._sources = _load_sources()
        logger.info(
            "VeilleRSS : %d sources chargées (%d actives)",
            len(self._sources),
            sum(1 for s in self._sources if s.active),
        )

    # --- Admin only ---
    async def cog_check(self, ctx: commands.Context) -> bool:  # type: ignore[override]
        if not ctx.guild or ctx.guild.id != ISTIC_GUILD_ID:
            return False
        if not isinstance(ctx.author, discord.Member):
            return False
        return any(role.id == ADMIN_ROLE_ID for role in ctx.author.roles)

    # --- Helpers internes ---
    async def _log_to_channel(
        self,
        message: str,
        *,
        title: str | None = None,
        color: int | None = None,
        fields: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        """
        Poste un message dans #logs sous forme d'embed.

        `message` : description principale de l'embed (markdown OK)
        `title`   : titre de l'embed (par défaut "Veille RSS")
        `color`   : couleur custom (par défaut bleu Discord)
        `fields`  : liste de tuples (name, value, inline) — optionnel
        """
        guild = self.bot.get_guild(ISTIC_GUILD_ID)
        if not guild:
            return
        channel = guild.get_channel(LOG_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=title or "📡 Veille RSS",
            description=message[:4096] if message else None,
            color=color if color is not None else 0x5865F2,  # bleu Discord par défaut
            timestamp=datetime.now(timezone.utc),
        )
        if fields:
            for name, value, inline in fields:
                # Discord limite : name 256 chars, value 1024 chars
                embed.add_field(
                    name=name[:256],
                    value=value[:1024],
                    inline=inline,
                )
        embed.set_footer(text="veille_rss")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            logger.exception("Échec envoi log embed veille_rss")

    async def _run_fetch_cycle(self) -> dict[str, list[Article]]:
        """Cycle complet : fetch → filtre → dédup. Retourne articles par catégorie."""
        state = _load_state()
        _prune_published(state)

        articles = await _fetch_all(self._sources, state)
        by_category = _filter_and_select(articles, state)

        # Sauvegarde fetch_state même si pas de nouveaux articles
        _save_state(state)

        # Auto-désactivation des sources qui plantent en boucle
        await self._auto_disable_failing_sources(state)

        return by_category

    async def _auto_disable_failing_sources(self, state: dict[str, Any]) -> None:
        fetch_state = state.get("fetch_state", {})
        for sid, fs in fetch_state.items():
            if fs.get("consecutive_errors", 0) >= SOURCE_ERROR_THRESHOLD:
                source = next((s for s in self._sources if s.id == sid), None)
                if source and source.active:
                    source.active = False
                    await self._log_to_channel(
                        f"La source **`{sid}`** a été désactivée automatiquement après "
                        f"**{SOURCE_ERROR_THRESHOLD}** erreurs consécutives.",
                        title="⚠️ VeilleRSS — Source désactivée",
                        color=0xE67E22,
                        fields=[
                            ("Source", f"`{sid}`", True),
                            ("Erreurs", str(fs.get('consecutive_errors', '?')), True),
                            ("Dernière erreur", f"`{fs.get('last_error', '?')}`", False),
                        ],
                    )

    def _digest_already_today(self, state: dict[str, Any]) -> bool:
        """
        Retourne True si last_digest_at correspond à aujourd'hui (date Paris).
        Utilisé pour éviter de reposter un digest si le bot redémarre après 8h00.
        """
        last = state.get("last_digest_at")
        if not last:
            return False
        try:
            last_dt_utc = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            return False
        last_paris = last_dt_utc.astimezone(PARIS_TZ).date()
        today_paris = datetime.now(PARIS_TZ).date()
        return last_paris == today_paris

    async def _post_morning_summary(
        self,
        state: dict[str, Any],
        posted_counts: dict[str, int],
    ) -> None:
        """
        Poste un récap matinal dans #logs sous forme d'embed structuré.
        """
        fetch_state = state.get("fetch_state", {})
        failing_sources = [
            (sid, fs.get("consecutive_errors", 0), fs.get("last_error", "?"))
            for sid, fs in fetch_state.items()
            if fs.get("consecutive_errors", 0) > 0
        ]

        # Champ "Articles postés" en table compacte
        cat_emojis = {"cyber": "📰", "ia": "🤖", "dev": "💻", "tech": "📱"}
        counts_lines = []
        total_posted = 0
        for cat in ("cyber", "ia", "dev", "tech"):
            n = posted_counts.get(cat, 0)
            emoji = cat_emojis[cat]
            skipped = " _(vide)_" if n == 0 else ""
            counts_lines.append(f"{emoji} **{cat}** : `{n}`{skipped}")
            total_posted += n
        counts_field = "\n".join(counts_lines)

        # Champ "Sources" : statut global
        if failing_sources:
            sources_lines = ["⚠️ Sources en erreur :"]
            for sid, errors, last_err in failing_sources:
                short_err = (last_err or "?")[:80]
                sources_lines.append(f"• `{sid}` — {errors} err. — `{short_err}`")
            sources_field = "\n".join(sources_lines)
            color = 0xF39C12  # orange si erreurs
        else:
            sources_field = "✅ Toutes les sources fonctionnent."
            color = 0x2ECC71  # vert si OK

        # Champ "Stats globales"
        total_published = len(state.get("published", {}))
        active_sources = sum(1 for s in self._sources if s.active)
        stats_field = (
            f"Articles trackés (30j) : `{total_published}`\n"
            f"Sources actives : `{active_sources}` / `{len(self._sources)}`"
        )

        await self._log_to_channel(
            f"Récapitulatif du cycle automatique. "
            f"**{total_posted}** article{'s' if total_posted > 1 else ''} "
            f"posté{'s' if total_posted > 1 else ''} au total.",
            title="📡 VeilleRSS — Digest matinal",
            color=color,
            fields=[
                ("Articles par catégorie", counts_field, False),
                ("Sources", sources_field, False),
                ("Stats globales", stats_field, False),
            ],
        )

    async def _run_daily_cycle(self, source: str) -> dict[str, int]:
        """
        Cycle complet : fetch → filtre → post → récap.
        `source` : 'auto' (loop quotidien) ou 'manual' (fetch-now).
        Retourne posted_counts par catégorie.
        """
        state = _load_state()

        # Garde anti-doublon : ne pas reposter un digest auto si déjà fait aujourd'hui
        if source == "auto" and self._digest_already_today(state):
            logger.info("Digest auto skip : déjà posté aujourd'hui (Paris)")
            await self._log_to_channel(
                "Le digest a déjà été posté aujourd'hui. Aucune action.",
                title="ℹ️ VeilleRSS — Digest skip",
                color=0x95A5A6,  # gris
            )
            return {}

        by_category = await self._run_fetch_cycle()
        state = _load_state()  # _run_fetch_cycle a sauvegardé fetch_state
        posted_counts = await self._post_digests(by_category, state)

        # Récap dans #logs UNIQUEMENT en mode auto (manuel = retour
        # direct dans le canal d'invocation, pas besoin de doublonner)
        if source == "auto":
            await self._post_morning_summary(state, posted_counts)

        return posted_counts

    async def _test_source_url(
        self, url: str, timeout_sec: int = HTTP_TIMEOUT_SECONDS,
    ) -> tuple[bool, str, int]:
        """
        Teste une URL RSS sans la persister.
        Retourne (ok, message, articles_count).
        """
        if not _is_valid_url(url):
            return False, "URL invalide (http/https requis)", 0

        fake_source = Source(
            id="__test__", url=url, category="cyber",
            language="fr", priority=1, active=True,
        )
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_sec)
            connector = aiohttp.TCPConnector(limit=1)
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector,
            ) as session:
                articles, err = await _fetch_one_source(session, fake_source, {})
        except Exception as e:
            return False, f"Erreur réseau : {type(e).__name__}: {e}", 0

        if err:
            return False, f"Erreur fetch : {err}", 0

        if not articles:
            return False, "Flux récupéré mais 0 article (flux vide ?)", 0

        return True, f"OK — {len(articles)} articles récupérés", len(articles)

    async def _post_digests(
        self,
        by_category: dict[str, list[Article]],
        state: dict[str, Any],
    ) -> dict[str, int]:
        """Poste un digest par catégorie. Retourne le compte d'articles postés / cat."""
        guild = self.bot.get_guild(ISTIC_GUILD_ID)
        if not guild:
            await self._log_to_channel("❌ Guild ISTIC introuvable, abandon digest")
            return {}

        posted_counts: dict[str, int] = {}
        published = state.setdefault("published", {})
        now_iso = datetime.now(timezone.utc).isoformat()

        for category, articles in by_category.items():
            channel_id = VEILLE_CHANNELS.get(category, 0)
            if channel_id == 0:
                logger.warning("Salon %s non configuré (ID=0), skip", category)
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                logger.warning("Salon ID %s introuvable pour %s", channel_id, category)
                continue

            # Skip silencieux des catégories vides en mode auto
            # (le digest manuel via fetch-now poste quand même un embed vide
            # pour confirmer que le cycle a tourné — distingué via paramètre)
            if SKIP_EMPTY_CATEGORIES and not articles:
                logger.info("Catégorie %s vide, skip post", category)
                posted_counts[category] = 0
                continue

            embeds = _build_digest_embeds(category, articles)

            first_msg_id: str | None = None
            send_failed = False

            # Discord accepte jusqu'à 10 embeds par message.
            # Avec DIGEST_MAX_EMBEDS_PER_CATEGORY=2, on est large.
            # Garde-fou : si jamais on dépasse, on chunk les embeds.
            for batch_start in range(0, len(embeds), MESSAGE_MAX_EMBEDS):
                batch = embeds[batch_start : batch_start + MESSAGE_MAX_EMBEDS]
                try:
                    msg = await channel.send(embeds=batch)
                    if first_msg_id is None:
                        first_msg_id = str(msg.id)
                except discord.HTTPException:
                    logger.exception("Échec envoi digest %s", category)
                    send_failed = True
                    break

            if send_failed:
                continue

            for art in articles:
                published[art.guid_hash] = {
                    "source_id": art.source_id,
                    "title": art.title,
                    "url": art.url,
                    "category": art.category,
                    "published_at": art.published_at.isoformat(),
                    "posted_at": now_iso,
                    "message_id": first_msg_id or "?",
                }
            posted_counts[category] = len(articles)

        state["last_digest_at"] = now_iso
        _save_state(state)
        return posted_counts

    # ============================================================
    # SCHEDULER QUOTIDIEN
    # ============================================================

    @tasks.loop(time=time(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, tzinfo=PARIS_TZ))
    async def _daily_digest_loop(self):
        """Loop quotidien à 8h00 Paris. Lance le cycle complet."""
        logger.info("Loop digest quotidien déclenché à %s", datetime.now(PARIS_TZ))

        if self._lock.locked():
            logger.warning("Digest auto skip : un cycle manuel est en cours")
            await self._log_to_channel(
                "Le digest auto a été skippé car un cycle manuel était en cours "
                "au moment du déclenchement.",
                title="⚠️ VeilleRSS — Conflit cycle",
                color=0xE67E22,  # orange foncé
            )
            return

        async with self._lock:
            try:
                await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("Erreur cycle digest auto")
                await self._log_to_channel(
                    f"Une erreur est survenue lors du cycle automatique :\n"
                    f"```\n{type(e).__name__}: {e}\n```",
                    title="❌ VeilleRSS — Erreur digest auto",
                    color=0xE74C3C,  # rouge
                )

    @_daily_digest_loop.before_loop
    async def _before_daily_loop(self):
        """Attend que le bot soit prêt avant de démarrer le loop."""
        await self.bot.wait_until_ready()
        logger.info(
            "Loop digest quotidien prêt à tourner (heure cible : %02d:%02d Paris)",
            DIGEST_HOUR, DIGEST_MINUTE,
        )

    @commands.Cog.listener()
    async def on_ready(self):
        """
        Démarre le loop quotidien au boot.
        Garde idempotente : si on_ready est rappelé après reconnect Discord,
        le loop n'est pas relancé.

        Catch-up : si le bot démarre après 8h00 Paris ET qu'aucun digest
        n'a encore été posté aujourd'hui, déclenche un cycle 'auto' immédiat.
        """
        # 1. Démarrage idempotent du loop
        if not self._daily_digest_loop.is_running():
            self._daily_digest_loop.start()
            logger.info("Loop digest quotidien démarré")
            await self._log_to_channel(
                f"Loops quotidiens (tech + politique) armés. Prochain déclenchement "
                f"automatique prévu à **{DIGEST_HOUR:02d}h{DIGEST_MINUTE:02d}** (Paris).",
                title="🟢 Veille — Démarrage",
                color=0x2ECC71,  # vert
            )
        else:
            logger.debug("Loop digest déjà en cours, on_ready idempotent")
            return  # déjà initialisé, pas de catch-up à refaire

        # 2. Catch-up : si on est après 8h00 Paris et que le digest du jour
        #    n'a pas encore été posté, déclencher un cycle auto immédiat.
        now_paris = datetime.now(PARIS_TZ)
        digest_time_today = now_paris.replace(
            hour=DIGEST_HOUR, minute=DIGEST_MINUTE,
            second=0, microsecond=0,
        )

        # On utilise <= pour éviter le catch-up à 08:00:00 pile,
        # qui se chevaucherait avec le déclenchement du loop quotidien.
        # Le @tasks.loop déclenche dans la même seconde, le _lock
        # protège déjà contre le double-post, mais autant éviter le
        # log "Catch-up déclenché" parasite.
        if now_paris <= digest_time_today:
            logger.info(
                "Catch-up skip : on est à ou avant %02d:%02d Paris "
                "(heure actuelle %s — le loop déclenchera)",
                DIGEST_HOUR, DIGEST_MINUTE, now_paris.strftime("%H:%M:%S"),
            )
            return

        state = _load_state()
        if self._digest_already_today(state):
            logger.info("Catch-up skip : digest déjà posté aujourd'hui")
            return

        logger.info(
            "Catch-up déclenché : démarrage à %s, digest 8h00 raté",
            now_paris.strftime("%H:%M"),
        )
        await self._log_to_channel(
            f"Le bot a démarré à **{now_paris.strftime('%H:%M')}** (Paris) après "
            f"l'heure du digest ({DIGEST_HOUR:02d}h{DIGEST_MINUTE:02d}). "
            f"Exécution immédiate du cycle pour ne pas rater la journée.",
            title="🔁 VeilleRSS — Catch-up digest",
            color=0xF39C12,  # orange
        )

        if self._lock.locked():
            logger.warning("Catch-up skip : un cycle est en cours")
            return

        async with self._lock:
            try:
                await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("Erreur catch-up digest")
                await self._log_to_channel(
                    f"Une erreur est survenue lors du catch-up :\n"
                    f"```\n{type(e).__name__}: {e}\n```",
                    title="❌ VeilleRSS — Erreur catch-up",
                    color=0xE74C3C,
                )

    # ============================================================
    # COMMANDES
    # ============================================================

    @commands.group(name="veille", invoke_without_command=True)
    async def veille_group(self, ctx: commands.Context):
        await ctx.send(
            "**Commandes `!veille` disponibles :**\n"
            "`!veille fetch-now` — cycle manuel (test rapide, pas de récap logs)\n"
            "`!veille trigger-now` — déclenche le cycle 'auto' (avec récap dans #logs)\n"
            "`!veille status` — état des sources et compteurs\n"
            "`!veille reload` — recharge `rss_sources.yaml` et `rss_keywords.yaml`\n"
            "`!veille sources …` — gestion des sources (list/add/remove/toggle/test)\n"
            "`!veille keywords` — affiche les mots-clés de scoring (boost + blacklist)\n"
            "\n"
            f"_Digest auto programmé à {DIGEST_HOUR:02d}h{DIGEST_MINUTE:02d} (Paris) chaque jour._"
        )

    @veille_group.command(name="fetch-now")
    async def fetch_now(self, ctx: commands.Context):
        if self._lock.locked():
            await ctx.send("⏳ Un cycle est déjà en cours, patiente.")
            return

        async with self._lock:
            await ctx.send("🔄 Cycle de fetch en cours…")
            try:
                posted = await self._run_daily_cycle(source="manual")
            except Exception as e:
                logger.exception("Erreur cycle fetch")
                await ctx.send(f"❌ Erreur : `{type(e).__name__}: {e}`")
                return

            summary = " · ".join(
                f"{cat}={n}" for cat, n in posted.items()
            ) or "rien posté"
            await ctx.send(f"✅ Cycle terminé. Posté : {summary}")

    @veille_group.command(name="status")
    async def status(self, ctx: commands.Context):
        state = _load_state()
        fetch_state = state.get("fetch_state", {})
        last_digest = state.get("last_digest_at") or "jamais"
        published_count = len(state.get("published", {}))

        lines = [
            f"**Veille RSS — État**",
            f"Dernier digest : `{last_digest}`",
            f"Articles trackés (30j) : `{published_count}`",
            f"Sources : `{sum(1 for s in self._sources if s.active)}` actives "
            f"/ `{len(self._sources)}` total",
            "",
            "**Détail par source :**",
        ]
        for s in self._sources:
            fs = fetch_state.get(s.id, {})
            errors = fs.get("consecutive_errors", 0)
            last_err = fs.get("last_error") or "OK"
            status_emoji = "✅" if s.active and errors == 0 else (
                "❌" if not s.active else "⚠️"
            )
            lines.append(
                f"{status_emoji} `{s.id}` ({s.category}, prio {s.priority}) — "
                f"erreurs : {errors} — {last_err}"
            )

        # Discord limite à 2000 chars / message
        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n…(tronqué)"
        await ctx.send(text)

    @veille_group.command(name="reload")
    async def reload(self, ctx: commands.Context):
        try:
            self._reload_sources()
            await ctx.send(
                f"✅ Rechargé : {len(self._sources)} sources "
                f"({sum(1 for s in self._sources if s.active)} actives)"
            )
        except Exception as e:
            await ctx.send(f"❌ Erreur de rechargement : `{type(e).__name__}: {e}`")

    @veille_group.command(name="trigger-now")
    async def trigger_now(self, ctx: commands.Context):
        """
        Déclenche manuellement le cycle 'auto' (avec récap dans #logs).
        Différent de fetch-now qui est en mode 'manual' (pas de récap logs).
        Utile pour tester le comportement R-B sans attendre 8h00.
        """
        if self._lock.locked():
            await ctx.send("⏳ Un cycle est déjà en cours, patiente.")
            return

        async with self._lock:
            await ctx.send("🔄 Déclenchement manuel du cycle auto (mode 'auto')…")
            try:
                posted = await self._run_daily_cycle(source="auto")
            except Exception as e:
                logger.exception("Erreur cycle auto manuel")
                await ctx.send(f"❌ Erreur : `{type(e).__name__}: {e}`")
                return

            summary = " · ".join(
                f"{cat}={n}" for cat, n in posted.items()
            ) or "rien posté (déjà fait aujourd'hui ou aucun nouveau)"
            await ctx.send(
                f"✅ Cycle auto terminé. Posté : {summary}\n"
                f"_(Vérifie #logs pour le récap matinal.)_"
            )

    # ============================================================
    # SOUS-GROUPE !veille sources
    # ============================================================

    @veille_group.group(name="sources", invoke_without_command=True)
    async def sources_group(self, ctx: commands.Context):
        """Commandes de gestion des sources RSS."""
        await ctx.send(
            "**Commandes `!veille sources` :**\n"
            "`!veille sources list` — liste toutes les sources\n"
            "`!veille sources add <id> <url> <cat> [prio]` — ajoute une source\n"
            "`!veille sources remove <id>` — retire une source\n"
            "`!veille sources toggle <id>` — active/désactive\n"
            "`!veille sources test <url>` — teste une URL sans l'ajouter\n"
            "\n"
            "_Catégories valides : cyber, ia, dev, tech_\n"
            "_Priorités valides : 1 (top), 2 (medium), 3 (low) — défaut 2_"
        )

    @sources_group.command(name="list")
    async def sources_list(self, ctx: commands.Context):
        """Liste toutes les sources sous forme d'embed groupé par catégorie."""
        by_cat: dict[str, list[Source]] = {c: [] for c in VALID_CATEGORIES}
        for s in self._sources:
            by_cat[s.category].append(s)

        cat_emojis = {"cyber": "📰", "ia": "🤖", "dev": "💻", "tech": "📱"}
        lines: list[str] = []
        for cat in ("cyber", "ia", "dev", "tech"):
            if not by_cat[cat]:
                continue
            lines.append(f"\n**{cat_emojis[cat]} {cat}**")
            for s in sorted(by_cat[cat], key=lambda x: (x.priority, x.id)):
                check = "✅" if s.active else "⛔"
                prio_emoji = {1: "🔴", 2: "🟠", 3: "🟡"}.get(s.priority, "⚪")
                lang_flag = "🇫🇷" if s.language == "fr" else "🇬🇧"
                lines.append(f"{check} {prio_emoji} {lang_flag} `{s.id}`")

        total = len(self._sources)
        actives = sum(1 for s in self._sources if s.active)

        embed = discord.Embed(
            title="📡 Sources de veille RSS",
            description="\n".join(lines) if lines else "_Aucune source configurée._",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"{actives} actives · {total} total")
        await ctx.send(embed=embed)

    @sources_group.command(name="add")
    async def sources_add(
        self,
        ctx: commands.Context,
        source_id: str,
        url: str,
        category: str,
        priority: int = 2,
    ):
        """
        Ajoute une nouvelle source au YAML.
        Exemple : !veille sources add wired https://www.wired.com/feed/rss tech 2
        """
        # 1. Validation paramètres
        err = _validate_source_id(source_id)
        if err:
            await ctx.send(f"❌ id invalide : {err}")
            return
        if category not in VALID_CATEGORIES:
            await ctx.send(
                f"❌ category invalide. Valides : {', '.join(VALID_CATEGORIES)}"
            )
            return
        if priority not in VALID_PRIORITIES:
            await ctx.send(f"❌ priority invalide (1, 2, ou 3)")
            return
        if not _is_valid_url(url):
            await ctx.send(f"❌ URL invalide (http/https + domaine requis)")
            return

        # 2. Vérifier unicité
        raw = _load_sources_raw()
        if _find_source_index(raw, source_id) >= 0:
            await ctx.send(f"❌ Une source avec l'id `{source_id}` existe déjà.")
            return

        # 3. Tester l'URL
        test_msg = await ctx.send(f"🔄 Test de l'URL `{url}` en cours…")
        ok, msg, count = await self._test_source_url(url)
        if not ok:
            await test_msg.edit(content=f"❌ Test échoué : {msg}")
            return
        await test_msg.edit(content=f"✅ Test OK : {msg}")

        # 4. Détecter la langue depuis le domaine (heuristique simple)
        lang = "fr" if any(d in url for d in (".fr/", ".fr?", "://fr.")) else "en"

        # 5. Construire l'entrée YAML
        from ruamel.yaml.comments import CommentedMap
        new_entry = CommentedMap()
        new_entry["id"] = source_id
        new_entry["url"] = url
        new_entry["category"] = category
        new_entry["language"] = lang
        new_entry["priority"] = priority
        new_entry["active"] = True
        new_entry["notes"] = f"Ajouté via !veille sources add le {datetime.now():%Y-%m-%d}"

        # 6. Insérer en respectant l'ordre par catégorie (à la fin de la cat)
        insert_idx = len(raw)
        cat_order = ["cyber", "ia", "dev", "tech"]
        target_cat_pos = cat_order.index(category)
        for i, item in enumerate(raw):
            item_cat = item.get("category") if hasattr(item, "get") else None
            if item_cat is None:
                continue
            try:
                item_pos = cat_order.index(item_cat)
            except ValueError:
                continue
            if item_pos > target_cat_pos:
                insert_idx = i
                break
        raw.insert(insert_idx, new_entry)

        # 7. Sauvegarder + reload en mémoire
        _save_sources_raw(raw)
        self._reload_sources()

        await ctx.send(
            f"✅ Source `{source_id}` ajoutée à la catégorie **{category}** "
            f"(priorité {priority}, langue {lang}).\n"
            f"_{count} articles détectés au test._"
        )
        await self._log_to_channel(
            f"Source **`{source_id}`** ajoutée par {ctx.author.mention}.",
            title="➕ VeilleRSS — Source ajoutée",
            color=0x2ECC71,
            fields=[
                ("URL", url, False),
                ("Catégorie", category, True),
                ("Priorité", str(priority), True),
                ("Langue", lang, True),
            ],
        )

    @sources_group.command(name="remove")
    async def sources_remove(
        self,
        ctx: commands.Context,
        source_id: str,
    ):
        """Retire une source du YAML (avec confirmation)."""
        raw = _load_sources_raw()
        idx = _find_source_index(raw, source_id)
        if idx < 0:
            await ctx.send(f"❌ Aucune source avec l'id `{source_id}`.")
            return

        confirm_msg = await ctx.send(
            f"⚠️ Confirmer la suppression de la source `{source_id}` ?\n"
            f"Réponds **oui** dans les 30 secondes."
        )

        def check(m: discord.Message) -> bool:
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.strip().lower() in ("oui", "yes", "y", "o")
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await confirm_msg.edit(content="⏱️ Délai dépassé, suppression annulée.")
            return

        del raw[idx]
        _save_sources_raw(raw)
        self._reload_sources()

        await ctx.send(f"✅ Source `{source_id}` supprimée.")
        await self._log_to_channel(
            f"Source **`{source_id}`** supprimée par {ctx.author.mention}.",
            title="🗑️ VeilleRSS — Source supprimée",
            color=0xE67E22,
        )

    @sources_group.command(name="toggle")
    async def sources_toggle(
        self,
        ctx: commands.Context,
        source_id: str,
    ):
        """Active ou désactive une source."""
        raw = _load_sources_raw()
        idx = _find_source_index(raw, source_id)
        if idx < 0:
            await ctx.send(f"❌ Aucune source avec l'id `{source_id}`.")
            return

        current_active = bool(raw[idx].get("active", True))
        new_active = not current_active
        raw[idx]["active"] = new_active

        _save_sources_raw(raw)
        self._reload_sources()

        state_word = "activée" if new_active else "désactivée"
        emoji = "✅" if new_active else "⛔"
        await ctx.send(f"{emoji} Source `{source_id}` **{state_word}**.")
        await self._log_to_channel(
            f"Source **`{source_id}`** {state_word} par {ctx.author.mention}.",
            title=f"{emoji} VeilleRSS — Source toggle",
            color=0x2ECC71 if new_active else 0x95A5A6,
        )

    @sources_group.command(name="test")
    async def sources_test(
        self,
        ctx: commands.Context,
        url: str,
    ):
        """
        Teste une URL RSS sans l'ajouter au YAML.
        Utile pour vérifier qu'un flux fonctionne avant un add.
        """
        if not _is_valid_url(url):
            await ctx.send(f"❌ URL invalide (http/https + domaine requis).")
            return

        msg = await ctx.send(f"🔄 Test de l'URL `{url}` en cours…")
        ok, result, count = await self._test_source_url(url)
        if ok:
            await msg.edit(
                content=f"✅ {result}\n_Tu peux maintenant l'ajouter via `!veille sources add`._"
            )
        else:
            await msg.edit(content=f"❌ {result}")

    @veille_group.command(name="keywords")
    async def keywords_show(self, ctx: commands.Context):
        """Affiche les mots-clés de scoring chargés."""
        try:
            keywords = _load_keywords()
        except Exception as e:
            await ctx.send(f"❌ Erreur lecture rss_keywords.yaml : `{type(e).__name__}: {e}`")
            return

        cat_emojis = {"all": "🌐", "cyber": "📰", "ia": "🤖", "dev": "💻", "tech": "📱"}
        fields = []
        for cat in ("all", "cyber", "ia", "dev", "tech"):
            cat_kw = keywords.get(cat, {"boost": [], "blacklist": []})
            boost = cat_kw.get("boost", [])
            blacklist = cat_kw.get("blacklist", [])
            if not boost and not blacklist:
                continue

            lines = []
            if boost:
                lines.append(f"**Boost ({len(boost)})** : " + ", ".join(f"`{w}`" for w in boost[:15]))
                if len(boost) > 15:
                    lines.append(f"  _(+ {len(boost) - 15} autres)_")
            if blacklist:
                lines.append(f"**Blacklist ({len(blacklist)})** : " + ", ".join(f"`{w}`" for w in blacklist[:10]))
                if len(blacklist) > 10:
                    lines.append(f"  _(+ {len(blacklist) - 10} autres)_")

            fields.append((
                f"{cat_emojis.get(cat, '❓')} {cat}",
                "\n".join(lines)[:1024],
                False,
            ))

        if not fields:
            await ctx.send("ℹ️ Aucun mot-clé configuré. Édite `datas/rss_keywords.yaml`.")
            return

        embed = discord.Embed(
            title="🔑 Mots-clés de scoring",
            description=(
                f"Boost = +{KEYWORD_BOOST_POINTS} points par match (article remonte).\n"
                f"Blacklist = article rejeté du digest.\n"
                f"_Édite `datas/rss_keywords.yaml` puis `!veille reload` pour mettre à jour._"
            ),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(VeilleRSS(bot))
