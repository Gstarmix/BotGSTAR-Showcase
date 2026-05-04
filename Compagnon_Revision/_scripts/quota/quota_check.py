"""
quota_check.py — Wrapper léger autour de ``Arsenal_Arguments/claude_usage.py``.

Expose deux fonctions au reste du compagnon :

- ``can_start_session()`` : refuse si la session 5h dépasse 85 % ou si l'hebdo
  dépasse 90 %.
- ``get_usage_snapshot()`` : snapshot dict JSON-able pour l'affichage front
  (poll toutes les 60 s sur ``/api/quota``).

Mode tolérant : si le fetch échoue (cookie absent ou expiré, network down,
endpoint changé), on laisse passer avec un warning loggué. La discipline du
quota Max 5x ne doit pas devenir un point de panne du compagnon en pleine
séance — l'utilisateur sera juste un peu moins informé pendant ce temps.

Note d'implémentation : le squelette de ARCHITECTURE.md §9.1 montre des
accès ``usage["five_hour"]["utilization"]``, mais ``claude_usage.fetch_usage``
retourne en réalité un dataclass ``Quota`` (cf. Arsenal). On utilise donc
l'attribut access (``usage.session_pct``, etc.) ici. Divergence à reporter
dans ARCHITECTURE.md à la prochaine relecture, pas critique.

Cf. ARCHITECTURE.md §9, CLAUDE.md §5.
"""

import logging
import sys

from config import ARSENAL_PATH

# Phase A : on ajoute Arsenal_Arguments au sys.path pour réutiliser
# claude_usage. À supprimer en Phase B quand Arsenal sera un vrai package
# importable (cf. CLAUDE.md §3.2).
if str(ARSENAL_PATH) not in sys.path:
    sys.path.insert(0, str(ARSENAL_PATH))

from claude_usage import Quota, fetch_usage  # noqa: E402

logger = logging.getLogger(__name__)


# ============================================================ Seuils Compagnon
# Cf. ARCHITECTURE.md §5.2. Différents de la GUI Arsenal qui est à 70/80.

THRESHOLD_5H_BLOCK_SESSION = 85    # > => refus de démarrer une nouvelle session
THRESHOLD_7D_BLOCK_SESSION = 90    # > => refus de démarrer une nouvelle session
THRESHOLD_5H_WARN_INSESSION = 90   # > => warning visuel, pas d'arrêt forcé


# ============================================================ API publique

def can_start_session() -> tuple[bool, str]:
    """Retourne ``(autorisé, raison_si_non)`` selon les seuils Compagnon.

    En cas d'erreur de fetch (cookie absent, network down, endpoint changé),
    retourne ``(True, "")`` : un échec du watcher ne doit pas bloquer le
    compagnon. Le warning loggué permet de tracer l'incident a posteriori.
    """
    try:
        usage = fetch_usage()
    except Exception as e:
        logger.warning(
            "Quota check echoue (%s : %s) — autorisation par defaut",
            type(e).__name__, e,
        )
        return True, ""

    if usage.session_pct > THRESHOLD_5H_BLOCK_SESSION:
        return False, (
            f"Quota 5h a {usage.session_pct:.0f}%, reset {_fmt_reset(usage.session_resets_at)}"
        )

    if usage.weekly_pct > THRESHOLD_7D_BLOCK_SESSION:
        return False, (
            f"Quota hebdo a {usage.weekly_pct:.0f}%, reset {_fmt_reset(usage.weekly_resets_at)}"
        )

    return True, ""


def get_usage_snapshot() -> dict:
    """Snapshot dict JSON-able pour le front.

    Si fetch échoue, retourne ``{"error": "unavailable", "detail": "..."}``.
    Le front sait alors afficher un placeholder.
    """
    try:
        usage = fetch_usage()
    except Exception as e:
        return {"error": "unavailable", "detail": f"{type(e).__name__}: {e}"}
    return _quota_to_dict(usage)


# ============================================================ Internes

def _fmt_reset(dt) -> str:
    """Format court pour un reset_at (datetime ou None)."""
    if dt is None:
        return "(inconnu)"
    return dt.isoformat(timespec="minutes")


def _quota_to_dict(q: Quota) -> dict:
    """Sérialise un Quota dataclass → dict JSON-able pour le front."""
    def iso(dt):
        return dt.isoformat() if dt is not None else None

    return {
        "session_pct": q.session_pct,
        "session_resets_at": iso(q.session_resets_at),
        "weekly_pct": q.weekly_pct,
        "weekly_resets_at": iso(q.weekly_resets_at),
        "weekly_sonnet_pct": q.weekly_sonnet_pct,
        "weekly_sonnet_resets_at": iso(q.weekly_sonnet_resets_at),
        "extra_used_credits": q.extra_used_credits,
        "extra_limit_credits": q.extra_limit_credits,
        "extra_pct": q.extra_pct,
        "fetched_at": iso(q.fetched_at),
    }
