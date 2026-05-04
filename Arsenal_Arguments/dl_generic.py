import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

"""
dl_generic.py — Téléchargeur universel Arsenal Intelligence Unit.

Accepte n'importe quelle URL supportée par yt-dlp (TikTok, Instagram,
YouTube, X/Twitter, Reddit, Threads, autres). Détecte la plateforme,
choisit les cookies adéquats, télécharge via yt-dlp dans 01_raw_videos/
au format `<id>_<auteur>_<date>.<ext>`, lit le `.info.json` produit pour
en extraire les métadonnées, et écrit une ligne dans suivi_global.csv.

Usage :
    python dl_generic.py --url "https://..."
    python dl_generic.py --url-file urls.txt
    python dl_generic.py --url "..." --base-dir /path/to/Arsenal_Arguments

Conventions :
- Cookies TikTok       → COOKIES_TIKTOK
- Cookies Instagram    → COOKIES_INSTAGRAM
- Cookies Threads      → COOKIES_INSTAGRAM (même propriétaire Meta)
- Autres plateformes   → pas de cookies
- Skip silencieux si la paire (plateforme, id) est déjà SUCCESS au CSV.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from typing import List, Optional, Tuple

from arsenal_config import (
    cfg, GLOBAL_CSV_COLUMNS, MAX_CAROUSEL_SLIDES,
    safe_username, normalize_str, now_timestamp,
    append_to_csv, load_csv, ScriptResult, get_logger,
)

log = get_logger("dl_generic")

# Extensions image/vidéo acceptées par le fallback gallery-dl (Y.21).
GDL_KEEP_EXTS = {
    "jpg", "jpeg", "png", "webp", "gif", "heic",
    "mp4", "mov", "webm", "mkv", "m4v",
}
GDL_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}


# =============================================================================
# DÉTECTION PLATEFORME + ID
# =============================================================================

PLATFORM_TIKTOK    = "TikTok"
PLATFORM_INSTAGRAM = "Instagram"
PLATFORM_THREADS   = "Threads"
PLATFORM_YOUTUBE   = "YouTube"
PLATFORM_X         = "X"
PLATFORM_REDDIT    = "Reddit"
PLATFORM_OTHER     = "Other"


def detect_platform(url: str) -> str:
    """Devine la plateforme à partir du host de l'URL."""
    u = (url or "").lower()
    if "tiktok.com" in u:
        return PLATFORM_TIKTOK
    if "instagram.com" in u:
        return PLATFORM_INSTAGRAM
    if "threads.net" in u or "threads.com" in u:
        return PLATFORM_THREADS
    if "youtube.com" in u or "youtu.be" in u:
        return PLATFORM_YOUTUBE
    if "x.com" in u or "twitter.com" in u:
        return PLATFORM_X
    if "reddit.com" in u:
        return PLATFORM_REDDIT
    return PLATFORM_OTHER


