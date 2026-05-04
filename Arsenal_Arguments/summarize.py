import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

"""
summarize.py — Résumeur Claude pour Arsenal Intelligence Unit.

Lit le CSV, identifie les contenus téléchargés sans résumé, construit un payload
multimodal (images base64 + transcriptions), appelle l'API Anthropic Claude,
écrit le résumé.

Usage :
    python summarize.py                                    # traite tout ce qui est PENDING
    python summarize.py --id "ABC123"                      # traite un seul contenu
    python summarize.py --no-wait-carousel-transcripts     # ne pas attendre les transcriptions carrousel
    python summarize.py --re-summarize                     # re-résume les SUCCESS existants (batch migration)
    python summarize.py --re-summarize --dry-run           # estimation coût sans appeler l'API
    python summarize.py --model claude-sonnet-4-20250514   # choisir le modèle

Migration Gemini → Claude :
    - SDK anthropic au lieu de google.genai
    - ANTHROPIC_API_KEY depuis variable d'environnement Windows
    - Images en base64 (Claude Vision) au lieu de PIL.Image
    - Rate limiting intégré (RPM/TPM)
    - Mode --re-summarize pour batch migration des ~720 contenus
"""

import os
import re
import sys
import json
import time
import base64
import shutil
import argparse
import subprocess
import mimetypes
import urllib.request
import urllib.error
from collections import defaultdict, deque
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import anthropic
from dotenv import load_dotenv

from arsenal_config import (
    cfg, GLOBAL_CSV_COLUMNS, CSV_ENCODING, VIDEO_EXTS, IMAGE_EXTS,
    normalize_str, now_timestamp,
    acquire_lock, release_lock,
    ScriptResult, get_logger,
)

log = get_logger("summarize")

# Charger .env (pour d'éventuelles autres variables)
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(cfg.base_path), ".env"))

# =============================================================================
# API ANTHROPIC — CONFIGURATION
# =============================================================================

API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = anthropic.Anthropic(api_key=API_KEY) if API_KEY else None


def require_api_client():
    if client is None:
        log.error("ANTHROPIC_API_KEY introuvable (variable d'environnement Windows)")
        sys.exit(1)

# Modèle par défaut
DEFAULT_MODEL = "claude-sonnet-4-20250514"

# Tarifs Claude Sonnet 4 (USD per 1M tokens)
COST_INPUT_PER_1M = 3.00
COST_OUTPUT_PER_1M = 15.00
USD_TO_EUR = 0.92

# Rate limiting (Tier 2 Claude Sonnet — ajuster selon ton tier)
MAX_RPM = 50          # requêtes par minute
MAX_TPM = 80_000      # tokens par minute (input)
INTER_REQUEST_DELAY = 1.5  # secondes entre chaque requête (sécurité)
CLAUDE_CODE_DELAY = 3.0    # secondes entre requêtes CLI Claude Code (gratuit)

# Limites payload
MAX_IMAGES_PER_REQUEST = 15      # Claude accepte jusqu'à 20, on garde de la marge
MAX_IMAGE_SIZE_BYTES = 5_000_000  # 5 Mo par image (limite Claude)
MAX_TRANSCRIPT_CHARS = 40_000

