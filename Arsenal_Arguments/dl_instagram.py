"""
dl_instagram.py — Téléchargeur Instagram pour Arsenal Intelligence Unit.

Gère vidéos, images, carrousels mixtes (images + vidéos).
Multiples fallbacks : yt-dlp → manifest → img_index probe → thumbnail.
Trois modes d'entrée : JSON scraper navigateur, HTML, liste d'URLs.

Usage :
    python dl_instagram.py                                     # depuis dl_insta_video_image.json
    python dl_instagram.py --url "https://instagram.com/p/..."  # une seule URL
    python dl_instagram.py --input-file "mon_fichier.json"      # fichier custom
"""

import os
import re
import sys
import json
import hashlib
import argparse
import subprocess
import urllib.request
from urllib.parse import urlparse
from datetime import datetime

from arsenal_config import (
    cfg, GLOBAL_CSV_COLUMNS, CSV_ENCODING, MAX_CAROUSEL_SLIDES,
    IG_POST_DIR_PREFIX, VIDEO_EXTS, IMAGE_EXTS,
    safe_username, is_numeric_like_username, normalize_str, now_timestamp,
    append_to_csv, load_csv, ScriptResult, get_logger,
)

log = get_logger("dl_instagram")
DEBUG = True


def dbg(msg):
    if DEBUG:
        log.debug(msg)


# =============================================================================
# CHEMINS HELPERS
# =============================================================================

def post_media_dir_path(post_id: str) -> str:
    post_id = str(post_id or "").strip()
    return os.path.join(cfg.IMAGE_DIR, f"{IG_POST_DIR_PREFIX}{post_id}" if post_id else "_unknown")


def post_media_dir_from_post_id(post_id: str) -> str:
    d = post_media_dir_path(post_id)
    os.makedirs(d, exist_ok=True)
    return d


def post_media_dir_from_base_name(base_name: str) -> str:
    post_id = str(base_name or "").split("_")[0]
    return post_media_dir_from_post_id(post_id)


def build_slide_out_stem(base_name: str, idx: int) -> str:
    post_dir = post_media_dir_from_base_name(base_name)
    return os.path.join(post_dir, f"{int(idx):02d}")


def list_post_media_files_any(base_name: str):
    """Liste les fichiers média d'un post (nouveau format sous-dossier + legacy à plat)."""
    out = []
    post_dir = post_media_dir_from_base_name(base_name)
    if os.path.isdir(post_dir):
        for f in os.listdir(post_dir):
            p = os.path.join(post_dir, f)
            if not os.path.isfile(p):
                continue
            m_new = re.match(r"^(\d{2})\.[^.]+$", f)
            m_old = re.match(rf"^{re.escape(base_name)}_(\d{{2}})\.[^.]+$", f)
            m = m_new or m_old
            if not m:
                continue
            out.append((int(m.group(1)), f, p))

    if os.path.isdir(cfg.IMAGE_DIR):
        for f in os.listdir(cfg.IMAGE_DIR):
            p = os.path.join(cfg.IMAGE_DIR, f)
            if not os.path.isfile(p):
                continue
            m = re.match(rf"^{re.escape(base_name)}_(\d{{2}})\.[^.]+$", f)
            if not m:
                continue
            out.append((int(m.group(1)), f, p))

    uniq = {}
    for idx, f, p in out:
        uniq[(idx, p)] = (idx, f, p)
    out = list(uniq.values())
    out.sort(key=lambda x: (x[0], x[1].lower()))
    return out


def _existing_indices_for_post(base_name: str):
    return {idx for idx, _, _ in list_post_media_files_any(base_name)}


def _next_free_slide_index(base_name: str, start_at: int = 1):
    used = _existing_indices_for_post(base_name)
    idx = max(1, int(start_at))
    while idx in used:
        idx += 1
    return idx


# =============================================================================
# PARSING DES ENTRÉES
# =============================================================================

def normalize_insta_url(url: str):
    if not url:
        return None
    url = url.replace("\u00A0", "").strip()
    if url.startswith("/"):
        url = "https://www.instagram.com" + url
    url = url.split("?")[0].split("#")[0].strip()
    if not re.match(r"^https://www\.instagram\.com/(p|reel|reels)/[A-Za-z0-9_-]+/?$", url):
        return None
    if not url.endswith("/"):
        url += "/"
    return url


def infer_type_from_url(url: str) -> str:
    m = re.search(r"instagram\.com/(p|reel|reels)/", url)
    if not m:
        return "Auto"
    return "Video" if m.group(1) in {"reel", "reels"} else "Auto"


def clean_insta_alt(alt_text):
    hashtags = re.findall(r"#\\w+", alt_text or "")
    description = (alt_text or "").split("#")[0]
    if "Photo by" in description:
        description = description.split("on")[0]
    return " ".join(hashtags), description.strip()


def _normalize_slide_kind(raw_kind: str, slide_url: str):
    k = (raw_kind or "").strip().lower()
    if k in {"image", "img", "photo"}:
        return "image"
    if k in {"video", "vid"}:
        return "video"
    if re.search(r"\\.(mp4|mov|webm)(?:\\?|$)", slide_url or "", flags=re.I):
        return "video"
    return "image"


