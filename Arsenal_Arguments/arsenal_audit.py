"""
arsenal_audit.py — Audit et réparation du pipeline Arsenal Intelligence Unit.

Vérifie la cohérence entre le CSV et le disque, identifie les contenus
manquants ou incomplets, et permet de re-télécharger uniquement ce qui
est cassé.

Usage :
    python arsenal_audit.py                          # Audit complet (lecture seule)
    python arsenal_audit.py --fix-csv                # Corrige le CSV selon l'état disque
    python arsenal_audit.py --retry-failed           # Re-télécharge les FAILED
    python arsenal_audit.py --check-carousels        # Audit détaillé des carrousels
    python arsenal_audit.py --reset-summaries        # Reset tous les summary_status → PENDING
    python arsenal_audit.py --full-repair            # fix-csv + retry-failed + reset-summaries
"""

import os
import re
import sys
import argparse
from collections import defaultdict, Counter

import pandas as pd

from arsenal_config import (
    cfg, GLOBAL_CSV_COLUMNS, CSV_ENCODING, VIDEO_EXTS, IMAGE_EXTS,
    IG_POST_DIR_PREFIX, normalize_str, now_timestamp,
    ScriptResult, get_logger,
)

log = get_logger("arsenal_audit")


# =============================================================================
# AUDIT HELPERS
# =============================================================================

def count_files_in_dir(dirpath: str, extensions: set = None) -> int:
    """Compte les fichiers dans un dossier (optionnellement filtré par extension)."""
    if not os.path.isdir(dirpath):
        return 0
    count = 0
    for f in os.listdir(dirpath):
        if not os.path.isfile(os.path.join(dirpath, f)):
            continue
        if extensions:
            ext = os.path.splitext(f)[1].lower()
            if ext not in extensions:
                continue
        count += 1
    return count


def list_media_for_id(item_id: str, platform: str) -> dict:
    """
    Vérifie ce qui existe sur disque pour un contenu donné.
    Retourne un dict avec les fichiers trouvés.
    """
    result = {
        "has_video": False,
        "video_files": [],
        "has_images": False,
        "image_count": 0,
        "image_dir": None,
        "has_transcript": False,
        "transcript_file": None,
        "has_summary": False,
        "summary_file": None,
        "has_carousel_transcripts": False,
        "carousel_transcript_count": 0,
    }

    # Vidéos
    if os.path.isdir(cfg.VIDEO_DIR):
        for f in os.listdir(cfg.VIDEO_DIR):
            if f.startswith(f"{item_id}_") and os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                result["has_video"] = True
                result["video_files"].append(f)

    # Images (dossier IG_<ID>)
    img_dir = os.path.join(cfg.IMAGE_DIR, f"{IG_POST_DIR_PREFIX}{item_id}")
    if os.path.isdir(img_dir):
        image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".avif", ".jfif"}
        video_exts_carousel = {".mp4", ".mov", ".webm", ".m4v"}
        all_exts = image_exts | video_exts_carousel
        media_count = count_files_in_dir(img_dir, all_exts)
        if media_count > 0:
            result["has_images"] = True
            result["image_count"] = media_count
            result["image_dir"] = img_dir

    # Transcription
    if os.path.isdir(cfg.TRANSCRIPT_DIR):
        for f in os.listdir(cfg.TRANSCRIPT_DIR):
            base = os.path.splitext(f)[0]
            if base.startswith(f"{item_id}_") or base == item_id:
                result["has_transcript"] = True
                result["transcript_file"] = f
                break

    # Transcriptions carrousel
    carousel_dir = os.path.join(cfg.TRANSCRIPT_CAROUSEL_DIR, f"{IG_POST_DIR_PREFIX}{item_id}")
    if os.path.isdir(carousel_dir):
        txt_count = count_files_in_dir(carousel_dir, {".txt"})
        if txt_count > 0:
            result["has_carousel_transcripts"] = True
            result["carousel_transcript_count"] = txt_count

    # Résumé
    for prefix in ["IG_", "TT_", "SRC_"]:
        summary_file = f"{prefix}{item_id}.txt"
        summary_path = os.path.join(cfg.SUMMARY_DIR, summary_file)
        if os.path.isfile(summary_path) and os.path.getsize(summary_path) > 50:
            result["has_summary"] = True
            result["summary_file"] = summary_file
            break

    return result


