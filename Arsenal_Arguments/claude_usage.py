"""
claude_usage.py — Module de scraping du quota Pro Max via claude.ai/settings/usage.

Stocke le cookie de session via Windows DPAPI (chiffré, lié à la session Windows).
Fetch l'endpoint interne /api/organizations/{ORG}/usage et expose un dataclass Quota.

Mode CLI pour tester :
    python claude_usage.py --set-cookie   (saisie masquée)
    python claude_usage.py --fetch        (affiche le quota courant)
    python claude_usage.py --state        (affiche / configure les seuils + throttle)
    python claude_usage.py --clear        (supprime le cookie)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

try:
    import win32crypt  # type: ignore
except ImportError:
    win32crypt = None  # type: ignore

HERE = Path(__file__).resolve().parent
SECRETS_DIR = HERE / "_secrets"
COOKIE_PATH = SECRETS_DIR / "claude_session.bin"
STATE_PATH = SECRETS_DIR / "quota_state.json"

ORG_UUID = "53fba259-ebdb-4018-9086-55eff8b39e6a"
DEVICE_ID = "3b43d392-39c8-45e8-a46a-364ea2a521f1"
ANONYMOUS_ID = "claudeai.v1.bd3c302b-3959-46cf-9c8e-e86eb0258691"

USAGE_URL = f"https://claude.ai/api/organizations/{ORG_UUID}/usage"
REFERER = "https://claude.ai/settings/usage"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 10

DEFAULT_SESSION_THRESHOLD_PCT = 70
DEFAULT_WEEKLY_THRESHOLD_PCT = 80


# ============================================================ Exceptions

class UsageError(Exception):
    """Base exception du module. user-facing message dans .args[0]."""


class CookieMissingError(UsageError):
    """Aucun cookie configuré (1ère utilisation ou clear)."""


class CookieExpiredError(UsageError):
    """Cookie présent mais rejeté par claude.ai (401)."""


class EndpointChangedError(UsageError):
    """Réponse JSON sans les clés attendues — Anthropic a changé le format."""


class NetworkError(UsageError):
    """Timeout / connexion / erreur HTTP non-401."""


class DPAPIUnavailableError(UsageError):
    """win32crypt indisponible (pywin32 non installé)."""


# ============================================================ Cookie storage (DPAPI)

def _ensure_secrets_dir() -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)


def save_cookie(cookie_value: str) -> None:
    """Chiffre et écrit le cookie de session via DPAPI."""
    if win32crypt is None:
        raise DPAPIUnavailableError("pywin32 non installé : `pip install pywin32`")
    if not cookie_value or not cookie_value.strip():
        raise ValueError("Cookie vide")
    _ensure_secrets_dir()
    blob = win32crypt.CryptProtectData(
        cookie_value.encode("utf-8"),
        "arsenal-claude-session",
        None, None, None, 0,
    )
    tmp = COOKIE_PATH.with_suffix(".bin.tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, COOKIE_PATH)


def load_cookie() -> Optional[str]:
    """Déchiffre et retourne le cookie clair, ou None si absent / illisible."""
    if win32crypt is None:
        raise DPAPIUnavailableError("pywin32 non installé : `pip install pywin32`")
    if not COOKIE_PATH.exists():
        return None
    try:
        blob = COOKIE_PATH.read_bytes()
        _, plain = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return plain.decode("utf-8")
    except Exception:
        return None


def delete_cookie() -> bool:
    """Supprime le fichier cookie. Retourne True si quelque chose a été supprimé."""
    if COOKIE_PATH.exists():
        COOKIE_PATH.unlink()
        return True
    return False


def has_cookie() -> bool:
    return COOKIE_PATH.exists()


# ============================================================ Quota dataclass

@dataclass
class Quota:
    session_pct: float
    session_resets_at: Optional[datetime]
    weekly_pct: float
    weekly_resets_at: Optional[datetime]
    weekly_sonnet_pct: Optional[float]
    weekly_sonnet_resets_at: Optional[datetime]
    extra_used_credits: Optional[int]
    extra_limit_credits: Optional[int]
    extra_pct: Optional[float]
    fetched_at: datetime

    def session_seconds_until_reset(self, now: Optional[datetime] = None) -> Optional[int]:
        return _seconds_until(self.session_resets_at, now)

    def weekly_seconds_until_reset(self, now: Optional[datetime] = None) -> Optional[int]:
        return _seconds_until(self.weekly_resets_at, now)


def _parse_iso8601(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _seconds_until(when: Optional[datetime], now: Optional[datetime] = None) -> Optional[int]:
    if when is None:
        return None
    now = now or datetime.now(timezone.utc)
    delta = (when - now).total_seconds()
    return max(0, int(delta))


def fmt_duration(sec: Optional[int]) -> str:
    if sec is None:
        return "—"
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}j{h:02d}h"


# ============================================================ Fetch

def _build_headers(cookie: str) -> dict:
    """Mimétise Chrome 147 + Anthropic web_claude_ai. Cloudflare check les
    sec-ch-ua-* / priority / sec-fetch-* — sans eux, 403."""
    return {
        "accept": "*/*",
        "accept-language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "anthropic-anonymous-id": ANONYMOUS_ID,
        "anthropic-client-platform": "web_claude_ai",
        "anthropic-client-version": "1.0.0",
        "anthropic-device-id": DEVICE_ID,
        "content-type": "application/json",
        "cookie": cookie,
        "priority": "u=1, i",
        "referer": REFERER,
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": USER_AGENT,
    }


def fetch_usage(cookie: Optional[str] = None) -> Quota:
    """GET https://claude.ai/api/organizations/{ORG}/usage et parse en Quota.

    Lève CookieMissingError / CookieExpiredError / EndpointChangedError /
    NetworkError selon le mode d'échec.
    """
    if cookie is None:
        cookie = load_cookie()
    if not cookie:
        raise CookieMissingError("Cookie claude.ai non configuré")

    try:
        resp = requests.get(USAGE_URL, headers=_build_headers(cookie),
                            timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout as e:
        raise NetworkError(f"Timeout après {REQUEST_TIMEOUT}s") from e
    except requests.exceptions.ConnectionError as e:
        raise NetworkError(f"Erreur connexion : {e}") from e
    except requests.exceptions.RequestException as e:
        raise NetworkError(f"Erreur réseau : {e}") from e

    if resp.status_code == 401 or resp.status_code == 403:
        raise CookieExpiredError(
            f"Cookie rejeté (HTTP {resp.status_code}). "
            "Re-saisir le cookie via la GUI ou --set-cookie."
        )
    if resp.status_code >= 400:
        raise NetworkError(f"HTTP {resp.status_code} : {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise EndpointChangedError(f"Réponse non-JSON : {resp.text[:200]}") from e

    return _parse_usage_response(data)


def _safe_float(v, default: float = 0.0) -> float:
    """Coerce v en float, retourne default si v est None ou non-coercible.

    Tolérance utile car l'endpoint claude.ai expose parfois des champs avec
    une valeur ``null`` explicite (ex: extra_usage.utilization quand aucun
    crédit overage n'a encore été consommé).
    """
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v, default: int = 0) -> int:
    """Coerce v en int (via float pour gérer 0.0). Retourne default si None."""
    if v is None:
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _parse_usage_response(data: dict) -> Quota:
    try:
        five = data.get("five_hour") or {}
        seven = data.get("seven_day") or {}
        seven_sonnet = data.get("seven_day_sonnet")
        extra = data.get("extra_usage")

        session_pct = _safe_float(five.get("utilization"))
        session_resets = _parse_iso8601(five.get("resets_at"))
        weekly_pct = _safe_float(seven.get("utilization"))
        weekly_resets = _parse_iso8601(seven.get("resets_at"))

        if isinstance(seven_sonnet, dict):
            sonnet_pct = _safe_float(seven_sonnet.get("utilization"))
            sonnet_resets = _parse_iso8601(seven_sonnet.get("resets_at"))
        else:
            sonnet_pct = None
            sonnet_resets = None

        if isinstance(extra, dict) and extra.get("is_enabled"):
            extra_used = _safe_int(extra.get("used_credits"))
            extra_limit = _safe_int(extra.get("monthly_limit"))
            extra_pct = _safe_float(extra.get("utilization"))
        else:
            extra_used = None
            extra_limit = None
            extra_pct = None

        return Quota(
            session_pct=session_pct,
            session_resets_at=session_resets,
            weekly_pct=weekly_pct,
            weekly_resets_at=weekly_resets,
            weekly_sonnet_pct=sonnet_pct,
            weekly_sonnet_resets_at=sonnet_resets,
            extra_used_credits=extra_used,
            extra_limit_credits=extra_limit,
            extra_pct=extra_pct,
            fetched_at=datetime.now(timezone.utc),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise EndpointChangedError(
            f"Format JSON inattendu (clé manquante ou type invalide : {e}). "
            f"Reçu : {json.dumps(data)[:300]}"
        ) from e


# ============================================================ Persistent state

@dataclass
class QuotaState:
    session_threshold_pct: int = DEFAULT_SESSION_THRESHOLD_PCT
    weekly_threshold_pct: int = DEFAULT_WEEKLY_THRESHOLD_PCT
    weekly_throttled: bool = False
    weekly_throttled_at: Optional[str] = None
    last_check_at: Optional[str] = None


def load_state() -> QuotaState:
    """Charge l'état throttle + seuils. Retourne un QuotaState par défaut si absent."""
    if not STATE_PATH.exists():
        return QuotaState()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return QuotaState(
            session_threshold_pct=int(data.get("session_threshold_pct",
                                               DEFAULT_SESSION_THRESHOLD_PCT)),
            weekly_threshold_pct=int(data.get("weekly_threshold_pct",
                                              DEFAULT_WEEKLY_THRESHOLD_PCT)),
            weekly_throttled=bool(data.get("weekly_throttled", False)),
            weekly_throttled_at=data.get("weekly_throttled_at"),
            last_check_at=data.get("last_check_at"),
        )
    except (OSError, ValueError, TypeError):
        return QuotaState()


def save_state(state: QuotaState) -> None:
    """Atomic write du state JSON."""
    _ensure_secrets_dir()
    payload = json.dumps(asdict(state), indent=2, ensure_ascii=False)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, STATE_PATH)