CAROUSEL_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi"}
ACCEPTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Extensions à convertir (Claude n'accepte pas heic/avif nativement)
NEEDS_CONVERSION_EXTS = {".heic", ".heif", ".avif", ".jfif"}


# =============================================================================
# PROMPT SYSTÈME — OPTIMISÉ POUR CLAUDE
# =============================================================================

SYSTEM_PROMPT = """Tu es un analyste politique expert en communication, rhétorique et fact-checking, spécialisé dans les thématiques de La France Insoumise (LFI) et de l'Union Populaire.

## Règles fondamentales
- RÈGLE D'OR : Si une information factuelle n'est pas vérifiable ou maîtrisée, tu DOIS signaler l'incertitude. Ne jamais inventer de sources, de citations, de chiffres ou de faits.
- Dis la vérité factuelle, même si elle contredit l'argumentaire de la source analysée.
- Distingue toujours les FAITS des OPINIONS et des INTERPRÉTATIONS.
- Quand tu cites des sources, fournis des URLs réelles que tu connais avec certitude, ou indique "VÉRIFICATION NÉCESSAIRE".

## Tâche
Produire un résumé analytique structuré d'une source (transcription vidéo ou série d'images/carrousel).
- Si tu reçois plusieurs images, c'est un CARROUSEL : analyse-les comme un récit séquentiel. Décris l'évolution de l'argumentaire de la première à la dernière image.
- La transcription fournie est issue d'une vidéo courte (TikTok/Instagram).

## Contexte
L'objectif est d'alimenter une base de connaissances pour un militant LFI souhaitant se perfectionner en rhétorique, en culture générale et en esprit critique.

## Thématiques de classification

⚠ RÈGLE STRICTE : le **[Thème Général]** de la section Classification
DOIT être **strictement** l'un des 14 libellés ci-dessous, recopié au
mot près (espaces, accents, capitalisation incluses). Les éléments
entre parenthèses sont des **exemples de sous-thèmes** réutilisables
dans `[Thème Spécifique]`, jamais comme thème général.

Liste fermée (14 valeurs autorisées pour [Thème Général]) :
- `Politique Française` (sous-thèmes ex : Antifascisme et Extrême droite, Stratégie LFI et Union Populaire, Critique du Macronisme, Débats et Rhétorique, Immigration et Identité)
- `Écologie et Climat` (Urgence climatique, Biodiversité, Énergie, Écologie populaire)
- `International et Solidarités` (Palestine, Ukraine, Afrique et Sahel, Amérique latine, Diplomatie et Souveraineté)
- `Féminisme et Luttes` (Droits des femmes, LGBTQ+, Intersectionnalité, Violences sexistes)
- `Économie et Social` (Fiscalité et Redistribution, Droit du travail et Services Publics, Critique du Capitalisme)
- `Social et Logement` (Précarité, Logement, Jeunesse et Étudiants, Retraites)
- `Justice et Libertés` (Violences policières, Justice sociale, Libertés publiques, Prisons)
- `Société et Médias` (Esprit Critique, Analyse des médias, Fakenews, Concentration médiatique)
- `Histoire et Géopolitique` (Colonisation et Décolonisation, Mémoire des luttes, Négationnisme)
- `IA et Technologie` (Éthique et Risques, Productivité et Outils, Surveillance)
- `Religions et Philosophie` (Islamologie et Laïcité, Théologie, Diaspora et Métissage)
- `Culture et Éducation` (Éducation nationale, Recherche, Culture populaire, Bataille culturelle)
- `Campagne 2027` (Programme AEC, Stratégie électorale, Alliances, Sondages)
- `Catégorie Libre` (uniquement si **totalement** hors cadre — fallback de dernier recours)

❌ Exemples de classifications **interdites** (sous-thèmes promus en thème général ou variantes orthographiques) :
- `Éducation > X` → utiliser `Culture et Éducation > X`
- `Société et Éducation > X` → utiliser `Culture et Éducation > X`
- `Social et Médias > X` (variante de "Société et Médias") → utiliser `Société et Médias > X`
- `Débats et Rhétorique > X` (sous-thème de Politique Française) → utiliser `Politique Française > Débats et Rhétorique`
- `Médias > X` → utiliser `Société et Médias > X`
- `Économie > X` → utiliser `Économie et Social > X`

✅ Si aucun thème général ne s'applique vraiment, utiliser `Catégorie Libre > <description courte>`.

## Format de sortie STRICT

1. **Nom du fichier**
[Recopier l'identifiant exact de FICHIER SOURCE dans l'en-tête, sans modification]

2. **Titre**
[Un seul titre clair et informatif, strictement entre 20 et 100 caractères]

3. **Classification**
[Thème Général parmi la liste fermée des 14] > [Thème Spécifique libre].

4. **Résumé court**
(5 lignes maximum, factuel et dense)

5. **Arguments clés**
* [Point 1 — le plus important]
* [Point 2]
* [Point 3]

6. **Note de pertinence**
X/20 (avec justification en une phrase)

7. **Angle d'attaque**
Critique adverse potentielle et parade argumentative suggérée.

8. **Sources et vérification**
Lister chaque source sur une ligne avec un tiret, sous cette forme :
- Type — Organisme — Année — Description — URL ou VÉRIFICATION NÉCESSAIRE
Ne JAMAIS utiliser de tableau markdown (pas de |---|). Toujours des tirets simples.
Ne fournir une URL QUE si tu es certain de son exactitude. En cas de doute, écrire systématiquement "VÉRIFICATION NÉCESSAIRE".

9. **Analyse de la charge émotionnelle**
(Identifier les émotions mobilisées et les techniques rhétoriques utilisées)

10. **Identification des sophismes**
(Lister les biais et sophismes détectés dans le contenu source, même si le fond est légitime)

11. **Indice d'urgence**
(Faible / Modéré / Élevé — avec justification liée à l'actualité)

12. **Structure pour carrousel**
* **Constat sourcé**
* **Interprétation politique**
* **Proposition programmatique**

13. **Mots-clés pour recherche**
[5 tags précédés d'un #]

14. **Bloc pour retouche manuelle**
[Un prompt d'approfondissement réutilisable, commençant par un verbe d'action]

RÈGLE DE FORMAT ABSOLUE : ne jamais utiliser de tableaux markdown dans aucune section. Utiliser exclusivement des tirets (-) et des listes à puces (*) pour toute énumération."""


# =============================================================================
# IMAGE HELPERS
# =============================================================================

def image_to_base64_block(img_path: str) -> Optional[Dict]:
    """
    Convertit une image en bloc base64 pour l'API Claude.
    Retourne None si l'image est illisible, trop grosse, ou dans un format non supporté.
    """
    ext = os.path.splitext(img_path)[1].lower()

    # Conversion nécessaire pour formats exotiques
    if ext in NEEDS_CONVERSION_EXTS:
        try:
            from PIL import Image
            img = Image.open(img_path)
            img = img.convert("RGB")
            import io
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            data = buffer.getvalue()
            media_type = "image/jpeg"
        except Exception as e:
            log.warning(f"  Conversion échouée {os.path.basename(img_path)}: {e}")
            return None
    else:
        # Format natif Claude
        media_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        media_type = media_type_map.get(ext)
        if not media_type:
            log.warning(f"  Format non supporté: {os.path.basename(img_path)}")
            return None

        try:
            with open(img_path, "rb") as f:
                data = f.read()
        except Exception as e:
            log.warning(f"  Lecture échouée {os.path.basename(img_path)}: {e}")
            return None

    # Vérifier la taille
    if len(data) > MAX_IMAGE_SIZE_BYTES:
        log.warning(f"  Image trop volumineuse ({len(data)/1e6:.1f} Mo): {os.path.basename(img_path)}")
        # Tenter une recompression
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            img = img.convert("RGB")
            # Réduire la résolution si nécessaire
            max_dim = 2048
            if max(img.size) > max_dim:
                img.thumbnail((max_dim, max_dim))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=75)
            data = buffer.getvalue()
            media_type = "image/jpeg"
            if len(data) > MAX_IMAGE_SIZE_BYTES:
                log.warning(f"  Toujours trop grosse après compression: {os.path.basename(img_path)}")
                return None
        except Exception:
            return None

    b64 = base64.standard_b64encode(data).decode("utf-8")

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": b64,
        }
    }