# =============================================================================
# AUDIT PRINCIPAL
# =============================================================================

def run_audit(df: pd.DataFrame) -> dict:
    """
    Audit complet : compare le CSV avec l'état du disque.
    Retourne un rapport détaillé.
    """
    report = {
        "total_rows": len(df),
        "platforms": dict(Counter(df["plateforme"].tolist())),
        "download_status": dict(Counter(df["download_status"].str.upper().str.strip().tolist())),
        "summary_status": dict(Counter(df["summary_status"].str.upper().str.strip().tolist())),
        "sync_status": dict(Counter(df["sync_status"].str.upper().str.strip().tolist())),
        "issues": [],
        "stats": {
            "csv_success_with_media": 0,
            "csv_success_no_media": 0,      # SUCCESS dans CSV mais rien sur disque
            "csv_failed_with_media": 0,      # FAILED dans CSV mais fichiers présents
            "has_video_no_transcript": 0,
            "has_images_no_summary": 0,
            "orphan_summaries": 0,
            "carousel_incomplete": 0,
        },
    }

    success_df = df[df["download_status"].str.upper().str.strip() == "SUCCESS"]
    failed_df = df[df["download_status"].str.upper().str.strip() == "FAILED"]

    # Vérifier les SUCCESS
    for _, row in success_df.iterrows():
        item_id = normalize_str(row.get("id"))
        platform = normalize_str(row.get("plateforme"))
        content_type = normalize_str(row.get("resolved_type_final") or row.get("type", ""))

        if not item_id:
            continue

        media = list_media_for_id(item_id, platform)

        has_any_media = media["has_video"] or media["has_images"]

        if has_any_media:
            report["stats"]["csv_success_with_media"] += 1
        else:
            report["stats"]["csv_success_no_media"] += 1
            report["issues"].append({
                "type": "ORPHAN_CSV",
                "id": item_id,
                "platform": platform,
                "detail": "SUCCESS dans CSV mais aucun média sur disque",
            })

        # Vidéo sans transcription
        if media["has_video"] and not media["has_transcript"]:
            report["stats"]["has_video_no_transcript"] += 1

    # Vérifier les FAILED qui ont quand même des fichiers
    for _, row in failed_df.iterrows():
        item_id = normalize_str(row.get("id"))
        platform = normalize_str(row.get("plateforme"))

        if not item_id:
            continue

        media = list_media_for_id(item_id, platform)
        if media["has_video"] or media["has_images"]:
            report["stats"]["csv_failed_with_media"] += 1
            report["issues"].append({
                "type": "RECOVERABLE",
                "id": item_id,
                "platform": platform,
                "detail": f"FAILED dans CSV mais {media['image_count']} images / {len(media['video_files'])} vidéos sur disque",
            })

    return report


def print_report(report: dict):
    """Affiche le rapport d'audit de façon lisible."""
    log.info("=" * 60)
    log.info("AUDIT ARSENAL INTELLIGENCE UNIT")
    log.info("=" * 60)

    log.info(f"\nTotal lignes CSV : {report['total_rows']}")

    log.info(f"\nPar plateforme :")
    for p, c in sorted(report["platforms"].items()):
        log.info(f"  {p:15s} : {c}")

    log.info(f"\nDownload status :")
    for s, c in sorted(report["download_status"].items()):
        log.info(f"  {s:10s} : {c}")

    log.info(f"\nSummary status :")
    for s, c in sorted(report["summary_status"].items()):
        log.info(f"  {s:10s} : {c}")

    log.info(f"\nSync status :")
    for s, c in sorted(report["sync_status"].items()):
        log.info(f"  {s:10s} : {c}")

    log.info(f"\nSanté des données :")
    stats = report["stats"]
    log.info(f"  SUCCESS avec média sur disque     : {stats['csv_success_with_media']}")
    log.info(f"  SUCCESS sans média (orphelins CSV) : {stats['csv_success_no_media']}")
    log.info(f"  FAILED avec média récupérable      : {stats['csv_failed_with_media']}")
    log.info(f"  Vidéo sans transcription            : {stats['has_video_no_transcript']}")

    issues = report["issues"]
    if issues:
        log.info(f"\nProblèmes détectés : {len(issues)}")

        by_type = defaultdict(list)
        for issue in issues:
            by_type[issue["type"]].append(issue)

        for itype, items in sorted(by_type.items()):
            log.info(f"\n  [{itype}] — {len(items)} cas")
            for item in items[:5]:  # Afficher max 5 exemples
                log.info(f"    {item['platform']:10s} {item['id']:20s} | {item['detail']}")
            if len(items) > 5:
                log.info(f"    ... et {len(items) - 5} autres")
    else:
        log.info(f"\nAucun problème détecté ✅")

    log.info("=" * 60)


