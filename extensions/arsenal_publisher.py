"""
arsenal_publisher.py — Cog Discord pour Arsenal Intelligence Unit.

Publie les résumés Claude dans des forums Discord classés par catégorie,
avec tags, médias, transcriptions et résumés.

Changements vs l'ancien arsenal_sync.py :
- Utilise arsenal_config.py pour tous les chemins
- Plus de processed_videos.json — uniquement sync_status CSV
- Commande !sync_arsenal pour lancer la sync à la demande
- Plus de sync automatique au démarrage (configurable)
"""

import os
import re
import json
import asyncio
from typing import List, Optional, Dict, Any

import pandas as pd
import discord
from discord.ext import commands, tasks
from datetime import datetime

from arsenal_config import (
    cfg, CSV_ENCODING, DISCORD_UPLOAD_LIMIT,
    normalize_str, now_timestamp, get_logger,
)

log = get_logger("arsenal_publisher")


# =============================================================================
# PERSISTANCE source_id → thread_id (Phase Y.15)
# =============================================================================
# Map indépendante du CSV pour empêcher les doublons de thread Discord
# quand le sync_status du CSV est foireux (race condition entre
# summarize.py et publisher qui écrivent CSV en parallèle, observé
# Phase Y.15 — bug "TotalEnergies × 6 dupes" dans économie-et-social).
# Cette map est la source de vérité pour "ce source_id a déjà un thread"
# du point de vue du publisher.
PUBLISHED_THREADS_PATH = os.path.join(cfg.base_path, "datas", "arsenal_published_threads.json")


def _load_published_threads() -> dict:
    """Lit la map persistante. Format :
    {"<platform>::<source_id>": {"thread_id": "...", "forum_id": "...",
                                  "title": "...", "created_at": "..."}}
    Retourne {} si fichier absent ou corrompu."""
    try:
        with open(PUBLISHED_THREADS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_published_threads(data: dict) -> None:
    """Écriture atomique."""
    os.makedirs(os.path.dirname(PUBLISHED_THREADS_PATH), exist_ok=True)
    tmp = PUBLISHED_THREADS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PUBLISHED_THREADS_PATH)


def _published_key(platform: str, source_id: str) -> str:
    return f"{(platform or '').strip().lower()}::{str(source_id).strip()}"


# =============================================================================
# WHITELIST CLASSIFICATION (Phase Y.6)
# =============================================================================
# 12 forums thématiques fins voulus par l'utilisateur dans la catégorie
# `ANALYSES POLITIQUES` + 2 forums fonctionnels (catégorie-libre fallback,
# campagne-2027 thématique). Toute classification produite par Claude via
# `parse_analysis` qui ne correspond à aucun de ces 14 forums est :
# 1. mappée via CLASSIFICATION_ALIASES si une variante connue est détectée
# 2. fallback sur `catégorie-libre` sinon
# Évite la création de forums orphelins (bug Y.6 : 4 forums à 1 thread
# `débats-et-rhétorique`, `éducation`, `social-et-médias`,
# `société-et-éducation` créés par variations Claude).

CANONICAL_FORUMS = {
    "politique-française",
    "économie-et-social",
    "société-et-médias",
    "ia-et-technologie",
    "histoire-et-géopolitique",
    "international-et-solidarités",
    "religions-et-philosophie",
    "justice-et-libertés",
    "féminisme-et-luttes",
    "social-et-logement",
    "culture-et-éducation",
    "écologie-et-climat",
    "catégorie-libre",
    "campagne-2027",
}

# Slugs alternatifs que Claude produit parfois (variations de naming) →
# slug canonique. Exhaustif basé sur les 4 cas observés Y.6 + variantes
# probables. Ajouter ici toute nouvelle variante détectée en prod.
CLASSIFICATION_ALIASES = {
    # Variantes éducation
    "éducation": "culture-et-éducation",
    "société-et-éducation": "culture-et-éducation",
    "culture": "culture-et-éducation",
    # Variantes médias / débats
    "social-et-médias": "société-et-médias",
    "débats-et-rhétorique": "société-et-médias",
    "débats-et-rhétoriques": "société-et-médias",
    "rhétorique": "société-et-médias",
    "médias": "société-et-médias",
    "médias-et-société": "société-et-médias",
    # Variantes économie
    "économie": "économie-et-social",
    "social": "économie-et-social",
    "économie-et-finance": "économie-et-social",
    # Variantes histoire / géopolitique
    "histoire": "histoire-et-géopolitique",
    "géopolitique": "histoire-et-géopolitique",
    # Variantes international
    "international": "international-et-solidarités",
    "solidarités": "international-et-solidarités",
    "international-et-solidarité": "international-et-solidarités",
    # Variantes justice
    "justice": "justice-et-libertés",
    "libertés": "justice-et-libertés",
    "droits-humains": "justice-et-libertés",
    # Variantes féminisme
    "féminisme": "féminisme-et-luttes",
    "luttes": "féminisme-et-luttes",
    "féminismes": "féminisme-et-luttes",
    # Variantes écologie
    "écologie": "écologie-et-climat",
    "climat": "écologie-et-climat",
    # Variantes IA / tech
    "ia": "ia-et-technologie",
    "technologie": "ia-et-technologie",
    "tech": "ia-et-technologie",
    # Variantes religion / philo
    "religions": "religions-et-philosophie",
    "religion": "religions-et-philosophie",
    "philosophie": "religions-et-philosophie",
    # Variantes politique
    "politique": "politique-française",
    "politique-france": "politique-française",
    "politique-fr": "politique-française",
    # Variantes social / logement
    "logement": "social-et-logement",
    "précarité": "social-et-logement",
    "pauvreté": "social-et-logement",
    # Variantes campagne
    "campagne": "campagne-2027",
    "présidentielle": "campagne-2027",
    "présidentielle-2027": "campagne-2027",
    "élections": "campagne-2027",
    "élection": "campagne-2027",
}

FALLBACK_FORUM = "catégorie-libre"


def _normalize_forum_slug(raw_slug: str) -> str:
    """Normalise un slug de forum candidate vers un forum canonique.

    1. Si déjà dans CANONICAL_FORUMS → retourne tel quel.
    2. Sinon si dans CLASSIFICATION_ALIASES → retourne la cible canonique.
    3. Sinon fallback sur FALLBACK_FORUM (`catégorie-libre`).

    Logging info à chaque alias/fallback pour repérer les nouveaux patterns
    Claude (à ajouter à CLASSIFICATION_ALIASES si récurrent).
    """
    if raw_slug in CANONICAL_FORUMS:
        return raw_slug
    if raw_slug in CLASSIFICATION_ALIASES:
        canon = CLASSIFICATION_ALIASES[raw_slug]
        log.info(f"Classification {raw_slug!r} → alias canonique {canon!r}")
        return canon
    log.info(f"Classification {raw_slug!r} hors whitelist → fallback {FALLBACK_FORUM!r}")
    return FALLBACK_FORUM


