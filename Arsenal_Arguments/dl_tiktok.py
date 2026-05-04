"""
dl_tiktok.py — Téléchargeur TikTok pour Arsenal Intelligence Unit.

Utilise yt-dlp pour télécharger des vidéos TikTok.
Gère deux modes d'entrée : liste d'URLs ou HTML sauvegardé.

Usage :
    python dl_tiktok.py                                   # depuis dl_tiktok_video.txt
    python dl_tiktok.py --url "https://tiktok.com/..."    # une seule URL
    python dl_tiktok.py --input-file "mon_fichier.txt"    # fichier custom
"""

import os
import re
import sys
import argparse
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

from arsenal_config import (
    cfg, GLOBAL_CSV_COLUMNS, CSV_ENCODING,
    safe_username, normalize_str, now_timestamp,
    append_to_csv, load_csv, ScriptResult, get_logger,
)

log = get_logger("dl_tiktok")


# Cache mémoire : URL courte → URL canonique. Évite de refaire le HEAD si la
# même URL apparaît deux fois dans une même run (cas batch).
_SHORT_URL_RESOLVE_CACHE: dict[str, str] = {}


def resolve_tiktok_short_url(url: str) -> str:
    """Résout `vm.tiktok.com/X` ou `vt.tiktok.com/X` vers l'URL canonique
    `https://www.tiktok.com/@user/video/<id_numérique>/` via une requête
    HEAD (suit les redirects). No-op si l'URL est déjà canonique ou si
    elle ne correspond pas à un short-URL TikTok.

    Bug fix Phase Y.4 : sans cette résolution, `extract_from_urls` tombait
    sur le fallback `re.sub(r"\\W+", "_", url)[-40:]` et générait des IDs
    sluggés type `https_vm_tiktok_com_ZNRxxxx` au lieu du vrai ID
    numérique TikTok. Conséquence : 416 lignes CSV avec ID incorrect, des
    summaries mal nommées, et des threads Discord avec des descriptions
    qui pointent vers un ID inexistant. Le HEAD redirect résout TikTok
    sans dépendre de yt-dlp (économise un round-trip).

    Fallback sur l'URL d'origine si réseau down / timeout / blocage TikTok.
    """
    if not url:
        return url
    if 'vm.tiktok.com' not in url and 'vt.tiktok.com' not in url:
        return url
    if url in _SHORT_URL_RESOLVE_CACHE:
        return _SHORT_URL_RESOLVE_CACHE[url]
    try:
        req = urllib.request.Request(url, method='HEAD',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                                   'Chrome/120.0.0.0 Safari/537.36'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            resolved = resp.geturl().split('?')[0].split('#')[0]
        _SHORT_URL_RESOLVE_CACHE[url] = resolved
        return resolved
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning(f"Résolution short URL TikTok échouée pour {url}: {e}")
        _SHORT_URL_RESOLVE_CACHE[url] = url  # cache l'échec pour éviter les retries
        return url


# =============================================================================
# PARSING DES ENTRÉES
# =============================================================================

def normalize_tiktok_url(url: str):
    """Valide et normalise une URL TikTok vidéo."""
    if not url:
        return None
    url = url.replace("\u00A0", "").strip()
    url = url.split("?")[0].split("#")[0].strip()
    if url.startswith("/"):
        url = "https://www.tiktok.com" + url

    # Liens vidéo classiques
    if re.match(r"^https://(www\.)?tiktok\.com/@[^/]+/video/\d+/?$", url):
        if not url.endswith("/"):
            url += "/"
        return url

    # Liens courts / share links
    if re.match(r"^https://([a-zA-Z0-9\-]+\.)?tiktok\.com/[A-Za-z0-9/_-]+/?$", url):
        return url

    return None


def parse_alt_text(alt_text):
    """Extraction hashtags + nom affiché depuis l'alt TikTok FR/EN."""
    alt_text = alt_text or ""
    hashtags = re.findall(r"#\w+", alt_text)
    display_name = "Inconnu"

    if "créé par " in alt_text.lower():
        m = re.search(r"créé par\s+(.+?)(?:\s+avec\s+|$)", alt_text, flags=re.IGNORECASE)
        if m:
            display_name = m.group(1).strip()
    elif "created by " in alt_text.lower():
        m = re.search(r"created by\s+(.+?)(?:\s+with\s+|$)", alt_text, flags=re.IGNORECASE)
        if m:
            display_name = m.group(1).strip()

    description = alt_text.split("#")[0].strip()
    return " ".join(hashtags), display_name, description


def extract_from_urls(raw_text: str):
    """Parse une liste d'URLs TikTok, une URL par ligne."""
    videos = []
    seen = set()

    for line in raw_text.splitlines():
        url = normalize_tiktok_url(line)
        if not url:
            continue
        url = resolve_tiktok_short_url(url)

        m = re.search(r"/video/(\d+)/?$", url)
        video_id = m.group(1) if m else re.sub(r"\W+", "_", url)[-40:]

        if video_id in seen:
            continue
        seen.add(video_id)

        username = "inconnu"
        m_user = re.search(r"tiktok\.com/@([^/]+)/", url)
        if m_user:
            username = m_user.group(1)

        videos.append({
            "id": video_id,
            "url": url,
            "plateforme": "TikTok",
            "source_input_mode": "URL_LIST",
            "type": "Video",
            "username": safe_username(username),
            "display_name": "Inconnu",
            "hashtags": "",
            "description": "",
            "thumbnail_url": "",
            "views_at_extraction": "",
        })

    return videos


def extract_from_html(raw_text: str):
    """Parse du HTML TikTok sauvegardé."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.error("beautifulsoup4 requis pour le mode HTML. pip install beautifulsoup4")
        return []

    soup = BeautifulSoup(raw_text, "html.parser")
    videos = []
    seen = set()

    items = soup.find_all("div", {"data-e2e": "user-repost-item"})
    if not items:
        items = soup.find_all("a", href=re.compile(r"tiktok\.com/@.+/video/\d+"))

    for item in items:
        if getattr(item, "name", None) == "a":
            link_tag = item
            parent = item.parent if item.parent else item
            img_tag = parent.find("img")
            views_tag = parent.find("strong", {"data-e2e": "video-views"})
        else:
            link_tag = item.find("a", href=True)
            img_tag = item.find("img", alt=True) or item.find("img")
            views_tag = item.find("strong", {"data-e2e": "video-views"})

        if not link_tag:
            continue

        url = normalize_tiktok_url(link_tag.get("href", ""))
        if not url:
            continue
        url = resolve_tiktok_short_url(url)

        m = re.search(r"/video/(\d+)/?$", url)
        video_id = m.group(1) if m else re.sub(r"\W+", "_", url)[-40:]

        if video_id in seen:
            continue
        seen.add(video_id)

        alt_text = img_tag.get("alt", "") if img_tag else ""
        hashtags, display_name, description = parse_alt_text(alt_text)

        username = "inconnu"
        m_user = re.search(r"@([^/]+)/video/", url)
        if m_user:
            username = m_user.group(1)

        thumbnail_url = img_tag.get("src", "") if img_tag else ""
        views_raw = views_tag.text.strip() if views_tag and views_tag.text else ""

        videos.append({
            "id": video_id,
            "url": url,
            "plateforme": "TikTok",
            "source_input_mode": "HTML",
            "type": "Video",
            "username": safe_username(username),
            "display_name": normalize_str(display_name) or "Inconnu",
            "hashtags": hashtags,
            "description": description,
            "thumbnail_url": thumbnail_url,
            "views_at_extraction": views_raw,
        })

    return videos


def extract_from_single_url(url: str):
    """Crée une entrée à partir d'une seule URL."""
    url = normalize_tiktok_url(url)
    if not url:
        return []
    url = resolve_tiktok_short_url(url)

    m = re.search(r"/video/(\d+)/?$", url)
    video_id = m.group(1) if m else re.sub(r"\W+", "_", url)[-40:]

    username = "inconnu"
    m_user = re.search(r"tiktok\.com/@([^/]+)/", url)
    if m_user:
        username = m_user.group(1)

    return [{
        "id": video_id,
        "url": url,
        "plateforme": "TikTok",
        "source_input_mode": "URL_SINGLE",
        "type": "Video",
        "username": safe_username(username),
        "display_name": "Inconnu",
        "hashtags": "",
        "description": "",
        "thumbnail_url": "",
        "views_at_extraction": "",
    }]


def extract_from_file(file_path: str):
    """Détecte automatiquement HTML ou liste d'URLs."""
    if not os.path.isfile(file_path):
        log.error(f"Fichier source introuvable : {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    looks_like_html = (
        ("<a" in raw and "href" in raw)
        or ("<html" in raw.lower())
        or ("data-e2e" in raw)
    )

    if looks_like_html:
        log.info("Mode entrée détecté = HTML")
        return extract_from_html(raw)
    else:
        log.info("Mode entrée détecté = Liste d'URLs")
        return extract_from_urls(raw)


# =============================================================================
# YT-DLP HELPERS
# =============================================================================

def cookie_args():
    """Arguments cookies TikTok pour yt-dlp."""
    if os.path.isfile(cfg.COOKIES_TIKTOK):
        return ["--cookies", cfg.COOKIES_TIKTOK]
    return ["--cookies-from-browser", "chrome"]


def run_cmd(cmd, timeout=None):
    """Exécute une commande et retourne le CompletedProcess."""
    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, check=False,
    )