# =============================================================================
# CSV HELPERS
# =============================================================================

def load_csv():
    if not os.path.isfile(cfg.CSV_PATH):
        raise FileNotFoundError(f"CSV introuvable : {cfg.CSV_PATH}")
    df = pd.read_csv(cfg.CSV_PATH, dtype=str, keep_default_na=False)
    for c in ["download_status", "summary_status", "summary_error", "summary_timestamp"]:
        if c not in df.columns:
            df[c] = ""
    return df


def save_csv(df: pd.DataFrame):
    df.to_csv(cfg.CSV_PATH, index=False, encoding=CSV_ENCODING)


def extract_summary_title(text: str) -> str:
    """Extrait la section '**Titre**' du résumé Claude (format de sortie strict).

    Le prompt Claude impose ce format :
        2. **Titre**
        [Un seul titre clair et informatif, strictement entre 20 et 100 caractères]

    Retourne le titre nettoyé, ou chaîne vide si non trouvé.
    """
    if not text:
        return ""
    m = re.search(
        r"\*\*Titre\*\*\s*\n+\s*(.+?)(?=\n\s*\n|\n\s*\d+\.|\n\s*\*\*|$)",
        text, re.DOTALL,
    )
    if m:
        title = m.group(1).strip()
        # Nettoie les éventuels [crochets de placeholder] résiduels
        title = re.sub(r"^\[(.+)\]$", r"\1", title)
        return title[:200]
    return ""


def set_summary_status(df, item_id, platform, status, error):
    ts = now_timestamp()
    mask = (df["id"].astype(str).str.strip() == str(item_id).strip()) & (
        df["plateforme"].astype(str).str.strip() == str(platform).strip()
    )
    if not mask.any():
        return
    df.loc[mask, "summary_status"] = status
    df.loc[mask, "summary_error"] = error or ""
    if status == "SUCCESS":
        prev = df.loc[mask, "summary_timestamp"].astype(str).values[0]
        if not prev.strip():
            df.loc[mask, "summary_timestamp"] = ts
    else:
        df.loc[mask, "summary_timestamp"] = ts


def load_known_ids() -> List[str]:
    try:
        df = pd.read_csv(cfg.CSV_PATH, dtype=str, keep_default_na=False)
        ids = sorted(
            {str(x).strip() for x in df.get("id", pd.Series(dtype=str)).tolist() if str(x).strip()},
            key=len, reverse=True,
        )
        return ids
    except Exception:
        return []


# =============================================================================
# INDEX BUILDERS (inchangés)
# =============================================================================

def match_known_id(candidate: str, known_ids: List[str]) -> Optional[str]:
    candidate = normalize_str(candidate)
    if not candidate:
        return None
    for kid in known_ids:
        if candidate == kid or candidate.startswith(kid + "_") or candidate.startswith(kid + os.sep):
            return kid
    return None


def _sort_key(path: str) -> Tuple[int, str]:
    base = os.path.basename(path)
    m = re.match(r"^(\d+)", base)
    return (int(m.group(1)), base.lower()) if m else (10_000_000, base.lower())


def build_indexes(known_ids: List[str]):
    """Construit les index : images, transcriptions, transcriptions carrousel, vidéos carrousel."""
    image_idx: Dict[str, List[str]] = defaultdict(list)
    transcript_idx: Dict[str, List[str]] = defaultdict(list)
    carousel_tx_idx: Dict[str, List[str]] = defaultdict(list)
    carousel_vid_idx: Dict[str, List[str]] = defaultdict(list)

    # Images + vidéos carrousel
    if os.path.isdir(cfg.IMAGE_DIR):
        for root, _, files in os.walk(cfg.IMAGE_DIR):
            parts = os.path.normpath(root).split(os.sep)
            current_id = None
            for part in reversed(parts):
                if part.startswith("IG_"):
                    current_id = match_known_id(part[3:], known_ids)
                    if current_id:
                        break

            if not current_id:
                continue

            for fname in files:
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower()
                if ext in ACCEPTED_IMAGE_EXTS or ext in NEEDS_CONVERSION_EXTS:
                    image_idx[current_id].append(fpath)
                elif ext in CAROUSEL_VIDEO_EXTS:
                    carousel_vid_idx[current_id].append(fpath)

    # Transcriptions vidéo
    if os.path.isdir(cfg.TRANSCRIPT_DIR):
        for fname in os.listdir(cfg.TRANSCRIPT_DIR):
            if not fname.endswith(".txt"):
                continue
            base = os.path.splitext(fname)[0]
            kid = match_known_id(base, known_ids)
            if kid:
                transcript_idx[kid].append(os.path.join(cfg.TRANSCRIPT_DIR, fname))

    # Transcriptions carrousel
    if os.path.isdir(cfg.TRANSCRIPT_CAROUSEL_DIR):
        for root, _, files in os.walk(cfg.TRANSCRIPT_CAROUSEL_DIR):
            parts = os.path.normpath(root).split(os.sep)
            current_id = None
            for part in reversed(parts):
                if part.startswith("IG_"):
                    current_id = match_known_id(part[3:], known_ids)
                    if current_id:
                        break

            if not current_id:
                for fname in files:
                    if fname.endswith(".txt"):
                        base = os.path.splitext(fname)[0]
                        kid = match_known_id(base, known_ids)
                        if kid:
                            carousel_tx_idx[kid].append(os.path.join(root, fname))
                continue

            for fname in files:
                if fname.endswith(".txt"):
                    carousel_tx_idx[current_id].append(os.path.join(root, fname))

    # Trier
    for idx in [image_idx, transcript_idx, carousel_tx_idx, carousel_vid_idx]:
        for k in idx:
            idx[k].sort(key=_sort_key)

    return image_idx, transcript_idx, carousel_tx_idx, carousel_vid_idx