def choose_best_instagram_username(dom_username, dom_display, ytdlp_uploader_id, ytdlp_uploader):
    candidates = [dom_username, ytdlp_uploader, ytdlp_uploader_id, dom_display]
    for raw in candidates:
        if not raw:
            continue
        clean = safe_username(raw)
        if not is_numeric_like_username(clean):
            return clean
    return safe_username(dom_display or "auteur_inconnu")


# --- Extraction depuis JSON (scraper navigateur) ---

def _normalize_browser_json_item(obj: dict):
    raw_url = obj.get("url") or obj.get("href") or obj.get("post_url") or ""
    url = normalize_insta_url(str(raw_url).strip())
    if not url:
        return None

    raw_id = obj.get("id") or obj.get("shortcode") or obj.get("post_id") or url.strip("/").split("/")[-1]
    post_id = str(raw_id).strip() or url.strip("/").split("/")[-1]

    raw_type = str(obj.get("type", "")).strip()
    if raw_type not in {"Video", "Carrousel", "Image", "Auto", "Post"}:
        raw_type = infer_type_from_url(url)
    if raw_type == "Post":
        raw_type = "Auto"

    dom_username = str(obj.get("username") or "").strip()
    display_name = str(obj.get("display_name") or obj.get("author") or "Auteur_Inconnu").strip() or "Auteur_Inconnu"
    hashtags = obj.get("hashtags", "")
    if isinstance(hashtags, list):
        hashtags = " ".join([str(x).strip() for x in hashtags if str(x).strip()])
    description = str(obj.get("description") or obj.get("caption") or "").strip()
    thumbnail_url = str(obj.get("thumbnail_url") or obj.get("thumbnail") or "").strip()

    carousel_items = obj.get("carousel_items", [])
    if not isinstance(carousel_items, list):
        carousel_items = []

    normalized_slides = []
    for s in carousel_items:
        if not isinstance(s, dict):
            continue
        surl = str(s.get("url") or "").strip()
        if not surl:
            continue
        skind = _normalize_slide_kind(str(s.get("kind") or s.get("type") or ""), surl)
        try:
            sidx = int(s.get("index", len(normalized_slides) + 1))
        except Exception:
            sidx = len(normalized_slides) + 1
        normalized_slides.append({"index": sidx, "kind": skind, "url": surl})

    content_type = raw_type
    if content_type == "Auto":
        if len(normalized_slides) > 1:
            content_type = "Carrousel"
        elif len(normalized_slides) == 1:
            content_type = "Video" if normalized_slides[0]["kind"] == "video" else "Image"
        else:
            content_type = infer_type_from_url(url)

    return {
        "id": post_id, "url": url, "plateforme": "Instagram",
        "type": content_type,
        "username": safe_username(dom_username) if dom_username else "",
        "display_name": display_name, "hashtags": str(hashtags or ""),
        "description": description, "thumbnail_url": thumbnail_url,
        "views_at_extraction": str(obj.get("views_at_extraction", "") or ""),
        "source_input_mode": "BROWSER_JSON",
        "carousel_items": normalized_slides,
    }


def extract_from_json(raw_text: str):
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []

    entries, seen = [], set()
    for obj in payload:
        if not isinstance(obj, dict):
            continue
        norm = _normalize_browser_json_item(obj)
        if not norm or norm["id"] in seen:
            continue
        seen.add(norm["id"])
        entries.append(norm)
    return entries


def extract_from_urls(raw_text: str):
    entries, seen = [], set()
    for line in raw_text.splitlines():
        url = normalize_insta_url(line)
        if not url:
            continue
        post_id = url.strip("/").split("/")[-1]
        if post_id in seen:
            continue
        seen.add(post_id)
        entries.append({
            "id": post_id, "url": url, "plateforme": "Instagram",
            "type": infer_type_from_url(url), "username": "auteur_inconnu",
            "display_name": "Auteur_Inconnu", "hashtags": "", "description": "",
            "thumbnail_url": "", "views_at_extraction": "",
            "source_input_mode": "URL_LIST", "carousel_items": [],
        })
    return entries