# =============================================================================
# ACTIONS DE RÉPARATION
# =============================================================================

def fix_csv_from_disk(df: pd.DataFrame) -> pd.DataFrame:
    """
    Corrige le CSV en fonction de l'état réel du disque :
    - SUCCESS sans fichier → FAILED (ORPHAN)
    - FAILED avec fichiers → SUCCESS
    """
    changes = 0

    for idx, row in df.iterrows():
        item_id = normalize_str(row.get("id"))
        platform = normalize_str(row.get("plateforme"))
        status = normalize_str(row.get("download_status")).upper()

        if not item_id:
            continue

        media = list_media_for_id(item_id, platform)
        has_media = media["has_video"] or media["has_images"]

        if status == "SUCCESS" and not has_media:
            df.at[idx, "download_status"] = "FAILED"
            df.at[idx, "error_message"] = normalize_str(row.get("error_message", "")) + " | AUDIT: no media on disk"
            changes += 1
            log.info(f"  FIX {item_id} : SUCCESS → FAILED (pas de média)")

        elif status == "FAILED" and has_media:
            df.at[idx, "download_status"] = "SUCCESS"
            df.at[idx, "error_message"] = ""
            changes += 1
            log.info(f"  FIX {item_id} : FAILED → SUCCESS (média trouvé)")

    log.info(f"CSV corrigé : {changes} ligne(s) modifiée(s)")
    return df


def reset_all_summaries(df: pd.DataFrame) -> pd.DataFrame:
    """Remet tous les summary_status à PENDING pour préparer un re-résumé complet."""
    mask = df["download_status"].str.upper().str.strip() == "SUCCESS"
    count = mask.sum()
    df.loc[mask, "summary_status"] = "PENDING"
    df.loc[mask, "summary_timestamp"] = ""
    df.loc[mask, "summary_error"] = ""
    log.info(f"Summary reset : {count} ligne(s) remises en PENDING")
    return df


def reset_all_syncs(df: pd.DataFrame) -> pd.DataFrame:
    """Remet tous les sync_status à PENDING."""
    mask = df["download_status"].str.upper().str.strip() == "SUCCESS"
    count = mask.sum()
    df.loc[mask, "sync_status"] = "PENDING"
    df.loc[mask, "sync_timestamp"] = ""
    df.loc[mask, "sync_error"] = ""
    log.info(f"Sync reset : {count} ligne(s) remises en PENDING")
    return df


# =============================================================================
# AUDIT CARROUSELS
# =============================================================================