def extract_content_id(url: str, platform: str) -> str:
    """Renvoie un id stable et lisible pour la paire (plateforme, contenu)."""
    if platform == PLATFORM_TIKTOK:
        m = re.search(r"/video/(\d+)", url)
        if m:
            return m.group(1)
    elif platform == PLATFORM_INSTAGRAM:
        m = re.search(r"/(?:p|reel|reels|tv)/([\w-]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    elif platform == PLATFORM_THREADS:
        m = re.search(r"/post/([\w-]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    elif platform == PLATFORM_YOUTUBE:
        m = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    elif platform == PLATFORM_X:
        m = re.search(r"/status/(\d+)", url)
        if m:
            return m.group(1)
    elif platform == PLATFORM_REDDIT:
        m = re.search(r"/comments/([\w]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)

    # Fallback : dernier segment normalisé
    tail = re.sub(r"\W+", "_", url).strip("_")
    return tail[-40:] or "unknown"


def resolve_tiktok_short_url(url: str) -> str:
    """Suit la redirection d'un lien court TikTok (vm./vt.) vers l'URL longue."""
    if not re.search(r"https?://(?:vm|vt)\.tiktok\.com/", url, re.IGNORECASE):
        return url
    try:
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            final = resp.geturl() or url
    except Exception as e:
        log.warning(f"Résolution short URL TikTok échouée ({url}) : {e}")
        return url
    if final != url:
        log.info(f"TikTok short URL résolu : {url} -> {final}")
    return final


# =============================================================================
# COOKIES PAR PLATEFORME
# =============================================================================

def cookie_args_for(platform: str) -> List[str]:
    """yt-dlp --cookies args, ou [] si pas de cookies pour cette plateforme."""
    if platform == PLATFORM_TIKTOK and os.path.isfile(cfg.COOKIES_TIKTOK):
        return ["--cookies", cfg.COOKIES_TIKTOK]
    if platform in (PLATFORM_INSTAGRAM, PLATFORM_THREADS) and os.path.isfile(cfg.COOKIES_INSTAGRAM):
        return ["--cookies", cfg.COOKIES_INSTAGRAM]
    return []


# =============================================================================
# CSV — DÉJÀ PRÉSENT ?
# =============================================================================

def already_in_csv(content_id: str, platform: str) -> bool:
    """True si le couple (plateforme, id) est déjà au CSV en statut SUCCESS.

    Avant Y.21 : on skippait dès qu'une ligne (plat, id) existait même en
    FAILED → le retry de `arsenal_retry_failed.py` ne faisait rien (les
    lignes FAILED restaient FAILED car dl_generic était short-circuité).
    Désormais : on skip seulement sur SUCCESS — un FAILED précédent
    déclenche un nouveau download (c'est exactement ce que veut un retry,
    et c'est cohérent avec le comportement attendu quand le user re-drop
    une URL après une panne transitoire).
    """
    if not os.path.isfile(cfg.CSV_PATH):
        return False
    try:
        df = load_csv(cfg.CSV_PATH)
        mask = (
            (df["id"].astype(str).str.strip() == str(content_id).strip())
            & (df["plateforme"].astype(str).str.strip().str.lower() == platform.lower())
            & (df["download_status"].astype(str).str.strip().str.upper() == "SUCCESS")
        )
        return bool(mask.any())
    except Exception as e:
        log.warning(f"Lecture CSV échouée pour la dédup : {e}")
        return False


# =============================================================================
# YT-DLP RUN + INFO JSON
# =============================================================================

def run_cmd(cmd, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
    )


def find_info_json(prefix: str) -> Optional[str]:
    """Cherche le `.info.json` produit par --write-info-json à côté du média."""
    if not os.path.isdir(cfg.VIDEO_DIR):
        return None
    for f in os.listdir(cfg.VIDEO_DIR):
        if f.startswith(prefix) and f.endswith(".info.json"):
            return os.path.join(cfg.VIDEO_DIR, f)
    return None


def find_media(prefix: str) -> Optional[str]:
    """Premier fichier média (non .info.json) commençant par le préfixe."""
    if not os.path.isdir(cfg.VIDEO_DIR):
        return None
    for f in os.listdir(cfg.VIDEO_DIR):
        if f.startswith(prefix) and not f.endswith(".info.json") and not f.endswith(".part"):
            return os.path.join(cfg.VIDEO_DIR, f)
    return None


def parse_info_json(path: str) -> dict:
    """Extrait les champs utiles du .info.json yt-dlp."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            info = json.load(f)
    except Exception as e:
        log.warning(f"info.json illisible : {e}")
        return {}

    # upload_date au format YYYYMMDD
    pub_date = info.get("upload_date") or ""
    pub_date_ddmmyy = ""
    if re.fullmatch(r"\d{8}", str(pub_date)):
        try:
            pub_date_ddmmyy = datetime.strptime(pub_date, "%Y%m%d").strftime("%d%m%y")
        except ValueError:
            pub_date_ddmmyy = ""

    uploader = (
        info.get("uploader_id")
        or info.get("uploader")
        or info.get("channel_id")
        or info.get("channel")
        or ""
    )
    display_name = info.get("uploader") or info.get("channel") or ""
    description = info.get("description") or info.get("title") or ""
    thumbnail = info.get("thumbnail") or ""
    views = info.get("view_count") or ""

    # Hashtags : yt-dlp ne les expose pas comme tels — on les extrait du
    # texte description.
    tags = " ".join(re.findall(r"#\w+", str(description)))

    return {
        "pub_date_ddmmyy": pub_date_ddmmyy,
        "uploader_safe": safe_username(uploader),
        "display_name": str(display_name),
        "description": str(description),
        "hashtags": tags,
        "thumbnail": str(thumbnail),
        "views": str(views) if views != "" else "",
    }


# =============================================================================
# FALLBACK GALLERY-DL — IMAGES + MIXED MEDIA (Y.21)
# =============================================================================

def _guess_ext(url: str, default: str = "jpg") -> str:
    """Devine l'extension fichier depuis une URL média."""
    m = re.search(r"\.([A-Za-z0-9]{2,5})(?:\?|$)", url)
    if m:
        ext = m.group(1).lower()
        if ext in GDL_KEEP_EXTS:
            return ext
    return default


def _download_binary(url: str, out_path: str, timeout: int = 60) -> bool:
    """Télécharge une URL binaire vers un fichier local. Retourne True si OK."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if not data:
            return False
        with open(out_path, "wb") as f:
            f.write(data)
        return os.path.getsize(out_path) > 0
    except Exception as e:
        log.warning(f"  download {url[:80]} échoué : {e}")
        return False


def gallery_dl_fallback(url: str, platform: str, content_id: str) -> dict:
    """Y.21 — Fallback gallery-dl pour les posts image-only ou mixed-media
    quand yt-dlp ne ramène pas de vidéo (ex : tweets X texte / images, posts
    Threads avec photos, galeries Reddit).

    Sauve images + vidéos dans `01_raw_images/<PREFIX><id>/` (NN.ext).
    Si le post a un texte, l'écrit dans `_post_text.txt` au même endroit
    (utilisé par ocr_carousels.py pour prepend `[POST TEXT]`).

    Retourne un dict {ok, count_images, count_videos, error, has_text,
    username, display_name, description, pub_date_ddmmyy}.
    """
    out = {
        "ok": False, "count_images": 0, "count_videos": 0, "has_text": False,
        "error": "", "username": "", "display_name": "",
        "description": "", "pub_date_ddmmyy": "",
    }

    # gallery-dl --dump-json sort la liste de toutes les opérations sans
    # rien télécharger. On limite à MAX_CAROUSEL_SLIDES via --range.
    cmd = [
        sys.executable, "-m", "gallery_dl",
        "--dump-json",
        "--range", f"1-{MAX_CAROUSEL_SLIDES}",
        url,
    ]
    # X / Twitter ne nécessite pas de cookies (gallery-dl gère son propre
    # auth via guest token). Threads peut bénéficier des cookies IG.
    if platform == PLATFORM_THREADS and os.path.isfile(cfg.COOKIES_INSTAGRAM):
        cmd.extend(["--cookies", cfg.COOKIES_INSTAGRAM])

    try:
        res = run_cmd(cmd, timeout=90)
    except subprocess.TimeoutExpired:
        out["error"] = "gallery_dl:timeout"
        return out
    except Exception as e:
        out["error"] = f"gallery_dl:exception:{type(e).__name__}"
        return out

    if res.returncode != 0:
        tail = (res.stderr or "")[-300:].replace("\n", " | ")
        out["error"] = f"gallery_dl:rc={res.returncode}:{tail}"
        return out

    raw = (res.stdout or "").strip()
    if not raw:
        out["error"] = "gallery_dl:empty_stdout"
        return out

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        out["error"] = f"gallery_dl:json_parse={e}"
        return out

    if not isinstance(data, list):
        out["error"] = "gallery_dl:not_a_list"
        return out

    # Extraction métadonnées (premier 2-marker = métadonnées du post).
    # gallery-dl émet [type_marker, data, ...] où :
    #   2 = directory metadata (post-level info)
    #   3 = media (url + meta)
    media_urls: List[Tuple[str, dict]] = []
    post_text = ""
    for entry in data:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        marker = entry[0]
        if marker == 2 and isinstance(entry[1], dict):
            meta = entry[1]
            if not post_text:
                post_text = (meta.get("content") or meta.get("text")
                              or meta.get("description") or meta.get("title") or "")
            if not out["username"]:
                user = meta.get("user") or meta.get("author") or {}
                if isinstance(user, dict):
                    out["username"] = safe_username(
                        user.get("name") or user.get("username") or user.get("nick") or ""
                    )
                    out["display_name"] = (user.get("nick") or user.get("name")
                                            or user.get("display_name") or "")
                else:
                    out["username"] = safe_username(str(user))
            if not out["pub_date_ddmmyy"]:
                date_raw = meta.get("date") or meta.get("created_at") or ""
                try:
                    if isinstance(date_raw, str) and date_raw:
                        dt = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
                        out["pub_date_ddmmyy"] = dt.strftime("%d%m%y")
                except ValueError:
                    pass
        elif marker == 3 and len(entry) >= 2:
            url_or_path = entry[1]
            meta = entry[2] if len(entry) >= 3 and isinstance(entry[2], dict) else {}
            if isinstance(url_or_path, str) and url_or_path.startswith(("http://", "https://")):
                media_urls.append((url_or_path, meta))

    out["description"] = post_text[:2000] if post_text else ""

    # Pas de média mais texte présent → cas tweet text-only : on traite
    # quand même comme un succès (le _post_text.txt sera la seule source).
    if not media_urls and not post_text:
        out["error"] = "gallery_dl:no_media_no_text"
        return out

    post_dir = cfg.post_dir(platform, content_id)
    os.makedirs(post_dir, exist_ok=True)

    # Écrit le texte du post si présent (toujours UTF-8).
    if post_text:
        try:
            with open(os.path.join(post_dir, "_post_text.txt"), "w",
                      encoding="utf-8") as f:
                f.write(post_text.strip() + "\n")
            out["has_text"] = True
        except OSError as e:
            log.warning(f"  écriture _post_text.txt échouée : {e}")

    # Téléchargement séquentiel des médias avec naming `NN.ext`.
    next_idx = 1
    for url_media, meta in media_urls:
        ext = (meta.get("extension") or "").lower() or _guess_ext(url_media)
        if ext not in GDL_KEEP_EXTS:
            ext = "jpg"
        out_path = os.path.join(post_dir, f"{next_idx:02d}.{ext}")
        if _download_binary(url_media, out_path):
            if ext in GDL_VIDEO_EXTS:
                out["count_videos"] += 1
            else:
                out["count_images"] += 1
            next_idx += 1

    out["ok"] = (out["count_images"] + out["count_videos"] > 0) or out["has_text"]
    if not out["ok"] and not out["error"]:
        out["error"] = "gallery_dl:all_downloads_failed"
    return out


# =============================================================================
# DOWNLOAD — UNE URL
# =============================================================================

def download_one(url: str, source_input_mode: str = "URL_SINGLE") -> dict:
    """Télécharge une URL via yt-dlp, écrit la ligne CSV, retourne un dict
    récap (status / id / platform / etc)."""
    platform = detect_platform(url)

    # Résolution liens courts TikTok avant tout
    if platform == PLATFORM_TIKTOK:
        url = resolve_tiktok_short_url(url)

    # Threads : yt-dlp ne supporte que `threads.net`, pas `threads.com`.
    # Meta a unifié sur `threads.com` mi-2025 mais yt-dlp n'a pas suivi.
    # Conversion silencieuse `.com` → `.net` avant download.
    if platform == PLATFORM_THREADS and "threads.com" in url:
        url = url.replace("threads.com", "threads.net")

    content_id = extract_content_id(url, platform)

    if already_in_csv(content_id, platform):
        log.info(f"  SKIP [{platform}] {content_id} (déjà au CSV)")
        return {"status": "SKIP", "id": content_id, "platform": platform, "url": url}

    # Pré-fetch métadonnées light (uploader_id + upload_date) pour
    # construire le préfixe de sortie. yt-dlp gère les NA via "NA"/"None".
    pre_cmd = [
        cfg.YTDLP_PATH,
        "--print", "upload_date",
        "--print", "uploader_id",
        "--print", "uploader",
        "--no-warnings",
        "--ignore-no-formats-error",
        *cookie_args_for(platform),
        url,
    ]

    pub_date_ddmmyy = datetime.now().strftime("%d%m%y")
    uploader_safe = "inconnu"
    display_name = "Inconnu"

    try:
        r_meta = run_cmd(pre_cmd, timeout=45)
        lines = [l.strip() for l in (r_meta.stdout or "").splitlines() if l.strip()]
        for line in lines:
            if re.fullmatch(r"\d{8}", line):
                try:
                    pub_date_ddmmyy = datetime.strptime(line, "%Y%m%d").strftime("%d%m%y")
                except ValueError:
                    pass
                continue
            if line.lower() not in {"na", "none", "null"} and uploader_safe == "inconnu":
                uploader_safe = safe_username(line)
                display_name = line
    except Exception as e:
        log.warning(f"  Pré-fetch méta échoué pour {content_id} : {e}")

    prefix = f"{content_id}_{uploader_safe}_{pub_date_ddmmyy}"
    output_template = os.path.join(cfg.VIDEO_DIR, f"{prefix}.%(ext)s")

    dl_cmd = [
        cfg.YTDLP_PATH,
        "--no-warnings",
        "--write-info-json",
        "-o", output_template,
        *cookie_args_for(platform),
        url,
    ]

    log.info(f"  DL [{platform}] {content_id} → {prefix}")
    res = run_cmd(dl_cmd, timeout=240)
    ok = res.returncode == 0

    # Parse info.json si présent (même en cas d'échec partiel yt-dlp en
    # publie souvent un)
    info_path = find_info_json(prefix)
    info = parse_info_json(info_path) if info_path else {}

    # Si le pré-fetch n'avait rien donné mais que info.json oui, on enrichit
    if info.get("pub_date_ddmmyy"):
        pub_date_ddmmyy = info["pub_date_ddmmyy"]
    if info.get("uploader_safe") and uploader_safe == "inconnu":
        uploader_safe = info["uploader_safe"]
    if info.get("display_name") and display_name == "Inconnu":
        display_name = info["display_name"]

    media_path = find_media(prefix)
    filename = os.path.basename(media_path) if media_path else content_id

    # Y.21 — Fallback gallery-dl pour X / Threads / Reddit quand yt-dlp
    # n'a pas trouvé de vidéo (tweet text/image, post Threads photos,
    # galerie Reddit). yt-dlp et gallery-dl ont des stratégies différentes :
    # yt-dlp = "play page" only, gallery-dl = GraphQL + manifests + multi-media.
    # On déclenche le fallback si yt-dlp a échoué OU n'a pas produit de
    # média, et seulement pour les plateformes où images/text sont fréquents.
    fallback_used = False
    fallback_summary = ""
    if (not ok or not media_path) and platform in (
        PLATFORM_X, PLATFORM_THREADS, PLATFORM_REDDIT,
    ):
        log.info(f"  ↻ yt-dlp KO → fallback gallery-dl pour [{platform}] {content_id}")
        gdl = gallery_dl_fallback(url, platform, content_id)
        if gdl["ok"]:
            fallback_used = True
            ok = True  # override yt-dlp pour la ligne CSV
            count_img = gdl["count_images"]
            count_vid = gdl["count_videos"]
            has_text = gdl["has_text"]
            fallback_summary = (
                f"gallery_dl:{count_img}img+{count_vid}vid"
                + (f"+text" if has_text else "")
            )
            # Enrichit les méta vides avec celles de gallery-dl (priorité
            # à yt-dlp si déjà rempli).
            if uploader_safe == "inconnu" and gdl["username"]:
                uploader_safe = gdl["username"]
            if display_name == "Inconnu" and gdl["display_name"]:
                display_name = gdl["display_name"]
            if not pub_date_ddmmyy and gdl["pub_date_ddmmyy"]:
                pub_date_ddmmyy = gdl["pub_date_ddmmyy"]
            if not info.get("description") and gdl["description"]:
                info["description"] = gdl["description"]
            # Le filename CSV pointe sur le dossier (utilisable pour
            # retrouver le post) — pas un fichier média unique.
            filename = os.path.basename(cfg.post_dir(platform, content_id))
            log.info(f"  ✓ {fallback_summary}")
        else:
            log.warning(f"  ✗ fallback gallery-dl échoué : {gdl['error']}")

    # Détermine le type final pour la colonne CSV.
    if fallback_used:
        gdl_img = gdl["count_images"]
        gdl_vid = gdl["count_videos"]
        if gdl_img > 0 and gdl_vid > 0:
            type_final = "Mixed"
        elif gdl_img > 0:
            type_final = "Image"
        elif gdl_vid > 0:
            type_final = "Video"
        else:
            type_final = "Text"  # text-only (pas de média mais _post_text.txt)
        download_mode = f"gallery_dl_{platform.lower()}"
    elif ok and media_path:
        type_final = "Video"
        download_mode = "video_direct"
    else:
        type_final = ""
        download_mode = "video_direct_failed"

    error_msg = ""
    if not ok:
        error_msg = (res.stderr or "")[:1000].strip()
    elif fallback_used and fallback_summary:
        # On garde la stderr yt-dlp pour traçabilité mais on préfixe avec
        # le résumé du fallback réussi.
        error_msg = ""  # success → pas d'erreur

    row = {
        "id": content_id,
        "url": url,
        "plateforme": platform,
        "source_input_mode": source_input_mode,
        "type": type_final or "Video",
        "detected_type_initial": "Video",
        "resolved_type_final": type_final,
        "download_mode": download_mode,
        "download_status": "SUCCESS" if ok else "FAILED",
        "error_message": error_msg,
        "username": uploader_safe,
        "display_name": display_name or "Inconnu",
        "hashtags": info.get("hashtags", ""),
        "description": (info.get("description") or "")[:2000],
        "thumbnail_url": info.get("thumbnail", ""),
        "views_at_extraction": info.get("views", ""),
        "filename": filename,
        "date_publication": pub_date_ddmmyy,
        "download_timestamp": now_timestamp(),
    }

    append_to_csv([row], cfg.CSV_PATH)

    return {
        "status": row["download_status"],
        "id": content_id,
        "platform": platform,
        "url": url,
        "filename": filename,
        "error": row["error_message"] if row["download_status"] == "FAILED" else "",
        "fallback": fallback_summary,
    }


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Arsenal — Téléchargeur universel yt-dlp")
    cfg.add_base_dir_arg(parser)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", type=str, help="URL unique à télécharger")
    group.add_argument("--url-file", type=str,
                       help="Fichier texte avec une URL par ligne")
    parser.add_argument("--source-input-mode", type=str, default="URL_SINGLE",
                        help="Valeur de la colonne CSV source_input_mode")
    args = parser.parse_args()

    cfg.init_from_args(args)
    cfg.ensure_dirs()

    if not os.path.isfile(cfg.YTDLP_PATH):
        log.error(f"yt-dlp.exe introuvable : {cfg.YTDLP_PATH}")
        sys.exit(1)

    result = ScriptResult("dl_generic")

    # Construire la liste d'URLs à traiter
    urls: List[str] = []
    if args.url:
        urls = [args.url]
    else:
        if not os.path.isfile(args.url_file):
            log.error(f"Fichier URLs introuvable : {args.url_file}")
            result.add_fail(f"file not found: {args.url_file}")
            result.print_summary()
            result.exit()
        with open(args.url_file, "r", encoding="utf-8", errors="replace") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not urls:
        log.warning("Aucune URL à traiter")
        result.print_summary()
        result.exit()

    log.info(f"{len(urls)} URL(s) à traiter")

    for url in urls:
        try:
            r = download_one(url, source_input_mode=args.source_input_mode)
            if r["status"] == "SUCCESS":
                result.add_success()
            elif r["status"] == "SKIP":
                result.add_skip()
            else:
                result.add_fail(f"{r['id']}: {r.get('error', '')[:200]}")
        except Exception as e:
            log.error(f"Exception sur {url} : {e}")
            result.add_fail(f"{url}: {e}")

    result.print_summary()
    result.exit()


if __name__ == "__main__":
    main()
