import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

"""
arsenal_pipeline.py — Cog d'automatisation du pipeline Arsenal Intelligence Unit.

Écoute le salon #arsenal-liens pour détecter les liens TikTok/Instagram,
puis exécute le pipeline complet :
    1. Téléchargement (dl_tiktok.py / dl_instagram.py)
    2. Normalisation CSV (csv_normalize.py)
    3. Transcription Whisper (arsenal_transcribe.ps1)
    4. Résumé Claude (summarize.py)
    5. Publication Discord (sync interne via arsenal_publisher)

Usage :
    Ajouter "extensions.arsenal_pipeline" dans bot.py setup_hook.
    Poster un lien TikTok ou Instagram dans #arsenal-liens.
    Le bot traite automatiquement.

Commandes manuelles :
    !pipeline <url>           — Lancer le pipeline sur une URL
    !pipeline_batch           — Traiter tous les PENDING (download→publish)
    !pipeline_status          — État du pipeline
    !pipeline_resummarize     — Re-résumer tous les contenus (migration batch)
"""

import os
import re
import sys
import json
import asyncio
import subprocess
from datetime import datetime
from typing import Optional, List, Tuple

import discord
from discord.ext import commands

from arsenal_config import cfg, get_logger, load_engine_pref

log = get_logger("arsenal_pipeline")

# =============================================================================
# CONFIGURATION
# =============================================================================

# Salon d'écoute : 🔗・liens dans 🔒 PERSONNEL (ISTIC L1 G2).
# Migration 2026-04-29 — précédemment #liens sur le serveur Veille (1493701174656766122).
LISTEN_CHANNEL_ID = 1498918445763268658

# Salon de logs : 📋・logs dans BLABLA (ISTIC L1 G2).
# Migration 2026-04-29 — précédemment #logs sur le serveur Veille (1475955504332411187).
LOG_CHANNEL_ID = 1493760267300110466

# Admin Discord
ADMIN_USER_ID = 200750717437345792

# Chemins des scripts (relatifs à Arsenal_Arguments)
ARSENAL_DIR = cfg.base_path
BOT_DIR = os.path.dirname(ARSENAL_DIR)

# Python et PowerShell
PYTHON_EXE = sys.executable
POWERSHELL_EXE = "powershell.exe"

# Timeout par étape (secondes)
TIMEOUTS = {
    "download": 300,       # 5 min
    "normalize": 60,       # 1 min
    "transcribe": 600,     # 10 min
    "ocr": 600,            # 10 min (easyocr GPU init ~5s, puis ~1s/image)
    "summarize": 120,      # 2 min par contenu
    "sync": 300,           # 5 min
}


# =============================================================================
# REGEX DÉTECTION URLS
# =============================================================================

TIKTOK_PATTERNS = [
    re.compile(r"https?://(?:www\.)?tiktok\.com/@[\w.-]+/video/(\d+)", re.IGNORECASE),
    re.compile(r"https?://(?:vm|vt)\.tiktok\.com/[\w]+", re.IGNORECASE),
]