class ArsenalPublisher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Migration 2026-04-29 : guild ISTIC L1 G2.
        # Catégorie cible : "ANALYSES POLITIQUES" (sans emoji), créée manuellement
        # par l'utilisateur avec 12 forums thématiques fins (économie-et-social,
        # culture-et-éducation, justice-et-libertés, féminisme-et-luttes,
        # histoire-et-géopolitique, etc.). NE PAS confondre avec la catégorie
        # `📂 ANALYSES POLITIQUES` (avec emoji) qui était une cat orpheline de
        # ma migration et qui a été supprimée.
        self.guild_id = 1466806132998672466
        self.log_channel_id = 1493760267300110466
        self.category_name = "ANALYSES POLITIQUES"
        self.user_admin_id = 200750717437345792
        self.is_syncing = False  # verrou pour éviter double sync

        # Auto-sync en temps réel (boucle 15s — TODO 11 du 2026-04-29).
        # Dès que summarize.py finit un résumé (= modifie le CSV : passe une
        # ligne en summary=SUCCESS, sync=PENDING), le tick suivant détecte
        # le mtime changé, recharge, et lance _sync_task. Le 1er tick au boot
        # rattrape automatiquement le backlog (les 89 PENDING actuels après
        # la migration 2026-04-29). Pas d'usage de LLM, juste copie de fichiers
        # déjà résumés vers les threads Discord.
        self._auto_sync_started = False
        self._last_csv_mtime = 0.0

        # Auto-archive (Phase Y.5) : la guilde ISTIC est non-boostée et plafonne
        # à 1000 threads actifs. Au-delà, Discord renvoie 160006 sur toute
        # création de thread, bloquant l'auto-sync. La boucle horaire archive
        # les plus vieux threads d'ANALYSES POLITIQUES quand le total guilde
        # passe au-dessus de ARCHIVE_THRESHOLD pour redescendre à ARCHIVE_TARGET.
        # Archivage = thread devient inactif, reste visible et accessible, mais
        # ne compte plus dans le quota actif. Auto-unarchive si message posté.
        self._auto_archive_started = False
        self.ARCHIVE_THRESHOLD = 900  # déclenche si > 900 threads actifs guilde-wide
        self.ARCHIVE_TARGET = 800     # cible après archivage (= 200 slots libres)
        self.ARCHIVE_INTERVAL_HOURS = 1

        # Y.22 : queue de posts `✅ Dossier indexé` à différer dans les fils
        # `🔗・liens` jusqu'à la FIN du pipeline (après l'embed `Pipeline
        # terminé`). Avant Y.22, le Dossier indexé apparaissait entre
        # l'embed `✅ Résumé Claude` et `✅ Publication Discord` car il était
        # posté pendant `_sync_task`. Le user le veut tout en bas du fil.
        # Format des entrées : tuples `(link_thread, embed_kwargs_dict)`.
        self._deferred_thread_dossier_posts: list = []

        self.df_suivi = self._load_csv()

    # =========================
    # PERSISTENCE CSV
    # =========================
    def _load_csv(self):
        if not os.path.isfile(cfg.CSV_PATH):
            return None
        try:
            df = pd.read_csv(cfg.CSV_PATH, encoding=CSV_ENCODING, dtype=str).fillna("")
        except Exception:
            return None

        required = ["id", "url", "plateforme", "download_status", "filename",
                     "sync_status", "sync_timestamp", "sync_error"]
        for col in required:
            if col not in df.columns:
                df[col] = ""

        df["download_status"] = df["download_status"].str.upper().str.strip()
        df["sync_status"] = df["sync_status"].str.upper().str.strip()
        df.loc[df["sync_status"] == "", "sync_status"] = "PENDING"
        return df

    def _save_csv(self):
        if self.df_suivi is None:
            return
        self.df_suivi.to_csv(cfg.CSV_PATH, index=False, encoding=CSV_ENCODING)

    def _reload_csv(self):
        self.df_suivi = self._load_csv()

    # =========================
    # LOGS DISCORD
    # =========================
    @staticmethod
    def _build_embed(title, description, color, fields):
        embed = discord.Embed(title=title, description=description,
                              color=color, timestamp=datetime.now())
        if fields:
            for name, value in fields.items():
                embed.add_field(name=name, value=str(value)[:1024], inline=True)
        embed.set_footer(text="Arsenal Intelligence Unit")
        return embed

    async def _post_to_thread(self, link_thread, *, title, description,
                                color=discord.Color.blue(), fields=None):
        """Y.22 : poste un embed UNIQUEMENT dans `link_thread`, sans
        toucher `📋・logs`. Utilisé pour différer le post du `Dossier
        indexé` jusqu'après l'embed `Pipeline terminé`."""
        if not link_thread:
            return
        embed = self._build_embed(title, description, color, fields)
        try:
            await link_thread.send(embed=embed)
        except discord.HTTPException as e:
            log.warning(f"Post fil échoué : {e}")

    async def send_log(self, title, description, color=discord.Color.blue(), fields=None,
                       link_thread=None):
        """Envoie un embed dans `📋・logs` ET (optionnellement) dans
        `link_thread` (Phase Y.11) — fil sur le message d'origine de
        `🔗・liens`. Permet à l'embed "✅ Dossier indexé" d'apparaître
        aussi dans le fil par drop, pas seulement noyé dans #logs.
        """
        channel = self.bot.get_channel(self.log_channel_id)
        embed = discord.Embed(title=title, description=description,
                              color=color, timestamp=datetime.now())
        if fields:
            for name, value in fields.items():
                embed.add_field(name=name, value=str(value)[:1024], inline=True)
        embed.set_footer(text="Arsenal Intelligence Unit")
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                pass
        if link_thread:
            try:
                await link_thread.send(embed=embed)
            except Exception:
                pass

    async def flush_deferred_dossier_to_fil(self):
        """Y.22 : poste dans les fils `🔗・liens` les embeds `Dossier
        indexé` mis en queue par `_sync_task` ou `_forward_dossier_to_fil`
        pendant un appel pipeline avec `defer_dossier_forwards=True`.
        Appelée par `arsenal_pipeline.run_pipeline` après l'embed
        `Pipeline terminé`, pour que le Dossier indexé apparaisse en fin
        de fil et pas au milieu (entre Résumé Claude et Publication).
        Idempotent : la queue est consommée et vidée à chaque appel."""
        pending = self._deferred_thread_dossier_posts
        self._deferred_thread_dossier_posts = []
        for link_thread, embed_kwargs in pending:
            await self._post_to_thread(link_thread, **embed_kwargs)

    async def _link_thread_has_dossier(self, link_thread) -> bool:
        """Y.17 : True si le fil contient déjà un embed `✅ Dossier indexé`.
        Évite le double-post quand on retro-forward après race auto-sync."""
        try:
            async for msg in link_thread.history(limit=50):
                for em in msg.embeds:
                    if em.title and "Dossier indexé" in em.title:
                        return True
        except Exception:
            pass
        return False

    async def _forward_dossier_to_fil(self, metadata, link_thread, *,
                                       skip_if_present=True, defer=False):
        """Y.17 : forward l'embed `✅ Dossier indexé` d'une source déjà
        publiée vers `link_thread` (le fil sur le message d'origine de
        `🔗・liens`). Couvre la race auto-sync vs pipeline live : quand
        l'auto-sync 15s gagne, le Dossier indexé part dans `📋・logs` sans
        link_thread → le fil reste orphelin. Cette méthode est appelée
        par les paths skip de `_sync_task` quand `only_source_id +
        link_thread` sont fournis (= appel pipeline).

        Y.22 : `defer=True` → le post dans le fil est mis en attente dans
        `self._deferred_thread_dossier_posts` au lieu d'être envoyé
        immédiatement, pour être flush APRÈS l'embed `Pipeline terminé`
        du run_pipeline (sinon le Dossier indexé apparaît au milieu du
        fil entre Résumé Claude et Publication Discord). Le post dans
        `📋・logs` reste immédiat dans tous les cas."""
        if skip_if_present and await self._link_thread_has_dossier(link_thread):
            return
        source_id = metadata.get("source_id")
        platform = metadata.get("platform")
        pkey = _published_key(platform, source_id)
        pmap_entry = _load_published_threads().get(pkey)
        if not pmap_entry:
            return
        try:
            existing = await self.bot.fetch_channel(int(pmap_entry["thread_id"]))
        except (discord.NotFound, discord.HTTPException):
            return
        if not existing:
            return
        title = metadata.get("title") or ""
        embed_kwargs = {
            "title": "✅ Dossier indexé",
            "description": f"ID `{source_id}`\n🔗 [Ouvrir]({existing.jump_url})",
            "color": discord.Color.green(),
            "fields": {"Note": f"{metadata.get('score', '?')}/20",
                       "Forum": metadata.get("forum_name", "?"),
                       "Titre": (title[:80] if title else "N/A")},
        }
        if defer:
            # Poste maintenant dans `📋・logs` seulement, défère le post
            # dans le fil pour après `Pipeline terminé`.
            await self.send_log(**embed_kwargs)
            self._deferred_thread_dossier_posts.append((link_thread, embed_kwargs))
        else:
            await self.send_log(**embed_kwargs, link_thread=link_thread)

    async def check_csv_duplicates(self):
        if self.df_suivi is None or self.df_suivi.empty:
            return False
        dup_ids = self.df_suivi[self.df_suivi.duplicated(subset=["plateforme", "id"], keep=False)]
        if not dup_ids.empty:
            count = len(dup_ids[["plateforme", "id"]].drop_duplicates())
            await self.send_log("🔍 Doublons CSV", f"{count} doublons (plateforme+id) détectés",
                                discord.Color.orange())
            return True
        return False

    # =========================
    # TEXTE / PARSING
    # =========================
    def clean_markdown_lists(self, text):
        """Nettoie le résumé Claude pour le post Discord — version sobre :

        - Les **sections numérotées** Claude (`N. **Titre**`) deviennent
          des **headers Discord H3** (`### Titre`) — Discord rend ces
          headers en gras + grande taille, naturellement lisibles, sans
          besoin d'emojis ni de gras manuel.
        - Les **bullets `- xxx`** ou `* xxx` au début de ligne sont laissés
          intacts (Discord les rend nativement comme listes à puce).
        - Les **sous-numérotations `1. xxx`** sans markdown gras
          (couramment générées par Claude dans la section Arguments)
          deviennent des bullets `- xxx` pour éviter la double
          numérotation visuelle quand le post est imbriqué dans une
          section déjà numérotée à la source.
        - Les **tirets em-dash `—`** ou autres séparateurs internes au
          texte sont laissés intacts.

        Robuste : la regex de section est tolérante aux espaces parasites,
        au point final ou aux deux-points en fin de titre.
        """
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            # Section principale : `N. **Titre**` (avec ou sans : ou . final)
            m = re.match(r"^\s*\d+\.\s+\*\*\s*([^*]+?)\s*\*\*\s*[:.]?\s*$", line)
            if m:
                section = m.group(1).strip().rstrip(":").rstrip(".").strip()
                cleaned.append(f"### {section}")
                continue
            # Sous-numérotation `1. xxx` (sans markdown gras) → bullet `- xxx`
            # pour éviter la double numérotation imbriquée
            m = re.match(r"^(\s*)\d+[.)]\s+(.*)$", line)
            if m and not re.search(r"\*\*", m.group(2)[:50]):
                indent, content = m.group(1), m.group(2)
                cleaned.append(f"{indent}- {content}")
                continue
            # Bullets `* xxx` → `- xxx` : Discord parse mal les `*` en début
            # de ligne quand le contenu contient d'autres `*` ailleurs (matche
            # à tort comme italique). Le tiret `-` est interprété sans
            # ambiguïté comme bullet.
            m = re.match(r"^(\s*)\*\s+(.*)$", line)
            if m:
                indent, content = m.group(1), m.group(2)
                cleaned.append(f"{indent}- {content}")
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    def wrap_links(self, text):
        return re.sub(r"(https?://[^\s>]+)", r"<\1>", text)

    def split_text(self, text, limit=1850):
        """Split intelligent du résumé pour Discord (limite ~2000 chars).

        Priorité des frontières de coupure (de la plus propre à la moins) :
        1. Avant un header Discord `\\n### ` — cassure idéale, le chunk
           suivant commence par un nouveau header.
        2. Sur un saut de paragraphe `\\n\\n`.
        3. Avant un bullet `\\n- `.
        4. Après une fin de phrase `. \\n`.
        5. Sur un simple saut de ligne `\\n`.
        6. Fallback brut à la limite.

        Chaque chunk est `rstrip()` + le suivant `lstrip()` pour éviter
        les blanks parasites au milieu — sans introduire de séparateur
        visible (ni `-`, ni `…`, etc.).
        """
        if not text:
            return []
        text = self.wrap_links(text)
        chunks = []
        while len(text) > limit:
            zone = text[:limit]
            cut = -1
            # 1. Avant un header `### `
            for m in re.finditer(r"\n###\s", zone):
                cut = m.start()
            # 2. Saut de paragraphe
            if cut == -1:
                cut = zone.rfind("\n\n")
            # 3. Avant un bullet `- xxx` (ou `* xxx`)
            if cut == -1:
                for m in re.finditer(r"\n[-*]\s", zone):
                    cut = m.start()
            # 4. Fin de phrase suivie d'un saut
            if cut == -1:
                idx = zone.rfind(". \n")
                if idx >= 0:
                    cut = idx + 2  # après le `. `
            # 5. Simple saut de ligne
            if cut == -1:
                cut = zone.rfind("\n")
            # 6. Fallback brut
            if cut == -1:
                cut = limit
            chunks.append(text[:cut].rstrip())
            text = text[cut:].lstrip()
        if text:
            chunks.append(text)
        return chunks

    def normalize_thread_title(self, title_raw: str) -> str:
        if not title_raw:
            return ""
        t = title_raw.replace("\r", " ").replace("\n", " ").strip()
        t = re.sub(r"\s+", " ", t).strip()
        return t[:100].rstrip()

    async def find_existing_thread_by_names(self, forum, names: List[str]):
        """Cherche un thread existant qui matche un des `names` :
        - match exact, OU
        - thread.name est le préfixe d'un name (ex : on cherche "Foo · xxxx",
          un thread "Foo" existe → on le réutilise), OU
        - un name est le préfixe de thread.name (ex : on cherche "Foo", un
          thread "Foo · xxxx" existe → match), OU
        - cas "truncation-prefix" : le préfixe tronqué d'un name suffixé
          (ex "Foo bar préca · X") est un préfixe d'un thread non-suffixé
          plus long (ex "Foo bar précarité"). build_thread_name produit ce
          candidate quand un thread "...précarité" existe déjà, et l'ancien
          matcher ne trouvait pas la correspondance avec le thread original
          — cause de 28 doublons observés en prod.

        Cela évite la création de doublons quand `build_thread_name` ajoute
        un suffixe `· source_id` à un titre dont le thread original existe
        déjà.
        """
        if not forum:
            return None
        want = {n for n in names if n}
        if not want:
            return None
        # Pour chaque name avec suffixe " · xxxx", on accepte aussi le préfixe
        want_prefixes = {
            n.rsplit(" · ", 1)[0].rstrip()
            for n in want if " · " in n
        }

        def match(t_name: str) -> bool:
            if not t_name:
                return False
            if t_name in want:
                return True
            if t_name in want_prefixes:
                return True
            # Inverse : un want simple est le préfixe d'un thread suffixé
            for w in want:
                if " · " not in w and t_name.startswith(w + " · "):
                    return True
            # Truncation-prefix : want = "...préca · X", t_name = "...précarité".
            # On match si le préfixe tronqué est un préfixe strict du nom existant
            # ET le caractère suivant continue un mot (alphanumérique). Restreint
            # aux préfixes ≥80 chars pour éviter les faux positifs sur préfixes
            # génériques courts.
            for w_prefix in want_prefixes:
                if (len(w_prefix) >= 80
                        and t_name.startswith(w_prefix)
                        and len(t_name) > len(w_prefix)
                        and t_name[len(w_prefix)].isalnum()):
                    return True
            return False

        for t in forum.threads:
            if match(t.name):
                return t
        async for t in forum.archived_threads():
            if match(t.name):
                return t
        return None

    async def build_thread_name(self, forum, source_id: str, title: str) -> str:
        base = self.normalize_thread_title(title)
        if not base:
            base = self.normalize_thread_title(str(source_id))
        base = base[:100].rstrip()

        exists = await self.find_existing_thread_by_names(forum, [base])
        if not exists:
            return base

        suffix = str(source_id)[-8:] if source_id else "dup"
        candidate = f"{base} · {suffix}"
        if len(candidate) > 100:
            cut = 100 - (len(suffix) + 3)
            candidate = f"{base[:cut].rstrip()} · {suffix}" if cut > 0 else suffix[:100]

        exists2 = await self.find_existing_thread_by_names(forum, [candidate])
        if not exists2:
            return candidate
        # Fallback : retourner candidate quand même. Si un thread matche déjà
        # ce candidate (via match exact ou par préfixe), `find_existing_thread_by_names`
        # dans `_sync_task` le trouvera et set sync=SUCCESS sans recréer.
        # Préférable au précédent fallback `str(source_id)[:100]` qui créait
        # des threads orphelins avec un titre court (= source_id) — bug observé
        # en prod : `DOlfIBSiFIP` créé en doublon d'un thread "Le sophisme...".
        return candidate

    def parse_analysis(self, content, summary_filename=None):
        data = {
            "filename_label": None, "title": None, "source_id": None,
            "platform": None, "display_theme": None, "specific_theme": None,
            "forum_name": None,
            "score": 0, "full_emotion": "", "has_sophism": False,
            "has_cta": False, "is_urgent": False,
        }
        try:
            m = re.search(r"^DOSSIER\s+—\s+(.+?)\s*$", content, re.MULTILINE)
            if m:
                data["source_id"] = m.group(1).strip()

            m = re.search(r"^PLATEFORME\s+—\s+(.+?)\s*$", content, re.MULTILINE)
            if m:
                data["platform"] = m.group(1).strip()

            m = re.search(r"\*\*Nom du fichier\*\*\s*\n(.*?)(?:\n|$)", content)
            if m:
                data["filename_label"] = m.group(1).strip()

            m = re.search(r"\*\*Titre\*\*\s*\n(.*?)(?:\n|$)", content)
            if m:
                t = m.group(1).strip().replace("\r", " ").replace("\n", " ")
                t = re.sub(r"\s+", " ", t).strip()
                data["title"] = t if t else None

            if not data["source_id"] and summary_filename:
                base = os.path.splitext(summary_filename)[0]
                m2 = re.match(r"^(IG|TT|SRC)_(.+)$", base, flags=re.IGNORECASE)
                if m2:
                    data["source_id"] = m2.group(2).strip()
                    if not data["platform"]:
                        pref = m2.group(1).upper()
                        data["platform"] = {"IG": "Instagram", "TT": "TikTok"}.get(pref)
                else:
                    data["source_id"] = base

            if not data["source_id"] and data["filename_label"]:
                data["source_id"] = data["filename_label"].strip()

            m = re.search(r"\*\*Classification\*\*\s*\n(.*?)(?:\n|$)", content)
            if m:
                parts = m.group(1).strip().split(">")
                # Strip ponctuation finale (point, virgule, etc.) AVANT slug.
                # Sans ce strip, "Catégorie Libre." → forum_name "catégorie-libre."
                # alors que Discord normalise le nom de salon en "catégorie-libre"
                # à la création (point retiré). Le lookup `discord.utils.get(name=…)`
                # ne match plus, → un nouveau forum est créé à chaque sync (bug
                # observé : 35 dupes "catégorie-libre" pour 1 seul item).
                display_theme = parts[0].strip().rstrip(".,:;!?").strip().replace("Général — ", "")
                data["display_theme"] = display_theme
                slug = display_theme.lower().replace(" ", "-")
                raw_slug = re.sub(r"-+", "-", slug).strip("-.")
                # Phase Y.6 : whitelist + alias map pour empêcher la création
                # de forums orphelins (cf CANONICAL_FORUMS, CLASSIFICATION_ALIASES,
                # FALLBACK_FORUM en haut du module).
                data["forum_name"] = _normalize_forum_slug(raw_slug)
                if len(parts) > 1:
                    specific = parts[1].strip().rstrip(".").strip()
                    data["specific_theme"] = specific or None

            m = re.search(r"Note de pertinence.*?\s*(\d+)\s*/\s*20", content, re.IGNORECASE)
            data["score"] = int(m.group(1)) if m else 0

            m = re.search(r"\*\*Analyse de la charge émotionnelle\*\*\s*\n(.*?)(?:\n|$)", content)
            data["full_emotion"] = m.group(1).strip().lower() if m else ""

            lower = content.lower()
            data["has_sophism"] = "sophisme" in lower
            data["has_cta"] = "appel à l'action" in lower or "proposition" in lower

            m = re.search(r"\*\*Indice d'urgence\*\*\s*\n(.*?)(?:\n|$)", content)
            urg = m.group(1).lower() if m else ""
            data["is_urgent"] = any(x in urg for x in ["brûlante", "élevé", "critique", "immédiat", "urgent"])

            if not data["source_id"] or not data["forum_name"]:
                return None
            return data
        except Exception:
            return None

    # =========================
    # ACCÈS CSV
    # =========================
    def get_row_by_source_id(self, source_id, platform=None):
        if self.df_suivi is None or self.df_suivi.empty:
            return None
        df = self.df_suivi[self.df_suivi["download_status"] == "SUCCESS"]
        df = df[df["id"].str.strip() == str(source_id).strip()]
        if platform:
            dfp = df[df["plateforme"].str.lower() == str(platform).strip().lower()]
            if not dfp.empty:
                df = dfp
        if df.empty:
            return None
        return df.iloc[-1].to_dict()

    def set_sync_status(self, source_id, platform, status, error_msg=""):
        if self.df_suivi is None or self.df_suivi.empty:
            return
        ts = now_timestamp()
        mask = self.df_suivi["id"].str.strip() == str(source_id).strip()
        if platform:
            mask = mask & (self.df_suivi["plateforme"].str.lower() == str(platform).strip().lower())
        if mask.any():
            self.df_suivi.loc[mask, "sync_status"] = status.upper()
            self.df_suivi.loc[mask, "sync_timestamp"] = ts
            self.df_suivi.loc[mask, "sync_error"] = (error_msg or "")[:1000]
            self._save_csv()

    def should_process(self, summary_filename, metadata):
        if not metadata:
            return False
        source_id = metadata.get("source_id")
        platform = metadata.get("platform")
        row = self.get_row_by_source_id(source_id, platform)
        if not row:
            return False
        if str(row.get("sync_status", "")).upper().strip() == "SUCCESS":
            return False
        return True

    # =========================
    # RÉSOLUTION MÉDIAS
    # =========================
    def resolve_prefixed_dir(self, base_dir: str, exact_name: str) -> Optional[str]:
        if not os.path.isdir(base_dir):
            return None
        exact = os.path.join(base_dir, exact_name)
        if os.path.isdir(exact):
            return exact
        prefix = exact_name + "_"
        candidates = [os.path.join(base_dir, n) for n in os.listdir(base_dir)
                      if os.path.isdir(os.path.join(base_dir, n)) and n.startswith(prefix)]
        if not candidates:
            return None
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return candidates[0]

    def find_video_for_source(self, source_id):
        if not os.path.isdir(cfg.VIDEO_DIR):
            return None
        candidates = [f for f in os.listdir(cfg.VIDEO_DIR)
                      if f.lower().endswith((".mp4", ".webm", ".mkv", ".mov"))
                      and f.startswith(f"{source_id}_")]
        if not candidates:
            return None
        candidates.sort(key=lambda x: os.path.getmtime(os.path.join(cfg.VIDEO_DIR, x)), reverse=True)
        return os.path.join(cfg.VIDEO_DIR, candidates[0])

    def find_transcript_for_source(self, source_id):
        if not os.path.isdir(cfg.TRANSCRIPT_DIR):
            return None
        candidates = [f"{source_id}.txt", f"TT_{source_id}.txt", f"IG_{source_id}.txt"]
        for name in candidates:
            p = os.path.join(cfg.TRANSCRIPT_DIR, name)
            if os.path.isfile(p):
                return p
        for f in os.listdir(cfg.TRANSCRIPT_DIR):
            if not f.lower().endswith(".txt"):
                continue
            if f.startswith(f"{source_id}_") or f.startswith(f"TT_{source_id}") or f.startswith(f"IG_{source_id}"):
                return os.path.join(cfg.TRANSCRIPT_DIR, f)
        return None

    def _media_sort_key(self, path_or_name: str):
        name = os.path.basename(path_or_name)
        stem = os.path.splitext(name)[0]
        m = re.match(r"^(\d+)$", stem) or re.search(r"_(\d+)$", stem)
        return (0, int(m.group(1)), name.lower()) if m else (1, 999999, name.lower())

    def _extract_media_index(self, path_or_name: str) -> Optional[int]:
        name = os.path.basename(path_or_name)
        stem = os.path.splitext(name)[0]
        m = re.match(r"^(\d+)$", stem) or re.search(r"_(\d+)$", stem)
        return int(m.group(1)) if m else None

    def find_carousel_transcript(self, source_id: str, media_index: Optional[int]) -> Optional[str]:
        if media_index is None:
            return None
        if not os.path.isdir(cfg.TRANSCRIPT_CAROUSEL_DIR):
            return None
        folder = self.resolve_prefixed_dir(cfg.TRANSCRIPT_CAROUSEL_DIR, f"IG_{source_id}")
        if not folder or not os.path.isdir(folder):
            return None
        candidates = [
            os.path.join(folder, f"{media_index:02d}.txt"),
            os.path.join(folder, f"{media_index}.txt"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        for f in os.listdir(folder):
            if f.lower().endswith(".txt"):
                stem = os.path.splitext(f)[0]
                if stem == str(media_index) or stem == f"{media_index:02d}":
                    return os.path.join(folder, f)
        return None

    def find_mixed_media_for_source(self, source_id: str) -> List[Dict[str, Any]]:
        """Y.23 : scan multi-plateforme. Avant Y.23, seul `IG_<id>/` était
        cherché dans `01_raw_images/`, donc les images/vidéos téléchargées
        par le fallback gallery-dl Y.21 (X / Threads / Reddit) restaient
        sur disque mais n'étaient pas postées dans le thread forum.
        Désormais on scanne TOUS les préfixes plateforme connus
        (IG_/X_/THREADS_/REDDIT_/YT_/TT_) et le premier dir qui existe et
        contient des médias gagne. Les dossiers sont mutuellement exclusifs
        car les IDs sont uniques par plateforme."""
        items = []
        if not os.path.isdir(cfg.IMAGE_DIR):
            return items
        allowed = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".webm", ".mkv", ".mov")

        # Préfixes uniques (déduplique X_ qui est mappé pour x + twitter).
        try:
            from arsenal_config import PLATFORM_DIR_PREFIXES, IG_POST_DIR_PREFIX
            prefixes = list(set(PLATFORM_DIR_PREFIXES.values()) | {IG_POST_DIR_PREFIX})
        except ImportError:
            prefixes = ["IG_"]
        # IG_ d'abord pour ne pas casser le path Instagram pré-Y.21.
        prefixes.sort(key=lambda p: 0 if p == "IG_" else 1)

        for prefix in prefixes:
            post_dir = self.resolve_prefixed_dir(cfg.IMAGE_DIR, f"{prefix}{source_id}")
            if not post_dir or not os.path.isdir(post_dir):
                continue
            paths = sorted(
                [os.path.join(post_dir, f) for f in os.listdir(post_dir)
                 if os.path.isfile(os.path.join(post_dir, f))
                 and not f.startswith("_")  # exclut _post_text.txt et autres meta
                 and f.lower().endswith(allowed)],
                key=self._media_sort_key,
            )
            for p in paths:
                ext = os.path.splitext(p)[1].lower()
                idx = self._extract_media_index(p)
                kind = "video" if ext in (".mp4", ".webm", ".mkv", ".mov") else "image"
                items.append({
                    "index": idx, "kind": kind, "path": p,
                    "transcript_path": self.find_carousel_transcript(source_id, idx) if kind == "video" else None,
                })
            if items:
                return items

        # Fallback flat (IG legacy carousel pré-Phase E)
        flat = sorted(
            [os.path.join(cfg.IMAGE_DIR, f) for f in os.listdir(cfg.IMAGE_DIR)
             if f.startswith(f"{source_id}_") and f.lower().endswith(allowed)],
            key=self._media_sort_key,
        )
        for p in flat:
            ext = os.path.splitext(p)[1].lower()
            kind = "video" if ext in (".mp4", ".webm", ".mkv", ".mov") else "image"
            items.append({"index": self._extract_media_index(p), "kind": kind, "path": p, "transcript_path": None})
        return items

    # =========================
    # TAGS FORUM
    # =========================
    # Mapping plateforme → tag
    PLATFORM_TAG_MAP = {
        "tiktok":    ("📱TikTok",    discord.Colour.from_str("#000000")),
        "instagram": ("📸Instagram", discord.Colour.from_str("#E1306C")),
        "youtube":   ("▶️YouTube",   discord.Colour.from_str("#FF0000")),
        "x":         ("𝕏Twitter",    discord.Colour.from_str("#1DA1F2")),
        "twitter":   ("𝕏Twitter",    discord.Colour.from_str("#1DA1F2")),
        "reddit":    ("💬Reddit",    discord.Colour.from_str("#FF4500")),
    }

    async def get_or_create_tags(self, forum, metadata, row=None):
        applied_tags = []
        current_map = {tag.name.lower(): tag for tag in forum.available_tags}
        new_list = list(forum.available_tags)

        raw_tags = []

        # 1) Score
        score = metadata["score"]
        if score >= 17:
            raw_tags.append(("⭐>=17", discord.Colour.gold()))
        elif score >= 14:
            raw_tags.append(("✅14-16", discord.Colour.green()))
        elif score >= 10:
            raw_tags.append(("💡10-13", discord.Colour.blue()))
        elif score >= 7:
            raw_tags.append(("⚠️07-09", discord.Colour.orange()))
        else:
            raw_tags.append(("❌<07", discord.Colour.red()))

        # 2) Plateforme
        platform_raw = (metadata.get("platform") or "").strip().lower()
        if platform_raw in self.PLATFORM_TAG_MAP:
            raw_tags.append(self.PLATFORM_TAG_MAP[platform_raw])

        # 3) Type de contenu (depuis CSV resolved_type_final)
        type_raw = ""
        if row:
            type_raw = str(row.get("resolved_type_final") or row.get("type") or "").strip().lower()
        if type_raw in ("video", "vidéo"):
            raw_tags.append(("🎥Vidéo", discord.Colour.from_str("#3498db")))
        elif type_raw in ("carousel", "carrousel"):
            raw_tags.append(("📊Carrousel", discord.Colour.from_str("#9b59b6")))

        # 4) Sous-thème (partie après > dans Classification)
        specific = metadata.get("specific_theme")
        if specific:
            short = specific.split(",")[0].strip()[:20].strip()
            if short:
                raw_tags.append((short, discord.Colour.from_str("#7289da")))

        # 5) Attributs
        if metadata["is_urgent"]:
            raw_tags.append(("🔥Urgent", discord.Colour.red()))
        if metadata["has_sophism"]:
            raw_tags.append(("🧠Sophisme", discord.Colour.purple()))
        if metadata["has_cta"]:
            raw_tags.append(("📢Action", discord.Colour.brand_red()))

        # Dédup (préserve l'ordre de priorité)
        seen, unique = set(), []
        for name, color in raw_tags:
            key = name.lower()
            if key not in seen:
                unique.append((name, color))
                seen.add(key)

        # Création des tags manquants (limite Discord : 20 tags / forum)
        changed = False
        for name, _ in unique:
            if name.lower() not in current_map and len(new_list) < 20:
                new_list.append(discord.ForumTag(name=name, moderated=False))
                changed = True

        if changed:
            # Dedup strict : Discord rejette le PATCH avec 40061 si 2 tags ont
            # le même name après normalisation (cas vu en prod : tags tronqués
            # à 20 chars qui collisionnent avec un tag existant après strip).
            seen_names: set[str] = set()
            deduped = []
            for tag in new_list:
                key = tag.name.lower().strip()
                if not key or key in seen_names:
                    continue
                seen_names.add(key)
                deduped.append(tag)
            # Fallback robuste : si Discord rejette quand même le PATCH (cas
            # mystérieux genre normalisation différente côté serveur), on
            # n'ajoute aucun nouveau tag pour ce thread mais on ne plante
            # pas la sync — meilleure UX que tout bloquer.
            try:
                updated_forum = await forum.edit(available_tags=deduped)
                current_map = {tag.name.lower(): tag for tag in updated_forum.available_tags}
            except discord.HTTPException as e:
                if e.code == 40061 or "40061" in str(e):
                    log.warning(
                        f"40061 sur forum #{forum.name} (Tag names must be unique) "
                        f"— fallback : on garde les tags actuels, on n'en ajoute pas pour ce thread"
                    )
                    # current_map reste tel quel (avant les nouveaux ajouts)
                else:
                    raise

        # Application (ordre = priorité)
        for name, _ in unique:
            if name.lower() in current_map:
                applied_tags.append(current_map[name.lower()])

        # Discord : max 5 tags appliqués par thread
        return [t for t in applied_tags if t][:5]

    # =========================
    # COMMANDES
    # =========================
    @commands.command(name="sync_arsenal")
    @commands.has_permissions(administrator=True)
    async def cmd_sync_arsenal(self, ctx):
        """Lance la synchronisation Arsenal → Discord."""
        if self.is_syncing:
            return await ctx.send("⚠️ Une synchronisation est déjà en cours.")
        await ctx.send("🚀 Synchronisation lancée...")
        self.bot.loop.create_task(self._sync_task())

    @commands.command(name="stats_arsenal")
    @commands.has_permissions(administrator=True)
    async def cmd_stats_arsenal(self, ctx):
        """Affiche les stats du pipeline."""
        self._reload_csv()
        if self.df_suivi is None or self.df_suivi.empty:
            return await ctx.send("❌ CSV vide ou introuvable.")

        dl = self.df_suivi["download_status"].value_counts().to_dict()
        sm = self.df_suivi["summary_status"].value_counts().to_dict()
        sy = self.df_suivi["sync_status"].value_counts().to_dict()

        embed = discord.Embed(title="📊 Arsenal — État du pipeline",
                              color=discord.Color.blue(), timestamp=datetime.now())
        embed.add_field(name="Total lignes", value=str(len(self.df_suivi)), inline=True)
        embed.add_field(name="Download", value=f"✅{dl.get('SUCCESS',0)} ❌{dl.get('FAILED',0)} ⏳{dl.get('PENDING',0)}", inline=True)
        embed.add_field(name="Summary", value=f"✅{sm.get('SUCCESS',0)} ❌{sm.get('FAILED',0)} ⏳{sm.get('PENDING',0)}", inline=True)
        embed.add_field(name="Sync", value=f"✅{sy.get('SUCCESS',0)} ❌{sy.get('FAILED',0)} ⏳{sy.get('PENDING',0)}", inline=True)

        plat = self.df_suivi["plateforme"].value_counts().to_dict()
        embed.add_field(name="Plateformes", value=" | ".join(f"{k}: {v}" for k, v in plat.items()), inline=False)
        embed.set_footer(text="Arsenal Intelligence Unit")
        await ctx.send(embed=embed)

    @commands.command(name="clear_arsenal")
    @commands.has_permissions(administrator=True)
    async def cmd_clear_arsenal(self, ctx):
        """Purge complète : supprime tous les forums et reset sync_status."""
        guild = self.bot.get_guild(self.guild_id)
        category = discord.utils.get(guild.categories, name=self.category_name)
        if not category:
            return await ctx.send(f"❌ Catégorie {self.category_name} introuvable.")

        await self.send_log("🔥 Purge Totale", "Action demandée.", discord.Color.dark_red())
        for channel in category.channels:
            await channel.delete()
        await category.delete()

        if self.df_suivi is not None and not self.df_suivi.empty:
            self.df_suivi["sync_status"] = "PENDING"
            self.df_suivi["sync_timestamp"] = ""
            self.df_suivi["sync_error"] = ""
            self._save_csv()

        await ctx.send("✅ Purge terminée. `sync_status` remis en PENDING.")

    # =========================
    # SYNC TASK
    # =========================
    async def _sync_task(self, only_source_id: Optional[str] = None,
                         silent_if_no_work: bool = False,
                         link_thread: Optional[discord.Thread] = None,
                         wait_if_busy: bool = False,
                         defer_dossier_forwards: bool = False):
        """Synchronise les résumés vers les forums Discord.

        `silent_if_no_work=True` (utilisé par l'auto-sync 15s) → les embeds
        🚀 Lancement et 🏁 Terminée sont supprimés quand aucun thread n'a été
        publié et aucune erreur. Évite le spam #logs quand un batch
        --re-summarize ne fait que rafraîchir des résumés déjà publiés.
        Les erreurs et avertissements (forum manquant, parse fail) sont
        toujours loggés indépendamment du flag.

        `link_thread` (Phase Y.11) : si fourni ET que `only_source_id`
        est défini, l'embed "✅ Dossier indexé" pour ce source_id est
        aussi posté dans `link_thread` (le fil sur le message
        d'origine dans `🔗・liens`).

        `wait_if_busy=True` (Y.17, utilisé par step_sync du pipeline live)
        → si un sync est déjà en cours (typiquement l'auto-sync 15s qui
        vient de gagner la race), attend jusqu'à 5min sa fin avant de
        lancer le sien. Sans ça, le pipeline retourne immédiatement et
        le Dossier indexé n'arrive jamais dans le fil du drop.
        """
        if self.is_syncing:
            if wait_if_busy:
                for _ in range(300):  # 300 × 1s = 5min cap
                    await asyncio.sleep(1)
                    if not self.is_syncing:
                        break
                else:
                    log.warning("_sync_task wait_if_busy : timeout 5min, abandon")
                    return
            else:
                return
        self.is_syncing = True
        emit_lifecycle = not silent_if_no_work

        try:
            await self.bot.wait_until_ready()
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                log.error("Guild introuvable")
                return

            self._reload_csv()
            await self.check_csv_duplicates()
            if emit_lifecycle:
                await self.send_log("🚀 Synchronisation", f"Lancement sur **{guild.name}**.", discord.Color.blue())

            main_category = discord.utils.get(guild.categories, name=self.category_name)
            if not main_category:
                main_category = await guild.create_category(self.category_name)

            synced, failed, skipped = 0, 0, 0

            if not os.path.isdir(cfg.SUMMARY_DIR):
                await self.send_log("⚠️ Dossier résumés vide", cfg.SUMMARY_DIR, discord.Color.orange())
                return

            for summary_filename in os.listdir(cfg.SUMMARY_DIR):
                if not summary_filename.lower().endswith(".txt"):
                    continue

                summary_path = os.path.join(cfg.SUMMARY_DIR, summary_filename)
                try:
                    with open(summary_path, "r", encoding="utf-8") as f:
                        full_summary = f.read()
                except Exception as e:
                    await self.send_log("❌ Erreur lecture", f"{summary_filename}\n`{e}`", discord.Color.red())
                    failed += 1
                    continue

                metadata = self.parse_analysis(full_summary, summary_filename=summary_filename)
                if not metadata:
                    skipped += 1
                    continue

                source_id = metadata["source_id"]
                platform = metadata.get("platform")
                # Y.18 : fallback CSV pour la plateforme si parse_analysis
                # n'a pas pu la déduire (cas SRC_*.txt — X, YouTube, Reddit,
                # Threads — qui n'ont pas de header `PLATEFORME — …`).
                # Sans ce fallback la pmap Y.15 stocke une clé `::source_id`
                # avec plateforme vide, qui rend le forward Dossier indexé
                # impossible à retrouver.
                if not platform:
                    csv_row = self.get_row_by_source_id(source_id)
                    if csv_row and csv_row.get("plateforme"):
                        platform = csv_row["plateforme"]
                        metadata["platform"] = platform
                title = metadata.get("title") or ""

                if only_source_id and str(source_id) != str(only_source_id):
                    skipped += 1
                    continue

                # Y.17 : pipeline live (only_source_id + link_thread) sur
                # cette source précise. On retient ce contexte pour
                # forward le Dossier indexé au fil dans tous les paths
                # skip ci-dessous (race auto-sync vs pipeline = source
                # déjà SUCCESS quand on arrive ici).
                is_live_forward = (
                    only_source_id is not None
                    and link_thread is not None
                    and str(source_id) == str(only_source_id)
                )

                if not self.should_process(summary_filename, metadata):
                    if is_live_forward:
                        await self._forward_dossier_to_fil(metadata, link_thread,
                                                              defer=defer_dossier_forwards)
                    skipped += 1
                    continue

                # Phase Y.15 : check JSON map indépendant du CSV. Évite
                # les dupes quand le CSV est en race condition entre
                # summarize.py et publisher (cas observé : 6 dupes
                # "TotalEnergies" dans économie-et-social).
                pkey = _published_key(platform, source_id)
                pmap = _load_published_threads()
                pmap_entry = pmap.get(pkey)
                if pmap_entry:
                    # Vérifie que le thread existe encore côté Discord
                    try:
                        existing = await self.bot.fetch_channel(int(pmap_entry["thread_id"]))
                        if existing is not None:
                            self.set_sync_status(source_id, platform, "SUCCESS", "")
                            if is_live_forward:
                                await self._forward_dossier_to_fil(metadata, link_thread,
                                                              defer=defer_dossier_forwards)
                            skipped += 1
                            log.debug(f"Y.15 : {pkey} déjà mappé → thread {existing.id}, skip")
                            continue
                    except (discord.NotFound, discord.HTTPException):
                        # Thread supprimé manuellement, retire de la map
                        pmap.pop(pkey, None)
                        _save_published_threads(pmap)

                forum = discord.utils.get(main_category.forums, name=metadata["forum_name"])
                if not forum and len(main_category.channels) < 50:
                    forum = await main_category.create_forum(name=metadata["forum_name"])
                if not forum:
                    self.set_sync_status(source_id, platform, "FAILED", "Forum introuvable/limite atteinte")
                    failed += 1
                    continue

                thread_name = await self.build_thread_name(forum, source_id, title)
                exists = await self.find_existing_thread_by_names(forum, [thread_name, source_id])
                if exists:
                    # Self-heal : ajoute à la map Y.15 puis skip
                    pmap[pkey] = {
                        "thread_id": str(exists.id),
                        "forum_id": str(forum.id),
                        "title": exists.name,
                        "created_at": now_timestamp(),
                        "self_healed": True,
                    }
                    _save_published_threads(pmap)
                    self.set_sync_status(source_id, platform, "SUCCESS", "")
                    skipped += 1
                    continue

                try:
                    row = self.get_row_by_source_id(source_id, platform)
                    tags = await self.get_or_create_tags(forum, metadata, row=row)
                    url = row.get("url") if row else None

                    media_items = self.find_mixed_media_for_source(source_id)
                    transcript_path = self.find_transcript_for_source(source_id)
                    video_legacy = self.find_video_for_source(source_id)

                    v_status = "❌ Absent"
                    initial_files, remaining_chunks, valid_media = [], [], []
                    oversize_count = 0

                    # Header
                    link_text = f"🔗 **Lien source** — <{url}>" if url else "⚠️ *Source introuvable*"
                    if platform:
                        link_text += f"\n🏷️ **Plateforme** — {platform}"
                    if row and row.get("username"):
                        link_text += f"\n👤 **Auteur** — `{row['username']}`"
                    link_text += f"\n🆔 **ID** — `{source_id}`"

                    # Médias
                    if media_items:
                        for item in media_items:
                            try:
                                if os.path.getsize(item["path"]) <= DISCORD_UPLOAD_LIMIT:
                                    valid_media.append(item)
                                else:
                                    oversize_count += 1
                            except OSError:
                                oversize_count += 1

                        if valid_media:
                            file_chunks, current = [], []
                            for item in valid_media:
                                current.append(discord.File(item["path"], filename=os.path.basename(item["path"])))
                                if len(current) == 10:
                                    file_chunks.append(current)
                                    current = []
                            if current:
                                file_chunks.append(current)

                            initial_files = file_chunks[0]
                            remaining_chunks = file_chunks[1:]

                            n_img = sum(1 for x in valid_media if x["kind"] == "image")
                            n_vid = sum(1 for x in valid_media if x["kind"] == "video")
                            total = len(valid_media)

                            if n_vid > 0 and n_img > 0:
                                v_status = f"✅ Carrousel mixte ({total})"
                                link_text += f"\n\n🎞️ **Contenu** — Carrousel mixte ({total} éléments, {n_img} img, {n_vid} vid)"
                            elif n_vid > 0:
                                v_status = f"✅ Vidéo" if total == 1 else f"✅ Carrousel vidéo ({total})"
                                link_text += f"\n\n🎥 **Contenu** — {'Vidéo' if total == 1 else f'Carrousel vidéo ({total})'}"
                            else:
                                v_status = f"✅ Carrousel ({total})"
                                link_text += f"\n\n📸 **Contenu** — Carrousel de {total} diapositives"

                            if oversize_count:
                                link_text += f"\n⚠️ {oversize_count} élément(s) > 10Mo ignoré(s)"
                        else:
                            link_text += "\n*(Tous les médias trop lourds)*"
                            v_status = "⚠️ Trop lourd"

                    elif video_legacy:
                        try:
                            if os.path.getsize(video_legacy) <= DISCORD_UPLOAD_LIMIT:
                                initial_files = [discord.File(video_legacy, filename=os.path.basename(video_legacy))]
                                v_status = "✅ Vidéo"
                                link_text += "\n\n🎥 **Contenu** — Vidéo"
                            else:
                                link_text += "\n*(Média > 10Mo)*"
                                v_status = "⚠️ Trop lourd"
                        except OSError:
                            v_status = "⚠️ Illisible"

                    # Créer le thread
                    try:
                        res = await forum.create_thread(
                            name=thread_name, content=link_text,
                            files=initial_files, applied_tags=tags)
                        thread = res.thread
                        await res.message.pin()

                        total_parts = len(remaining_chunks) + (1 if initial_files else 0)
                        for i, chunk in enumerate(remaining_chunks, 1):
                            await asyncio.sleep(2.5)
                            await thread.send(
                                content=f"🖼️ **Suite carrousel ({i+1}/{max(total_parts,1)})**",
                                files=chunk)

                    except discord.HTTPException as e:
                        if e.code == 40005:
                            res = await forum.create_thread(name=thread_name, content=link_text, applied_tags=tags)
                            thread = res.thread
                            await res.message.pin()
                            await thread.send("⚠️ *Média rejeté par Discord.*")
                        else:
                            raise

                    # Transcriptions slides vidéo carrousel
                    vid_with_tx = [i for i in valid_media if i["kind"] == "video"
                                   and i.get("transcript_path") and os.path.isfile(i["transcript_path"])]
                    if vid_with_tx:
                        await asyncio.sleep(1)
                        await thread.send("📄 **Transcriptions slides vidéo**")
                        for item in vid_with_tx:
                            idx = item.get("index")
                            label = f"{idx:02d}" if isinstance(idx, int) else "??"
                            await asyncio.sleep(1)
                            await thread.send(
                                content=f"🎥 **Slide {label}**",
                                file=discord.File(item["transcript_path"],
                                                  filename=os.path.basename(item["transcript_path"])))

                    # Transcription globale
                    if transcript_path and os.path.isfile(transcript_path):
                        await thread.send(
                            content="📄 **Transcription Whisper**",
                            file=discord.File(transcript_path, filename=os.path.basename(transcript_path)))

                    # Résumé
                    cleaned = self.clean_markdown_lists(full_summary)
                    await thread.send(content="🧠 **Synthèse stratégique**")
                    for chunk in self.split_text(cleaned):
                        await thread.send(content=chunk)
                        await asyncio.sleep(1)

                    self.set_sync_status(source_id, platform, "SUCCESS", "")
                    synced += 1

                    # Phase Y.15 : enregistre le mapping source_id → thread_id
                    # dans la map persistante. Recharge avant écriture pour
                    # éviter d'écraser des entrées concurrentes.
                    pmap_now = _load_published_threads()
                    pmap_now[pkey] = {
                        "thread_id": str(thread.id),
                        "forum_id": str(forum.id),
                        "title": thread.name,
                        "created_at": now_timestamp(),
                    }
                    _save_published_threads(pmap_now)

                    # Phase Y.11 : forward "Dossier indexé" embed dans le
                    # fil du drop si on syncait spécifiquement ce source_id
                    # (pipeline live triggered via step_sync).
                    # Y.22 : si `defer_dossier_forwards=True` (passé par le
                    # pipeline live), le post dans le fil est mis en queue
                    # `_deferred_thread_dossier_posts` pour être flush
                    # APRÈS l'embed `Pipeline terminé`. Le post #logs reste
                    # immédiat dans tous les cas.
                    forward_to_link_thread = (
                        link_thread is not None
                        and only_source_id is not None
                        and str(source_id) == str(only_source_id)
                    )
                    embed_kwargs = {
                        "title": "✅ Dossier indexé",
                        "description": f"ID `{source_id}`\n🔗 [Ouvrir]({thread.jump_url})",
                        "color": discord.Color.green(),
                        "fields": {"Note": f"{metadata['score']}/20",
                                    "Forum": metadata["forum_name"],
                                    "Média": v_status,
                                    "Titre": (title[:80] if title else "N/A")},
                    }
                    if forward_to_link_thread and defer_dossier_forwards:
                        await self.send_log(**embed_kwargs)  # #logs only
                        self._deferred_thread_dossier_posts.append(
                            (link_thread, embed_kwargs)
                        )
                    else:
                        await self.send_log(
                            **embed_kwargs,
                            link_thread=link_thread if forward_to_link_thread else None,
                        )

                    await asyncio.sleep(2.5)

                except Exception as e:
                    self.set_sync_status(source_id, platform, "FAILED", str(e))
                    failed += 1
                    await self.send_log("❌ Erreur", f"`{summary_filename}`\n`{str(e)[:500]}`", discord.Color.red())

            # Fin — silencieux en mode auto-sync si rien n'a été fait
            # (synced=0 et failed=0). Les manual sync (!sync_arsenal)
            # affichent toujours le récap pour la transparence utilisateur.
            if synced > 0 or failed > 0 or emit_lifecycle:
                await self.send_log(
                    "🏁 Sync terminée",
                    f"✅ {synced} publiés | ❌ {failed} erreurs | ⏭️ {skipped} ignorés",
                    discord.Color.green() if failed == 0 else discord.Color.orange())

        except Exception as e:
            log.error(f"Erreur sync globale : {e}")
            await self.send_log("❌ Erreur sync globale", str(e)[:1000], discord.Color.dark_red())

        finally:
            self.is_syncing = False

    # =========================
    # AUTO-SYNC LOOP (TODO 11)
    # =========================
    @tasks.loop(seconds=15)
    async def _auto_sync_loop(self):
        """Poll CSV toutes les 15s. Si mtime changé ET au moins une ligne
        summary=SUCCESS / sync=PENDING → lance _sync_task pour publier les
        résumés frais dans la catégorie ANALYSES POLITIQUES.

        Anti-boucle : si un sync précédent vient de planter sur les mêmes
        IDs (sync_error non vide + sync_status=FAILED en moins de 10 min),
        on ne retente pas pour ne pas spam l'API Discord. Le user peut
        forcer une retry avec `!sync_arsenal`."""
        if self.is_syncing:
            return
        try:
            mtime = os.path.getmtime(cfg.CSV_PATH)
        except OSError:
            return
        if mtime <= self._last_csv_mtime:
            return  # Pas de changement, on ne touche rien
        self._last_csv_mtime = mtime

        self._reload_csv()
        if self.df_suivi is None or self.df_suivi.empty:
            return
        mask = (
            (self.df_suivi["summary_status"].str.upper().str.strip() == "SUCCESS")
            & (self.df_suivi["sync_status"].str.upper().str.strip() == "PENDING")
        )
        n_pending = int(mask.sum())
        if n_pending == 0:
            return

        # Anti-boucle : si une fraction conséquente des FAILED a un sync_timestamp
        # très récent (< 5 min), c'est qu'on est en train de boucler sur le même
        # bug. On suspend l'auto-sync pendant 5 min pour laisser respirer.
        recent_fail_mask = (
            (self.df_suivi["summary_status"].str.upper().str.strip() == "SUCCESS")
            & (self.df_suivi["sync_status"].str.upper().str.strip() == "FAILED")
        )
        if recent_fail_mask.any():
            from datetime import datetime, timedelta
            now = datetime.now()
            cutoff = now - timedelta(minutes=5)
            try:
                ts = pd.to_datetime(self.df_suivi.loc[recent_fail_mask, "sync_timestamp"],
                                     errors="coerce")
                recent_fails = (ts >= cutoff).sum()
                if recent_fails >= 3:
                    log.warning(
                        f"Auto-sync : {recent_fails} échecs récents (<5min), "
                        f"suspension du loop ce tick (cooldown anti-boucle)."
                    )
                    return
            except Exception:
                pass

        log.info(f"Auto-sync : {n_pending} résumé(s) à publier détecté(s)")
        await self._sync_task(silent_if_no_work=True)

    @_auto_sync_loop.before_loop
    async def _before_auto_sync(self):
        await self.bot.wait_until_ready()

    # =========================
    # AUTO-ARCHIVE LOOP (Phase Y.5)
    # =========================
    @tasks.loop(hours=1)
    async def _auto_archive_loop(self):
        """Archive les vieux threads d'ANALYSES POLITIQUES quand la guilde
        approche la limite Discord 1000 threads actifs (serveurs non-boostés).
        Sans ce loop, l'auto-sync échoue avec `400 ... 160006: Maximum number
        of active threads reached` dès que la limite est atteinte (cas vu
        Phase Y.4, 86 erreurs spam dans #logs).

        Stratégie : si total > THRESHOLD (900), archiver les plus anciens
        threads de la catégorie ARSENAL en partant du forum le plus chargé,
        jusqu'à ce que le total redescende à TARGET (800). Archivage =
        thread inactif (reste visible, accessible, indexable Discord) mais
        hors du quota actif. Auto-unarchive si quelqu'un poste dedans.

        Skip si une sync est en cours (`is_syncing`) pour éviter les
        conflits PATCH concurrents sur le même forum.
        """
        if self.is_syncing:
            return
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return
            try:
                active_payload = await guild.active_threads()
            except discord.HTTPException as e:
                log.warning(f"Auto-archive : guild.active_threads() échoué : {e}")
                return
            n_active = len(active_payload)
            if n_active <= self.ARCHIVE_THRESHOLD:
                log.debug(f"Auto-archive : {n_active}/1000 actifs, sous seuil "
                           f"{self.ARCHIVE_THRESHOLD}, skip")
                return

            cat = discord.utils.get(guild.categories, name=self.category_name)
            if not cat:
                log.warning(f"Auto-archive : catégorie {self.category_name!r} introuvable")
                return
            forum_ids = {f.id for f in cat.forums}
            arsenal_threads = [t for t in active_payload if t.parent_id in forum_ids]
            arsenal_threads.sort(key=lambda t: t.id)  # plus vieux d'abord

            target_archive = n_active - self.ARCHIVE_TARGET
            if not arsenal_threads:
                log.warning(f"Auto-archive : aucun thread dans {self.category_name} "
                             f"alors que guilde à {n_active}/1000")
                return

            archived_ok = 0
            failed = 0
            per_forum = {}
            for t in arsenal_threads[:target_archive]:
                try:
                    await t.edit(archived=True, reason="Auto-archive Y.5 — quota Discord")
                    archived_ok += 1
                    per_forum[t.parent_id] = per_forum.get(t.parent_id, 0) + 1
                    await asyncio.sleep(0.5)  # rate limit safety
                except discord.HTTPException as e:
                    failed += 1
                    log.warning(f"Auto-archive : edit thread {t.id} échoué : {e}")
                    if failed >= 5:
                        log.error("Auto-archive : >5 échecs consécutifs, abandon ce tick")
                        break

            if archived_ok == 0:
                return  # rien archivé, pas de log embed (silencieux)

            forum_summary = ", ".join(
                f"{discord.utils.get(cat.forums, id=fid).name}: {n}"
                for fid, n in sorted(per_forum.items(), key=lambda x: -x[1])[:5]
            )
            await self.send_log(
                "🗄️ Auto-archive Arsenal",
                (f"Guilde à **{n_active}/1000** threads actifs (seuil "
                 f"{self.ARCHIVE_THRESHOLD}). **{archived_ok}** threads archivés "
                 f"dans `{self.category_name}` pour redescendre à ~{n_active - archived_ok}.\n"
                 f"Top forums : {forum_summary}\n"
                 f"Threads archivés restent visibles et auto-unarchivent si message posté."),
                discord.Color.blue(),
            )
        except Exception as e:
            log.error(f"Auto-archive erreur : {type(e).__name__}: {e}")

    @_auto_archive_loop.before_loop
    async def _before_auto_archive(self):
        await self.bot.wait_until_ready()

    # =========================
    # COMMANDE MANUELLE — !archive_arsenal
    # =========================
    @commands.command(name="archive_arsenal")
    async def cmd_archive_arsenal(self, ctx, target: int = None):
        """Force un cycle d'auto-archive maintenant. Optionnel : `!archive_arsenal 700`
        pour cibler une autre valeur. Utile si la guilde sature avant le tick horaire."""
        if target is not None:
            if not (100 <= target <= 999):
                return await ctx.send("⚠️ Target doit être entre 100 et 999.")
            old_target = self.ARCHIVE_TARGET
            self.ARCHIVE_TARGET = target
            await ctx.send(f"🗄️ Override target temporaire : {old_target} → {target}")
        await ctx.send("🗄️ Cycle d'archivage manuel lancé...")
        await self._auto_archive_loop()
        if target is not None:
            self.ARCHIVE_TARGET = old_target
        await ctx.send("✅ Cycle terminé (voir #logs pour le récap).")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._auto_sync_started:
            self._auto_sync_started = True
            self._auto_sync_loop.start()
            log.info("Auto-sync loop démarré (poll toutes les 15s, mtime-driven)")
            await self.send_log(
                "🔄 Auto-sync Arsenal — démarré",
                "Le publisher poll le CSV toutes les 15s et publie chaque "
                "nouveau résumé `summary=SUCCESS, sync=PENDING` dans "
                f"`{self.category_name}` automatiquement, sans intervention.",
                discord.Color.blue(),
            )
        if not self._auto_archive_started:
            self._auto_archive_started = True
            self._auto_archive_loop.start()
            log.info(f"Auto-archive loop démarré (toutes les "
                     f"{self.ARCHIVE_INTERVAL_HOURS}h, seuil {self.ARCHIVE_THRESHOLD})")

    def cog_unload(self):
        for loop in (self._auto_sync_loop, self._auto_archive_loop):
            try:
                loop.cancel()
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(ArsenalPublisher(bot))
