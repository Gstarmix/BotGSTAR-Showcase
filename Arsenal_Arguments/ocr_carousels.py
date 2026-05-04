import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

"""
ocr_carousels.py — OCR des images de posts (carrousels IG, X, Threads,
Reddit) via easyocr.

Scanne 01_raw_images/<PREFIX><id>/ pour tous les préfixes plateforme connus
(IG_, X_, THREADS_, REDDIT_…) et produit pour chaque post un fichier texte
02_whisper_transcripts/<id>_ocr.txt qui agrège le texte extrait de chaque
image, précédé du `_post_text.txt` (si présent — utilisé par le fallback
gallery-dl de dl_generic.py pour les tweets text/image et les posts
Threads/Reddit avec photos).

Format de sortie :
    [POST TEXT]
    <contenu du tweet / post / caption>

    [SLIDE 1 — 01.jpg]
    <texte extrait par OCR>

    [SLIDE 2 — 02.jpg]
    <texte extrait par OCR>
    ...

Permet à summarize.py --use-claude-code (mode CLI gratuit, text-only) de
traiter ces posts sans passer par l'API Vision payante.

Idempotent : skip si <id>_ocr.txt existe déjà (sauf --force).

Le nom <id>_ocr.txt est conçu pour être pris en charge par
summarize.match_known_id() : "<id>_ocr" commence par "<id>_", donc le
fichier est automatiquement indexé sous l'id du contenu CSV.

Usage :
    python ocr_carousels.py                       # tous les posts non traités
    python ocr_carousels.py --force               # tout réécrire
    python ocr_carousels.py --id "DPQXvh5E-Hw"    # un seul post
"""

import argparse
import os
import re
from typing import List, Optional

from arsenal_config import (
    cfg, IG_POST_DIR_PREFIX, PLATFORM_DIR_PREFIXES,
    ScriptResult, get_logger,
)

log = get_logger("ocr_carousels")

# Y.21 : ensemble des préfixes de dossiers à scanner (IG_, X_, THREADS_, …).
# Trié par longueur décroissante pour matcher le plus spécifique d'abord
# (évite que IG_ matche un dossier hypothétique IG_X_…).
KNOWN_DIR_PREFIXES = sorted(
    set(PLATFORM_DIR_PREFIXES.values()) | {IG_POST_DIR_PREFIX},
    key=len, reverse=True,
)

# Formats image natifs supportés par easyocr (sans conversion préalable)
OCR_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _strip_known_prefix(name: str) -> Optional[str]:
    """Retourne le post_id si `name` commence par un préfixe connu, sinon None."""
    for prefix in KNOWN_DIR_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return None


def list_carousel_dirs(target_id: Optional[str] = None) -> List[str]:
    """Retourne les chemins absolus des dossiers de posts à traiter
    (IG_<id>/, X_<id>/, THREADS_<id>/, REDDIT_<id>/…)."""
    if not os.path.isdir(cfg.IMAGE_DIR):
        return []

    dirs = []
    for name in sorted(os.listdir(cfg.IMAGE_DIR)):
        post_id = _strip_known_prefix(name)
        if post_id is None:
            continue
        full = os.path.join(cfg.IMAGE_DIR, name)
        if not os.path.isdir(full):
            continue
        if target_id and post_id != target_id:
            continue
        dirs.append(full)
    return dirs


def _slide_sort_key(fname: str):
    """01.jpg < 02.jpg < 10.jpg (tri numérique sur le préfixe)."""
    m = re.match(r"^(\d+)", fname)
    return (int(m.group(1)), fname.lower()) if m else (10_000_000, fname.lower())


def list_images_in_dir(dir_path: str) -> List[str]:
    """Noms de fichiers image triés par numéro de slide."""
    files = [
        fname for fname in os.listdir(dir_path)
        if os.path.splitext(fname)[1].lower() in OCR_IMAGE_EXTS
        and os.path.isfile(os.path.join(dir_path, fname))
    ]
    files.sort(key=_slide_sort_key)
    return files


def ocr_image(reader, img_path: str) -> str:
    """Texte concaténé extrait par easyocr (paragraph=True : regroupe les lignes proches)."""
    try:
        results = reader.readtext(img_path, detail=0, paragraph=True)
        return "\n".join(line.strip() for line in results if line and line.strip())
    except Exception as e:
        log.warning(f"  OCR échoué {os.path.basename(img_path)}: {e}")
        return ""