INSTAGRAM_PATTERNS = [
    re.compile(r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/([\w-]+)", re.IGNORECASE),
]

# Multi-plateforme : utilisé par !scrape_channel. La 1ʳᵉ valeur du tuple
# alimente la colonne CSV `plateforme` (cohérent avec dl_generic.py).
_URL_TAIL = r"(?:\?[^\s)]*)?"
ALL_PLATFORM_PATTERNS = [
    ("TikTok",    re.compile(r"https?://(?:www\.)?tiktok\.com/@[\w.-]+/video/\d+" + _URL_TAIL, re.IGNORECASE)),
    ("TikTok",    re.compile(r"https?://(?:vm|vt)\.tiktok\.com/[\w]+" + _URL_TAIL, re.IGNORECASE)),
    ("Instagram", re.compile(r"https?://(?:www\.)?instagram\.com/(?:p|reel|reels|tv)/[\w-]+" + _URL_TAIL, re.IGNORECASE)),
    ("YouTube",   re.compile(r"https?://(?:www\.|m\.)?youtube\.com/watch\?v=[\w-]+" + _URL_TAIL, re.IGNORECASE)),
    ("YouTube",   re.compile(r"https?://youtu\.be/[\w-]+" + _URL_TAIL, re.IGNORECASE)),
    ("YouTube",   re.compile(r"https?://(?:www\.|m\.)?youtube\.com/shorts/[\w-]+" + _URL_TAIL, re.IGNORECASE)),
    ("X",         re.compile(r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/[\w]+/status/\d+" + _URL_TAIL, re.IGNORECASE)),
    ("Reddit",    re.compile(r"https?://(?:www\.|old\.|new\.)?reddit\.com/r/[\w]+/comments/[\w]+(?:/[\w-]*)?" + _URL_TAIL, re.IGNORECASE)),
    ("Threads",   re.compile(r"https?://(?:www\.)?threads\.(?:net|com)/@[\w.-]+/post/[\w-]+" + _URL_TAIL, re.IGNORECASE)),
]


def extract_urls_all_platforms(text: str) -> List[dict]:
    """Extrait toutes les URLs reconnues (6 plateformes), dédupliquées par URL.
    L'ordre du retour suit l'ordre d'apparition dans `text`."""
    if not text:
        return []
    found = []
    seen = set()
    for platform, pattern in ALL_PLATFORM_PATTERNS:
        for match in pattern.finditer(text):
            url = match.group(0).rstrip(".,;:!?)…")
            if url not in seen:
                seen.add(url)
                found.append({"url": url, "platform": platform})
    return found


def extract_id_for_platform(url: str, platform: str) -> Optional[str]:
    """Symétrique à dl_generic.extract_content_id côté cog (sans import).
    Utilisé pour la dédup CSV avant de lancer le subprocess."""
    if platform == "TikTok":
        m = re.search(r"/video/(\d+)", url)
        return m.group(1) if m else None
    if platform == "Instagram":
        m = re.search(r"/(?:p|reel|reels|tv)/([\w-]+)", url, re.IGNORECASE)
        return m.group(1) if m else None
    if platform == "Threads":
        m = re.search(r"/post/([\w-]+)", url, re.IGNORECASE)
        return m.group(1) if m else None
    if platform == "YouTube":
        m = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]+)", url, re.IGNORECASE)
        return m.group(1) if m else None
    if platform == "X":
        m = re.search(r"/status/(\d+)", url)
        return m.group(1) if m else None
    if platform == "Reddit":
        m = re.search(r"/comments/([\w]+)", url, re.IGNORECASE)
        return m.group(1) if m else None
    return None


def csv_known_pairs() -> set:
    """Retourne l'ensemble des (plateforme_lower, id) déjà au CSV (tout statut).
    Lecture unique, utilisée par scrape_channel pour la dédup en bloc."""
    if not os.path.isfile(cfg.CSV_PATH):
        return set()
    try:
        import pandas as pd
        df = pd.read_csv(cfg.CSV_PATH, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        if "id" not in df.columns or "plateforme" not in df.columns:
            return set()
        pairs = set()
        for _, row in df.iterrows():
            plat = str(row["plateforme"]).strip().lower()
            cid = str(row["id"]).strip()
            if plat and cid:
                pairs.add((plat, cid))
        return pairs
    except Exception as e:
        log.warning(f"csv_known_pairs : lecture CSV échouée ({e})")
        return set()


def resolve_tiktok_short_url(url: str) -> str:
    """Résout un lien court TikTok (vm.tiktok.com / vt.tiktok.com) vers
    l'URL longue `tiktok.com/@user/video/<id>`.

    Fait une requête HEAD qui suit les redirections et lit l'URL finale.
    Retourne l'URL originale si ce n'est pas un lien court ou si la
    résolution échoue.
    """
    if not re.search(r"https?://(?:vm|vt)\.tiktok\.com/", url, re.IGNORECASE):
        return url

    import urllib.request
    try:
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl() or url
    except Exception as e:
        log.warning(f"Résolution lien court TikTok échouée ({url}) : {e}")
        return url

    if final_url != url:
        log.info(f"TikTok short URL résolu : {url} -> {final_url}")
    return final_url


async def resolve_tiktok_short_url_async(url: str) -> str:
    """Wrapper async pour resolve_tiktok_short_url (exécuté dans un thread
    pour ne pas bloquer la loop)."""
    return await asyncio.to_thread(resolve_tiktok_short_url, url)


def extract_urls(text: str) -> List[dict]:
    """Extrait les URLs des 6 plateformes (TikTok, Instagram, YouTube, X,
    Reddit, Threads). La plateforme est retournée en lowercase pour rester
    compatible avec le routing downstream (step_download, run_pipeline)."""
    return [
        {"url": item["url"], "platform": item["platform"].lower()}
        for item in extract_urls_all_platforms(text)
    ]


# =============================================================================
# SUBPROCESS RUNNER
# =============================================================================

async def run_script(cmd: list, cwd: str, timeout: int, label: str) -> dict:
    """
    Exécute un script en subprocess async.
    Retourne {ok, stdout, stderr, duration, return_code}.
    """
    start = datetime.now()
    log.info(f"[{label}] Lancement : {' '.join(cmd[:4])}...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            duration = (datetime.now() - start).total_seconds()
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"TIMEOUT après {timeout}s",
                "duration": duration,
                "return_code": -1,
            }

        duration = (datetime.now() - start).total_seconds()
        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        # Tenter de parser le JSON ScriptResult depuis stdout
        script_result = None
        if stdout_str:
            # Le JSON est la dernière partie de stdout (après les logs stderr)
            for line in reversed(stdout_str.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        script_result = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        pass

        return {
            "ok": proc.returncode == 0,
            "stdout": stdout_str[-2000:],  # Limiter la taille
            "stderr": stderr_str[-2000:],
            "duration": duration,
            "return_code": proc.returncode,
            "script_result": script_result,
        }

    except Exception as e:
        duration = (datetime.now() - start).total_seconds()
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(e),
            "duration": duration,
            "return_code": -1,
        }


# =============================================================================
# PIPELINE STEPS
# =============================================================================

async def step_download(url: str, platform: str) -> dict:
    """Étape 1 : Téléchargement.
    TikTok → dl_tiktok.py (parsing HTML/alt natif).
    Instagram → dl_instagram.py (carrousels + fallbacks multiples).
    YouTube/X/Reddit/Threads → dl_generic.py (yt-dlp universel).
    """
    if platform == "tiktok":
        script = os.path.join(ARSENAL_DIR, "dl_tiktok.py")
    elif platform == "instagram":
        script = os.path.join(ARSENAL_DIR, "dl_instagram.py")
    else:
        return await step_dl_generic(url, source_input_mode="LISTEN_CHANNEL")

    cmd = [PYTHON_EXE, script, "--url", url, "--base-dir", ARSENAL_DIR]
    return await run_script(cmd, ARSENAL_DIR, TIMEOUTS["download"], f"DL-{platform}")


async def step_normalize() -> dict:
    """Étape 2 : Normalisation CSV."""
    cmd = [PYTHON_EXE, os.path.join(ARSENAL_DIR, "csv_normalize.py"), "--base-dir", ARSENAL_DIR]
    return await run_script(cmd, ARSENAL_DIR, TIMEOUTS["normalize"], "NORMALIZE")


async def step_dl_generic(url: str, source_input_mode: str = "SCRAPE_CHANNEL") -> dict:
    """Téléchargement universel via dl_generic.py (toutes plateformes)."""
    script = os.path.join(ARSENAL_DIR, "dl_generic.py")
    cmd = [
        PYTHON_EXE, script,
        "--url", url,
        "--source-input-mode", source_input_mode,
        "--base-dir", ARSENAL_DIR,
    ]
    return await run_script(cmd, ARSENAL_DIR, TIMEOUTS["download"], "DL-GENERIC")


async def step_dl_tiktok(url: str) -> dict:
    """Téléchargement TikTok via dl_tiktok.py (parsing HTML/alt text natif)."""
    script = os.path.join(ARSENAL_DIR, "dl_tiktok.py")
    cmd = [PYTHON_EXE, script, "--url", url, "--base-dir", ARSENAL_DIR]
    return await run_script(cmd, ARSENAL_DIR, TIMEOUTS["download"], "DL-TIKTOK")


async def step_dl_instagram(url: str) -> dict:
    """Téléchargement Instagram via dl_instagram.py (gère carrousels et fallbacks).
    Utilisé aussi pour Threads (cookies Meta partagés)."""
    script = os.path.join(ARSENAL_DIR, "dl_instagram.py")
    cmd = [PYTHON_EXE, script, "--url", url, "--base-dir", ARSENAL_DIR]
    return await run_script(cmd, ARSENAL_DIR, TIMEOUTS["download"], "DL-INSTAGRAM")


def _probe_max_video_duration_seconds() -> Optional[float]:
    """Scanne `01_raw_videos/` pour les MP4/WEBM/MOV qui n'ont pas encore de
    transcription correspondante dans `02_whisper_transcripts/`. Retourne
    la durée max trouvée via `ffprobe`. Sert à dimensionner le timeout
    Whisper dynamiquement (cas Instagram Reel longs en VP9 qui crashent
    le timeout fixe 600s).

    Retourne None si ffprobe échoue ou aucun fichier trouvé.
    """
    src_dir = os.path.join(ARSENAL_DIR, "01_raw_videos")
    out_dir = os.path.join(ARSENAL_DIR, "02_whisper_transcripts")
    if not os.path.isdir(src_dir):
        return None
    max_dur = 0.0
    for fn in os.listdir(src_dir):
        if not fn.lower().endswith((".mp4", ".webm", ".mov", ".mkv", ".m4v")):
            continue
        base = os.path.splitext(fn)[0]
        if os.path.isfile(os.path.join(out_dir, f"{base}.txt")):
            continue  # déjà transcrit
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1",
                 os.path.join(src_dir, fn)],
                capture_output=True, text=True, timeout=10,
            )
            d = float(res.stdout.strip())
            if d > max_dur:
                max_dur = d
        except (subprocess.SubprocessError, ValueError, OSError):
            continue
    return max_dur if max_dur > 0 else None


async def step_transcribe() -> dict:
    """Étape 3 : Transcription Whisper.

    Appelle directement whisper_engine.ps1 (le wrapper arsenal_transcribe.ps1
    a un bug de splat d'arguments qui relie mal -Model).

    Phase Y.8 : timeout dynamique basé sur la durée max des vidéos en
    attente. Whisper sur RTX 2060 + int8_float16 + VAD prend ~2-3× la
    durée de la vidéo (plus en VP9 que H.264). Avec marge de sécurité 4×
    + plancher 10 min + plafond 60 min. Sans ce dynamisme, un Reel
    Instagram de 4min17s en VP9 échouait au timeout fixe 600s (cas
    `DXuQxSOCMxj` du 30/04 — Whisper coupé à 624s alors qu'il était
    encore en cours).
    """
    script = os.path.join(ARSENAL_DIR, "whisper_engine.ps1")
    src_dir = os.path.join(ARSENAL_DIR, "01_raw_videos")
    out_root = os.path.join(ARSENAL_DIR, "02_whisper_transcripts")
    log_root = os.path.join(ARSENAL_DIR, "02_whisper_logs", "videos")

    # Timeout dynamique : 4× la durée max + plancher 10min + plafond 60min
    base_timeout = TIMEOUTS["transcribe"]  # 600s par défaut (plancher)
    max_dur = _probe_max_video_duration_seconds()
    if max_dur:
        dynamic = int(max_dur * 4)
        timeout = max(base_timeout, min(dynamic, 3600))
        log.info(f"step_transcribe : durée max {max_dur:.0f}s → timeout {timeout}s")
    else:
        timeout = base_timeout
        log.info(f"step_transcribe : durée non probée, timeout par défaut {timeout}s")

    cmd = [
        POWERSHELL_EXE,
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", script,
        "-SrcDir", src_dir,
        "-OutRoot", out_root,
        "-LogRoot", log_root,
        "-Model", "large-v3",
        "-Lang", "fr",
        "-Device", "cuda",
        "-ComputeType", "int8_float16",
    ]
    return await run_script(cmd, ARSENAL_DIR, timeout, "WHISPER")