# ============================================================ CLI

def _cli_set_cookie() -> int:
    print("Coller le cookie (sessionKey ou cookie complet — il sera masqué) :")
    cookie = getpass.getpass(prompt="> ")
    if not cookie.strip():
        print("Cookie vide, abandon.")
        return 1
    save_cookie(cookie.strip())
    print(f"OK — cookie chiffré dans {COOKIE_PATH}")
    return 0


def _cli_fetch() -> int:
    try:
        q = fetch_usage()
    except UsageError as e:
        print(f"ERREUR : {e}")
        return 1

    print(f"Récupéré à {q.fetched_at.isoformat(timespec='seconds')}")
    print()
    print(f"  Session 5h    : {q.session_pct:6.2f} %  "
          f"(reset dans {fmt_duration(q.session_seconds_until_reset())})")
    print(f"  Hebdo 7j      : {q.weekly_pct:6.2f} %  "
          f"(reset dans {fmt_duration(q.weekly_seconds_until_reset())})")
    if q.weekly_sonnet_pct is not None:
        sonnet_sec = _seconds_until(q.weekly_sonnet_resets_at)
        print(f"  Hebdo Sonnet  : {q.weekly_sonnet_pct:6.2f} %  "
              f"(reset dans {fmt_duration(sonnet_sec)})")
    if q.extra_pct is not None:
        print(f"  Overage       : {q.extra_pct:6.2f} %  "
              f"({q.extra_used_credits}/{q.extra_limit_credits} crédits)")
    return 0


