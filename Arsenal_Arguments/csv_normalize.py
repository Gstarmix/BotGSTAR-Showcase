"""
csv_normalize.py — Normalisateur et dédoublonneur CSV pour Arsenal Intelligence Unit.

Nettoie le CSV, normalise les statuts/plateformes/types, fusionne les doublons
intelligemment, et crée un backup automatique avant chaque écriture.

Usage :
    python csv_normalize.py                        # normalise le CSV par défaut
    python csv_normalize.py --reset-summary-failed  # remet les summary FAILED en PENDING
    python csv_normalize.py --reset-all-summary     # remet TOUS les summary en PENDING
    python csv_normalize.py --reset-all-sync        # remet TOUS les sync en PENDING
"""

import os
import sys
import argparse

import pandas as pd

from arsenal_config import (
    cfg, GLOBAL_CSV_COLUMNS, COLUMN_DEFAULTS, CSV_ENCODING,
    normalize_str, ScriptResult, get_logger,
)

log = get_logger("csv_normalize")


# =============================================================================
# HELPERS NORMALISATION
# =============================================================================

def normalize_status(x, allowed, default_if_empty=""):
    v = normalize_str(x).upper()
    if not v:
        return default_if_empty
    return v


def normalize_platform(x):
    v = normalize_str(x).lower()
    if v == "instagram":
        return "Instagram"
    if v == "tiktok":
        return "TikTok"
    return normalize_str(x)


def normalize_url(x):
    v = normalize_str(x)
    return v.replace("\u00A0", "") if v else ""


def normalize_type(x):
    v = normalize_str(x)
    if not v:
        return ""
    m = {"video": "Video", "image": "Image", "carrousel": "Carrousel",
         "carousel": "Carrousel", "auto": "Auto", "post": "Post"}
    return m.get(v.lower(), v)


def choose_better_value(series: pd.Series):
    vals = [normalize_str(v) for v in series.tolist()]
    vals = [v for v in vals if v]
    if not vals:
        return ""
    vals.sort(key=lambda s: (len(s), s), reverse=True)
    return vals[0]


def choose_best_status(values, priority):
    normed = [normalize_str(v).upper() for v in values if normalize_str(v)]
    if not normed:
        return ""
    for p in priority:
        if p in normed:
            return p
    return normed[0]


def choose_latest_timestamp(values):
    vals = [normalize_str(v) for v in values if normalize_str(v)]
    if not vals:
        return ""
    vals.sort(reverse=True)
    return vals[0]


# =============================================================================
# RAPPORT
# =============================================================================

def report_stats(df_before, df_after):
    def count_status(df, col):
        if col not in df.columns:
            return {}
        s = df[col].fillna("").astype(str).str.upper()
        return {
            "PENDING": int((s == "PENDING").sum()),
            "SUCCESS": int((s == "SUCCESS").sum()),
            "FAILED": int((s == "FAILED").sum()),
            "EMPTY": int((s == "").sum()),
        }

    log.info(f"Lignes avant  : {len(df_before)}")
    log.info(f"Lignes après  : {len(df_after)}")
    log.info(f"Doublons retirés : {len(df_before) - len(df_after)}")

    if "plateforme" in df_after.columns:
        plat = df_after["plateforme"].fillna("").value_counts(dropna=False)
        log.info(f"Plateformes : {plat.to_dict()}")

    for col_name in ["download_status", "summary_status", "sync_status"]:
        log.info(f"{col_name} : {count_status(df_after, col_name)}")


# =============================================================================
# CORE NORMALIZATION
# =============================================================================