def output_path_for(post_id: str) -> str:
    return os.path.join(cfg.TRANSCRIPT_DIR, f"{post_id}_ocr.txt")


def _read_post_text(dir_path: str) -> str:
    """Y.21 : lit `_post_text.txt` (texte du tweet/post écrit par le
    fallback gallery-dl) si présent. Retourne "" sinon."""
    path = os.path.join(dir_path, "_post_text.txt")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def process_carousel(reader, dir_path: str, force: bool, result: ScriptResult) -> None:
    name = os.path.basename(dir_path)
    post_id = _strip_known_prefix(name)
    if post_id is None:
        # ne devrait pas arriver vu list_carousel_dirs, mais safe-guard
        log.warning(f"SKIP dir préfixe inconnu : {name}")
        result.add_skip()
        return
    output = output_path_for(post_id)

    if not force and os.path.isfile(output):
        log.info(f"SKIP {post_id} — {os.path.basename(output)} existe déjà")
        result.add_skip()
        return

    post_text = _read_post_text(dir_path)
    images = list_images_in_dir(dir_path)

    # Y.21 : un dossier sans image MAIS avec _post_text.txt (cas tweet
    # text-only) est traité comme une tâche valide. Avant Y.21, on
    # skippait silencieusement.
    if not images and not post_text:
        log.warning(f"SKIP {post_id} — ni image OCR-able, ni _post_text.txt")
        result.add_skip()
        return

    n_images = len(images)
    label = f"{n_images} slide{'s' if n_images > 1 else ''}" if n_images else "texte seul"
    log.info(f"OCR {post_id} ({label}{' + post text' if post_text and n_images else ''})")

    blocks = []
    if post_text:
        blocks.append(f"[POST TEXT]\n{post_text}")

    for i, fname in enumerate(images, start=1):
        img_path = os.path.join(dir_path, fname)
        text = ocr_image(reader, img_path)
        body = text if text else "[Aucun texte détecté]"
        blocks.append(f"[SLIDE {i} — {fname}]\n{body}")

    os.makedirs(cfg.TRANSCRIPT_DIR, exist_ok=True)
    try:
        with open(output, "w", encoding="utf-8") as f:
            f.write("\n\n".join(blocks) + "\n")
        log.info(f"  → {os.path.basename(output)}")
        result.add_success()
    except Exception as e:
        log.error(f"  ERREUR écriture {post_id}: {e}")
        result.add_fail(f"{post_id}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR Instagram carousels (easyocr fr+en, GPU)")
    cfg.add_base_dir_arg(parser)
    parser.add_argument("--force", action="store_true",
                        help="Réécrire même si <id>_ocr.txt existe déjà")
    parser.add_argument("--id", type=str, default=None,
                        help="Traiter un seul carrousel par post id Instagram")
    args = parser.parse_args()

    cfg.init_from_args(args)
    cfg.ensure_dirs()

    result = ScriptResult("ocr_carousels")

    dirs = list_carousel_dirs(target_id=args.id)
    if not dirs:
        if args.id:
            log.warning(f"Aucun dossier <PREFIX>{args.id}/ trouvé dans {cfg.IMAGE_DIR}")
        else:
            log.warning(f"Aucun dossier post dans {cfg.IMAGE_DIR}")
        result.print_summary()
        result.exit()

    # Y.21 : pré-filtre — skip les dossiers déjà traités (ocr.txt présent)
    # AVANT de charger easyocr (économie 5-10s si rien à faire).
    pending = []
    for d in dirs:
        name = os.path.basename(d)
        post_id = _strip_known_prefix(name)
        if post_id is None:
            continue
        if not args.force and os.path.isfile(output_path_for(post_id)):
            continue
        pending.append(d)

    if not pending:
        log.info(f"{len(dirs)} dossier(s) déjà traité(s), rien à faire")
        result.print_summary()
        result.exit()

    log.info(f"{len(pending)} dossier(s) à traiter (sur {len(dirs)} total)")

    log.info("Chargement easyocr (fr+en, GPU)…")
    try:
        import easyocr
        reader = easyocr.Reader(['fr', 'en'], gpu=True)
    except Exception as e:
        log.error(f"Impossible d'initialiser easyocr: {e}")
        result.add_fail(f"init easyocr: {e}")
        result.print_summary()
        result.exit()

    log.info("easyocr prêt")

    for dir_path in pending:
        process_carousel(reader, dir_path, force=args.force, result=result)

    result.print_summary()
    result.exit()


if __name__ == "__main__":
    main()
