"""
arsenal_config.py — Module de configuration centralisé pour Arsenal Intelligence Unit.

Toutes les constantes, chemins, colonnes CSV et helpers partagés sont définis ici.
Chaque script du pipeline importe ce module au lieu de redéfinir ses propres valeurs.

Résolution du chemin de base (par ordre de priorité) :
  1. Variable d'environnement ARSENAL_BASE_PATH
  2. Argument --base-dir passé au script appelant (via init_from_args)
  3. Détection automatique : remonte depuis le répertoire du script appelant
  4. Chemin par défaut codé en dur (fallback Windows)

Usage dans un script :
    from arsenal_config import cfg
    print(cfg.CSV_PATH)
    print(cfg.VIDEO_DIR)

Usage avec argparse :
    parser = argparse.ArgumentParser()
    cfg.add_base_dir_arg(parser)
    args = parser.parse_args()
    cfg.init_from_args(args)
"""

import os
import re
import sys
import logging
from datetime import datetime
from typing import Optional

# =============================================================================
# CONSOLE ENCODING (Windows)
# =============================================================================
# Les scripts logguent des emojis (✅ ⚠️ 🎬 …) qui font crasher le logger sur
# stdout cp1252 par défaut. On force UTF-8 dès l'import de ce module ; tous les
# scripts CLI Arsenal qui importent arsenal_config bénéficient du fix.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

# =============================================================================
# LOGGING
# =============================================================================
LOG_FORMAT = "[%(asctime)s] %(levelname)-7s %(message)s"
LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Crée un logger standard pour un script Arsenal."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FMT))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


# =============================================================================
# COLONNES CSV — SOURCE DE VÉRITÉ UNIQUE
# =============================================================================
# Cet ordre est canonique. Tous les scripts DOIVENT utiliser cette liste.
# Ne JAMAIS redéfinir ces colonnes ailleurs.

GLOBAL_CSV_COLUMNS = [
    "id", "url", "plateforme",
    "source_input_mode",
    "type", "detected_type_initial", "resolved_type_final",
    "download_mode", "download_status", "error_message",
    "username", "display_name", "hashtags", "description",
    "thumbnail_url", "views_at_extraction",
    "filename", "date_publication", "download_timestamp",
    "summary_status", "summary_timestamp", "summary_error",
    "sync_status", "sync_timestamp", "sync_error",
]

# Valeurs par défaut pour les colonnes lors de la création/normalisation
COLUMN_DEFAULTS = {
    "source_input_mode": "",
    "type": "",
    "detected_type_initial": "",
    "resolved_type_final": "",
    "download_mode": "",
    "download_status": "",
    "error_message": "",
    "username": "",
    "display_name": "",
    "hashtags": "",
    "description": "",
    "thumbnail_url": "",
    "views_at_extraction": "",
    "filename": "",
    "date_publication": "",
    "download_timestamp": "",
    "summary_status": "PENDING",
    "summary_timestamp": "",
    "summary_error": "",
    "sync_status": "PENDING",
    "sync_timestamp": "",
    "sync_error": "",
}

# Statuts valides
VALID_STATUSES = {"PENDING", "SUCCESS", "FAILED"}

# Encodage CSV standard (utf-8-sig pour compatibilité Excel)
CSV_ENCODING = "utf-8-sig"

# =============================================================================
# EXTENSIONS FICHIERS
# =============================================================================
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".jfif", ".avif", ".heic", ".heif"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".opus"}
TRANSCRIPT_EXTS = {".txt"}

# Formats image exotiques à convertir en JPG
CONVERT_TO_JPG_EXTS = {".heic", ".heif", ".avif", ".jfif"}

# Limite upload Discord (10 Mo)
DISCORD_UPLOAD_LIMIT = 10 * 1024 * 1024

# Préfixe des dossiers images Instagram (legacy, kept for backwards compat)
IG_POST_DIR_PREFIX = "IG_"

# Y.21 : préfixe par plateforme pour 01_raw_images/<PREFIX><id>/. Permet le
# fallback gallery-dl côté dl_generic.py (X / Threads / Reddit avec images
# ou mixed media). La clé est la valeur de la colonne CSV `plateforme`
# normalisée en lowercase. Les valeurs servent aussi de pattern de scan
# dans ocr_carousels.py (élargi au-delà du préfixe IG_).
PLATFORM_DIR_PREFIXES = {
    "instagram": "IG_",
    "x": "X_",
    "twitter": "X_",
    "threads": "THREADS_",
    "reddit": "REDDIT_",
    "youtube": "YT_",
    "tiktok": "TT_",
}

