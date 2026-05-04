"""
config.py — Constantes et chemins du projet Compagnon_Revision.

Source de vérité unique pour les chemins et les versions de schéma. Tout
le reste du code importe depuis ici, jamais de chemin absolu en dur.

Cf. CLAUDE.md §3.1.
"""

from pathlib import Path
from zoneinfo import ZoneInfo

# ============================================================ Racines

PROJECT_ROOT = Path(__file__).resolve().parent
COURS_ROOT = Path(r"C:\Users\Gstar\OneDrive\Documents\COURS")
ARSENAL_PATH = PROJECT_ROOT.parent / "Arsenal_Arguments"

# ============================================================ Sous-dossiers projet

SCRIPTS_DIR = PROJECT_ROOT / "_scripts"
PROMPTS_DIR = PROJECT_ROOT / "_prompts"
SESSIONS_DIR = PROJECT_ROOT / "_sessions"
POINTS_FAIBLES_DIR = PROJECT_ROOT / "_points_faibles"
PHOTOS_INBOX_DIR = PROJECT_ROOT / "_photos_inbox"
CACHE_DIR = PROJECT_ROOT / "_cache"
TTS_CACHE_DIR = CACHE_DIR / "tts"
SECRETS_DIR = PROJECT_ROOT / "_secrets"
LOGS_DIR = PROJECT_ROOT / "_logs"
AUDIO_LOGS_DIR = LOGS_DIR / "audio"

# ============================================================ Fichiers spéciaux

ENGINE_PREF_PATH = SECRETS_DIR / "engine_pref.json"
PROMPT_SYSTEME_PATH = PROMPTS_DIR / "PROMPT_SYSTEME_COMPAGNON.md"

# ============================================================ Timezone

TIMEZONE = ZoneInfo("Europe/Paris")

# ============================================================ Versions de schéma

SCHEMA_VERSION_SESSION = 1
SCHEMA_VERSION_ENGINE_PREF = 1

# ============================================================ Moteur Claude par défaut

DEFAULT_ENGINE = "cli_subscription"  # cf. CLAUDE.md §5.3