def extract_from_html(raw_text: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.error("beautifulsoup4 requis pour le mode HTML")
        return []

    soup = BeautifulSoup(raw_text, "html.parser")
    entries, seen = [], set()

    for item in soup.find_all("a", href=re.compile(r"/(p|reel|reels)/")):
        href = (item.get("href") or "").replace("\u00A0", "").strip()
        url = normalize_insta_url(href)
        if not url:
            continue
        post_id = url.strip("/").split("/")[-1]
        if post_id in seen:
            continue
        seen.add(post_id)

        content_type = "Auto"
        svg_label = item.find("svg", {"aria-label": True})
        if svg_label:
            label = (svg_label.get("aria-label") or "").strip().lower()
            if label in {"clip", "reel"}:
                content_type = "Video"
            elif label in {"carrousel", "carousel"}:
                content_type = "Carrousel"
            elif label in {"photo", "image"}:
                content_type = "Image"
        if content_type == "Auto":
            content_type = infer_type_from_url(url)

        img_tag = item.find("img")
        alt_text = img_tag.get("alt", "") if img_tag else ""
        thumb = img_tag.get("src", "") if img_tag else ""
        hashtags, clean_desc = clean_insta_alt(alt_text)

        display_name = "Auteur_Inconnu"
        if alt_text and alt_text.lower().startswith("photo by "):
            m = re.match(r"Photo by\\s+([^\\s]+)", alt_text, flags=re.IGNORECASE)
            if m:
                display_name = m.group(1)

        entries.append({
            "id": post_id, "url": url, "plateforme": "Instagram",
            "type": content_type, "username": "", "display_name": display_name,
            "hashtags": hashtags, "description": clean_desc,
            "thumbnail_url": thumb, "views_at_extraction": "",
            "source_input_mode": "HTML", "carousel_items": [],
        })
    return entries


def extract_from_single_url(url: str):
    url = normalize_insta_url(url)
    if not url:
        return []
    post_id = url.strip("/").split("/")[-1]
    return [{
        "id": post_id, "url": url, "plateforme": "Instagram",
        "type": infer_type_from_url(url), "username": "auteur_inconnu",
        "display_name": "Auteur_Inconnu", "hashtags": "", "description": "",
        "thumbnail_url": "", "views_at_extraction": "",
        "source_input_mode": "URL_SINGLE", "carousel_items": [],
    }]


def extract_from_file(file_path: str):
    if not os.path.isfile(file_path):
        log.error(f"Fichier source introuvable : {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    looks_like_json = raw.lstrip().startswith("[") and (
        '"url"' in raw or '"href"' in raw or '"shortcode"' in raw
    )
    looks_like_html = ("<a" in raw and "href" in raw) or ("<html" in raw.lower())

    if looks_like_json:
        data = extract_from_json(raw)
        if data:
            log.info("Mode entrée = JSON enrichi (browser/export)")
            return data
    if looks_like_html:
        log.info("Mode entrée = HTML")
        return extract_from_html(raw)

    log.info("Mode entrée = Liste d'URLs")
    return extract_from_urls(raw)


# =============================================================================
# YT-DLP HELPERS
# =============================================================================

def cookie_args():
    if os.path.isfile(cfg.COOKIES_INSTAGRAM):
        return ["--cookies", cfg.COOKIES_INSTAGRAM]
    return ["--cookies-from-browser", "chrome"]


def run_cmd(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def get_insta_post_meta(post_url):
    cmd = [
        cfg.YTDLP_PATH, "--print", "upload_date", "--print", "uploader_id",
        "--print", "uploader", "--no-warnings", "--ignore-no-formats-error",
        *cookie_args(), post_url,
    ]
    pub_date = datetime.now().strftime("%d%m%y")
    uploader_id, uploader = "", ""
    try:
        res = run_cmd(cmd, timeout=30)
        lines = [l.strip() for l in (res.stdout or "").splitlines() if l.strip()]
        for line in lines:
            if re.fullmatch(r"\\d{8}", line):
                try:
                    pub_date = datetime.strptime(line, "%Y%m%d").strftime("%d%m%y")
                except ValueError:
                    pass
                continue
            low = line.lower()
            if low in {"na", "none", "null"}:
                continue
            if not uploader_id:
                uploader_id = line
            elif not uploader:
                uploader = line
    except Exception:
        pass
    return {"pub_date_ddmmyy": pub_date, "uploader_id": uploader_id, "uploader": uploader}


def is_temp_or_partial_media_file(filename: str) -> bool:
    name = os.path.basename(str(filename or "")).lower()
    if any(m in name for m in [".part", ".ytdl", ".temp", ".tmp"]):
        return True
    if ".fdash-" in name or ".fhls-" in name:
        return True
    return False


def list_video_files_for_post_id(post_id: str):
    post_id = str(post_id or "").strip()
    if not post_id or not os.path.isdir(cfg.VIDEO_DIR):
        return []
    out = []
    for f in os.listdir(cfg.VIDEO_DIR):
        p = os.path.join(cfg.VIDEO_DIR, f)
        if not os.path.isfile(p):
            continue
        if not (f.startswith(f"{post_id}_") or f.startswith(f"{post_id}.")):
            continue
        if is_temp_or_partial_media_file(f):
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext not in {".mp4", ".mkv", ".webm", ".mov"}:
            continue
        try:
            if os.path.getsize(p) <= 0:
                continue
        except OSError:
            continue
        out.append(f)
    out.sort()
    return out


def has_local_media_for_post_id(post_id: str) -> bool:
    post_id = str(post_id or "").strip()
    if list_video_files_for_post_id(post_id):
        return True

    candidates = [f"{IG_POST_DIR_PREFIX}{post_id}"]
    if os.path.isdir(cfg.IMAGE_DIR):
        for name in os.listdir(cfg.IMAGE_DIR):
            p = os.path.join(cfg.IMAGE_DIR, name)
            if os.path.isdir(p) and (name == f"{IG_POST_DIR_PREFIX}{post_id}" or name.startswith(f"{IG_POST_DIR_PREFIX}{post_id}_")):
                candidates.append(name)

    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mkv", ".webm", ".mov"}
    seen = set()
    for dname in candidates:
        if dname in seen:
            continue
        seen.add(dname)
        dpath = os.path.join(cfg.IMAGE_DIR, dname)
        if not os.path.isdir(dpath):
            continue
        for f in os.listdir(dpath):
            p = os.path.join(dpath, f)
            if os.path.isfile(p) and not is_temp_or_partial_media_file(f):
                if os.path.splitext(f)[1].lower() in valid_exts:
                    try:
                        if os.path.getsize(p) > 0:
                            return True
                    except OSError:
                        continue
    return False


def already_downloaded(item_id):
    return has_local_media_for_post_id(str(item_id or "").strip())


# =============================================================================
# DOWNLOAD FUNCTIONS
# =============================================================================

def download_video(item, pub_date):
    out_tpl = os.path.join(cfg.VIDEO_DIR, f"{item['id']}_{item['username']}_{pub_date}.%(ext)s")
    cmd = [cfg.YTDLP_PATH, "--no-warnings", "--no-playlist", "-o", out_tpl, *cookie_args(), item["url"]]
    res = run_cmd(cmd, timeout=240)
    err = ((res.stderr or "") + "\\n" + (res.stdout or "")).strip()
    created = list_video_files_for_post_id(item["id"])
    return bool(created), err


def guess_ext_from_url(url: str, default="jpg"):
    try:
        path = urlparse(url).path
        ext = os.path.splitext(path)[1].lower().replace(".", "")
        if ext:
            return ext
    except Exception:
        pass
    return default


def download_binary_url(url: str, out_path: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.instagram.com/"})
    with urllib.request.urlopen(req, timeout=90) as r, open(out_path, "wb") as f:
        f.write(r.read())


def strip_url_query(url: str) -> str:
    try:
        p = urlparse(str(url or "").strip())
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return str(url or "").strip().split("?")[0]


def _is_blob_url(url: str) -> bool:
    return str(url or "").strip().lower().startswith("blob:")


def _hash_file_sha1(path_file: str):
    h = hashlib.sha1()
    with open(path_file, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def fetch_instagram_manifest(post_url):
    cmd = [cfg.YTDLP_PATH, "-J", "--no-warnings", "--ignore-no-formats-error", *cookie_args(), post_url]
    res = run_cmd(cmd, timeout=120)
    raw = (res.stdout or "").strip()
    err = ((res.stderr or "") + "\\n" + (res.stdout or "")).strip()
    if res.returncode != 0 or not raw:
        return None, err
    try:
        return json.loads(raw), err
    except Exception as e:
        return None, f"{err}\\nJSON_PARSE_ERROR={e}"


def classify_manifest_entry(entry):
    if not isinstance(entry, dict):
        return "unknown"
    ext = str(entry.get("ext") or "").lower()
    vcodec = str(entry.get("vcodec") or "").lower()
    formats = entry.get("formats") or []
    direct_url = str(entry.get("url") or "")

    if ext in {"jpg", "jpeg", "png", "webp", "heic"}:
        return "image"
    if ext in {"mp4", "mov", "webm", "mkv"}:
        return "video"
    if vcodec and vcodec != "none":
        return "video"
    if isinstance(formats, list) and len(formats) > 0:
        return "video"
    if re.search(r"\\.(jpg|jpeg|png|webp|heic)(?:\\?|$)", direct_url, flags=re.I):
        return "image"
    if re.search(r"\\.(mp4|mov|webm)(?:\\?|$)", direct_url, flags=re.I):
        return "video"
    return "unknown"


def best_image_url_from_entry(entry):
    direct_url = str(entry.get("url") or "")
    if re.search(r"\\.(jpg|jpeg|png|webp|heic)(?:\\?|$)", direct_url, flags=re.I):
        return direct_url
    thumbs = entry.get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        valid = sorted(
            [t for t in thumbs if isinstance(t, dict) and t.get("url")],
            key=lambda t: (int(t.get("width") or 0), int(t.get("height") or 0)), reverse=True,
        )
        if valid:
            return str(valid[0].get("url"))
    thumb = str(entry.get("thumbnail") or "")
    return thumb or ""


def download_video_entry_to_images_dir(entry, out_stem):
    direct_url = str(entry.get("url") or "").strip()
    webpage_url = str(entry.get("webpage_url") or "").strip()
    target = direct_url or webpage_url
    if not target:
        return False, "video_entry_no_url"

    cmd = [cfg.YTDLP_PATH, "--no-warnings", "--no-playlist", "-o", out_stem + ".%(ext)s", *cookie_args(), target]
    res = run_cmd(cmd, timeout=240)
    err = ((res.stderr or "") + "\\n" + (res.stdout or "")).strip()
    stem = os.path.basename(out_stem)
    stem_dir = os.path.dirname(out_stem) or cfg.IMAGE_DIR
    if os.path.isdir(stem_dir):
        created = [f for f in os.listdir(stem_dir) if f.startswith(stem + ".")]
    else:
        created = []
    return bool(created) or res.returncode == 0, err


def save_single_slide_from_manifest_entry(entry, out_stem):
    kind = classify_manifest_entry(entry)
    if kind == "image":
        img_url = best_image_url_from_entry(entry)
        if not img_url:
            return False, "image_url_absent"
        ext = guess_ext_from_url(img_url, default="jpg").lower()
        if ext not in {"jpg", "jpeg", "png", "webp", "heic"}:
            ext = "jpg"
        out_path = f"{out_stem}.{ext}"
        download_binary_url(img_url, out_path)
        return os.path.exists(out_path), ("" if os.path.exists(out_path) else "image_not_written")

    if kind == "video":
        return download_video_entry_to_images_dir(entry, out_stem)

    img_url = best_image_url_from_entry(entry)
    if img_url:
        ext = guess_ext_from_url(img_url, default="jpg").lower()
        if ext not in {"jpg", "jpeg", "png", "webp", "heic"}:
            ext = "jpg"
        out_path = f"{out_stem}.{ext}"
        download_binary_url(img_url, out_path)
        if os.path.exists(out_path):
            return True, ""
    return download_video_entry_to_images_dir(entry, out_stem)


def _download_thumbnail_direct_fallback(item, base_name):
    candidate_urls = []
    thumb_url = (item.get("thumbnail_url") or "").strip()
    if thumb_url:
        candidate_urls.append(thumb_url)

    slides = item.get("carousel_items") or []
    if isinstance(slides, list):
        for s in slides:
            if not isinstance(s, dict):
                continue
            surl = str(s.get("url") or "").strip()
            skind = str(s.get("kind") or s.get("type") or "").strip().lower()
            if surl and not _is_blob_url(surl) and skind != "video":
                candidate_urls.append(surl)
                break

    dedup, seen = [], set()
    for u in candidate_urls:
        key = strip_url_query(u)
        if not u or key in seen:
            continue
        seen.add(key)
        dedup.append(u)

    if not dedup:
        return False, "thumbnail_url_absent"

    last_err = "fallback_thumbnail_url_absent"
    for u in dedup:
        try:
            ext = guess_ext_from_url(u, default="jpg").lower()
            if ext not in {"jpg", "jpeg", "png", "webp", "heic"}:
                ext = "jpg"
            out_path = build_slide_out_stem(base_name, 1) + f".{ext}"
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            download_binary_url(u, out_path)
            if os.path.exists(out_path):
                return True, "fallback_thumbnail_url=OK"
            last_err = "fallback_thumbnail_url=download_no_file"
        except Exception as e:
            last_err = f"fallback_thumbnail_url_failed={e}"
    return False, last_err


def _media_signature_from_manifest_entry(entry: dict):
    kind = classify_manifest_entry(entry)
    direct = strip_url_query(entry.get("url") or "")
    thumb = strip_url_query(entry.get("thumbnail") or "")
    eid = str(entry.get("id") or "")
    if not direct:
        direct = strip_url_query(best_image_url_from_entry(entry))
    return (kind, eid, direct, thumb)


def download_carousel_from_browser_json(item, pub_date):
    slides = item.get("carousel_items") or []
    if not isinstance(slides, list) or not slides:
        return False, "carousel_items_absent", 0, {"created_indices": set(), "failed_indices": set(), "had_blob": False, "expected_count": 0}

    base_name = f"{item['id']}_{item['username']}_{pub_date}"
    created_count, errors = 0, []
    created_indices, failed_indices = set(), set()
    had_blob = False

    slides = sorted([s for s in slides if isinstance(s, dict)], key=lambda s: int(s.get("index", 9999)))
    deduped, seen_urls = [], set()
    for s in slides:
        u = str(s.get("url") or "").strip()
        if not u:
            continue
        ukey = strip_url_query(u)
        if ukey in seen_urls:
            continue
        seen_urls.add(ukey)
        deduped.append(s)

    for logical_pos, slide in enumerate(deduped, start=1):
        url = str(slide.get("url") or "").strip()
        kind = str(slide.get("kind") or "").strip().lower()
        try:
            file_idx = int(slide.get("index", logical_pos))
        except Exception:
            file_idx = logical_pos
        if file_idx < 1:
            file_idx = logical_pos

        if not url:
            errors.append(f"slide{file_idx}:url_absent")
            failed_indices.add(file_idx)
            continue
        if _is_blob_url(url):
            had_blob = True
            errors.append(f"slide{file_idx}:blob_url")
            failed_indices.add(file_idx)
            continue

        ext = guess_ext_from_url(url, default="jpg").lower()
        if kind == "video":
            if ext not in {"mp4", "mov", "webm"}:
                ext = "mp4"
        else:
            if ext not in {"jpg", "jpeg", "png", "webp", "heic"}:
                ext = "jpg"

        out_path = build_slide_out_stem(base_name, file_idx) + f".{ext}"
        try:
            download_binary_url(url, out_path)
            if os.path.exists(out_path):
                created_count += 1
                created_indices.add(file_idx)
            else:
                errors.append(f"slide{file_idx}:not_written")
                failed_indices.add(file_idx)
        except Exception as e:
            errors.append(f"slide{file_idx}:exception:{e}")
            failed_indices.add(file_idx)

    return created_count > 0, " | ".join(errors), created_count, {
        "created_indices": created_indices, "failed_indices": failed_indices,
        "had_blob": had_blob, "expected_count": len(deduped),
    }


def download_via_gallery_dl(item, pub_date):
    """Fallback gallery-dl : extrait le manifest JSON puis télécharge chaque média en direct.

    Utilisé quand yt-dlp n'expose ni `entries` ni URLs valides (cas connu pour
    certains carrousels d'images IG depuis 2026 — yt-dlp retourne le metadata
    mais pas les médias). gallery-dl utilise une stratégie différente
    (web_profile_app_v2 / GraphQL) qui passe là où yt-dlp échoue.

    Sauvegarde dans le layout Arsenal (01_raw_images/IG_<id>/NN.ext).
    Retourne (ok, err_msg, media_count).
    """
    if not os.path.isfile(cfg.COOKIES_INSTAGRAM):
        return False, "gallery_dl:cookies_absent", 0

    base_name = f"{item['id']}_{item['username']}_{pub_date}"
    cmd = [sys.executable, "-m", "gallery_dl", "-j",
           "--cookies", cfg.COOKIES_INSTAGRAM, item["url"]]
    try:
        res = run_cmd(cmd, timeout=180)
    except subprocess.TimeoutExpired:
        return False, "gallery_dl:timeout", 0

    if res.returncode != 0:
        stderr_tail = (res.stderr or "")[-300:].replace("\n", " | ")
        return False, f"gallery_dl:rc={res.returncode}:{stderr_tail}", 0

    raw = (res.stdout or "").strip()
    if not raw:
        return False, "gallery_dl:empty_stdout", 0

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return False, f"gallery_dl:json_parse={e}", 0

    if not isinstance(data, list):
        return False, "gallery_dl:not_a_list", 0

    media_urls = []
    for entry in data:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        if entry[0] != 3:
            continue
        url = entry[1]
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            media_urls.append(url)

    if not media_urls:
        return False, "gallery_dl:no_media_urls", 0

    next_idx = _next_free_slide_index(base_name, start_at=1)
    created = 0
    errors = []
    for url in media_urls:
        ext = guess_ext_from_url(url, default="jpg").lower()
        if ext not in {"jpg", "jpeg", "png", "webp", "heic",
                       "mp4", "mov", "webm", "mkv"}:
            ext = "jpg"
        out_path = build_slide_out_stem(base_name, next_idx) + f".{ext}"
        try:
            download_binary_url(url, out_path)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                created += 1
                next_idx = _next_free_slide_index(base_name, start_at=next_idx + 1)
            else:
                errors.append(f"gallery_dl:slide{next_idx}:not_written")
        except Exception as e:
            errors.append(f"gallery_dl:slide{next_idx}:{e}")

    return created > 0, " | ".join(errors), created


def download_carousel_by_img_index_probe(item, pub_date, target_indices=None, append_mode=True):
    base_name = f"{item['id']}_{item['username']}_{pub_date}"
    created_count, errors = 0, []
    seen_signatures, seen_file_hashes = set(), set()
    consecutive_failures = 0

    existing = list_post_media_files_any(base_name)
    used_indices = {idx for idx, _, _ in existing}
    for _, _, fpath in existing:
        try:
            seen_file_hashes.add(_hash_file_sha1(fpath))
        except Exception:
            pass

    if target_indices:
        idx_sequence = sorted({int(x) for x in target_indices if int(x) >= 1})
    else:
        idx_sequence = list(range(1, MAX_CAROUSEL_SLIDES + 1))

    for idx in idx_sequence:
        url_idx = f"{item['url']}?img_index={idx}"
        manifest, _ = fetch_instagram_manifest(url_idx)
        if manifest is None:
            consecutive_failures += 1
            errors.append(f"img_index={idx}:manifest_fail")
            if consecutive_failures >= 2 and not target_indices and idx > 1:
                break
            continue

        entries = manifest.get("entries")
        entry = entries[0] if isinstance(entries, list) and entries else manifest
        sig = _media_signature_from_manifest_entry(entry)
        if sig in seen_signatures:
            if not target_indices and created_count > 0:
                break
            continue
        seen_signatures.add(sig)

        out_idx = idx
        if append_mode and out_idx in used_indices:
            out_idx = _next_free_slide_index(base_name, start_at=max(used_indices | {idx}) + 1 if used_indices else idx)
        out_stem = build_slide_out_stem(base_name, out_idx)

        ok, err = save_single_slide_from_manifest_entry(entry, out_stem)
        if ok:
            created_file = None
            prefix = os.path.basename(out_stem) + "."
            out_dir = os.path.dirname(out_stem) or cfg.IMAGE_DIR
            if os.path.isdir(out_dir):
                for f in os.listdir(out_dir):
                    if f.startswith(prefix):
                        created_file = os.path.join(out_dir, f)
                        break

            is_dup = False
            if created_file and os.path.exists(created_file):
                try:
                    fhash = _hash_file_sha1(created_file)
                    if fhash in seen_file_hashes:
                        is_dup = True
                    else:
                        seen_file_hashes.add(fhash)
                except Exception:
                    pass

            if is_dup:
                try:
                    os.remove(created_file)
                except Exception:
                    pass
                if not target_indices and created_count > 0:
                    break
                continue

            created_count += 1
            used_indices.add(out_idx)
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            errors.append(f"img_index={idx}:{err or 'save_fail'}")
            if consecutive_failures >= 2 and not target_indices and idx > 1:
                break

    return created_count > 0, " | ".join(errors).strip(), created_count


def download_images_or_carousel(item, pub_date):
    """Stratégie complète : browser_json → manifest → probe → thumbnail."""
    base_name = f"{item['id']}_{item['username']}_{pub_date}"
    created_count = len(_existing_indices_for_post(base_name))
    errors, manifest_log = [], ""

    # Cas carrousel avec browser_json enrichi
    if item.get("type") == "Carrousel" and item.get("carousel_items"):
        ok_json, err_json, count_json, meta_json = download_carousel_from_browser_json(item, pub_date)
        created_count = len(_existing_indices_for_post(base_name))
        if err_json:
            errors.append(err_json)

        missing_indices = sorted(set(meta_json.get("failed_indices") or set()))
        expected_count = int(meta_json.get("expected_count") or 0)

        if meta_json.get("had_blob") or missing_indices:
            _, err_probe, _ = download_carousel_by_img_index_probe(item, pub_date, target_indices=missing_indices or None, append_mode=False)
            if err_probe:
                errors.append(err_probe)
            created_count = len(_existing_indices_for_post(base_name))

            if expected_count and created_count < expected_count:
                _, err_all, _ = download_carousel_by_img_index_probe(item, pub_date, target_indices=None, append_mode=True)
                if err_all:
                    errors.append(err_all)
                created_count = len(_existing_indices_for_post(base_name))

        if created_count > 0 and (not expected_count or created_count >= min(expected_count, MAX_CAROUSEL_SLIDES)):
            return True, " | ".join(x for x in errors if x), created_count

    # Fallback manifest
    manifest, manifest_log = fetch_instagram_manifest(item["url"])
    if manifest is not None:
        entries = manifest.get("entries")
        if not isinstance(entries, list) or not entries:
            entries = [manifest]
        next_idx = _next_free_slide_index(base_name, start_at=1)
        for entry in entries:
            out_stem = build_slide_out_stem(base_name, next_idx)
            try:
                ok, err = save_single_slide_from_manifest_entry(entry, out_stem)
                if ok:
                    next_idx = _next_free_slide_index(base_name, start_at=next_idx + 1)
                else:
                    errors.append(f"entry{next_idx}:{err or 'save_fail'}")
            except Exception as e:
                errors.append(f"entry{next_idx}:exception:{e}")
    else:
        errors.append("manifest_initial_fail")

    created_count = len(_existing_indices_for_post(base_name))

    # Probe append global — élargi à Auto/Image puisqu'une URL nue (URL_SINGLE)
    # arrive en type=Auto et aurait ignoré le probe avant ce changement.
    if item.get("type") in {"Carrousel", "Auto", "Image"} and created_count <= 1:
        _, err_probe, _ = download_carousel_by_img_index_probe(item, pub_date, target_indices=None, append_mode=True)
        if err_probe:
            errors.append(err_probe)
        created_count = len(_existing_indices_for_post(base_name))

    # Fallback gallery-dl — quand yt-dlp ne ramène ni entries, ni manifest,
    # ni URLs probables. gallery-dl passe via une stratégie GraphQL différente
    # qui récupère les carrousels d'images modernes là où yt-dlp échoue depuis 2026.
    if created_count == 0:
        _, err_gdl, _ = download_via_gallery_dl(item, pub_date)
        if err_gdl:
            errors.append(err_gdl)
        created_count = len(_existing_indices_for_post(base_name))

    # Fallback thumbnail (dernier recours absolu, ramène 1 image preview au moins)
    if created_count == 0:
        _, fb_msg = _download_thumbnail_direct_fallback(item, base_name)
        if fb_msg:
            errors.append(fb_msg)
        created_count = len(_existing_indices_for_post(base_name))

    merged = " | ".join(x for x in [manifest_log] + errors if x).strip()
    return created_count > 0, merged, created_count


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def process_downloads(media_list: list, result: ScriptResult):
    new_metadata = []

    for item in media_list:
        if already_downloaded(item["id"]):
            log.info(f"  SKIP {item['id']} (déjà téléchargé)")
            result.add_skip()
            continue

        log.info(f"  [{item.get('type', 'Auto')}] Traitement {item['id']}...")

        meta = get_insta_post_meta(item["url"])
        pub_date = meta["pub_date_ddmmyy"]

        dom_username = item.get("username", "")
        dom_display = item.get("display_name", "Auteur_Inconnu")
        item["username"] = choose_best_instagram_username(
            dom_username, dom_display, meta.get("uploader_id", ""), meta.get("uploader", ""),
        )
        if not item.get("display_name") or item["display_name"] == "Auteur_Inconnu":
            up = meta.get("uploader", "")
            up_id = meta.get("uploader_id", "")
            if up and not is_numeric_like_username(up):
                item["display_name"] = up
            elif dom_display:
                item["display_name"] = dom_display
            elif up_id and not is_numeric_like_username(up_id):
                item["display_name"] = up_id

        item["detected_type_initial"] = item.get("type", "Auto")
        item["resolved_type_final"] = ""
        item["download_mode"] = ""
        item["download_status"] = "FAILED"
        item["error_message"] = ""

        try:
            ok, err_video, err_img, media_count = False, "", "", 0

            if item["type"] == "Video":
                ok, err_video = download_video(item, pub_date)
                if ok:
                    item["download_mode"] = "video_direct"
                    item["resolved_type_final"] = "Video"
                else:
                    ok, err_img, media_count = download_images_or_carousel(item, pub_date)
                    if ok:
                        item["download_mode"] = "fallback_video_to_media"
                        item["resolved_type_final"] = "ImageOrCarousel"
            elif item["type"] in {"Image", "Carrousel"}:
                ok, err_img, media_count = download_images_or_carousel(item, pub_date)
                if ok:
                    item["download_mode"] = "image_or_carousel_full"
                    item["resolved_type_final"] = "Carrousel" if media_count > 1 else "Image"
                else:
                    ok, err_video = download_video(item, pub_date)
                    if ok:
                        item["download_mode"] = "fallback_image_to_video"
                        item["resolved_type_final"] = "Video"
            else:
                ok, err_video = download_video(item, pub_date)
                if ok:
                    item["download_mode"] = "auto_video_first_success"
                    item["resolved_type_final"] = "Video"
                else:
                    ok, err_img, media_count = download_images_or_carousel(item, pub_date)
                    if ok:
                        item["download_mode"] = "auto_video_then_carousel"
                        item["resolved_type_final"] = "Carrousel" if media_count > 1 else "Image"

            prefix = f"{item['id']}_{item['username']}_{pub_date}"
            created_video = list_video_files_for_post_id(item["id"])
            created_img = [os.path.basename(p) for _, _, p in list_post_media_files_any(prefix)]

            item["filename"] = created_video[0] if created_video else item["id"]
            item["date_publication"] = pub_date
            item["download_timestamp"] = now_timestamp()

            trace = " | ".join(x for x in [err_video, err_img] if x).strip()

            if ok and has_local_media_for_post_id(item["id"]):
                item["download_status"] = "SUCCESS"
                item["error_message"] = trace[:1000]
                result.add_success()
                log.info(f"  ✅ {item['id']} téléchargé")
            else:
                item["download_status"] = "FAILED"
                item["error_message"] = (" | ".join(x for x in [trace, "post_download_verify:no_local_media"] if x))[:1000]
                result.add_fail(f"{item['id']}: échec malgré fallback")
                log.warning(f"  ⚠️ Échec {item['id']}")

            new_metadata.append(item)

        except Exception as e:
            item["filename"] = item["id"]
            item["date_publication"] = pub_date
            item["download_timestamp"] = now_timestamp()
            item["download_mode"] = "exception"
            item["error_message"] = str(e)[:1000]
            new_metadata.append(item)
            result.add_fail(f"{item['id']}: exception {e}")
            log.error(f"  ⚠️ Exception {item['id']} → {e}")

    if new_metadata:
        append_to_csv(new_metadata, cfg.CSV_PATH)
        log.info(f"CSV mis à jour : {len(new_metadata)} ligne(s) ajoutée(s)")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Arsenal — Téléchargeur Instagram")
    cfg.add_base_dir_arg(parser)
    parser.add_argument("--url", type=str, help="URL Instagram unique")
    parser.add_argument("--input-file", type=str, help="Fichier d'entrée (JSON/HTML/URLs)")
    args = parser.parse_args()

    cfg.init_from_args(args)
    cfg.ensure_dirs()

    if not os.path.isfile(cfg.YTDLP_PATH):
        log.error(f"yt-dlp.exe introuvable : {cfg.YTDLP_PATH}")
        sys.exit(1)

    result = ScriptResult("dl_instagram")

    if args.url:
        log.info(f"Mode URL unique : {args.url}")
        data = extract_from_single_url(args.url)
    elif args.input_file:
        log.info(f"Mode fichier : {args.input_file}")
        data = extract_from_file(args.input_file)
    else:
        log.info(f"Mode fichier par défaut : {cfg.INPUT_INSTAGRAM}")
        data = extract_from_file(cfg.INPUT_INSTAGRAM)

    log.info(f"{len(data)} élément(s) identifié(s)")

    if not data:
        log.warning("Aucun contenu à traiter")
        result.print_summary()
        result.exit()

    process_downloads(data, result)
    result.print_summary()
    result.exit()


if __name__ == "__main__":
    main()
