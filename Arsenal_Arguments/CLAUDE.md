# CLAUDE.md — Arsenal Intelligence Unit

Fichier lu automatiquement par Claude Code à chaque session.
Contient tout le contexte technique pour travailler sur ce projet.

---

## Projet

Pipeline local qui transforme des contenus bruts issus de réseaux sociaux (TikTok, Instagram) en publications structurées sur Discord. Analyse politique orientée LFI / Union Populaire. Auteur : Gaylord (seul développeur). Langue de travail : français.

**Chemins clés :**
- Bot Discord : `C:\Users\Gstar\OneDrive\Documents\BotGSTAR\`
- Pipeline Arsenal : `C:\Users\Gstar\OneDrive\Documents\BotGSTAR\Arsenal_Arguments\`
- CSV source de vérité : `Arsenal_Arguments\suivi_global.csv`
- Résumés IA : `Arsenal_Arguments\03_ai_summaries\`
- Vidéos : `Arsenal_Arguments\01_raw_videos\`
- Images/carrousels : `Arsenal_Arguments\01_raw_images\IG_<POSTID>\`
- Transcriptions : `Arsenal_Arguments\02_whisper_transcripts\`
- Transcriptions carrousels : `Arsenal_Arguments\02_whisper_transcripts_carousels\IG_<POSTID>\`

---

## Stack

| Composant | Détail |
|-----------|--------|
| OS | Windows 10/11, PowerShell, VS Code |
| Python | 3.12 |
| Bot Discord | discord.py, système de cogs/extensions |
| Résumés IA | API Anthropic Claude (ANTHROPIC_API_KEY en var env Windows) |
| Transcription | faster-whisper, large-v3, GPU CUDA RTX 2060, int8_float16, VAD |
| Téléchargement | yt-dlp.exe + cookies Netscape (Instagram, TikTok) |
| Données | CSV central (suivi_global.csv), 25 colonnes, 3 statuts pipeline |
| Hardware | RTX 2060 (6 Go VRAM) |

---

## Architecture — Scripts pipeline

| Script | Rôle |
|--------|------|
| `arsenal_config.py` | Module centralisé : chemins, colonnes CSV, helpers, ScriptResult |
| `dl_tiktok.py` | Téléchargeur TikTok (argparse, --url / --input-file) |
| `dl_instagram.py` | Téléchargeur Instagram (carrousels, fallbacks multiples) |
| `dl_generic.py` | Téléchargeur universel (yt-dlp) pour YouTube / X / Reddit / Threads. **Y.21** : `gallery_dl_fallback()` quand yt-dlp ne ramène pas de média (tweet text/image, post Threads/Reddit avec photos) → sauve dans `01_raw_images/<PREFIX><id>/NN.ext` + `_post_text.txt`. |
| `csv_normalize.py` | Normalisateur CSV (dédup, backup auto, flags reset) |
| `summarize.py` | Résumeur IA — API Anthropic OU CLI Claude Code (`--use-claude-code`, fallback gratuit subscription depuis l'épuisement de la clé API en 2026-04) |
| `ocr_carousels.py` | OCR easyocr (fr+en, GPU) sur tous les dossiers `01_raw_images/<PREFIX><id>/` (IG_, X_, THREADS_, REDDIT_…) → `02_whisper_transcripts/<id>_ocr.txt`. **Y.21** : prepend `_post_text.txt` en bloc `[POST TEXT]` (tweets text-only OK), pré-filtre des dossiers déjà traités avant init easyocr (économie ~5s quand rien à faire). |
| `arsenal_transcribe.ps1` | Wrapper Whisper Arsenal (2 passes : vidéos + carrousels) |
| `whisper_engine.ps1` | Moteur Whisper (faster-whisper, GPU, progress bar) |
| `whisper_supervisor.py` | **NEW 2026-04-28** : supervise Whisper, auto-restart si stall > 15 min, isole les vidéos qui font hanger libav vers `_corrupted_videos/`. Log Discord embeds (#logs) à chaque event. |
| `progress_monitor.py` | **NEW 2026-04-28** : surveille un glob (`02_whisper_transcripts/*.txt`), poste un embed Discord par fichier traité avec metadata Whisper (audio_duration, transcribe_time, ratio, segments, langue) parsées depuis `session.log`. Mode `--kind whisper|ocr|generic`. |
| `post_whisper_orchestrator.py` | **NEW 2026-04-28** : attend que `whisper_supervisor.log` contienne `[done]`, puis chaîne OCR carrousels + audit final. Posts récap Discord. |
| `arsenal_audit.py` | Audit santé pipeline (CSV, transcriptions, summaries) avec `--fix-csv` |
| `arsenal_agent.py` | Agent Flask (port 5679) — pont n8n Docker ↔ Windows |
| `summarize_gui.py` | **NEW 2026-04-28** : GUI Tkinter pour piloter `summarize.py` (lancer/pause/stop, console live, progression+coût parsés depuis stdout, raccourcis vers logs/audit/billing/usage). **Phase Y (30/04)** : fenêtre scrollable (Canvas + Scrollbar verticale), choix Moteur (CLI vs API) persistant via `_secrets/engine_pref.json`. |
| `start_summarize_gui.vbs` | **NEW 2026-04-28** : lanceur silencieux (double-clic, pas de console) — pattern jumeau de `start_tray.vbs`. |
| `claude_usage.py` | **NEW 2026-04-29** : module standalone qui lit le quota Pro Max via l'endpoint privé `/api/organizations/{ORG}/usage`. Cookie chiffré DPAPI. CLI `--set-cookie/--fetch/--state/--clear`. Utilisé par la GUI pour le poll 60 s. |
| `arsenal_retry_failed.py` | **NEW 2026-04-29** : rattrape les `download_status=FAILED` du CSV (déduplication par (plateforme, id) sur le timestamp le plus récent), relance via le bon downloader, profite du fix gallery-dl. Modes `--apply / --limit / --platform`. |
| `_claude_logs/audit_liens_channel.py` | **NEW 2026-05-03** (Y.17+) : audit exhaustif du salon `🔗・liens` — détecte 11 catégories d'incohérences par fil (Dossier indexé manquant, embeds pipeline absents, ❌ persistante, fil non archivé, etc.) et fixe 9/11 avec `--apply`. Idempotent. Voir `BotGSTAR/CLAUDE.md` §13. |
| `_claude_logs/retrofit_link_threads.py` | **v1.4 (2026-05-03)** : retrofit Y.9/Y.11/Y.12/Y.13 pour les drops historiques. v1.2 ajoute la résolution TikTok short URL avant lookup ; v1.3 ajoute le fallback pmap Y.15 quand l'embed Dossier indexé n'est plus dans la fenêtre #logs ; v1.4 essaie plusieurs clés pmap (`{plat}::cid`, `::cid`, `unknown::cid`) pour couvrir les drops avant migration Y.18. |

## Architecture — Bot Discord

| Script | Rôle |
|--------|------|
| `bot.py` | Point d'entrée, charge les extensions |
| `extensions/arsenal_publisher.py` | Cog publication : forums, tags, médias, transcriptions, résumés |

---

## CSV — Schéma (25 colonnes)

```
id, url, plateforme, source_input_mode, type, detected_type_initial, resolved_type_final,
download_mode, download_status, error_message, username, display_name, hashtags, description,
thumbnail_url, views_at_extraction, filename, date_publication, download_timestamp,
summary_status, summary_timestamp, summary_error, sync_status, sync_timestamp, sync_error
```

**Statuts** : toujours `PENDING`, `SUCCESS`, `FAILED` (majuscules).
**Source de vérité** : le CSV pilote tout le pipeline. Chaque script lit/écrit via `arsenal_config.py`.

---

## Module centralisé : arsenal_config.py

Tous les scripts importent `arsenal_config` au lieu de définir leurs propres chemins.

```python
from arsenal_config import cfg, GLOBAL_CSV_COLUMNS, append_to_csv, ScriptResult, get_logger

cfg.VIDEO_DIR       # → C:\...\Arsenal_Arguments\01_raw_videos
cfg.CSV_PATH        # → C:\...\Arsenal_Arguments\suivi_global.csv
cfg.SUMMARY_DIR     # → C:\...\Arsenal_Arguments\03_ai_summaries
cfg.YTDLP_PATH      # → C:\...\Arsenal_Arguments\yt-dlp.exe
cfg.COOKIES_INSTAGRAM
cfg.COOKIES_TIKTOK
cfg.SUMMARIZER_LOCK
cfg.backup_csv("label")
cfg.summary_filename("Instagram", "ABC123")  # → IG_ABC123.txt
```

**ScriptResult** : objet de résultat standard → compteurs, sortie JSON, sys.exit(0/1).
**Argparse** : `cfg.add_base_dir_arg(parser)` ajoute `--base-dir` à tout script.

---

## Conventions de code

- Imports depuis `arsenal_config` — jamais de chemins en dur
- Scripts CLI : argparse + sys.exit(0/1) + ScriptResult JSON sur stdout
- CSV : encodage `utf-8-sig` (compatibilité Excel), colonnes dans l'ordre `GLOBAL_CSV_COLUMNS`
- Nommage résumés : `IG_<POSTID>.txt`, `TT_<ID>.txt`, `SRC_<ID>.txt`
- Nommage médias IG : `01_raw_images/IG_<POSTID>/01.jpg`, `02.mp4`, etc.
- Toujours appeler `csv_normalize.py` après un downloader
- Lock fichier pour le summarizer (anti-double-run)
- Logs via `get_logger("nom_script")`

---

## Commandes Discord (arsenal_publisher)

| Commande | Rôle |
|----------|------|
| `!sync_arsenal` | Lance la publication des résumés PENDING → forums Discord |
| `!stats_arsenal` | Stats pipeline (download/summary/sync counts) |
| `!clear_arsenal` | Purge : supprime tous les forums, reset sync_status |
| `!archive_arsenal [target]` | (Y.5) Force un cycle d'auto-archive immédiat. Optionnel : `target` override `ARCHIVE_TARGET` pour ce run (ex `!archive_arsenal 600` pour archivage agressif) |

## Auto-archive Discord (Phase Y.5)

`arsenal_publisher` a un `tasks.loop(hours=1)` `_auto_archive_loop` qui
archive les vieux threads d'`ANALYSES POLITIQUES` quand la guilde dépasse
**900/1000 threads actifs** (limite Discord serveurs non-boostés). Sans ce
loop, l'auto-sync échoue avec `400 ... 160006: Maximum number of active
threads reached` dès que la limite est atteinte.

Comportement :
- Skip si `is_syncing` (évite conflits PATCH concurrents).
- `guild.active_threads()` REST → si > 900, trie threads d'ANALYSES
  POLITIQUES par snowflake ID (plus vieux d'abord), archive jusqu'à
  redescendre à 800.
- Sleep 0.5 s entre PATCHs, abandon après 5 échecs consécutifs.
- Embed récap dans `📋・logs` avec breakdown par forum (silencieux si
  rien archivé).
- Threads archivés restent visibles, auto-unarchivent si message posté.

Constantes modifiables dans `__init__` : `ARCHIVE_THRESHOLD = 900`,
`ARCHIVE_TARGET = 800`, `ARCHIVE_INTERVAL_HOURS = 1`.

---

## Whisper — Deux passes Arsenal

**Passe 1** : Vidéos simples
- Source : `01_raw_videos/`
- Sortie : `02_whisper_transcripts/`

**Passe 2** : Slides vidéo de carrousels
- Source : `01_raw_images/IG_<POSTID>/*.mp4`
- Sortie : `02_whisper_transcripts_carousels/IG_<POSTID>/`

Commande : `.\arsenal_transcribe.ps1` (appelle `whisper_engine.ps1` deux fois)

---

## API Anthropic — Pattern d'appel Python

```python
import anthropic
client = anthropic.Anthropic()  # lit ANTHROPIC_API_KEY depuis env
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=4096,
    messages=[{"role": "user", "content": payload}]
)
text = response.content[0].text
```

Pour les images (carrousels) : envoyer en base64 dans le message avec type `image`.

---

## Pièges connus

- **Cookies yt-dlp** : les fichiers cookies contiennent des tokens de session réels. Ne jamais les logger ni les committer.
- **Lock summarizer** : si le script crash, le lock reste → supprimer manuellement `_locks/summarizer.lock`. Le lock vérifie si le PID est encore vivant avant de refuser.
- **Carrousels IG complexes** : multiples fallbacks dans `dl_instagram.py` :
  1. `download_carousel_from_browser_json` (si JSON enrichi présent)
  2. `fetch_instagram_manifest` + parcours entries (yt-dlp)
  3. `download_carousel_by_img_index_probe` (probe `?img_index=N`, élargi à `Auto/Image` depuis 2026-04-29)
  4. **`download_via_gallery_dl`** (NEW 2026-04-29) — sauve les carrousels d'images IG modernes que yt-dlp 2026 ne récupère plus
  5. `_download_thumbnail_direct_fallback` (dernier recours, 1 image preview)
  Ne pas simplifier cette logique, elle gère des cas réels.
- **CSV concurrence** : aucun lock inter-processus. Ne pas lancer deux scripts qui écrivent le CSV en même temps. `arsenal_retry_failed.py` est OK car il lance les downloaders en série.
- **Encodage CSV** : toujours `utf-8-sig`, jamais `utf-8` seul (sinon Excel casse les accents).
- **Encodage console (Windows)** : `arsenal_config.py` force `sys.stdout.reconfigure(encoding="utf-8")` au load pour éviter les `UnicodeEncodeError` sur les emojis (✅ ⚠️ 🎬) en console cp1252 par défaut.
- **Discord upload limit** : 10 Mo max par fichier. Les médias plus gros sont ignorés.
- **Cookies Cloudflare claude.ai** : les `__cf_bm` (~30 min) et `cf_clearance` (~1h) du cookie session expirent rapidement. Si `claude_usage.py --fetch` retourne 403, recopier le cookie depuis Chrome DevTools (Network → request `usage` → Headers → `cookie:`) et `--set-cookie` à nouveau. Pas besoin de `curl_cffi` / TLS bypass — les headers Chrome (sec-ch-ua-*, priority, sec-fetch-*) suffisent quand le cookie est frais.
- **Threads.com vs threads.net** (Y.20) : Meta a unifié sur `threads.com` mi-2025 mais yt-dlp et gallery-dl 1.32 gardent leur extracteur sur `threads.net`. Drop d'une URL `.com` → yt-dlp suit le redirect HTTP `.net→.com` côté serveur puis "Unsupported URL". Le bot reconnaît les deux dans ses regex et `dl_generic` convertit `.com→.net` avant yt-dlp, mais yt-dlp re-redirige. **Limitation amont** — pas de fix côté bot. À surveiller dans les versions futures de yt-dlp.
- **`fix_text_tweets.py` déprécié** (Y.21) : la logique a été intégrée dans `dl_generic.gallery_dl_fallback()`. Le fichier reste en place sous forme de stub (`exit 2`) pour éviter qu'un appel historique passe silencieusement. Pour rattraper des FAILED historiques, utiliser `arsenal_retry_failed.py --platform X --apply` qui re-lance désormais le bon downloader (gallery-dl pour text/image, yt-dlp sinon).
- **Naming dossiers `01_raw_images/`** (Y.21) : nouveau préfixe par plateforme (`X_<id>/`, `THREADS_<id>/`, `REDDIT_<id>/`). Le legacy `IG_<id>/` reste seul utilisé pour Instagram. `cfg.post_dir(platform, id)` est la source de vérité — ne pas hardcoder. `summarize.py:build_indexes` ne scanne `image_idx` que sous `IG_*` aujourd'hui, donc les images X/Threads/Reddit ne sont PAS envoyées à Claude Vision : elles sont consommées via le `<id>_ocr.txt` produit par OCR (mode text → compatible CLI gratuit). Si tu veux activer Claude Vision sur ces images plus tard, il faudra étendre le scan dans `summarize.build_indexes`.
- **Tweets text-only** : yt-dlp ne télécharge que les tweets avec vidéo. `No video could be found in this tweet` = pas de média à analyser. Le pipeline ne sait pas traiter du texte pur de tweet.
- **pmap clé `::cid`** (Y.18) : si tu vois des entries `::sid` dans `datas/arsenal_published_threads.json`, c'est qu'un drop SRC_*.txt (X / YouTube / Reddit / Threads) a été synced sans la fallback CSV plateforme. Relancer le script de migration one-shot (voir CHANGELOG Y.18) pour les renommer en `{plat}::cid`.

## Migration 2026-04-29 — Veille → ISTIC L1 G2

| Élément | Avant (Veille `1475846763909873727`) | Après (ISTIC L1 G2 `1466806132998672466`) |
|---|---|---|
| Salon `#liens` | `1493701174656766122` | `1498918445763268658` (`🔗・liens` dans `QG`, public lecture / admin écriture) |
| Salon `#logs` | `1475955504332411187` | `1493760267300110466` (`📋・logs` dans `QG`) |
| Catégorie forums Arsenal | `ARSENAL` | `📂 ANALYSES POLITIQUES` (`1498918425584603168`) |
| Catégorie veille politique | `📡 VEILLE POLITIQUE` séparée | Fusionnée dans `📡 VEILLE` (`1497581043086135478`) avec les 4 salons tech |

8 fichiers modifiés (cogs + scripts annexes). Scripts de migration archivés
dans `_claude_logs/` : `audit_discord_structure.py`, `migrate_setup_istic.py`,
`migrate_cleanup_veille.py`. Le serveur Veille reste actif pour TRAVAUX,
LOGICIELS, PROMPTS et salons annexes (général, blabla, n8n, whisper, test,
inspirations, rules) — non concernés par la migration.

---

## Tests rapides

```powershell
cd "C:\Users\Gstar\OneDrive\Documents\BotGSTAR\Arsenal_Arguments"

# Vérifier la config
python arsenal_config.py

# Normaliser le CSV (safe, backup auto)
python csv_normalize.py

# Tester un download TikTok
python dl_tiktok.py --url "https://www.tiktok.com/@user/video/123"

# Lancer le summarizer (un seul contenu)
python summarize.py --id "ABC123"

# Workflow recommandé : GUI Tkinter pour le batch summarize
wscript start_summarize_gui.vbs

# Configurer le cookie claude.ai (1ère fois seulement, voir CLAUDE.md §13 BotGSTAR)
python claude_usage.py --set-cookie
python claude_usage.py --fetch    # vérifier le quota

# Rattraper les downloads FAILED (post-fix gallery-dl)
python arsenal_retry_failed.py            # dry-run
python arsenal_retry_failed.py --apply    # exécute (long)

# Lancer le bot Discord (normalement géré par bot_tray.py)
cd ..
python bot.py
```

---

## Workflow dans Claude Code

1. Lire le fichier concerné avant modification
2. Modifier via str_replace (chirurgical) ou réécriture si petit fichier (<200 lignes)
3. Tester : `python <script>.py --help` ou test rapide
4. Valider que les imports fonctionnent : `python -c "from arsenal_config import cfg; print(cfg.base_path)"`