# Limite de sécurité pour les slides de carrousel
MAX_CAROUSEL_SLIDES = 20


# =============================================================================
# CONFIGURATION DES CHEMINS — CLASSE CENTRALE
# =============================================================================
DEFAULT_BASE_PATH = r"C:\Users\Gstar\OneDrive\Documents\BotGSTAR\Arsenal_Arguments"


class ArsenalPaths:
    """
    Résout et expose tous les chemins du pipeline Arsenal.

    Tous les chemins sont recalculés dynamiquement à partir de `base_path`.
    Modifier `base_path` (via init_from_args ou set_base) met tout à jour.
    """

    def __init__(self, base_path: Optional[str] = None):
        self._base_path = self._resolve_base(base_path)

    # ----- Résolution du chemin de base -----

    @staticmethod
    def _resolve_base(explicit: Optional[str] = None) -> str:
        """
        Résout le chemin de base par ordre de priorité :
        1. Argument explicite (--base-dir ou appel direct)
        2. Variable d'environnement ARSENAL_BASE_PATH
        3. Détection auto : remonte depuis __file__ ou cwd
        4. Fallback codé en dur
        """
        # 1. Argument explicite
        if explicit and os.path.isdir(explicit):
            return os.path.abspath(explicit)

        # 2. Variable d'environnement
        env_path = os.getenv("ARSENAL_BASE_PATH", "").strip()
        if env_path and os.path.isdir(env_path):
            return os.path.abspath(env_path)

        # 3. Détection auto — cherche suivi_global.csv en remontant
        search_roots = [
            os.getcwd(),
            os.path.dirname(os.path.abspath(__file__)),
        ]
        # Ajouter le dossier du script appelant si différent
        if len(sys.argv) > 0:
            caller_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            if caller_dir not in search_roots:
                search_roots.append(caller_dir)

        for root in search_roots:
            candidate = root
            for _ in range(5):  # max 5 niveaux
                csv_test = os.path.join(candidate, "suivi_global.csv")
                if os.path.isfile(csv_test):
                    return os.path.abspath(candidate)
                # Tester aussi Arsenal_Arguments/ en sous-dossier
                sub = os.path.join(candidate, "Arsenal_Arguments")
                if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "suivi_global.csv")):
                    return os.path.abspath(sub)
                parent = os.path.dirname(candidate)
                if parent == candidate:
                    break
                candidate = parent

        # 4. Fallback
        if os.path.isdir(DEFAULT_BASE_PATH):
            return DEFAULT_BASE_PATH

        return DEFAULT_BASE_PATH

    # ----- Propriétés de chemins -----

    @property
    def base_path(self) -> str:
        return self._base_path

    def set_base(self, path: str):
        """Change le chemin de base (tous les chemins dérivés suivent)."""
        self._base_path = os.path.abspath(path)

    # --- Dossiers de données ---

    @property
    def VIDEO_DIR(self) -> str:
        return os.path.join(self._base_path, "01_raw_videos")

    @property
    def IMAGE_DIR(self) -> str:
        return os.path.join(self._base_path, "01_raw_images")

    @property
    def TRANSCRIPT_DIR(self) -> str:
        return os.path.join(self._base_path, "02_whisper_transcripts")

    @property
    def TRANSCRIPT_CAROUSEL_DIR(self) -> str:
        return os.path.join(self._base_path, "02_whisper_transcripts_carousels")

    @property
    def WHISPER_LOG_DIR(self) -> str:
        return os.path.join(self._base_path, "02_whisper_logs")

    @property
    def SUMMARY_DIR(self) -> str:
        return os.path.join(self._base_path, "03_ai_summaries")

    @property
    def EXPORT_DIR(self) -> str:
        return os.path.join(self._base_path, "04_exports")

    @property
    def BACKUP_DIR(self) -> str:
        return os.path.join(self._base_path, "_backups")

    @property
    def LOCKS_DIR(self) -> str:
        return os.path.join(self._base_path, "_locks")

    # --- Fichiers clés ---

    @property
    def CSV_PATH(self) -> str:
        return os.path.join(self._base_path, "suivi_global.csv")

    @property
    def GLOBAL_RESUMES_FILE(self) -> str:
        return os.path.join(self._base_path, "tous_les_resumes.txt")

    @property
    def YTDLP_PATH(self) -> str:
        return os.path.join(self._base_path, "yt-dlp.exe")

    @property
    def COOKIES_INSTAGRAM(self) -> str:
        return os.path.join(self._base_path, "cookies_instagram.txt")

    @property
    def COOKIES_TIKTOK(self) -> str:
        return os.path.join(self._base_path, "cookies_tiktok.txt")

    @property
    def INPUT_INSTAGRAM(self) -> str:
        return os.path.join(self._base_path, "dl_insta_video_image.json")

    @property
    def INPUT_TIKTOK(self) -> str:
        return os.path.join(self._base_path, "dl_tiktok_video.txt")

    @property
    def SUMMARIZER_LOCK(self) -> str:
        return os.path.join(self.LOCKS_DIR, "summarizer.lock")

    # --- Argparse integration ---

    @staticmethod
    def add_base_dir_arg(parser):
        """Ajoute l'argument --base-dir à un ArgumentParser existant."""
        parser.add_argument(
            "--base-dir",
            type=str,
            default=None,
            help="Chemin vers le dossier Arsenal_Arguments (défaut: auto-détecté)"
        )
        return parser

    def init_from_args(self, args):
        """
        Met à jour le chemin de base depuis les args parsés.
        Appeler après parser.parse_args().
        """
        if hasattr(args, "base_dir") and args.base_dir:
            self._base_path = self._resolve_base(args.base_dir)

    # --- Helpers ---

    def ensure_dirs(self):
        """Crée tous les dossiers nécessaires s'ils n'existent pas."""
        for d in [
            self.VIDEO_DIR, self.IMAGE_DIR,
            self.TRANSCRIPT_DIR, self.TRANSCRIPT_CAROUSEL_DIR,
            self.WHISPER_LOG_DIR,
            self.SUMMARY_DIR, self.EXPORT_DIR,
            self.BACKUP_DIR, self.LOCKS_DIR,
        ]:
            os.makedirs(d, exist_ok=True)

    def backup_csv(self, label: str = "backup") -> str:
        """
        Crée une copie horodatée du CSV dans _backups/.
        Retourne le chemin du backup.
        """
        import shutil
        os.makedirs(self.BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.BACKUP_DIR, f"suivi_global_{label}_{ts}.csv")
        if os.path.isfile(self.CSV_PATH):
            shutil.copy2(self.CSV_PATH, backup_path)
        return backup_path

    def ig_post_dir(self, post_id: str) -> str:
        """Retourne le chemin du dossier image pour un post Instagram."""
        post_id = str(post_id or "").strip()
        name = f"{IG_POST_DIR_PREFIX}{post_id}" if post_id else "_unknown"
        return os.path.join(self.IMAGE_DIR, name)

    def post_dir(self, platform: str, post_id: str) -> str:
        """Y.21 : chemin du dossier image multi-plateforme.

        Utilisé par dl_generic.py (fallback gallery-dl pour X / Threads /
        Reddit) pour ranger images + vidéos + texte d'un post sous
        `01_raw_images/<PREFIX><id>/`. Fallback IG_ si plateforme inconnue.
        """
        post_id = str(post_id or "").strip()
        prefix = PLATFORM_DIR_PREFIXES.get((platform or "").lower(), IG_POST_DIR_PREFIX)
        name = f"{prefix}{post_id}" if post_id else "_unknown"
        return os.path.join(self.IMAGE_DIR, name)

    def summary_filename(self, platform: str, item_id: str) -> str:
        """Génère le nom de fichier résumé standardisé."""
        p = (platform or "").strip().upper()
        if p.startswith("INST"):
            return f"IG_{item_id}.txt"
        if p.startswith("TIK"):
            return f"TT_{item_id}.txt"
        return f"SRC_{item_id}.txt"

    def __repr__(self) -> str:
        return f"ArsenalPaths(base_path={self._base_path!r})"


