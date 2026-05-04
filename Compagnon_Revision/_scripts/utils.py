"""
utils.py — Helpers transverses : atomic write JSON, ISO timestamps.

Importé par session_state.py, parser.py, quota_check.py, etc.

Cf. CLAUDE.md §3.4 et ARCHITECTURE.md §6.3.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import TIMEZONE


def atomic_write_json(path: Path, data: dict) -> None:
    """Écrit data en JSON dans path de façon atomique (.tmp + os.replace).

    Crée les dossiers parents si absents. Encodage utf-8 sans escape ASCII
    (les accents et emojis restent lisibles en clair dans le JSON).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now_iso() -> str:
    """Heure courante en ISO 8601 timezone-aware Europe/Paris.

    Format : ``2026-05-02T19:30:00+02:00`` (résolution seconde).
    """
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    """Parse un ISO 8601 (suffixe ``Z`` accepté) en datetime aware."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def seconds_since(iso_string: Optional[str]) -> Optional[float]:
    """Secondes écoulées depuis l'ISO timestamp donné.

    Retourne ``None`` si l'argument est nul ou non parsable. Utilisé pour
    détecter les sessions reprenables (last_alive ancien) — cf.
    ARCHITECTURE.md §1.3.
    """
    if not iso_string:
        return None
    try:
        past = parse_iso(iso_string)
    except (ValueError, TypeError):
        return None
    return (datetime.now(TIMEZONE) - past).total_seconds()
