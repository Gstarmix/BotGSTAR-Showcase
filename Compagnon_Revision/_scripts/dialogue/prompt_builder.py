"""
prompt_builder.py — Charge le prompt système et assemble le contexte initial
de la session à envoyer à Claude comme premier message utilisateur.

Définit aussi la dataclass ``SessionContext`` (hébergement officiel — cf.
ARCHITECTURE.md §5.3). ``session_state.py`` re-exporte ce symbole pour la
rétrocompatibilité.

Cf. ARCHITECTURE.md §5.
"""

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import TIMEZONE
from utils import parse_iso

logger = logging.getLogger(__name__)


# ============================================================ Constantes

#: Cap sur la transcription CM (éviter de saturer le contexte avec un CM 1h)
CM_TRANSCRIPTION_WORD_CAP = 4000

#: Nombre de points faibles historiques à injecter dans le contexte
PREVIOUS_WEAK_POINTS_TOP_N = 5


# ============================================================ SessionContext

@dataclass
class SessionContext:
    """Contexte d'une séance de révision.

    Hébergement officiel selon ARCHITECTURE.md §5.3. ``session_state.py``
    re-exporte ce symbole, donc ``from session_state import SessionContext``
    fonctionne toujours.
    """

    matiere: str
    type: str            # "TD" | "TP" | "CC" | "Examen" | "Quiz"
    num: str
    exo: str             # "3" ou "full"
    enonce_path: Path    # peut être absolu
    cm_transcription_path: Optional[Path] = None
    cm_poly_path: Optional[Path] = None
    previous_weak_points_path: Optional[Path] = None


# ============================================================ PromptBuilder