# =============================================================================
# HELPERS PARTAGÉS
# =============================================================================

def normalize_str(value) -> str:
    """Normalise une valeur en string propre. Gère None, NaN, etc."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in {"nan", "none", "null", "na"}:
        return ""
    return s


def safe_username(text: str, max_len: int = 40) -> str:
    """Normalise un username pour usage dans les noms de fichiers."""
    text = normalize_str(text) or "inconnu"
    text = text.lower()
    text = re.sub(r"[^\w\-\.]+", "_", text, flags=re.UNICODE)
    return text[:max_len].strip("_") or "inconnu"


def is_numeric_like_username(value: str) -> bool:
    """Détecte les usernames qui sont juste des IDs numériques ou des placeholders."""
    v = normalize_str(value).lower()
    if not v:
        return False
    if v in {"auteur_inconnu", "inconnu", "none", "null", "na"}:
        return True
    return bool(re.fullmatch(r"\d{6,}", v))


def normalize_status(value: str, default_if_empty: str = "") -> str:
    """Normalise un statut CSV (PENDING/SUCCESS/FAILED)."""
    v = normalize_str(value).upper()
    if not v:
        return default_if_empty
    return v


def now_timestamp() -> str:
    """Retourne un timestamp formaté pour le CSV."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def detect_platform_from_url(url: str) -> str:
    """Détecte la plateforme depuis une URL."""
    url_lower = (url or "").lower()
    if "instagram.com" in url_lower:
        return "Instagram"
    if "tiktok.com" in url_lower:
        return "TikTok"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "YouTube"
    if "twitter.com" in url_lower or "x.com" in url_lower:
        return "Twitter"
    return ""