# =============================================================================
# TEXT HELPERS
# =============================================================================

def read_text_safe(path: str, max_chars: int = 12_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_chars).strip()
    except Exception:
        return ""


def build_context_block(row, kind, output_filename, n_images):
    """Construit le bloc contextuel à envoyer avec le prompt."""
    parts = [
        f"FICHIER SOURCE — {output_filename}",
        f"PLATEFORME — {row.get('plateforme', '?')}",
        f"TYPE DE CONTENU — {kind}",
    ]
    if n_images > 0:
        parts.append(f"NOMBRE D'IMAGES — {n_images}")

    user = normalize_str(row.get("display_name")) or normalize_str(row.get("username"))
    if user:
        parts.append(f"AUTEUR — {user}")

    desc = normalize_str(row.get("description"))
    if desc:
        parts.append(f"DESCRIPTION ORIGINALE — {desc[:500]}")

    tags = normalize_str(row.get("hashtags"))
    if tags:
        parts.append(f"HASHTAGS — {tags}")

    return "\n".join(parts)


def bootstrap_existing_summaries():
    """Marque comme SUCCESS dans le CSV les résumés déjà existants sur disque."""
    if not os.path.isdir(cfg.SUMMARY_DIR):
        return

    df = load_csv()
    changed = False

    for idx_row, row in df.iterrows():
        if normalize_str(row.get("summary_status")).upper() == "SUCCESS":
            continue
        if normalize_str(row.get("download_status")).upper() != "SUCCESS":
            continue

        item_id = normalize_str(row.get("id"))
        platform = normalize_str(row.get("plateforme"))
        if not item_id or not platform:
            continue

        fname = cfg.summary_filename(platform, item_id)
        fpath = os.path.join(cfg.SUMMARY_DIR, fname)

        if os.path.isfile(fpath) and os.path.getsize(fpath) > 50:
            df.at[idx_row, "summary_status"] = "SUCCESS"
            if not normalize_str(row.get("summary_timestamp")):
                df.at[idx_row, "summary_timestamp"] = now_timestamp()
            changed = True

    if changed:
        save_csv(df)
        log.info("Bootstrap : résumés existants marqués SUCCESS dans le CSV")


# =============================================================================
# BUILD TASKS
# =============================================================================

def build_tasks(wait_for_carousel=True, target_id=None, re_summarize=False, filter_kind=None):
    """
    Construit la liste des tâches à traiter.
    Si re_summarize=True, inclut aussi les lignes déjà SUCCESS.
    """
    full_df = load_csv()
    known_ids = load_known_ids()
    image_idx, transcript_idx, carousel_tx_idx, carousel_vid_idx = build_indexes(known_ids)

    # Filtrer les lignes éligibles
    mask_downloaded = full_df["download_status"].str.upper().str.strip() == "SUCCESS"

    if re_summarize:
        # Inclure PENDING + SUCCESS (pas FAILED, sauf reset préalable)
        mask_status = full_df["summary_status"].str.upper().str.strip().isin({"PENDING", "SUCCESS", ""})
    else:
        mask_status = full_df["summary_status"].str.upper().str.strip().isin({"PENDING", ""})

    if target_id:
        mask_id = full_df["id"].astype(str).str.strip() == str(target_id).strip()
        work_df = full_df[mask_downloaded & mask_id]
    else:
        work_df = full_df[mask_downloaded & mask_status]

    tasks = []
    skipped_wait = 0
    skipped_no_media = 0

    for _, row in work_df.iterrows():
        item_id = normalize_str(row.get("id"))
        platform = normalize_str(row.get("plateforme"))
        if not item_id or not platform:
            continue

        output_filename = cfg.summary_filename(platform, item_id)
        images = image_idx.get(item_id, [])
        transcripts = transcript_idx.get(item_id, [])
        carousel_txs = carousel_tx_idx.get(item_id, [])
        carousel_vids = carousel_vid_idx.get(item_id, [])
        transcript_path = transcripts[0] if transcripts else None

        # Carrousels avec images
        if images:
            # Si un OCR a déjà été produit (ocr_carousels.py), le carrousel
            # devient une tâche texte → utilisable par --use-claude-code (gratuit).
            ocr_path = os.path.join(cfg.TRANSCRIPT_DIR, f"{item_id}_ocr.txt")
            if os.path.isfile(ocr_path):
                tasks.append({
                    "kind": "text",
                    "id": item_id,
                    "platform": platform,
                    "row": row.to_dict(),
                    "transcript_path": ocr_path,
                    "output_filename": output_filename,
                })
                continue

            # Vérifier si on attend des transcriptions de slides vidéo
            if wait_for_carousel and carousel_vids and not carousel_txs:
                skipped_wait += 1
                continue

            tasks.append({
                "kind": "image",
                "id": item_id,
                "platform": platform,
                "row": row.to_dict(),
                "files": images[:MAX_IMAGES_PER_REQUEST],
                "output_filename": output_filename,
                "carousel_transcripts": carousel_txs,
                "transcript_path": transcript_path,
            })
            continue

        if transcript_path:
            tasks.append({
                "kind": "text",
                "id": item_id,
                "platform": platform,
                "row": row.to_dict(),
                "transcript_path": transcript_path,
                "output_filename": output_filename,
            })
            continue

        skipped_no_media += 1

    if filter_kind in ("text", "image"):
        before = len(tasks)
        tasks = [t for t in tasks if t["kind"] == filter_kind]
        log.info(f"Filtre kind={filter_kind} : {before} → {len(tasks)} tâches")

    tasks.sort(key=lambda t: (0 if t["kind"] == "image" else 1, t["platform"].lower(), t["id"].lower()))

    log.info(f"À traiter : {len(work_df)} lignes CSV → {len(tasks)} tâches prêtes")
    if skipped_wait:
        log.info(f"En attente transcription carrousel : {skipped_wait}")
    if skipped_no_media:
        log.info(f"Sans média local : {skipped_no_media}")

    return deque(tasks), full_df