async def step_ocr() -> dict:
    """Y.21 — Étape 3bis : OCR easyocr sur les dossiers
    `01_raw_images/<PREFIX><id>/` produits par dl_generic gallery-dl
    (X / Threads / Reddit) et dl_instagram (carrousels IG).

    Idempotent : skip silencieux les dossiers ayant déjà un
    `<id>_ocr.txt` dans `02_whisper_transcripts/`. Inclus le pré-filtre
    qui évite de charger easyocr GPU si rien à faire (cf.
    `ocr_carousels.py:main`). Coût zéro quand rien à OCR-iser.
    """
    script = os.path.join(ARSENAL_DIR, "ocr_carousels.py")
    cmd = [PYTHON_EXE, script, "--base-dir", ARSENAL_DIR]
    return await run_script(cmd, ARSENAL_DIR, TIMEOUTS["ocr"], "OCR")


def _check_quota_before_summarize() -> dict:
    """Check quota Pro Max avant de lancer summarize.py sur un drop arsenal.
    Retourne {can_proceed: bool, reason: str, message: str}.
    Mode tolérant : si check fail (pas de cookie, network down…), on autorise
    par défaut pour ne pas bloquer le pipeline sur une panne du quota watcher.
    Réutilise les seuils configurés dans la GUI summarize_gui (state JSON).

    Phase Y.16 : bypass complet si engine_pref = 'api'. Le quota Pro Max
    ne s'applique qu'au CLI Claude Code (subscription). L'API Anthropic
    est facturée séparément (crédits) et n'a rien à voir avec le quota
    hebdo claude.ai.

    Phase Y.19 : auto-clear du flag persistant `weekly_throttled` quand
    le live quota check montre qu'on est redescendu sous le seuil. Avant
    Y.19, le flag restait sticky tant que le user n'avait pas cliqué
    « Reset throttle hebdo » dans la GUI (et la GUI n'auto-clear que
    quand elle tourne en foreground). Conséquence : un drop posté
    plusieurs jours après le throttle initial était bloqué « quota
    atteint » alors que le quota live était à 5 %. On regarde donc
    AVANT le check du flag persistant si la live quota est OK ; si oui,
    on auto-clear le flag et on continue.
    """
    try:
        if ARSENAL_DIR not in sys.path:
            sys.path.insert(0, ARSENAL_DIR)
        from arsenal_config import load_engine_pref
        if load_engine_pref() == "api":
            return {"can_proceed": True, "reason": "engine_api",
                     "message": "Engine = api → quota Pro Max bypass (facturation API séparée)"}
        from claude_usage import (
            fetch_usage, load_state, save_state, has_cookie, fmt_duration,
            CookieMissingError, CookieExpiredError, NetworkError,
        )
        if not has_cookie():
            return {"can_proceed": True, "reason": "no_cookie",
                     "message": "Pas de cookie claude.ai configuré, quota check désactivé"}
        state = load_state()
        # Y.19 : on essaye de fetch la live quota AVANT de regarder le flag
        # persistant, pour pouvoir l'auto-clear si on est redescendu sous
        # le seuil.
        live_quota = None
        try:
            live_quota = fetch_usage()
        except (CookieExpiredError, NetworkError) as e:
            # Si le watcher est down, on retombe sur le flag persistant.
            if state.weekly_throttled:
                return {"can_proceed": False, "reason": "weekly_throttled",
                         "message": (f"Throttle hebdo actif depuis {state.weekly_throttled_at} "
                                     f"(live check unavailable: {type(e).__name__}). "
                                     f"Reset via le bouton 'Reset throttle hebdo' dans la GUI.")}
            return {"can_proceed": True, "reason": "quota_check_unavailable",
                     "message": f"Quota check indisponible ({type(e).__name__}), pipeline continue"}
        # Live quota dispo : auto-clear flag si on est sous le seuil.
        if state.weekly_throttled and live_quota.weekly_pct < state.weekly_threshold_pct:
            log.info(
                f"Y.19 : throttle hebdo levé auto (weekly_pct={live_quota.weekly_pct:.1f} %"
                f" < seuil {state.weekly_threshold_pct} %, flag posé le "
                f"{state.weekly_throttled_at})"
            )
            state.weekly_throttled = False
            state.weekly_throttled_at = None
            save_state(state)
        # Flag persistant toujours actif → bloque (cas où la live quota est
        # toujours ≥ seuil, ou où le state a été set manuellement).
        if state.weekly_throttled:
            return {"can_proceed": False, "reason": "weekly_throttled",
                     "message": (f"Throttle hebdo actif depuis {state.weekly_throttled_at}. "
                                 f"Reset via le bouton 'Reset throttle hebdo' dans la GUI.")}
        if live_quota.weekly_pct >= state.weekly_threshold_pct:
            # Set le flag pour la prochaine fois et bloque maintenant.
            state.weekly_throttled = True
            state.weekly_throttled_at = live_quota.fetched_at.isoformat()
            save_state(state)
            return {"can_proceed": False, "reason": "quota_weekly",
                     "message": (f"Quota hebdo {live_quota.weekly_pct:.1f} % ≥ seuil "
                                 f"{state.weekly_threshold_pct} %. Reset hebdo dans "
                                 f"{fmt_duration(live_quota.weekly_seconds_until_reset())}.")}
        if live_quota.session_pct >= state.session_threshold_pct:
            return {"can_proceed": False, "reason": "quota_session",
                     "message": (f"Quota session 5h {live_quota.session_pct:.1f} % ≥ seuil "
                                 f"{state.session_threshold_pct} %. Reset session dans "
                                 f"{fmt_duration(live_quota.session_seconds_until_reset())}.")}
        return {"can_proceed": True, "reason": "ok", "message": ""}
    except Exception as e:
        # Tout autre échec : on continue par défaut (tolérance)
        return {"can_proceed": True, "reason": "check_exception",
                 "message": f"Quota check exception : {type(e).__name__}: {e!s:.150}"}


async def step_summarize(content_id: Optional[str] = None) -> dict:
    """Étape 4 : Résumé via le moteur choisi dans la GUI summarize_gui.

    Le moteur est lu dans `_secrets/engine_pref.json` (partagé avec la GUI) :
    - `claude_code` (défaut) → CLI Claude Code subscription (gratuit, plafond
      quota Pro Max). Argument `--use-claude-code` ajouté à summarize.py.
    - `api` → API Anthropic facturée (nécessite ANTHROPIC_API_KEY). Aucun
      argument ajouté, summarize.py utilise son model par défaut.

    Garde-fou quota : avant le subprocess summarize.py, on vérifie le quota
    Pro Max via claude_usage. Si seuil session ou hebdo dépassé, on skip
    proprement (retour ok=False + quota_blocked=True) au lieu de consommer
    du Sonnet. Le caller log un embed orange et l'étape Sync est skip aussi
    puisqu'il n'y aura pas de résumé à publier. Le check est utile dans les
    deux modes : il garde aussi le moteur API au chaud quand le user a mis
    un seuil de sécurité côté Pro Max (le check est tolérant côté API si le
    cookie claude.ai n'est pas configuré).
    """
    quota_check = _check_quota_before_summarize()
    if not quota_check["can_proceed"]:
        return {
            "ok": False,
            "quota_blocked": True,
            "quota_reason": quota_check["reason"],
            "stdout": "",
            "stderr": quota_check["message"],
            "duration": 0.0,
            "errors": [quota_check["message"]],
        }
    cmd = [PYTHON_EXE, os.path.join(ARSENAL_DIR, "summarize.py"),
           "--base-dir", ARSENAL_DIR]
    if load_engine_pref() == "claude_code":
        cmd.append("--use-claude-code")
    if content_id:
        cmd.extend(["--id", content_id])
    timeout = TIMEOUTS["summarize"] * (1 if content_id else 30)  # Plus long si batch
    return await run_script(cmd, ARSENAL_DIR, timeout, "SUMMARIZE")