def get_video_meta(video_url: str) -> dict:
    """
    Récupère les métadonnées yt-dlp en un seul appel :
    upload_date, uploader_id, uploader.
    """
    cmd = [
        cfg.YTDLP_PATH,
        "--print", "upload_date",
        "--print", "uploader_id",
        "--print", "uploader",
        "--no-warnings",
        "--ignore-no-formats-error",
        *cookie_args(),
        video_url,
    ]

    pub_date = datetime.now().strftime("%d%m%y")
    username_safe = "inconnu"
    display_name = "Inconnu"

    try:
        result = run_cmd(cmd, timeout=30)
        lines = [l.strip() for l in (result.stdout or "").splitlines() if l.strip()]

        for line in lines:
            if re.fullmatch(r"\d{8}", line):
                try:
                    pub_date = datetime.strptime(line, "%Y%m%d").strftime("%d%m%y")
                except ValueError:
                    pass
                continue

            if line.lower() not in {"na", "none", "null"} and username_safe == "inconnu":
                username_safe = safe_username(line)
                display_name = line
    except Exception:
        pass

    return {
        "pub_date_ddmmyy": pub_date,
        "username_safe": username_safe,
        "display_name": display_name,
    }


def already_downloaded(video_id: str) -> bool:
    """Vérifie si la vidéo est déjà téléchargée (via CSV + disque)."""
    # Vérification CSV (rapide)
    if os.path.isfile(cfg.CSV_PATH):
        try:
            df = load_csv(cfg.CSV_PATH)
            mask = (
                (df["id"].str.strip() == str(video_id).strip())
                & (df["plateforme"].str.lower() == "tiktok")
                & (df["download_status"].str.upper() == "SUCCESS")
            )
            if mask.any():
                return True
        except Exception:
            pass

    # Vérification disque (fallback)
    if os.path.isdir(cfg.VIDEO_DIR):
        for f in os.listdir(cfg.VIDEO_DIR):
            if f.startswith(f"{video_id}_"):
                return True

    return False