class PromptBuilder:
    """Charge le prompt système (invariant) et compose les messages contextuels.

    Le prompt système est lu une fois au constructeur — immuable pour la durée
    de vie du process. Le contexte initial est reconstruit pour chaque session
    car il dépend de la séance ciblée.
    """

    SECTION_HEADER = "=== {title} ==="
    DEFAULT_DURATION_HINT = "45-60 minutes"

    def __init__(self, system_prompt_path: Path, cours_root: Path):
        self._system_prompt = system_prompt_path.read_text(encoding="utf-8")
        self._cours_root = Path(cours_root)

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def build_initial_context_message(
        self,
        ctx: SessionContext,
        is_resume: bool = False,
    ) -> str:
        """Construit le premier message user pour démarrer ou reprendre la séance.

        Ordre des sections :

        1. ``[RESUME_SESSION]`` si ``is_resume=True``
        2. CONTEXTE DE LA SÉANCE (matière, type, num, exo, date, heure)
        3. ÉNONCÉ DE L'EXERCICE (extrait PDF)
        4. TRANSCRIPTION CM PERTINENTE (cap 4000 mots, optionnel)
        5. POLY DU PROF — extraits PDF (optionnel)
        6. POINTS FAIBLES HISTORIQUES (top 5 par date desc, optionnel)
        7. INSTRUCTIONS (variables selon resume vs nouvelle session)
        """
        parts: list[str] = []
        if is_resume:
            parts.append("[RESUME_SESSION]")
            parts.append("")

        parts.append(self._section("CONTEXTE DE LA SÉANCE"))
        parts.append(self._build_session_header(ctx))
        parts.append("")

        parts.append(self._section("ÉNONCÉ DE L'EXERCICE"))
        parts.append(self._extract_pdf_text(ctx.enonce_path))
        parts.append("")

        if ctx.cm_transcription_path is not None:
            parts.append(self._section("TRANSCRIPTION CM PERTINENTE"))
            parts.append(self._read_cm_transcription(ctx.cm_transcription_path))
            parts.append("")

        if ctx.cm_poly_path is not None:
            parts.append(self._section("POLY DU PROF (extraits)"))
            parts.append(self._extract_pdf_text(ctx.cm_poly_path))
            parts.append("")

        if ctx.previous_weak_points_path is not None:
            parts.append(
                self._section(f"POINTS FAIBLES HISTORIQUES SUR {ctx.matiere}")
            )
            parts.append(self._read_previous_weak_points(ctx.previous_weak_points_path))
            parts.append("")

        parts.append(self._section("INSTRUCTIONS"))
        if is_resume:
            parts.append(
                "Reprends la séance interrompue. Fais un récap court de "
                "où on en était puis enchaîne selon §2.2 du prompt système."
            )
        else:
            parts.append(
                "Démarre la séance. Pose la première question selon §2.2 "
                "du prompt système."
            )

        return "\n".join(parts).strip() + "\n"

    # ---------------------------------------------------------------- internes

    def _section(self, title: str) -> str:
        return self.SECTION_HEADER.format(title=title)

    def _build_session_header(self, ctx: SessionContext) -> str:
        now = datetime.now(TIMEZONE)
        lines = [
            f"Matière : {ctx.matiere}",
            f"Type : {ctx.type} {ctx.num}",
        ]
        if ctx.exo and ctx.exo != "full":
            lines.append(f"Exercice ciblé : exercice {ctx.exo}")
        else:
            lines.append("Exercice ciblé : tout le TD/TP")
        lines.append(f"Date : {now.strftime('%Y-%m-%d')}")
        lines.append(f"Heure de début : {now.strftime('%H:%M')}")
        lines.append(f"Durée prévue : {self.DEFAULT_DURATION_HINT}")
        return "\n".join(lines)

    def _extract_pdf_text(self, pdf_path: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError:
            return (
                f"[pypdf indisponible — joindre le PDF en multimodal : {pdf_path}]"
            )
        if not pdf_path.exists():
            return f"[PDF introuvable : {pdf_path}]"
        try:
            reader = PdfReader(str(pdf_path))
            chunks: list[str] = []
            for page in reader.pages:
                txt = page.extract_text() or ""
                if txt.strip():
                    chunks.append(txt)
            text = "\n".join(chunks).strip()
            if not text:
                return f"[Extraction PDF vide — PDF probablement scanné : {pdf_path}]"
            return text
        except Exception as e:
            logger.warning("Echec extraction PDF %s : %s", pdf_path, e)
            return f"[Extraction PDF échouée ({e}) : {pdf_path}]"

    def _read_cm_transcription(self, txt_path: Path) -> str:
        try:
            text = txt_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Echec lecture transcription CM %s : %s", txt_path, e)
            return f"[Lecture transcription CM échouée : {e}]"
        return self._cap_words(text, CM_TRANSCRIPTION_WORD_CAP)

    @staticmethod
    def _cap_words(text: str, max_words: int) -> str:
        words = text.split()
        if len(words) <= max_words:
            return text
        kept = " ".join(words[:max_words])
        return (
            f"{kept}\n\n[...tronqué à {max_words} mots, "
            f"total {len(words)}]"
        )

    def _read_previous_weak_points(self, csv_path: Path) -> str:
        if not csv_path.exists():
            return "(aucun point faible historique enregistré)"
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception as e:
            logger.warning("Echec lecture CSV %s : %s", csv_path, e)
            return f"[Lecture CSV points faibles échouée : {e}]"
        if not rows:
            return "(CSV présent mais vide)"

        def sort_key(r: dict) -> datetime:
            ts = r.get("captured_at", "") or ""
            try:
                return parse_iso(ts)
            except (ValueError, TypeError):
                return datetime.min.replace(tzinfo=TIMEZONE)

        rows.sort(key=sort_key, reverse=True)
        lines: list[str] = []
        for r in rows[:PREVIOUS_WEAK_POINTS_TOP_N]:
            captured = (r.get("captured_at") or "?")[:10]
            score = r.get("score", "?")
            concept = r.get("concept", "?")
            what = r.get("what_failed", "?")
            ctx_str = r.get("exercise_context", "") or ""
            line = f"- [{captured}] score={score} | {concept} — {what}"
            if ctx_str:
                line += f"  ({ctx_str})"
            lines.append(line)
        return "\n".join(lines)