def get_latest_content_id(max_age_seconds: int = 120) -> Optional[str]:
    """Retourne l'id de la ligne la plus récente du CSV.

    Filtre uniquement les lignes dont `download_timestamp` date de moins de
    `max_age_seconds` (défaut 2 min). Utile pour distinguer le contenu qui
    vient d'être traité par le pipeline de ceux téléchargés antérieurement.
    À appeler APRÈS csv_normalize, car le downloader peut résoudre un lien
    court vers un ID différent et c'est normalize qui consolide.
    """
    try:
        import pandas as pd
        df = pd.read_csv(cfg.CSV_PATH, encoding="utf-8-sig")
        if df.empty or "download_timestamp" not in df.columns:
            return None
        df = df.dropna(subset=["download_timestamp"])
        if df.empty:
            return None

        ts = pd.to_datetime(df["download_timestamp"], errors="coerce")
        df = df.assign(_ts=ts).dropna(subset=["_ts"])
        if df.empty:
            return None

        now = pd.Timestamp.now()
        cutoff = now - pd.Timedelta(seconds=max_age_seconds)
        recent = df[df["_ts"] >= cutoff]
        if recent.empty:
            log.warning(
                f"get_latest_content_id : aucune ligne < {max_age_seconds}s "
                f"(plus récente : {df['_ts'].max()})"
            )
            return None

        latest = recent.sort_values("_ts", ascending=False).iloc[0]
        return str(latest["id"])
    except Exception as e:
        log.warning(f"get_latest_content_id a échoué : {e}")
        return None


async def step_sync(bot, only_source_id: Optional[str] = None,
                    link_thread: Optional["discord.Thread"] = None) -> dict:
    """Étape 5 : Déclenche la sync Discord via le cog arsenal_publisher.

    Si `only_source_id` est fourni, ne synchronise que ce contenu.
    `link_thread` (Phase Y.11) propagé jusqu'à `_sync_task` pour que
    l'embed "✅ Dossier indexé" soit aussi posté dans le fil du drop.

    Y.17 : `wait_if_busy=True` quand le pipeline appelle, pour ne pas
    rater le forward Dossier indexé si l'auto-sync 15s a gagné la race
    sur la même source.

    Y.22 : `defer_dossier_forwards=True` quand `link_thread` est fourni
    → le `Dossier indexé` est mis en queue côté publisher au lieu d'être
    posté immédiatement dans le fil. Le pipeline flush la queue après
    l'embed `Pipeline terminé` pour que le Dossier indexé apparaisse en
    DERNIÈRE position du fil (et pas au milieu, entre Résumé Claude et
    Publication Discord). Le post `📋・logs` reste immédiat.
    """
    publisher = bot.get_cog("ArsenalPublisher")
    if not publisher:
        return {"ok": False, "stderr": "Cog ArsenalPublisher non chargé", "duration": 0}

    start = datetime.now()
    try:
        await publisher._sync_task(only_source_id=only_source_id,
                                    link_thread=link_thread,
                                    wait_if_busy=link_thread is not None,
                                    defer_dossier_forwards=link_thread is not None)
        duration = (datetime.now() - start).total_seconds()
        return {"ok": True, "stdout": "Sync terminée", "duration": duration}
    except Exception as e:
        duration = (datetime.now() - start).total_seconds()
        return {"ok": False, "stderr": str(e), "duration": duration}


# =============================================================================
# COG DISCORD
# =============================================================================