def extract_id_from_url(url: str) -> str:
    """Extrait un ID de contenu depuis une URL (best effort)."""
    url = normalize_str(url).rstrip("/")
    if not url:
        return ""
    # Instagram: /p/<ID>/ ou /reel/<ID>/
    m = re.search(r"instagram\.com/(?:p|reel|reels)/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    # TikTok: /video/<ID>
    m = re.search(r"tiktok\.com/.+/video/(\d+)", url)
    if m:
        return m.group(1)
    # Fallback: dernier segment de l'URL
    return url.split("/")[-1] or ""


# =============================================================================
# CSV HELPERS
# =============================================================================

def append_to_csv(rows: list, csv_path: str):
    """
    Ajoute des lignes au CSV en imposant le schéma canonique.
    Crée le fichier avec header si nécessaire.
    Thread-safe (best effort) via écriture atomique.
    """
    import pandas as pd

    if not rows:
        return

    df = pd.DataFrame(rows)

    for col in GLOBAL_CSV_COLUMNS:
        if col not in df.columns:
            df[col] = COLUMN_DEFAULTS.get(col, "")

    df = df[GLOBAL_CSV_COLUMNS]

    write_header = not os.path.isfile(csv_path)

    df.to_csv(
        csv_path,
        index=False,
        mode="a",
        header=write_header,
        encoding=CSV_ENCODING,
    )


def load_csv(csv_path: str):
    """
    Charge le CSV avec le bon encodage et type str.
    Retourne un DataFrame pandas.
    """
    import pandas as pd

    try:
        return pd.read_csv(csv_path, dtype=str, encoding=CSV_ENCODING, keep_default_na=False)
    except UnicodeDecodeError:
        return pd.read_csv(csv_path, dtype=str, encoding="utf-8", keep_default_na=False)


# =============================================================================
# LOCK HELPERS
# =============================================================================

def acquire_lock(lock_path: str) -> int:
    """
    Acquiert un lock fichier exclusif. Retourne le file descriptor.
    Vérifie si un ancien lock est orphelin (PID mort) et le nettoie.
    Raise RuntimeError si un autre processus est actif.
    """
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    # Vérifier si un lock existe déjà
    if os.path.isfile(lock_path):
        try:
            with open(lock_path, "r") as f:
                old_pid = int(f.read().strip())
            # Vérifier si le processus est encore vivant
            if _is_pid_alive(old_pid):
                raise RuntimeError(
                    f"Lock actif {lock_path} (PID {old_pid} encore vivant). "
                    f"Un autre run tourne probablement."
                )
            else:
                # PID mort → lock orphelin, on le nettoie
                os.remove(lock_path)
        except (ValueError, OSError):
            # Fichier lock corrompu → on le nettoie
            try:
                os.remove(lock_path)
            except OSError:
                pass

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        return fd
    except FileExistsError:
        raise RuntimeError(f"Lock déjà présent {lock_path} (race condition).")


def release_lock(fd: Optional[int], lock_path: str):
    """Libère un lock fichier."""
    try:
        if fd is not None:
            os.close(fd)
    except OSError:
        pass
    try:
        if os.path.isfile(lock_path):
            os.remove(lock_path)
    except OSError:
        pass


def _is_pid_alive(pid: int) -> bool:
    """Vérifie si un PID est encore vivant (Windows + Unix)."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


# =============================================================================
# RÉSULTAT STANDARD POUR N8N
# =============================================================================

class ScriptResult:
    """
    Objet de résultat standard pour les scripts du pipeline.
    Permet de retourner un résumé exploitable par n8n.
    """

    def __init__(self, script_name: str):
        self.script_name = script_name
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0
        self.errors: list = []
        self.start_time = datetime.now()

    def add_success(self):
        self.success_count += 1

    def add_fail(self, error_msg: str = ""):
        self.fail_count += 1
        if error_msg:
            self.errors.append(error_msg[:500])

    def add_skip(self):
        self.skip_count += 1

    @property
    def total(self) -> int:
        return self.success_count + self.fail_count + self.skip_count

    @property
    def is_ok(self) -> bool:
        return self.fail_count == 0

    @property
    def duration_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    def to_dict(self) -> dict:
        return {
            "script": self.script_name,
            "success": self.success_count,
            "failed": self.fail_count,
            "skipped": self.skip_count,
            "total": self.total,
            "duration_seconds": round(self.duration_seconds, 1),
            "errors": self.errors[:20],
            "ok": self.is_ok,
        }

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def exit(self):
        """Affiche le résultat JSON sur stdout et sort avec le bon code."""
        print(self.to_json())
        sys.exit(0 if self.is_ok else 1)

    def print_summary(self):
        """Affiche un résumé lisible sur stderr (n'interfère pas avec stdout JSON)."""
        import sys as _sys
        _sys.stderr.write(
            f"\n{'='*50}\n"
            f"[{self.script_name}] Terminé en {self.duration_seconds:.1f}s\n"
            f"  Succès: {self.success_count} | Échecs: {self.fail_count} | Skips: {self.skip_count}\n"
            f"{'='*50}\n"
        )


# =============================================================================
# INSTANCE GLOBALE
# =============================================================================
# Importable directement : from arsenal_config import cfg
cfg = ArsenalPaths()


# =============================================================================
# PRÉFÉRENCE MOTEUR SUMMARIZE — partagée entre la GUI et arsenal_pipeline
# =============================================================================
# La GUI summarize_gui écrit ce fichier quand on change le radiobutton
# « Moteur » ; arsenal_pipeline.step_summarize le lit pour décider s'il
# passe `--use-claude-code` à summarize.py (CLI subscription gratuit) ou
# rien (API Anthropic facturée). Source de vérité unique partagée.

VALID_ENGINES = ("claude_code", "api")
DEFAULT_ENGINE = "claude_code"


def _engine_pref_path() -> str:
    return os.path.join(cfg.base_path, "_secrets", "engine_pref.json")


def load_engine_pref() -> str:
    """Lit la préférence moteur. Retourne 'claude_code' ou 'api'.
    Fallback sur DEFAULT_ENGINE si fichier absent / corrompu / valeur inconnue."""
    import json as _json
    try:
        with open(_engine_pref_path(), encoding="utf-8") as f:
            engine = _json.load(f).get("engine")
        return engine if engine in VALID_ENGINES else DEFAULT_ENGINE
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        return DEFAULT_ENGINE


def save_engine_pref(engine: str) -> None:
    """Écriture atomique de la préférence moteur."""
    import json as _json
    if engine not in VALID_ENGINES:
        raise ValueError(f"Engine invalide '{engine}', attendu {VALID_ENGINES}")
    path = _engine_pref_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump({"engine": engine}, f)
    os.replace(tmp, path)


# =============================================================================
# TEST RAPIDE
# =============================================================================
if __name__ == "__main__":
    print(f"Arsenal Config — Module de configuration centralisé")
    print(f"{'='*60}")
    print(f"Base path résolu : {cfg.base_path}")
    print(f"CSV path         : {cfg.CSV_PATH}")
    print(f"Vidéos           : {cfg.VIDEO_DIR}")
    print(f"Images           : {cfg.IMAGE_DIR}")
    print(f"Transcriptions   : {cfg.TRANSCRIPT_DIR}")
    print(f"Trans. carrousels: {cfg.TRANSCRIPT_CAROUSEL_DIR}")
    print(f"Résumés          : {cfg.SUMMARY_DIR}")
    print(f"Backups          : {cfg.BACKUP_DIR}")
    print(f"Locks            : {cfg.LOCKS_DIR}")
    print(f"yt-dlp           : {cfg.YTDLP_PATH}")
    print(f"Cookies IG       : {cfg.COOKIES_INSTAGRAM}")
    print(f"Cookies TT       : {cfg.COOKIES_TIKTOK}")
    print(f"{'='*60}")
    print(f"Colonnes CSV ({len(GLOBAL_CSV_COLUMNS)}) : {', '.join(GLOBAL_CSV_COLUMNS[:6])}...")
    print(f"CSV existe       : {os.path.isfile(cfg.CSV_PATH)}")
    print(f"yt-dlp existe    : {os.path.isfile(cfg.YTDLP_PATH)}")