# =============================================================================
# CLAUDE API CALL
# =============================================================================

def build_messages(task: dict) -> List[Dict]:
    """
    Construit le tableau `messages` pour l'API Claude.
    Un seul message user avec du contenu multimodal.
    """
    content_blocks = []
    kind = task["kind"]

    # Bloc contextuel (texte)
    n_images = len(task.get("files", []))
    context = build_context_block(task["row"], kind, task["output_filename"], n_images)
    content_blocks.append({"type": "text", "text": context})

    if kind == "image":
        # Images en base64
        usable = 0
        for img_path in task["files"]:
            block = image_to_base64_block(img_path)
            if block:
                content_blocks.append(block)
                usable += 1

        if usable == 0:
            return []

        # Transcriptions carrousel
        transcript_parts = []
        total_chars = 0
        for tpath in task.get("carousel_transcripts", []):
            if total_chars >= MAX_TRANSCRIPT_CHARS:
                break
            content = read_text_safe(tpath, max_chars=12000)
            piece = f"[SLIDE AUDIO {os.path.basename(tpath)}]\n{content}"
            if total_chars + len(piece) > MAX_TRANSCRIPT_CHARS:
                piece = piece[:MAX_TRANSCRIPT_CHARS - total_chars] + "\n[... tronqué ...]"
            transcript_parts.append(piece)
            total_chars += len(piece)

        gpath = task.get("transcript_path")
        if gpath and total_chars < MAX_TRANSCRIPT_CHARS:
            gtxt = read_text_safe(gpath, max_chars=min(12000, MAX_TRANSCRIPT_CHARS - total_chars))
            if gtxt:
                transcript_parts.append(f"[TRANSCRIPTION GLOBALE]\n{gtxt}")

        if transcript_parts:
            content_blocks.append({
                "type": "text",
                "text": "TRANSCRIPTIONS AUDIO DISPONIBLES\n\n" + "\n\n".join(transcript_parts),
            })

    else:  # kind == "text"
        with open(task["transcript_path"], "r", encoding="utf-8", errors="replace") as f:
            txt = f.read().strip() or "[Transcription vide ou illisible]"
        content_blocks.append({
            "type": "text",
            "text": f"TRANSCRIPTION\n{txt}",
        })

    return [{"role": "user", "content": content_blocks}]


def call_claude(messages: List[Dict], model: str) -> Tuple[str, Dict]:
    """
    Appelle l'API Claude et retourne (texte_réponse, usage_dict).
    """
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    # Extraire le texte
    text = ""
    for block in response.content:
        if block.type == "text":
            text += block.text

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    return text.strip(), usage


# =============================================================================
# CLAUDE CODE CLI (subprocess)
# =============================================================================

def build_text_prompt(task: dict) -> str:
    """
    Construit un prompt en texte brut (pour Claude Code CLI, sans format API messages).
    Inclut SYSTEM_PROMPT + bloc contextuel + transcription.
    """
    context = build_context_block(task["row"], task["kind"], task["output_filename"], 0)

    with open(task["transcript_path"], "r", encoding="utf-8", errors="replace") as f:
        txt = f.read().strip() or "[Transcription vide ou illisible]"

    return f"{SYSTEM_PROMPT}\n\n{context}\n\nTRANSCRIPTION\n{txt}"