def extract_content_id_from_url(url: str, platform: str) -> Optional[str]:
    """Retourne l'ID de contenu à partir d'une URL longue (après résolution
    des liens courts TikTok). Retourne None si non reconnu.

    Y.20 : couvre les 6 plateformes du listener. Avant, seuls instagram
    et tiktok étaient gérés ; X/YouTube/Reddit/Threads tombaient sur
    `None`, ce qui faisait passer la dédup catchup `cid and cid in
    known_ids` → enqueue systématique des FAILED à chaque restart bot.
    Ajout de `reels` pour IG (alias récent), de YouTube/X/Reddit/Threads.
    """
    plat = (platform or "").lower()
    if plat == "instagram":
        m = re.search(r"instagram\.com/(?:p|reel|reels|tv)/([\w-]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    elif plat == "tiktok":
        m = re.search(r"tiktok\.com/@[\w.-]+/video/(\d+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    elif plat == "youtube":
        m = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    elif plat in ("x", "twitter"):
        m = re.search(r"/status/(\d+)", url)
        if m:
            return m.group(1)
    elif plat == "reddit":
        m = re.search(r"/comments/([\w]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    elif plat == "threads":
        m = re.search(r"/post/([\w-]+)", url, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


class ArsenalPipeline(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.is_running = False  # Flag de statut (pas un verrou)
        self.current_task: Optional[str] = None
        self.queue: "asyncio.Queue[dict]" = asyncio.Queue()
        self.pipeline_lock = asyncio.Lock()
        self.worker_task: Optional[asyncio.Task] = None

    async def cog_load(self):
        self.worker_task = asyncio.create_task(
            self._queue_worker(), name="arsenal-pipeline-worker"
        )

    async def cog_unload(self):
        if self.worker_task and not self.worker_task.done():
            self.worker_task.cancel()

    # =========================
    # QUEUE WORKER + RATTRAPAGE
    # =========================

    async def _queue_worker(self):
        """Boucle de consommation de la queue. Traite un lien à la fois."""
        try:
            await self.bot.wait_until_ready()
        except Exception:
            pass

        # Rattrapage au démarrage (une seule fois)
        try:
            await self._catchup_scan()
        except Exception as e:
            log.error(f"Rattrapage au démarrage échoué : {e}")

        while True:
            try:
                item = await self.queue.get()
            except asyncio.CancelledError:
                break
            try:
                url = item["url"]
                platform = item["platform"]
                message: Optional[discord.Message] = item.get("message")
                channel = item.get("channel") or (message.channel if message else None)

                # Phase Y.9 : créer un fil sur le message d'origine pour
                # avoir un endroit dédié par lien (vs #logs polluée par
                # toutes les sources d'embeds). Le fil reçoit copies des
                # embeds via run_pipeline → send_log.
                thread: Optional[discord.Thread] = None
                if message:
                    try:
                        cid = extract_content_id_from_url(url, platform) or 'unknown'
                        thread_name = f"📱 {platform.title()} · {cid[:60]}"[:100]
                        thread = await message.create_thread(
                            name=thread_name,
                            auto_archive_duration=60,  # 1h d'auto-archive (Y.12 archive aussi explicitement)
                            reason="Y.9 — fil pipeline par drop",
                        )
                        # Phase Y.13 : retirer l'auteur du message des
                        # membres du fil pour qu'il ne reçoive pas une
                        # notif Discord par embed posté (auto-follow par
                        # défaut quand son message a un fil).
                        try:
                            await thread.remove_user(message.author)
                        except discord.HTTPException:
                            pass  # silencieux, pas critique
                    except discord.HTTPException as e:
                        # Thread déjà existant ? perms manquantes ? On
                        # continue sans fil — embeds vont juste dans #logs.
                        log.warning(f"create_thread échoué pour {url} : {e}")

                async with self.pipeline_lock:
                    result = await self.run_pipeline(url, platform, ctx_or_channel=channel, thread=thread)

                if message:
                    try:
                        await message.add_reaction("✅" if result.get("ok") else "❌")
                    except Exception:
                        pass
            except Exception as e:
                log.error(f"Worker erreur sur item={item} : {e}")
            finally:
                self.queue.task_done()

    async def _catchup_scan(self):
        """Scanne les 50 derniers messages de LISTEN_CHANNEL_ID et enqueue
        ceux qui n'ont JAMAIS été vus par le pipeline.

        Y.20 : on considère « déjà vu » toute ligne CSV avec un
        download_timestamp non vide (SUCCESS ou FAILED), pas seulement
        SUCCESS. Avant Y.20 le catchup re-enqueuait les FAILED à chaque
        restart bot, ce qui :
        - re-spawnait du subprocess yt-dlp pour rien (FAILED reste FAILED)
        - re-postait ❌ sur les messages user de #liens (overrides les ✅
          posés par audit/retrofit)
        - polluait #logs avec des embeds Pipeline | Download échoués
        Le rattrapage ad-hoc des FAILED se fait via `arsenal_retry_failed.py`
        avec ses propres heuristiques (limite, plateforme, etc.).
        """
        if not LISTEN_CHANNEL_ID:
            return
        channel = self.bot.get_channel(LISTEN_CHANNEL_ID)
        if not channel:
            log.warning(f"Rattrapage : salon {LISTEN_CHANNEL_ID} introuvable")
            return

        # Y.20 : « déjà vu » = ligne CSV avec download_timestamp non vide.
        # Couvre SUCCESS + FAILED + tout autre état → pas de re-trigger
        # automatique des FAILED au boot.
        # On garde aussi un set des URLs déjà vues pour dédup quand
        # `extract_content_id_from_url` retourne None — typiquement quand
        # la résolution TikTok short échoue (timeout SSL transitoire) →
        # l'URL reste en forme courte `vm.tiktok.com/Xxxx` qui ne match
        # pas la regex `tiktok.com/@/video/N`. Sans cette dédup-URL, le
        # catchup ré-enqueue indéfiniment ces drops à chaque restart.
        known_ids = set()
        known_urls = set()
        try:
            import pandas as pd
            df = pd.read_csv(cfg.CSV_PATH, encoding="utf-8-sig", dtype=str,
                              keep_default_na=False)
            seen = df[df["download_timestamp"].astype(str).str.strip() != ""]
            known_ids = set(seen["id"].astype(str).str.strip())
            known_urls = set(seen["url"].astype(str).str.strip())
        except Exception as e:
            log.warning(f"Rattrapage : CSV illisible, on traite tout : {e}")

        added = 0
        scanned = 0
        async for msg in channel.history(limit=50):
            scanned += 1
            if msg.author.bot:
                continue
            urls = extract_urls(msg.content)
            for url_info in urls:
                url = url_info["url"]
                platform = url_info["platform"]
                if platform == "tiktok":
                    url = await resolve_tiktok_short_url_async(url)
                cid = extract_content_id_from_url(url, platform)
                if cid and cid in known_ids:
                    continue
                # Y.20 : dédup-URL fallback quand cid extraction échoue
                # (résolution TikTok timeout, etc.). Évite que le bot
                # ré-enqueue 1000 fois le même drop FAILED.
                if url.strip() in known_urls:
                    continue
                # Y.20 : si on n'a PAS pu extraire un cid (résolution
                # short URL échouée, regex non matchée), on n'enqueue
                # PAS — le user re-droppera plus tard si vraiment nouveau.
                # Sans ce skip, chaque restart ré-enqueue les drops dont
                # la résolution timeout (cas vu : SSL handshake timeout
                # sur vm.tiktok.com → 1 enqueue inutile par restart).
                if not cid:
                    log.info(f"Rattrapage : skip {url} (cid non extractible)")
                    continue
                await self.queue.put({
                    "url": url,
                    "platform": platform,
                    "message": msg,
                })
                added += 1
                try:
                    await msg.add_reaction("🔄")
                except Exception:
                    pass

        log.info(f"Rattrapage : {scanned} messages scannés, {added} lien(s) enqueue")
        if added > 0:
            await self.send_log(
                "🔁 Rattrapage au démarrage",
                f"{added} lien(s) ajouté(s) à la queue sur {scanned} messages scannés",
                discord.Color.blue(),
            )

    # =========================
    # LOGGING DISCORD
    # =========================

    async def send_log(self, title: str, description: str,
                       color=discord.Color.blue(), fields: dict = None,
                       thread: Optional[discord.Thread] = None):
        """Envoie un embed de log dans #logs ET (optionnellement) dans un fil
        attaché au message d'origine (Phase Y.9). Le fil donne au user un
        endroit dédié par lien droppé, vs #logs qui mélange tout (sync
        Arsenal, RSS, summarize, etc.)."""
        channel = self.bot.get_channel(LOG_CHANNEL_ID)
        embed = discord.Embed(
            title=f"⚙️ Pipeline | {title}",
            description=description[:4000],
            color=color,
            timestamp=datetime.now(),
        )
        if fields:
            for name, value in fields.items():
                embed.add_field(name=name, value=str(value)[:1024], inline=True)
        embed.set_footer(text="Arsenal Pipeline")
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                pass
        if thread:
            try:
                await thread.send(embed=embed)
            except Exception:
                pass

    def step_emoji(self, ok: bool) -> str:
        return "✅" if ok else "❌"

    def detect_anti_bot_pattern(self, stderr: str, platform: str) -> Optional[dict]:
        """Phase Y.14 : détecte des erreurs spécifiques anti-bot et retourne
        un dict {kind, title, description} pour poster un embed avec
        procédure manuelle. Retourne None si pas un cas connu."""
        s = (stderr or "").lower()
        # Instagram anti-bot (post visible browser, refusé en script)
        if platform.lower() == 'instagram' and any(p in s for p in [
            'redirect to home', 'redirect to login',
            'http error 404', 'page not found',
            'échec malgré fallback', 'echec malgre fallback',
            'video info extraction failed',
        ]):
            return {
                'title': 'ℹ️ Procédure manuelle — Instagram anti-bot',
                'description': (
                    "Ce post est probablement **visible dans ton navigateur** "
                    "mais bloqué pour les outils scriptés (yt-dlp, gallery-dl). "
                    "Anti-bot Instagram détecté.\n\n"
                    "**Pour récupérer le contenu manuellement** :\n"
                    "1. Ouvre l'URL dans ton navigateur (où tu es loggué).\n"
                    "2. **Carrousel d'images** : crée le dossier "
                    "`Arsenal_Arguments/01_raw_images/IG_<ID>/` (ex `IG_DXwTXqbDZXj/`), "
                    "puis right-click sur chaque image → *Enregistrer sous* → "
                    "nomme `01.jpg`, `02.jpg`, ... (numéroté à partir de 01).\n"
                    "3. **Reel/vidéo** : right-click sur la vidéo → *Save as* → "
                    "place dans `01_raw_videos/<ID>_<username>_<DDMMYY>.mp4`.\n"
                    "4. Préviens le bot que c'est fait — on relance OCR/Whisper "
                    "+ summarize + sync.\n\n"
                    "*Alternative future : installer Playwright/Selenium pour "
                    "browser headless (gros chantier, voir avec dev).*"
                ),
                'color': discord.Color.dark_orange(),
            }
        # TikTok IP block per-post
        if platform.lower() == 'tiktok' and 'ip address is blocked' in s:
            return {
                'title': 'ℹ️ TikTok — IP bloquée pour ce post',
                'description': (
                    "TikTok bloque ton IP pour cette vidéo spécifique "
                    "(détection comportementale, pas un ban global).\n\n"
                    "**Solutions** :\n"
                    "- ⏳ Attendre 24-48h puis re-drop le lien (souvent suffit).\n"
                    "- 🌐 Utiliser un VPN.\n"
                    "- 📥 Manuel : downloader la vidéo via ton browser, placer "
                    "dans `01_raw_videos/<video_id>_<username>_<DDMMYY>.mp4`.\n"
                    "- Le bot relance ensuite Whisper + summarize + sync."
                ),
                'color': discord.Color.dark_orange(),
            }
        # DNS / network transient
        if 'could not resolve host' in s or 'transporterror' in s:
            return {
                'title': 'ℹ️ Erreur réseau transitoire',
                'description': (
                    "DNS/réseau a foiré au moment du download "
                    "(`Could not resolve host` ou TransportError). C'est presque "
                    "toujours temporaire.\n\n"
                    "**Solution** : re-drop le lien dans `🔗・liens` dans 5 min. "
                    "Le bot rééssayera automatiquement."
                ),
                'color': discord.Color.dark_orange(),
            }
        return None

    # =========================
    # PIPELINE COMPLET
    # =========================

    async def run_pipeline(self, url: str, platform: str,
                           ctx_or_channel=None, skip_sync=False,
                           thread: Optional[discord.Thread] = None) -> dict:
        """
        Exécute le pipeline complet pour une URL.
        Retourne un dict résumé de toutes les étapes.

        `thread` (Phase Y.9) : si fourni, tous les embeds posts seront ALSO
        postés dans ce fil (en plus de #logs). Le fil est attaché au
        message d'origine dans #liens, donnant un endroit dédié par lien
        droppé.
        """
        self.is_running = True
        results = {}
        overall_start = datetime.now()

        try:
            await self.send_log(
                "Démarrage",
                f"🔗 `{url}`\n📱 Plateforme : **{platform}**",
                discord.Color.blue(),
                thread=thread,
            )

            # --- Étape 1 : Download ---
            self.current_task = "download"
            r = await step_download(url, platform)
            results["download"] = r
            await self.send_log(
                f"{self.step_emoji(r['ok'])} Download",
                f"Durée : {r['duration']:.1f}s\n```{r.get('stderr', '')[:500]}```" if not r["ok"]
                else f"Durée : {r['duration']:.1f}s",
                discord.Color.green() if r["ok"] else discord.Color.red(),
                thread=thread,
            )
            if not r["ok"]:
                # Phase Y.14 : poste un embed avec procédure manuelle si
                # erreur connue (anti-bot IG, IP block TikTok, DNS).
                hint = self.detect_anti_bot_pattern(r.get('stderr', ''), platform)
                if hint:
                    await self.send_log(hint['title'], hint['description'],
                                         hint['color'], thread=thread)
                return {"ok": False, "step": "download", "results": results}

            # --- Étape 2 : Normalize ---
            self.current_task = "normalize"
            r = await step_normalize()
            results["normalize"] = r
            # Pas de log détaillé pour normalize (rapide et fiable)

            # Extraire l'ID du contenu qu'on vient de télécharger.
            # APRÈS normalize : dl_tiktok.py peut résoudre un lien court
            # (vm.tiktok.com) vers un ID numérique différent, et normalize
            # consolide les lignes. On ne garde que les download_timestamp
            # récents (< 2 min) pour éviter de capter un ancien contenu.
            content_id = get_latest_content_id(max_age_seconds=120)
            if content_id:
                log.info(f"Pipeline : content_id détecté = {content_id}")
            else:
                log.warning("Pipeline : aucun content_id récent détecté — sync risque d'être large")

            # --- Étape 3 : Transcribe ---
            self.current_task = "transcribe"
            r = await step_transcribe()
            results["transcribe"] = r
            await self.send_log(
                f"{self.step_emoji(r['ok'])} Transcription Whisper",
                f"Durée : {r['duration']:.1f}s",
                discord.Color.green() if r["ok"] else discord.Color.orange(),
                thread=thread,
            )
            # Whisper peut "échouer" partiellement (ex: pas de nouveau fichier à transcrire)
            # On continue quand même

            # --- Étape 3bis : OCR (Y.21) ---
            # Posts X/Threads/Reddit avec images : OCR easyocr produit
            # un transcript text-only consommable par summarize en mode CLI
            # gratuit. Idempotent + skip silencieux si rien à faire (pas
            # de coût quand le drop est une vidéo classique).
            self.current_task = "ocr"
            r = await step_ocr()
            results["ocr"] = r
            # Log seulement si l'OCR a vraiment produit quelque chose
            # (évite un embed bruyant à chaque drop vidéo). ScriptResult
            # affiche `Succès: N` sur stderr — N > 0 => au moins un dossier
            # a été OCR-isé (donc un drop image / mixed).
            ocr_stderr = (r.get("stderr") or "") + (r.get("stdout") or "")
            ocr_match = re.search(r"Succ[eè]s:\s*(\d+)", ocr_stderr)
            ocr_did_work = ocr_match and int(ocr_match.group(1)) > 0
            if ocr_did_work:
                await self.send_log(
                    f"{self.step_emoji(r['ok'])} OCR images",
                    f"Durée : {r['duration']:.1f}s · "
                    f"{ocr_match.group(1)} dossier(s) traité(s)",
                    discord.Color.green() if r["ok"] else discord.Color.orange(),
                    thread=thread,
                )

            # --- Étape 4 : Summarize ---
            self.current_task = "summarize"
            r = await step_summarize(content_id)
            results["summarize"] = r
            if r.get("quota_blocked"):
                # Skip propre quota — embed orange, pas d'erreur rouge
                await self.send_log(
                    "⚠ Résumé sauté — quota atteint",
                    f"{r['stderr']}\n\n_Le téléchargement et la transcription "
                    f"sont conservés. Re-drop le lien quand le quota redescend, "
                    f"ou attends le reset auto._",
                    discord.Color.orange(),
                    thread=thread,
                )
                # Skip aussi l'étape Sync (rien à publier)
                skip_sync = True
            else:
                await self.send_log(
                    f"{self.step_emoji(r['ok'])} Résumé Claude",
                    f"Durée : {r['duration']:.1f}s",
                    discord.Color.green() if r["ok"] else discord.Color.red(),
                    thread=thread,
                )

            # --- Étape 5 : Sync Discord ---
            if not skip_sync:
                self.current_task = "sync"
                # Phase Y.11 : passer le fil du drop pour que l'embed
                # "✅ Dossier indexé" du publisher y soit aussi posté.
                r = await step_sync(self.bot, only_source_id=content_id,
                                     link_thread=thread)
                results["sync"] = r
                await self.send_log(
                    f"{self.step_emoji(r['ok'])} Publication Discord",
                    f"Durée : {r['duration']:.1f}s",
                    discord.Color.green() if r["ok"] else discord.Color.red(),
                    thread=thread,
                )

            # --- Résumé final ---
            total_duration = (datetime.now() - overall_start).total_seconds()
            steps_ok = sum(1 for v in results.values() if v.get("ok"))
            steps_total = len(results)

            await self.send_log(
                "Pipeline terminé" if steps_ok == steps_total else "Pipeline terminé (avec erreurs)",
                f"✅ {steps_ok}/{steps_total} étapes OK\n⏱️ Durée totale : {total_duration:.0f}s\n🔗 `{url}`",
                discord.Color.green() if steps_ok == steps_total else discord.Color.orange(),
                thread=thread,
            )

            # Y.22 : flush la queue des `✅ Dossier indexé` pour qu'ils
            # apparaissent EN DERNIER dans le fil (après `Pipeline terminé`),
            # pas entre `Résumé Claude` et `Publication Discord`. La queue
            # a été remplie par `_sync_task` quand `defer_dossier_forwards=
            # True` (cf. step_sync). Idempotent et silencieux si vide.
            publisher = self.bot.get_cog("ArsenalPublisher")
            if publisher and hasattr(publisher, "flush_deferred_dossier_to_fil"):
                try:
                    await publisher.flush_deferred_dossier_to_fil()
                except Exception as e:
                    log.warning(f"flush_deferred_dossier_to_fil échoué : {e}")

            # Pas de DM admin — les erreurs sont déjà loggées dans #logs.

            return {"ok": steps_ok == steps_total, "results": results, "duration": total_duration}

        except Exception as e:
            log.error(f"Pipeline exception : {e}")
            await self.send_log("💥 Erreur pipeline", str(e)[:2000], discord.Color.dark_red(), thread=thread)
            return {"ok": False, "error": str(e)}

        finally:
            self.is_running = False
            self.current_task = None
            # Phase Y.12 : auto-archive le fil à la fin du pipeline pour
            # ne pas polluer la liste des fils actifs de #liens. Le fil
            # reste accessible via clic sur le message d'origine, et
            # peut se ré-ouvrir si quelqu'un poste dedans.
            if thread is not None:
                try:
                    await thread.edit(archived=True, locked=False,
                                       reason="Y.12 — auto-archive fin pipeline")
                except discord.HTTPException as e:
                    log.warning(f"auto-archive thread {thread.id} échoué : {e}")

    # =========================
    # ÉCOUTE AUTO #arsenal-liens
    # =========================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Phase Y.10 : auto-delete des messages système Discord générés par
        # `message.create_thread()` (type=thread_created, "X a commencé
        # un fil…"). Polluent le salon #liens vu qu'on crée un fil par
        # drop maintenant. Le fil reste visible côté UI Discord même sans
        # ce message système.
        if (LISTEN_CHANNEL_ID and message.channel.id == LISTEN_CHANNEL_ID
                and message.type == discord.MessageType.thread_created):
            try:
                await message.delete()
            except discord.HTTPException as e:
                log.warning(f"auto-delete thread_created msg échoué : {e}")
            return

        # Ignorer les bots
        if message.author.bot:
            return

        # Vérifier le salon
        if LISTEN_CHANNEL_ID and message.channel.id != LISTEN_CHANNEL_ID:
            return

        # Si pas de salon configuré, ne rien faire en auto
        if not LISTEN_CHANNEL_ID:
            return

        # Extraire les URLs
        urls = extract_urls(message.content)
        if not urls:
            return

        # Enqueue : la réaction finale (✅/❌) sera posée par le worker
        try:
            await message.add_reaction("🔄")
        except Exception:
            pass

        for url_info in urls:
            url = url_info["url"]
            platform = url_info["platform"]
            if platform == "tiktok":
                url = await resolve_tiktok_short_url_async(url)
            await self.queue.put({
                "url": url,
                "platform": platform,
                "message": message,
            })

    # =========================
    # COMMANDES MANUELLES
    # =========================

    @commands.command(name="pipeline")
    @commands.is_owner()
    async def cmd_pipeline(self, ctx, url: str):
        """Lance le pipeline complet sur une URL."""
        urls = extract_urls(url)
        if not urls:
            await ctx.send("❌ URL non reconnue (plateformes acceptées : TikTok, Instagram, YouTube, X, Reddit, Threads).")
            return

        target_url = urls[0]["url"]
        platform = urls[0]["platform"]
        if platform == "tiktok":
            target_url = await resolve_tiktok_short_url_async(target_url)

        if self.pipeline_lock.locked():
            await ctx.send("⏳ Pipeline déjà en cours (attente du verrou)...")

        await ctx.send(f"🚀 Pipeline lancé pour `{target_url}` ({platform})")
        async with self.pipeline_lock:
            result = await self.run_pipeline(target_url, platform, ctx)

        if result.get("ok"):
            await ctx.send("✅ Pipeline terminé avec succès.")
        else:
            step = result.get("step", "?")
            await ctx.send(f"❌ Pipeline échoué à l'étape **{step}**.")

    @commands.command(name="pipeline_batch")
    @commands.is_owner()
    async def cmd_pipeline_batch(self, ctx):
        """Lance le pipeline batch : transcribe PENDING → summarize PENDING → sync PENDING."""
        if self.pipeline_lock.locked():
            await ctx.send("⏳ Pipeline déjà en cours (attente du verrou)...")

        await ctx.send("🚀 Pipeline batch lancé (transcribe → summarize → sync)...")

        async with self.pipeline_lock:
            self.is_running = True
            try:
                # Pas de download en batch (les contenus sont déjà DL)
                await step_normalize()

                r = await step_transcribe()
                await ctx.send(f"🎙️ Whisper : {'OK' if r['ok'] else 'Erreurs'} ({r['duration']:.0f}s)")

                r = await step_summarize()
                if r.get("quota_blocked"):
                    await ctx.send(f"⚠ Résumé sauté — quota atteint : {r.get('stderr', '')[:200]}")
                else:
                    sr = r.get("script_result", {})
                    success = sr.get("success", "?")
                    failed = sr.get("failed", "?")
                    await ctx.send(f"🧠 Claude : {success} résumés, {failed} erreurs ({r['duration']:.0f}s)")

                    r = await step_sync(self.bot)
                    await ctx.send(f"📢 Sync : {'OK' if r['ok'] else 'Erreurs'} ({r['duration']:.0f}s)")

                await ctx.send("✅ Pipeline batch terminé.")

            except Exception as e:
                await ctx.send(f"❌ Erreur batch : {e}")

            finally:
                self.is_running = False

    @commands.command(name="pipeline_status")
    async def cmd_pipeline_status(self, ctx):
        """Affiche l'état actuel du pipeline."""
        if self.is_running:
            await ctx.send(f"🔄 Pipeline en cours — étape : **{self.current_task or '?'}**")
        else:
            await ctx.send("💤 Pipeline inactif.")

    @commands.command(name="pipeline_resummarize")
    @commands.is_owner()
    async def cmd_pipeline_resummarize(self, ctx):
        """Re-résume tous les contenus existants avec Claude (migration Gemini→Claude)."""
        if self.pipeline_lock.locked():
            await ctx.send("⏳ Pipeline déjà en cours (attente du verrou)...")

        await ctx.send("🧠 Re-résumé batch lancé... Cela peut prendre ~20 minutes.")

        async with self.pipeline_lock:
            self.is_running = True
            try:
                cmd = [
                    PYTHON_EXE,
                    os.path.join(ARSENAL_DIR, "summarize.py"),
                    "--base-dir", ARSENAL_DIR,
                    "--re-summarize",
                ]
                r = await run_script(cmd, ARSENAL_DIR, timeout=3600, label="RE-SUMMARIZE")

                sr = r.get("script_result", {})
                success = sr.get("success", "?")
                failed = sr.get("failed", "?")
                duration = r.get("duration", 0)

                await ctx.send(
                    f"{'✅' if r['ok'] else '⚠️'} Re-résumé terminé\n"
                    f"Succès : **{success}** | Échecs : **{failed}** | Durée : **{duration:.0f}s**"
                )

                await ctx.send("📢 Lancement de la sync Discord...")
                r = await step_sync(self.bot)
                await ctx.send(f"Sync : {'✅' if r['ok'] else '❌'}")

            except Exception as e:
                await ctx.send(f"❌ Erreur re-résumé : {e}")

            finally:
                self.is_running = False

    @commands.command(name="pipeline_queue")
    async def cmd_pipeline_queue(self, ctx):
        """Affiche le nombre de liens en attente de traitement."""
        qsize = self.queue.qsize()
        busy = "oui" if (self.is_running or self.pipeline_lock.locked()) else "non"
        etape = self.current_task or "—"
        await ctx.send(
            f"📋 File d'attente : **{qsize}** lien(s)\n"
            f"🔄 En cours : **{busy}** (étape : `{etape}`)"
        )

    # =========================
    # SCRAPE CHANNEL (multi-plateforme, batch)
    # =========================

    @commands.command(name="scrape_channel")
    @commands.is_owner()
    async def cmd_scrape_channel(self, ctx, channel_id: int, *args):
        """Scrape un salon entier : extrait toutes les URLs (TikTok, Instagram,
        YouTube, X, Reddit, Threads), dédup contre le CSV, télécharge via
        dl_generic.py, normalise, lance Whisper à la fin.

        Usage : !scrape_channel <channel_id> [--limit N]

        Tient le pipeline_lock pendant toute l'opération (les liens postés
        en parallèle dans le salon d'écoute s'empilent en queue et sont
        traités après). Crash-safe : chaque DL est immédiatement écrit au
        CSV par dl_generic, donc une relance reprend là où ça s'était
        arrêté (les paires (plateforme, id) déjà au CSV sont skippées).
        """
        # --- Parse --limit ---
        limit: Optional[int] = None
        for i, tok in enumerate(args):
            if tok == "--limit" and i + 1 < len(args):
                try:
                    limit = int(args[i + 1])
                except ValueError:
                    await ctx.send(f"❌ `--limit` doit être un entier, reçu `{args[i + 1]}`")
                    return

        target = self.bot.get_channel(channel_id)
        if target is None:
            try:
                target = await self.bot.fetch_channel(channel_id)
            except Exception as e:
                await ctx.send(f"❌ Salon `{channel_id}` introuvable : {e}")
                return

        if not isinstance(target, (discord.TextChannel, discord.Thread)):
            await ctx.send(f"❌ `{channel_id}` n'est pas un salon texte ou un thread.")
            return

        if self.pipeline_lock.locked():
            await ctx.send("⏳ Pipeline déjà en cours — attente du verrou…")

        await ctx.send(
            f"🔍 Scan de {target.mention} (historique complet)…"
            + (f" — limit={limit}" if limit else "")
        )
        await self.send_log(
            "Scrape channel — démarrage",
            f"Source : {target.mention} (`{channel_id}`)\n"
            f"Limit  : {limit if limit else 'aucune'}",
            discord.Color.blue(),
        )

        # ---------- Phase 1 : SCAN ----------
        all_urls: List[dict] = []
        seen_urls: set = set()
        scanned_msgs = 0
        scan_start = datetime.now()

        try:
            async for msg in target.history(limit=None, oldest_first=True):
                scanned_msgs += 1
                if msg.author.bot:
                    continue
                for url_info in extract_urls_all_platforms(msg.content):
                    u = url_info["url"]
                    if u in seen_urls:
                        continue
                    seen_urls.add(u)
                    all_urls.append(url_info)

                # Heartbeat tous les 1000 messages pour les gros salons
                if scanned_msgs % 1000 == 0:
                    await ctx.send(
                        f"… {scanned_msgs} messages scannés, "
                        f"{len(all_urls)} URLs trouvées (en cours)"
                    )
        except Exception as e:
            await ctx.send(f"❌ Erreur durant le scan : {e}")
            await self.send_log("Scrape channel — scan échoué", str(e)[:1500],
                                discord.Color.red())
            return

        scan_duration = (datetime.now() - scan_start).total_seconds()

        # ---------- Phase 2 : DÉDUP CSV ----------
        known = csv_known_pairs()
        new_urls: List[dict] = []
        skipped_known = 0
        skipped_no_id = 0

        for u in all_urls:
            cid = extract_id_for_platform(u["url"], u["platform"])
            if not cid:
                # Lien court TikTok par exemple : on l'envoie quand même à
                # dl_generic, qui le résoudra et fera lui-même la dédup
                # avant écriture CSV. Mais on ne peut pas l'avoir détecté
                # ici → on l'inclut.
                if u["platform"] == "TikTok" and re.search(
                    r"https?://(?:vm|vt)\.tiktok\.com/", u["url"], re.IGNORECASE
                ):
                    new_urls.append(u)
                else:
                    skipped_no_id += 1
                continue
            if (u["platform"].lower(), cid) in known:
                skipped_known += 1
                continue
            new_urls.append(u)

        if limit:
            new_urls = new_urls[:limit]

        await ctx.send(
            f"📊 **Scan terminé** en {scan_duration:.0f}s\n"
            f"• Messages scannés : **{scanned_msgs}**\n"
            f"• URLs trouvées    : **{len(all_urls)}**\n"
            f"• Déjà au CSV      : **{skipped_known}**\n"
            f"• ID introuvable   : **{skipped_no_id}**\n"
            f"• À télécharger    : **{len(new_urls)}**"
        )

        if not new_urls:
            await self.send_log("Scrape channel — rien à faire",
                                "Aucune nouvelle URL.", discord.Color.greyple())
            return

        # ---------- Phase 3 : DOWNLOAD (sous lock) ----------
        async with self.pipeline_lock:
            self.is_running = True
            self.current_task = "scrape_channel"

            success = 0
            failed = 0
            skipped_dl = 0   # skips renvoyés par dl_generic (course, etc.)
            errors_sample: List[str] = []
            dl_start = datetime.now()
            total = len(new_urls)

            try:
                for i, u in enumerate(new_urls, start=1):
                    url = u["url"]
                    platform = u["platform"]

                    try:
                        # Router : TikTok → dl_tiktok ; Instagram/Threads
                        # → dl_instagram (carrousels + cookies Meta) ; reste
                        # → dl_generic (yt-dlp universel).
                        plat_low = platform.lower()
                        if plat_low == "tiktok":
                            r = await step_dl_tiktok(url)
                        elif plat_low in ("instagram", "threads"):
                            r = await step_dl_instagram(url)
                        else:
                            r = await step_dl_generic(url, source_input_mode="SCRAPE_CHANNEL")
                        sr = r.get("script_result") or {}
                        # ScriptResult expose success / failed / skipped
                        if sr.get("success", 0) > 0:
                            success += 1
                        elif sr.get("skipped", 0) > 0:
                            skipped_dl += 1
                        else:
                            failed += 1
                            err_msg = (sr.get("errors") or [r.get("stderr", "")])
                            err_str = err_msg[0] if err_msg else ""
                            if err_str and len(errors_sample) < 5:
                                errors_sample.append(f"`{platform}` {err_str[:200]}")
                    except Exception as e:
                        failed += 1
                        log.error(f"DL exception sur {url} : {e}")
                        if len(errors_sample) < 5:
                            errors_sample.append(f"`{platform}` exception: {e}")

                    # Progression + normalize tous les 10
                    if i % 10 == 0 or i == total:
                        elapsed = (datetime.now() - dl_start).total_seconds()
                        rate = i / elapsed if elapsed > 0 else 0
                        eta = (total - i) / rate if rate > 0 else 0
                        await self.send_log(
                            f"Scrape — progression {i}/{total}",
                            f"✅ {success}  ❌ {failed}  ⏭ {skipped_dl}\n"
                            f"⏱ {elapsed:.0f}s écoulés, ETA ~{eta:.0f}s",
                            discord.Color.blue(),
                        )
                        # Normalize CSV (consolide ids résolus, dédoublonne)
                        await step_normalize()

                dl_duration = (datetime.now() - dl_start).total_seconds()

                # ---------- Phase 4 : WHISPER ----------
                await self.send_log(
                    "Scrape — DL terminé, lancement Whisper",
                    f"✅ {success}  ❌ {failed}  ⏭ {skipped_dl}  •  "
                    f"durée DL : {dl_duration:.0f}s",
                    discord.Color.blue(),
                )
                r_whisper = await step_transcribe()
                await self.send_log(
                    f"{self.step_emoji(r_whisper['ok'])} Whisper post-scrape",
                    f"Durée : {r_whisper['duration']:.1f}s",
                    discord.Color.green() if r_whisper["ok"] else discord.Color.orange(),
                )

                # ---------- Récap final ----------
                fields = {
                    "✅ DL OK": success,
                    "❌ DL KO": failed,
                    "⏭ Skip": skipped_dl,
                    "🎙 Whisper": "OK" if r_whisper["ok"] else "Erreurs",
                    "⏱ Total": f"{(datetime.now() - dl_start).total_seconds():.0f}s",
                }
                desc = (
                    f"Salon : {target.mention}\n"
                    f"Traités : **{total}** URLs"
                )
                if errors_sample:
                    desc += "\n\n**Échantillon d'erreurs** :\n" + "\n".join(
                        f"• {e}" for e in errors_sample
                    )
                await self.send_log(
                    "Scrape channel — terminé",
                    desc,
                    discord.Color.green() if failed == 0 else discord.Color.orange(),
                    fields=fields,
                )
                await ctx.send(
                    f"✅ Scrape terminé — {success} OK / {failed} KO / "
                    f"{skipped_dl} skip — Whisper : "
                    f"{'OK' if r_whisper['ok'] else 'erreurs'}"
                )

            except Exception as e:
                log.error(f"Scrape channel exception : {e}")
                await self.send_log("Scrape channel — exception", str(e)[:1500],
                                    discord.Color.dark_red())
                await ctx.send(f"❌ Erreur scrape : {e}")
            finally:
                self.is_running = False
                self.current_task = None


async def setup(bot):
    await bot.add_cog(ArsenalPipeline(bot))
