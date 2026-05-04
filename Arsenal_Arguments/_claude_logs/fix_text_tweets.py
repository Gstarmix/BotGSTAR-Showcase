"""
fix_text_tweets.py — DEPRECATED 2026-05-04 (Phase Y.21).

Cette logique est désormais intégrée nativement dans `dl_generic.py` :
quand yt-dlp échoue ou ne trouve pas de vidéo sur X / Threads / Reddit,
un fallback `gallery_dl_fallback()` télécharge images + vidéos dans
`01_raw_images/<PREFIX><id>/` et écrit le texte du post dans
`_post_text.txt`. La step `step_ocr` du pipeline produit ensuite le
fichier `<id>_ocr.txt` consommable par summarize en mode CLI gratuit.

→ Les nouveaux drops X text-only / image-only fonctionnent désormais
  directement via le pipeline `🔗・liens` standard.

→ Pour rattraper des historiques FAILED, utiliser :
        python arsenal_retry_failed.py --platform X --apply

Ce stub existe seulement pour signaler la migration ; appel = exit 2.
"""
import sys

if __name__ == "__main__":
    print(__doc__)
    sys.exit(2)