def _cli_state() -> int:
    st = load_state()
    print(f"Seuil session  : {st.session_threshold_pct} %")
    print(f"Seuil hebdo    : {st.weekly_threshold_pct} %")
    print(f"Throttle hebdo : {'ACTIF (' + (st.weekly_throttled_at or '?') + ')' if st.weekly_throttled else 'inactif'}")
    print(f"Last check     : {st.last_check_at or 'jamais'}")
    print(f"Fichier        : {STATE_PATH}")
    return 0


def _cli_clear() -> int:
    deleted = delete_cookie()
    print(f"Cookie {'supprimé' if deleted else 'absent'} ({COOKIE_PATH})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper de quota claude.ai")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--set-cookie", action="store_true",
                   help="Saisir et chiffrer le cookie claude.ai (DPAPI)")
    g.add_argument("--fetch", action="store_true",
                   help="Récupérer et afficher le quota courant")
    g.add_argument("--state", action="store_true",
                   help="Afficher l'état throttle + seuils")
    g.add_argument("--clear", action="store_true",
                   help="Supprimer le cookie chiffré")
    args = parser.parse_args()

    if args.set_cookie:
        return _cli_set_cookie()
    if args.fetch:
        return _cli_fetch()
    if args.state:
        return _cli_state()
    if args.clear:
        return _cli_clear()
    return 1


if __name__ == "__main__":
    sys.exit(main())