def audit_carousels(df: pd.DataFrame):
    """Audit détaillé des carrousels Instagram."""
    carousel_df = df[
        (df["plateforme"].str.lower().str.strip() == "instagram") &
        (df["resolved_type_final"].str.lower().str.strip().isin({"carrousel", "carousel", "image"})) &
        (df["download_status"].str.upper().str.strip() == "SUCCESS")
    ]

    log.info(f"\n{'='*60}")
    log.info(f"AUDIT CARROUSELS — {len(carousel_df)} carrousels SUCCESS")
    log.info(f"{'='*60}")

    stats = {"total": 0, "ok": 0, "few_slides": 0, "empty": 0}
    few_slides_list = []

    for _, row in carousel_df.iterrows():
        item_id = normalize_str(row.get("id"))
        if not item_id:
            continue

        stats["total"] += 1
        img_dir = os.path.join(cfg.IMAGE_DIR, f"{IG_POST_DIR_PREFIX}{item_id}")

        if not os.path.isdir(img_dir):
            stats["empty"] += 1
            continue

        media_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v"}
        media_count = count_files_in_dir(img_dir, media_exts)

        if media_count == 0:
            stats["empty"] += 1
        elif media_count == 1:
            stats["few_slides"] += 1
            few_slides_list.append((item_id, media_count))
        else:
            stats["ok"] += 1

    log.info(f"  Complets (2+ slides)   : {stats['ok']}")
    log.info(f"  1 seule slide          : {stats['few_slides']}")
    log.info(f"  Vide (0 fichier)       : {stats['empty']}")

    if few_slides_list:
        log.info(f"\n  Carrousels à 1 slide (probablement incomplets) :")
        for cid, count in few_slides_list[:10]:
            log.info(f"    {cid} — {count} fichier(s)")
        if len(few_slides_list) > 10:
            log.info(f"    ... et {len(few_slides_list) - 10} autres")

    return stats


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Arsenal — Audit et réparation")
    cfg.add_base_dir_arg(parser)
    parser.add_argument("--fix-csv", action="store_true",
                        help="Corrige le CSV selon l'état du disque")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Re-télécharge les contenus FAILED")
    parser.add_argument("--check-carousels", action="store_true",
                        help="Audit détaillé des carrousels")
    parser.add_argument("--reset-summaries", action="store_true",
                        help="Reset tous les summary_status → PENDING")
    parser.add_argument("--reset-syncs", action="store_true",
                        help="Reset tous les sync_status → PENDING")
    parser.add_argument("--full-repair", action="store_true",
                        help="fix-csv + reset-summaries + reset-syncs (tout préparer pour re-résumé)")

    args = parser.parse_args()
    cfg.init_from_args(args)

    if not os.path.isfile(cfg.CSV_PATH):
        log.error(f"CSV introuvable : {cfg.CSV_PATH}")
        sys.exit(1)

    result = ScriptResult("arsenal_audit")

    # Charger le CSV
    df = pd.read_csv(cfg.CSV_PATH, dtype=str, keep_default_na=False, encoding=CSV_ENCODING)

    # Toujours faire l'audit en premier
    report = run_audit(df)
    print_report(report)

    if args.check_carousels or args.full_repair:
        audit_carousels(df)

    modified = False

    # Appliquer les réparations
    if args.fix_csv or args.full_repair:
        log.info("\n--- FIX CSV ---")
        cfg.backup_csv("pre_audit_fix")
        df = fix_csv_from_disk(df)
        modified = True

    if args.reset_summaries or args.full_repair:
        log.info("\n--- RESET SUMMARIES ---")
        df = reset_all_summaries(df)
        modified = True

    if args.reset_syncs or args.full_repair:
        log.info("\n--- RESET SYNCS ---")
        df = reset_all_syncs(df)
        modified = True

    if modified:
        df.to_csv(cfg.CSV_PATH, index=False, encoding=CSV_ENCODING)
        log.info(f"\nCSV sauvegardé → {cfg.CSV_PATH}")
        result.add_success()
    else:
        if not any([args.fix_csv, args.retry_failed, args.reset_summaries,
                     args.reset_syncs, args.full_repair]):
            log.info("\nMode lecture seule. Utilise --fix-csv, --full-repair, etc. pour agir.")
        result.add_success()

    if args.retry_failed:
        # Compter les FAILED pour info
        failed_count = (df["download_status"].str.upper().str.strip() == "FAILED").sum()
        log.info(f"\nPour re-télécharger les {failed_count} FAILED, lance ensuite :")
        log.info(f"  python csv_normalize.py --reset-summary-failed")
        log.info(f"  python dl_instagram.py   (pour les IG)")
        log.info(f"  python dl_tiktok.py      (pour les TT)")
        log.info(f"Note : les downloaders skipperont les SUCCESS existants automatiquement.")

    result.print_summary()
    result.exit()


if __name__ == "__main__":
    main()