def normalize_csv(csv_path: str, reset_summary_failed=False,
                  reset_all_summary=False, reset_all_sync=False):
    if not os.path.isfile(csv_path):
        log.error(f"Fichier introuvable : {csv_path}")
        return False

    try:
        df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, dtype=str, encoding="utf-8")

    df_before = df.copy()

    # Renommage legacy
    rename_map = {"platform": "plateforme", "Platform": "plateforme",
                  "ID": "id", "URL": "url", "Description": "description",
                  "Username": "username", "Nom du fichier": "filename"}
    existing_rename = {k: v for k, v in rename_map.items() if k in df.columns and v not in df.columns}
    if existing_rename:
        df = df.rename(columns=existing_rename)

    # Ajouter colonnes manquantes
    for col in GLOBAL_CSV_COLUMNS:
        if col not in df.columns:
            df[col] = COLUMN_DEFAULTS.get(col, "")

    # Nettoyage
    for col in df.columns:
        df[col] = df[col].apply(normalize_str)

    df["plateforme"] = df["plateforme"].apply(normalize_platform)
    df["url"] = df["url"].apply(normalize_url)

    for c in ["type", "detected_type_initial", "resolved_type_final"]:
        df[c] = df[c].apply(normalize_type)

    df["download_status"] = df["download_status"].apply(
        lambda x: normalize_status(x, {"SUCCESS", "FAILED", "PENDING"}, ""))
    df["summary_status"] = df["summary_status"].apply(
        lambda x: normalize_status(x, {"SUCCESS", "FAILED", "PENDING"}, "PENDING"))
    df["sync_status"] = df["sync_status"].apply(
        lambda x: normalize_status(x, {"SUCCESS", "FAILED", "PENDING"}, "PENDING"))

    # Extraction ID si manquant
    mask_missing_id = (df["id"] == "") & (df["url"] != "")
    if mask_missing_id.any():
        df.loc[mask_missing_id, "id"] = (
            df.loc[mask_missing_id, "url"].str.rstrip("/").str.split("/").str[-1].fillna("")
        )

    # Supprimer lignes sans clé
    invalid_mask = (df["id"] == "") | (df["plateforme"] == "")
    invalid_count = int(invalid_mask.sum())
    if invalid_count:
        log.warning(f"Lignes supprimées sans id/plateforme : {invalid_count}")
        df = df.loc[~invalid_mask].copy()

    # Y.21 — Colonnes download-related : prendre depuis la ligne au
    # `download_timestamp` le plus récent. Évite que `choose_better_value`
    # retourne `video_direct_failed` (19 chars) au lieu de `gallery_dl_x`
    # (12 chars) parce que la longueur de la chaîne tranche en sa faveur.
    # Avant Y.21, un retry réussi laissait les colonnes mode/type/filename/
    # error_message de l'ancienne ligne FAILED écraser celles de la nouvelle
    # SUCCESS — incohérent.
    DOWNLOAD_COLS = {
        "download_mode", "error_message", "filename", "username",
        "display_name", "description", "hashtags", "thumbnail_url",
        "views_at_extraction", "type", "detected_type_initial",
        "resolved_type_final", "date_publication", "source_input_mode",
    }

    # Dédup intelligente par (plateforme, id)
    grouped_rows = []
    for (plateforme, item_id), g in df.groupby(["plateforme", "id"], dropna=False, sort=False):
        # Tri du groupe par download_timestamp DESC pour identifier la
        # « ligne la plus récente côté download ». Vide = remontent en
        # dernier (ne polluent pas le top).
        g_sorted = g.copy()
        g_sorted["_dl_ts"] = g_sorted["download_timestamp"].fillna("").astype(str)
        g_sorted = g_sorted.sort_values("_dl_ts", ascending=False, kind="stable")
        latest_dl_row = g_sorted.iloc[0] if len(g_sorted) else None

        row = {}
        for col in GLOBAL_CSV_COLUMNS:
            if col in ["download_status", "summary_status", "sync_status",
                       "download_timestamp", "summary_timestamp", "sync_timestamp"]:
                continue
            if col in DOWNLOAD_COLS and latest_dl_row is not None:
                # Valeur de la ligne la plus récente côté download — fallback
                # sur choose_better_value si elle est vide pour cette ligne
                # (ex : nouvelle row a description="", ancienne en a une).
                latest_val = normalize_str(latest_dl_row.get(col, ""))
                if latest_val:
                    row[col] = latest_val
                    continue
            row[col] = choose_better_value(g[col]) if col in g.columns else COLUMN_DEFAULTS.get(col, "")

        row["download_status"] = choose_best_status(
            g["download_status"].tolist(), ["SUCCESS", "FAILED", "PENDING", ""])
        row["summary_status"] = choose_best_status(
            g["summary_status"].tolist(), ["SUCCESS", "PENDING", "FAILED", ""])
        row["sync_status"] = choose_best_status(
            g["sync_status"].tolist(), ["SUCCESS", "PENDING", "FAILED", ""])

        row["download_timestamp"] = choose_latest_timestamp(g["download_timestamp"].tolist())
        row["summary_timestamp"] = choose_latest_timestamp(g["summary_timestamp"].tolist())
        row["sync_timestamp"] = choose_latest_timestamp(g["sync_timestamp"].tolist())

        for col in GLOBAL_CSV_COLUMNS:
            if col not in row or row[col] is None:
                row[col] = COLUMN_DEFAULTS.get(col, "")

        if not row["type"] and row["resolved_type_final"]:
            row["type"] = row["resolved_type_final"]

        # Y.21 — si le download a réussi, ne PAS conserver une error_message
        # héritée d'un ancien FAILED (sinon l'embed Pipeline montre une
        # stderr alors que le download a finalement marché via fallback).
        if row.get("download_status") == "SUCCESS":
            row["error_message"] = ""

        grouped_rows.append(row)

    df_norm = pd.DataFrame(grouped_rows)

    for col in GLOBAL_CSV_COLUMNS:
        if col not in df_norm.columns:
            df_norm[col] = COLUMN_DEFAULTS.get(col, "")
    df_norm = df_norm[GLOBAL_CSV_COLUMNS]

    # --- Resets optionnels ---
    if reset_summary_failed:
        mask = df_norm["summary_status"].str.upper() == "FAILED"
        count = int(mask.sum())
        if count:
            df_norm.loc[mask, "summary_status"] = "PENDING"
            df_norm.loc[mask, "summary_timestamp"] = ""
            df_norm.loc[mask, "summary_error"] = ""
            log.info(f"Reset summary FAILED → PENDING : {count} ligne(s)")

    if reset_all_summary:
        mask = df_norm["summary_status"].str.upper() != ""
        df_norm.loc[mask, "summary_status"] = "PENDING"
        df_norm.loc[mask, "summary_timestamp"] = ""
        df_norm.loc[mask, "summary_error"] = ""
        log.info(f"Reset ALL summary → PENDING : {int(mask.sum())} ligne(s)")

    if reset_all_sync:
        mask = df_norm["sync_status"].str.upper() != ""
        df_norm.loc[mask, "sync_status"] = "PENDING"
        df_norm.loc[mask, "sync_timestamp"] = ""
        df_norm.loc[mask, "sync_error"] = ""
        log.info(f"Reset ALL sync → PENDING : {int(mask.sum())} ligne(s)")

    # Tri
    df_norm["_sort_plat"] = df_norm["plateforme"].map({"Instagram": 1, "TikTok": 2}).fillna(99)
    df_norm = df_norm.sort_values(
        by=["_sort_plat", "download_timestamp", "id"],
        ascending=[True, False, True], kind="stable",
    ).drop(columns=["_sort_plat"])

    # Backup + écriture
    backup_path = cfg.backup_csv("normalize")
    df_norm.to_csv(csv_path, index=False, encoding=CSV_ENCODING)

    log.info(f"CSV normalisé → {csv_path}")
    log.info(f"Backup → {backup_path}")
    report_stats(df_before, df_norm)
    return True


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Arsenal — Normalisateur CSV")
    cfg.add_base_dir_arg(parser)
    parser.add_argument("--csv-path", type=str, help="Chemin CSV à normaliser")
    parser.add_argument("--reset-summary-failed", action="store_true",
                        help="Remet summary_status FAILED → PENDING")
    parser.add_argument("--reset-all-summary", action="store_true",
                        help="Remet TOUS les summary_status → PENDING")
    parser.add_argument("--reset-all-sync", action="store_true",
                        help="Remet TOUS les sync_status → PENDING")
    args = parser.parse_args()

    cfg.init_from_args(args)
    csv_path = args.csv_path or cfg.CSV_PATH

    result = ScriptResult("csv_normalize")

    ok = normalize_csv(
        csv_path,
        reset_summary_failed=args.reset_summary_failed,
        reset_all_summary=args.reset_all_summary,
        reset_all_sync=args.reset_all_sync,
    )

    if ok:
        result.add_success()
    else:
        result.add_fail("Normalisation échouée")

    result.print_summary()
    result.exit()


if __name__ == "__main__":
    main()