def download_video(video: dict, pub_date: str):
    """Télécharge une vidéo TikTok via yt-dlp."""
    clean_name = safe_username(video.get("username", "inconnu"))
    output_template = os.path.join(
        cfg.VIDEO_DIR,
        f"{video['id']}_{clean_name}_{pub_date}.%(ext)s"
    )

    cmd = [
        cfg.YTDLP_PATH,
        "--no-warnings",
        "-o", output_template,
        *cookie_args(),
        video["url"],
    ]

    res = run_cmd(cmd, timeout=180)
    return res.returncode == 0, (res.stderr or "").strip()


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def process_downloads(video_list: list, result: ScriptResult):
    """Traite une liste de vidéos : enrichissement + téléchargement + écriture CSV."""
    new_entries = []

    for video in video_list:
        if already_downloaded(video["id"]):
            log.info(f"  SKIP {video['id']} (déjà téléchargé)")
            result.add_skip()
            continue

        log.info(f"  Traitement TikTok {video['id']}...")

        # Enrichissement métadonnées via yt-dlp
        meta = get_video_meta(video["url"])
        pub_date = meta["pub_date_ddmmyy"]

        if not video.get("username") or video["username"] == "inconnu":
            video["username"] = meta["username_safe"]
        if not video.get("display_name") or video["display_name"] == "Inconnu":
            video["display_name"] = meta["display_name"]

        video["detected_type_initial"] = video.get("type", "Video")
        video["resolved_type_final"] = ""
        video["download_mode"] = ""
        video["download_status"] = "FAILED"
        video["error_message"] = ""

        try:
            ok, err = download_video(video, pub_date)

            video["filename"] = video["id"]
            video["date_publication"] = pub_date
            video["download_timestamp"] = now_timestamp()

            if ok:
                # Trouver le fichier créé
                prefix = f"{video['id']}_{safe_username(video.get('username', 'inconnu'))}_{pub_date}"
                created = [f for f in os.listdir(cfg.VIDEO_DIR) if f.startswith(prefix)]
                if created:
                    video["filename"] = created[0]

                video["resolved_type_final"] = "Video"
                video["download_mode"] = "video_direct"
                video["download_status"] = "SUCCESS"
                video["error_message"] = ""
                result.add_success()
                log.info(f"  ✅ {video['id']} téléchargé")
            else:
                video["download_mode"] = "video_direct_failed"
                video["error_message"] = err[:1000]
                result.add_fail(f"{video['id']}: {err[:200]}")
                log.warning(f"  ⚠️ Échec {video['id']}")

            new_entries.append(video)

        except Exception as e:
            video["filename"] = video["id"]
            video["date_publication"] = pub_date
            video["download_timestamp"] = now_timestamp()
            video["download_mode"] = "exception"
            video["error_message"] = str(e)[:1000]
            new_entries.append(video)
            result.add_fail(f"{video['id']}: exception {e}")
            log.error(f"  ⚠️ Exception {video['id']} → {e}")

    # Écriture CSV
    if new_entries:
        append_to_csv(new_entries, cfg.CSV_PATH)
        log.info(f"CSV mis à jour : {len(new_entries)} ligne(s) ajoutée(s)")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Arsenal — Téléchargeur TikTok")
    cfg.add_base_dir_arg(parser)
    parser.add_argument("--url", type=str, help="URL TikTok unique à télécharger")
    parser.add_argument("--input-file", type=str, help="Fichier d'entrée (URLs ou HTML)")
    args = parser.parse_args()

    cfg.init_from_args(args)
    cfg.ensure_dirs()

    # Vérifier yt-dlp
    if not os.path.isfile(cfg.YTDLP_PATH):
        log.error(f"yt-dlp.exe introuvable : {cfg.YTDLP_PATH}")
        sys.exit(1)

    result = ScriptResult("dl_tiktok")

    # Déterminer la source
    if args.url:
        log.info(f"Mode URL unique : {args.url}")
        data = extract_from_single_url(args.url)
    elif args.input_file:
        log.info(f"Mode fichier : {args.input_file}")
        data = extract_from_file(args.input_file)
    else:
        log.info(f"Mode fichier par défaut : {cfg.INPUT_TIKTOK}")
        data = extract_from_file(cfg.INPUT_TIKTOK)

    log.info(f"{len(data)} vidéo(s) identifiée(s)")

    if not data:
        log.warning("Aucune vidéo à traiter")
        result.print_summary()
        result.exit()

    process_downloads(data, result)
    result.print_summary()
    result.exit()


if __name__ == "__main__":
    main()