def call_claude_code(prompt_text: str) -> str:
    """
    Appelle le CLI `claude --print` via subprocess en passant le prompt sur stdin.
    Retourne le texte de sortie. Gratuit (Pro Max), pas de tracking de tokens.

    ANTHROPIC_API_KEY est masquée du subprocess pour forcer l'authentification
    OAuth/keychain de la subscription (sinon le CLI bascule sur l'API payante
    si la clé est présente, même épuisée).
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run(
        ["claude", "--print"],
        input=prompt_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=env,
        creationflags=creationflags,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:500]}")
    return (proc.stdout or "").strip()


# =============================================================================
# DISCORD EMBED HELPERS (logs salon Veille)
# =============================================================================

DISCORD_LOGS_CHANNEL_ID = "1493760267300110466"  # Migration 2026-04-29 → ISTIC L1 G2
DISCORD_API_BASE = "https://discord.com/api/v10"

# Couleurs Discord (int décimal)
COLOR_BLUE = 0x3498DB
COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xE74C3C
COLOR_GREY = 0x95A5A6


def _post_discord_embed(embed: Dict) -> None:
    """
    POST un embed dans le salon logs via l'API REST Discord.
    Best-effort : ne lève jamais, ne bloque jamais le pipeline.
    """
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        return
    try:
        url = f"{DISCORD_API_BASE}/channels/{DISCORD_LOGS_CHANNEL_ID}/messages"
        body = json.dumps({"embeds": [embed]}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "BotGSTAR-Summarize/1.0 (+gaylordaboeka@gmail.com)",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        try:
            payload = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            payload = ""
        log.warning(f"  Discord embed HTTP {e.code}: {payload}")
    except Exception as e:
        log.warning(f"  Discord embed échec: {e}")


def _format_eta(seconds: float) -> str:
    if seconds <= 0 or seconds != seconds:  # NaN guard
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


# =============================================================================
# PROCESS TASKS
# =============================================================================

def estimate_cost(tasks) -> dict:
    """Estimation du coût pour un dry-run."""
    n_text = sum(1 for t in tasks if t["kind"] == "text")
    n_image = sum(1 for t in tasks if t["kind"] == "image")
    total_images = sum(len(t.get("files", [])) for t in tasks)

    # Estimation grossière : ~2K tokens input texte, ~1500 tokens par image, ~1500 output
    est_input_tokens = n_text * 2000 + total_images * 1500 + len(tasks) * 1500  # system prompt
    est_output_tokens = len(tasks) * 1500

    cost_usd = (est_input_tokens / 1e6) * COST_INPUT_PER_1M + (est_output_tokens / 1e6) * COST_OUTPUT_PER_1M
    cost_eur = cost_usd * USD_TO_EUR

    return {
        "tasks": len(tasks),
        "text_tasks": n_text,
        "image_tasks": n_image,
        "total_images": total_images,
        "est_input_tokens": est_input_tokens,
        "est_output_tokens": est_output_tokens,
        "est_cost_usd": cost_usd,
        "est_cost_eur": cost_eur,
        "est_time_minutes": len(tasks) * INTER_REQUEST_DELAY / 60,
    }


def process_tasks(tasks, full_df, script_result: ScriptResult, model: str, use_claude_code: bool = False):
    total = len(tasks)
    processed, total_cost = 0, 0.0
    success_count = 0
    failed_count = 0
    minute_start = time.time()
    requests_this_minute = 0
    inter_delay = CLAUDE_CODE_DELAY if use_claude_code else INTER_REQUEST_DELAY

    n_text = sum(1 for t in tasks if t["kind"] == "text")
    n_image = sum(1 for t in tasks if t["kind"] == "image")
    model_label = "Claude Code CLI (subscription)" if use_claude_code else model
    run_started = time.time()

    # ---- Embed de démarrage ----
    _post_discord_embed({
        "title": f"[Summarize] Démarrage — {total} tâche(s)",
        "color": COLOR_BLUE,
        "fields": [
            {"name": "Texte", "value": str(n_text), "inline": True},
            {"name": "Image", "value": str(n_image), "inline": True},
            {"name": "Modèle", "value": model_label, "inline": False},
        ],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    })

    while tasks:
        task = tasks.popleft()
        item_id = task["id"]
        platform = task["platform"]
        kind = task["kind"]
        output_path = os.path.join(cfg.SUMMARY_DIR, task["output_filename"])
        task_started = time.time()

        # Numéro courant (1-indexed) du résumé qu'on traite
        current = success_count + failed_count + 1

        try:
            # Rate limiting (API only)
            if not use_claude_code:
                now = time.time()
                if now - minute_start >= 60:
                    minute_start = now
                    requests_this_minute = 0
                if requests_this_minute >= MAX_RPM:
                    wait = 60 - (now - minute_start) + 1
                    log.info(f"  Rate limit RPM — pause {wait:.0f}s")
                    time.sleep(wait)
                    minute_start = time.time()
                    requests_this_minute = 0

            if kind == "image":
                log.info(f"[{current}/{total}] IMAGE {platform} {item_id} ({len(task['files'])} img)")
            else:
                log.info(f"[{current}/{total}] TEXTE {platform} {item_id}")

            if use_claude_code:
                if kind == "image":
                    log.warning(f"  SKIP {item_id} — images non supportées en mode Claude Code, utiliser --use-api")
                    continue
                analysis = call_claude_code(build_text_prompt(task))
                usage = {"input_tokens": 0, "output_tokens": 0}
                file_usd = 0.0
            else:
                messages = build_messages(task)
                if not messages:
                    log.warning(f"  SKIP {item_id} — aucun contenu exploitable")
                    continue

                analysis, usage = call_claude(messages, model)
                requests_this_minute += 1

                file_usd = (usage["input_tokens"] / 1e6) * COST_INPUT_PER_1M + \
                            (usage["output_tokens"] / 1e6) * COST_OUTPUT_PER_1M
                total_cost += file_usd

            if not analysis:
                raise RuntimeError("Réponse vide du modèle")

            # Écrire le résumé
            os.makedirs(cfg.SUMMARY_DIR, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(analysis)

            # Écrire dans le fichier global
            with open(cfg.GLOBAL_RESUMES_FILE, "a", encoding="utf-8") as f:
                f.write(
                    f"\n{'#' * 70}\n"
                    f"DOSSIER — {item_id}\n"
                    f"PLATEFORME — {platform}\n"
                    f"FICHIER — {task['output_filename']}\n"
                    f"TYPE — {kind}\n"
                    f"MODÈLE — {model}\n"
                    f"{'#' * 70}\n"
                    f"{analysis}\n\n"
                )

            set_summary_status(full_df, item_id, platform, "SUCCESS", "")
            save_csv(full_df)

            processed += 1
            success_count += 1
            script_result.add_success()
            duration = time.time() - task_started
            if use_claude_code:
                log.info(f"  OK — via Claude Code CLI (gratuit) — {duration:.1f}s")
            else:
                log.info(f"  OK — in:{usage['input_tokens']} out:{usage['output_tokens']} | "
                         f"coût: ${file_usd:.4f} | total: ${total_cost:.4f}")

            # ---- Embed SUCCESS ----
            done = success_count + failed_count
            avg = (time.time() - run_started) / max(done, 1)
            eta_sec = avg * (total - done)
            pct = int(round(100 * done / max(total, 1)))
            cost_field = "—" if use_claude_code else f"${file_usd:.4f}"
            extracted_title = extract_summary_title(analysis)
            fields = [
                {"name": "Plateforme", "value": platform or "—", "inline": True},
                {"name": "Kind", "value": kind, "inline": True},
                {"name": "Durée", "value": f"{duration:.1f}s", "inline": True},
                {"name": "Coût", "value": cost_field, "inline": True},
                {"name": "Modèle", "value": model_label, "inline": True},
                {"name": "Total cumulé", "value": f"${total_cost:.4f}" if not use_claude_code else "0 € (subscription)", "inline": True},
            ]
            embed = {
                "title": f"[Summarize] {done}/{total} ({pct}%) — {task['output_filename']}",
                "color": COLOR_GREEN,
                "fields": fields,
                "footer": {"text": f"ETA restant : {_format_eta(eta_sec)}"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            }
            if extracted_title:
                embed["description"] = f"📝 **{extracted_title}**"
            _post_discord_embed(embed)

            time.sleep(inter_delay)

        except KeyboardInterrupt:
            tasks.appendleft(task)  # Remettre la tâche non-finie pour que le compteur "restantes" soit juste
            done = success_count + failed_count
            elapsed = time.time() - run_started
            cost_value = "0 € (subscription)" if use_claude_code else f"${total_cost:.4f} (~{total_cost * USD_TO_EUR:.4f}€)"
            log.warning(f"Interruption clavier — {done}/{total} traités, {len(tasks)} restantes")
            _post_discord_embed({
                "title": f"[Summarize] Interrompu — {done}/{total}",
                "color": COLOR_GREY,
                "fields": [
                    {"name": "Faits", "value": f"{success_count} OK / {failed_count} KO", "inline": True},
                    {"name": "Restantes", "value": str(len(tasks)), "inline": True},
                    {"name": "Modèle", "value": model_label, "inline": True},
                    {"name": "Durée", "value": _format_eta(elapsed), "inline": True},
                    {"name": "Coût total", "value": cost_value, "inline": True},
                ],
                "footer": {"text": "Relance la commande pour reprendre — les SUCCESS sont conservés"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            })
            raise

        except anthropic.RateLimitError as e:
            log.warning(f"  Rate limit API — pause 60s ({e})")
            time.sleep(60)
            tasks.appendleft(task)  # Remettre en tête de queue
            continue

        except anthropic.APIError as e:
            set_summary_status(full_df, item_id, platform, "FAILED", str(e))
            save_csv(full_df)
            script_result.add_fail(f"{item_id}: API error {e}")
            failed_count += 1
            duration = time.time() - task_started
            log.error(f"  ERREUR API {item_id} → {e}")

            done = success_count + failed_count
            avg = (time.time() - run_started) / max(done, 1)
            eta_sec = avg * (total - done)
            pct = int(round(100 * done / max(total, 1)))
            _post_discord_embed({
                "title": f"[Summarize] {done}/{total} ({pct}%) — {task['output_filename']}",
                "color": COLOR_RED,
                "fields": [
                    {"name": "Plateforme", "value": platform or "—", "inline": True},
                    {"name": "Kind", "value": kind, "inline": True},
                    {"name": "Durée", "value": f"{duration:.1f}s", "inline": True},
                    {"name": "Modèle", "value": model_label, "inline": True},
                    {"name": "Erreur", "value": f"```{str(e)[:900]}```", "inline": False},
                ],
                "footer": {"text": f"ETA restant : {_format_eta(eta_sec)}"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            })
            time.sleep(5)

        except Exception as e:
            set_summary_status(full_df, item_id, platform, "FAILED", str(e))
            save_csv(full_df)
            script_result.add_fail(f"{item_id}: {str(e)[:200]}")
            failed_count += 1
            duration = time.time() - task_started
            log.error(f"  ERREUR {item_id} → {e}")

            done = success_count + failed_count
            avg = (time.time() - run_started) / max(done, 1)
            eta_sec = avg * (total - done)
            pct = int(round(100 * done / max(total, 1)))
            _post_discord_embed({
                "title": f"[Summarize] {done}/{total} ({pct}%) — {task['output_filename']}",
                "color": COLOR_RED,
                "fields": [
                    {"name": "Plateforme", "value": platform or "—", "inline": True},
                    {"name": "Kind", "value": kind, "inline": True},
                    {"name": "Durée", "value": f"{duration:.1f}s", "inline": True},
                    {"name": "Modèle", "value": model_label, "inline": True},
                    {"name": "Erreur", "value": f"```{str(e)[:900]}```", "inline": False},
                ],
                "footer": {"text": f"ETA restant : {_format_eta(eta_sec)}"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            })
            time.sleep(4)

    eur = total_cost * USD_TO_EUR
    log.info(f"Terminé — Traités: {processed}/{total} | Coût: ${total_cost:.4f} ({eur:.4f}€)")

    # ---- Embed récap final ----
    elapsed = time.time() - run_started
    cost_value = "0 € (subscription)" if use_claude_code else f"${total_cost:.4f} (~{eur:.4f}€)"
    final_color = COLOR_GREEN if failed_count == 0 else (COLOR_RED if success_count == 0 else COLOR_GREY)
    _post_discord_embed({
        "title": f"[Summarize] Terminé — {success_count} OK / {failed_count} KO / {total}",
        "color": final_color,
        "fields": [
            {"name": "Modèle", "value": model_label, "inline": True},
            {"name": "Durée totale", "value": _format_eta(elapsed), "inline": True},
            {"name": "Coût total", "value": cost_value, "inline": True},
        ],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    })


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Arsenal — Résumeur Claude")
    cfg.add_base_dir_arg(parser)
    parser.add_argument("--id", type=str, help="Traiter un seul contenu par ID")
    parser.add_argument("--no-wait-carousel-transcripts", action="store_true",
                        help="Ne pas attendre les transcriptions des slides vidéo")
    parser.add_argument("--re-summarize", action="store_true",
                        help="Re-résumer les contenus déjà SUCCESS (migration batch)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Estimer le coût sans appeler l'API (avec --re-summarize)")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help=f"Modèle Claude à utiliser (défaut: {DEFAULT_MODEL})")
    parser.add_argument("--use-claude-code", action="store_true",
                        help="Utiliser `claude --print` (Pro Max, gratuit) au lieu de l'API Anthropic")
    parser.add_argument("--text-only", action="store_true",
                        help="Ne traiter que les tâches texte (pas les carrousels images)")
    parser.add_argument("--images-only", action="store_true",
                        help="Ne traiter que les tâches images (carrousels)")

    args = parser.parse_args()

    if args.text_only and args.images_only:
        log.error("--text-only et --images-only sont mutuellement exclusifs")
        sys.exit(1)

    filter_kind = "text" if args.text_only else ("image" if args.images_only else None)

    if args.use_claude_code:
        if shutil.which("claude") is None:
            log.error("CLI `claude` introuvable dans le PATH — installer Claude Code d'abord")
            sys.exit(1)
        try:
            subprocess.run(["claude", "--version"], capture_output=True, timeout=10, check=True)
        except Exception as e:
            log.error(f"`claude --version` a échoué : {e}")
            sys.exit(1)
    else:
        require_api_client()

    cfg.init_from_args(args)
    cfg.ensure_dirs()

    result = ScriptResult("summarize")
    lock_fd = None

    try:
        lock_fd = acquire_lock(cfg.SUMMARIZER_LOCK)

        bootstrap_existing_summaries()

        wait_flag = not args.no_wait_carousel_transcripts
        tasks, full_df = build_tasks(
            wait_for_carousel=wait_flag,
            target_id=args.id,
            re_summarize=args.re_summarize,
            filter_kind=filter_kind,
        )

        if not tasks:
            log.info("Aucune tâche à traiter")
            result.print_summary()
            result.exit()

        if args.dry_run:
            est = estimate_cost(tasks)
            log.info(f"=== DRY RUN — Estimation ===")
            log.info(f"Tâches       : {est['tasks']} ({est['text_tasks']} texte, {est['image_tasks']} image)")
            log.info(f"Images total : {est['total_images']}")
            log.info(f"Tokens est.  : ~{est['est_input_tokens']:,} in + ~{est['est_output_tokens']:,} out")
            log.info(f"Coût est.    : ~${est['est_cost_usd']:.2f} (~{est['est_cost_eur']:.2f}€)")
            log.info(f"Durée est.   : ~{est['est_time_minutes']:.0f} minutes")
            log.info(f"Modèle       : {args.model}")
            result.print_summary()
            result.exit()

        # Renommer le fichier global avant re-résumé complet
        if args.re_summarize and os.path.isfile(cfg.GLOBAL_RESUMES_FILE):
            backup = cfg.GLOBAL_RESUMES_FILE + f".gemini_backup_{now_timestamp().replace(':', '-').replace(' ', '_')}"
            os.rename(cfg.GLOBAL_RESUMES_FILE, backup)
            log.info(f"Ancien fichier global sauvegardé → {os.path.basename(backup)}")

        process_tasks(tasks, full_df, result, model=args.model, use_claude_code=args.use_claude_code)

    except RuntimeError as e:
        log.error(str(e))
        result.add_fail(str(e))

    finally:
        release_lock(lock_fd, cfg.SUMMARIZER_LOCK)

    result.print_summary()
    result.exit()


if __name__ == "__main__":
    main()
