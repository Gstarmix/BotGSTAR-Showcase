# CLAUDE.md — Instructions Claude Code pour BotGSTAR
# Bot Discord multi-serveurs — Gaylord ABOEKA, 2025-2026
# Dernière mise à jour : 2026-05-06 (élagage majeur — historique → CHANGELOG.md)
#
# 📜 HISTORIQUE DÉTAILLÉ : voir `CHANGELOG.md` (Phases A → Y.23, bugs corrigés, migrations).
# 📂 SUB-PROJET ARSENAL : voir `Arsenal_Arguments/CLAUDE.md`.

---

## 1. Pied à terre — où est quoi

| Fichier | Rôle |
|---|---|
| `bot.py` | Entry point — charge le Cog, gère le rate-limit, on_ready. |
| `extensions/cours_pipeline.py` | **Source de vérité** : commandes, helpers, watcher, JSON I/O. |
| `extensions/veille_rss.py` | Cog veille RSS tech (4 salons digest matinal). |
| `extensions/veille_rss_politique.py` | Cog veille RSS politique (7 salons Option C). |
| `extensions/arsenal_pipeline.py` + `arsenal_publisher.py` | Pipeline veille vidéo politique 6 plateformes. |
| `bot_tray.py` + `start_tray.vbs` | Mode tray (icône system, auto-restart 10s). |
| `start_bot.bat` | Mode console (debug, click=restart). |
| `datas/_published.json` | Tracking publications audio/transcription/résumé. |
| `datas/discord_published.json` | Tracking forums correction (schéma v2). |
| `datas/discord_perso_published.json` | Tracking forum perso (schéma v1). |
| `datas/embed_logs.json` | Cache embed last-state pour `#logs`. |

**Mono-serveur** : `ISTIC_GUILD_ID = 1466806132998672466`. **Admin only** : rôle `ADMIN_ROLE_ID = 1493905604241129592` (check global via `cog_check`).

---

## 2. Commandes disponibles (24+)

Préfixe `!cours`. Triées par usage.

### Setup infrastructure (5)
| Commande | Rôle |
|---|---|
| `setup-channels` | Normalise emojis salons publics (🎧📝📌📋). |
| `setup-forums` | Crée 5 forums correction (`corrections-{mat}`). |
| `setup-tags` | Crée 7 tags forum public (4 type + 3 état). |
| `setup-perso` | Crée catégorie `🔒 PERSONNEL` + 5 forums `perso-{mat}`. |
| `setup-tags-perso` | Crée 8 tags forum perso (4 type + 4 matériel). |

### Publication corrections / énoncés (5)
| Commande | Rôle |
|---|---|
| `publish <type> <mat> <num> <date>` | Pipeline classique CM (audio + transcription + résumé). |
| `publish correction <mat> <type> <num> <exo>` | Publie 1 PDF correction. |
| `publish enonce <mat> <type> <num> [annee]` | Publie un énoncé seul. |
| `backfill <mat>` | Rattrape stock corrections (dry-run + confirm). |
| `backfill-enonces <mat>` | Rattrape énoncés manquants. |
| `republish <type> <mat> <num> <date>` | Re-poste résumé seul (CM). |
| `republish-correction ...` | Force republication v2. |

### Publication matériel perso (3)
| `publish-perso <mat> <type> <num> [annee]` | Publie tout matériel perso d'un TD/TP/CC. |
| `backfill-perso <mat>` | Rattrape matériel perso d'une matière. |
| `purge-perso <mat> <type> <num> [annee]` | Vide entrée tracking perso. |

### Watcher publication auto (1)
| `watcher <start\|stop\|status>` | Polling 60 s sur `corrections/*.pdf`. **Auto-start au boot**. Récap quotidien `#logs` ~23h Paris. |

### Maintenance (2)
| `purge-thread <mat> <type> <num> [annee]` | Réinitialise entrée JSON correction. |
| `sync-absences` | Scanne 6 derniers mois pour hydrater `_absences.json`. |

### Inspection / rapports (8)
| `status`, `missing`, `scan`, `auto`, `absent`, `absences`, `rapport [mat] [--deep]`, `inbox` | Voir code pour détails. |

---

## 3. Architecture des forums Discord

### Forums publics correction (Phase A → D)
5 forums `corrections-{matiere}` (an1/en1/prg2/psi/ise).

**7 tags** par forum :
- **Type** (4) : `TD`, `TP`, `CC`, `Quiz` (sans emoji)
- **État** (3) : `📄 Énoncé seul`, `✍️ Corrections présentes`, `📄 Énoncé manquant`

**1 thread par TD/TP/CC** : énoncé en 1ᵉʳ post (embed + PDF), corrections en posts suivants (1 par exercice). Un seul tag d'état à la fois (transitions auto).

**Versioning** : MD5 changé → ancien message supprimé + repost avec préfixe `🔄 Version N`.

### Forum privé matériel perso (Phase F1)
Catégorie `🔒 PERSONNEL` (admin only) + 5 forums `perso-{matiere}`.

**8 tags** : 4 type (TD/TP/CC/Quiz) + 4 matériel (`📋 TACHE`, `📝 Script oral`, `📊 Slides`, `🎬 Vidéo`).

**Ordre posts** (tri stable via `_PERSO_KIND_ORDER`) : TACHE → Script oral → Script imprimable (mappé tag « Script oral ») → Slides → Slides source → Vidéo.

Vidéos > 25 Mo : embed « trop lourd, conservé localement ».

### Salons publics CM
`{cm,td,tp}-{audio,transcription,resume}-{matiere}` (15 salons : 3 types × 5 matières). **Texte brut** (pas embed) pour compatibilité mobile + listener `on_message`.

---

## 4. Storage JSON sous `datas/`

| Fichier | Schéma | Clé principale |
|---|---|---|
| `_published.json` | v1 | `{TYPE}{NUM}_{MAT}_{DATE}` |
| `discord_published.json` | **v2** | `thread_key` (`AN1__TD__4`, `AN1__CC__4__2024-2025`) |
| `discord_perso_published.json` | v1 | `thread_key` même format |
| `_absences.json` | v1 | `{TYPE}{NUM}_{MAT}` |

**Règle critique** : ces fichiers sont écrits atomiquement (`.tmp` + `os.replace`). **À ne jamais modifier à la main pendant que le bot tourne**.

Désynchro suspectée :
- Côté correction : `!cours purge-thread <mat> <type> <num> [annee]`
- Côté perso : `!cours purge-perso <mat> <type> <num> [annee]`

### Structure `discord_published.json` v2
```json
{
  "schema_version": 2,
  "threads": {
    "AN1__TD__4": {
      "matiere": "AN1", "type": "TD", "num": "4", "annee": null,
      "thread_id": "...", "forum_id": "...", "titre_td": "...",
      "enonce": {"pdf_path": "...", "md5": "...", "message_id": "...", "status": "present", "version": 1},
      "corrections": {"5": {"pdf_path": "...", "md5": "...", "message_id": "...", "version": 1}},
      "state": "corrections_present",
      "tags_applied": ["TD", "Corrections présentes"],
      "created_at": "...", "last_updated": "..."
    }
  }
}
```

### Structure `discord_perso_published.json` v1
```json
{
  "threads": {
    "AN1__TD__4": {
      "matiere": "AN1", "type": "TD", "num": "4", "annee": null,
      "thread_id": "...", "forum_id": "...", "title": "[TD4] ...",
      "posts": {
        "tache:ex5": {"kind": "tache", "rel_key": "...", "md5": "...", "message_id": "...", "version": 1},
        "script:ex5:md": {"kind": "script", ...},
        "slides:global": {"kind": "slides", ...}
      },
      "tags_applied": ["TD", "TACHE", "Script oral", "Slides"],
      ...
    }
  }
}
```

`post_key` pour `script` inclut suffixe d'extension (`script:ex5:md`, `script:ex5:txt`, `script:global:json`).

---

## 5. Watcher corrections (Phase B)

Polling 60 s sur `COURS/{MAT}/**/corrections/*.pdf`. Pour chaque PDF :
1. `parse_correction_filename` extrait `(type, num, exo, annee)`.
2. `_do_publish_correction` (idempotent via MD5).
3. Si `status ∈ {ok, ok_v2}`, compteur jour incrémenté.

**Récap quotidien** dans `#logs` à **22h59 UTC** (~23h Paris). Embed : total + détail par matière.

**Auto-start au boot** via `on_ready` (idempotent : gardes `corrections_watcher_running`, `task non-done`). Annulé proprement au `cog_unload`.

**Pas de watcher** pour énoncés ni matériel perso — publication manuelle uniquement.

---

## 6. Watchdog `_INBOX` + watcher `_publish_queue`

### Watchdog `_INBOX` (60 s)
Range fichiers selon `{TYPE}{NUM}_{MAT}_{DATE}.{txt|m4a|pdf|docx}`. Stabilité (taille inchangée 2 ticks). Doublons MD5 supprimés. Log embed par fichier traité dans `#logs`. Voir `COURS/CLAUDE.md` §8.

### Phase L — Watcher `_publish_queue`
`@tasks.loop(seconds=60)` qui scanne `COURS/_publish_queue/*.json`. **Deux modes** depuis Phase O+ :

#### Mode A — Pipeline officiel (recommandé)
**À utiliser dès qu'on cible un thread canonique** :
```json
{
  "manifest_version": 1,
  "kind": "perso" | "correction" | "enonce",
  "matiere": "EN1",
  "type": "CC",            // TD, TP, CC, CM, Quiz
  "num": "2",              // numérique ou textuel (PSI : SHANNON, SGF…)
  "annee": "2024-25",      // optionnel (requis pour CC datés)
  "exo": "5",              // requis si kind=correction
  "force_republish": false,
  "purge_existing": false  // delete thread tracké + recrée from scratch
}
```

Route vers `_do_publish_perso` / `_do_publish_correction` / `_do_publish_enonce`. **Garanties** : tracking JSON atomique, idempotence MD5, versioning auto, tags appliqués, réconciliation thread supprimé. Le matériel posté vient du **disque** (`list_perso_material`, `resolve_correction_pdf`, `find_enonce_pdf`).

#### Mode B — Freeform (fallback)
**À éviter sauf hors-sujets** ou contenus non-canoniques :
```json
{
  "manifest_version": 1,
  "kind": "perso" | "off-topic",
  "matiere": "EN1",
  "title": "Titre du thread",
  "files": [{"path": "...", "label": "...", "kind": "..."}],
  "purge_existing": false
}
```

`_publish_freeform` poste les `files` listés. **Pas de tracking JSON, pas de versioning, pas de dédoublonnage MD5**.

**Règle de routage** : si tu peux exprimer ta publication via (matiere, type, num, annee, exo), **utilise toujours mode A**.

#### Logique commune
- `kind=perso` → forum `perso-{matiere}`
- `kind=correction` → forum `corrections-{matiere}`
- `kind=enonce` → forum `corrections-{matiere}` (1ᵉʳ post)
- `kind=off-topic` → forum `hors-sujets` (mode B uniquement)
- Archivage manifest : `_publish_queue/_done/<UTCstamp>__<original>.json`
- Embed récap dans `#logs` si `session_report` fourni

### Auto-publication CM (Phase K)
Quand watchdog range un `.txt` CM canonique vers `COURS/{MAT}/CM/`, déclenche `_auto_publish_cm` :
- 0/3 étapes → publish complet (`_publish_classic` avec `_HeadlessCtx`)
- 1-2/3 → log warning dans `#logs` (doublon évité)
- 3/3 → skip silencieux

Restriction : **CM uniquement** (TD/TP : transcription routée hors pipeline standard). Audio cherché dans `AUDIO_ROOT` (`C:\Users\Gstar\Music\Enregistrement\`).

Déclenchement aussi via `!cours inbox` (scan forcé).

### Phase L+ — Résumés via CLI subscription
`generate_and_post_latex_summary` n'utilise plus l'API Anthropic. À la place : `call_claude_code` qui spawn `claude --print` en sub-process avec `ANTHROPIC_API_KEY` UNSET (force OAuth keychain). **Coût : 0 €**, plafond quota subscription.

---

## 7. Helpers clés (`cours_pipeline.py`)

| Helper | Rôle |
|---|---|
| `thread_key(matiere, type, num, annee)` | Clé canonique threads (`AN1__TD__4`, `AN1__CC__4__2024-2025`, `PRG2__CM__7`, `PRG2__quiz__1`). |
| `parse_correction_filename(path)` | Inverse `correction_*` → `{type, num, exo, annee}`. |
| `parse_enonce_filename(path)` | Inverse `enonce_*` → `{type, num, annee}`. |
| `list_perso_material(matiere)` | Scan disque → 6 catégories : `tache, script, script_print, slides, slides_src, video`. |
| `find_correction_forum(guild, matiere)` | Forum public d'une matière. |
| `find_perso_forum(guild, matiere)` | Forum privé. |
| `load_titres_threads()` | Charge `COURS/_titres_threads.yaml`. |
| `load_discord_published_v2()` / `save_*` | I/O atomique tracking correction. |
| `load_discord_perso_published()` / `save_*` | I/O atomique tracking perso. |
| `resolve_correction_pdf(matiere, type, num, exo, annee)` | **`annee` crucial pour CC** (sinon mauvais millésime publié). |
| `find_enonce_pdf(folder, type, num, matiere, annee)` | Idem. |
| `_ensure_td_thread / _ensure_perso_thread` | Crée/réconcilie threads. |
| `_do_publish_correction / _enonce / _perso` | Méthodes officielles (idempotentes MD5). |
| `_apply_perso_thread_tags` | Tags type + matériel. |
| `_corrections_watcher_loop / _tick` | Watcher Phase B. |
| `_auto_publish_cm(matiere, num, date)` | Phase K — invoque `_publish_classic` avec `_HeadlessCtx` après arrivée `.txt` CM. |
| `_HeadlessCtx` | Faux ctx (no-op `send`) pour invoquer les commandes en interne. |

---

## 8. Règles de comportement

### Sécurité
- **Ne jamais modifier `datas/*.json` à la main** pendant que le bot tourne.
- Avant `backfill*` : **toujours dry-run / lire preview** avant de réagir ✅.
- Avant relance bot : vérifier qu'**une seule instance** tourne (`tasklist /FI "IMAGENAME eq python.exe"`).

### Idempotence
Tous les `_do_publish_*` sont idempotents par MD5. Relancer la même publication n'a aucun effet si fichier inchangé.

### Versioning
Fichier changé → ancien message Discord supprimé via `thread.fetch_message().delete()` + nouveau message avec préfixe `🔄 Version N`. `version` incrémenté dans le JSON. Historique conservé dans `versions[]`.

### Tags forum
Tags `discord.ForumTag` ont `name` et `emoji` **séparés**. Coller emoji dans `name` casse l'idempotence. Toujours :
```python
discord.ForumTag(name="Énoncé seul", emoji=discord.PartialEmoji(name="📄"))
```
Helper `get_forum_tag(forum, label)` cherche par `tag.name` uniquement.

### RÈGLE — Application automatique des tags
À chaque création OU mise à jour d'un thread forum, Claude applique **systématiquement** les tags qui correspondent au contenu posté. **Pas besoin de demande explicite**.

S'applique à : `_do_publish_correction`, `_do_publish_enonce`, `_do_publish_perso`, `_publish_queue_watcher`, toute commande de maintenance touchant un thread.

`tags_applied[]` dans les JSON reflète l'état que le bot **croit** avoir appliqué. En cas de doute (visuellement les tags manquent), relancer la publication ou commande de tagging idempotente.

### Réconciliation thread supprimé
Si thread Discord supprimé manuellement (pas via `purge-thread`), prochain appel à `_ensure_*_thread` détecte 404, purge entrée JSON, recrée. **Important** : check MD5 fait **après** `_ensure_td_thread`, sinon suppression manuelle laisserait JSON désynchro et `skip_same_md5` empêcherait republication.

---

## 9. Démarrage automatique

**Une seule instance à la fois** sinon double watcher / double digest / double on_ready.

### Mode tray (recommandé) — `bot_tray.py` + `start_tray.vbs`
- Process parent qui spawne `python -u bot.py` en subprocess (sans console via `CREATE_NO_WINDOW`).
- Capture stdout/stderr → `%TEMP%\BotGSTAR_startup.log` + buffer mémoire `deque(maxlen=4000)`.
- Auto-restart 10 s après crash (`RESTART_DELAY_SECONDS = 10`).
- Icône tray colorée : vert RUNNING / orange PAUSED / rouge CRASHED_WAITING / bleu RESTARTING.
- Menu clic droit (8 entrées) : voir logs / dossier logs / dossier datas / pause-reprise / redémarrer / démarrer avec Windows / quitter.
- `start_tray.vbs` : lanceur silencieux (`pythonw.exe`).
- Auto-démarrage Windows : `BotGSTAR_Tray.vbs` dans `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`.
- `taskkill /F /T /PID` pour kill proprement.

### Mode console (debug) — `start_bot.bat`
- Boucle relance (10 s entre crashs), `python -u bot.py` via PowerShell `Tee-Object`.
- **Sémantique « click = restart »** : tue toute autre instance (par WINDOWTITLE ou cmdline `bot.py`) avant de prendre la main.
- Titre fenêtre `BotGSTAR - Pipeline COURS` posé après le kill.

### Communs aux deux modes
- Rattrapage commandes manquées via `on_ready` (< 24 h, admin only).
- Auto-start watchers (corrections + RSS) via leurs listeners `on_ready` (idempotents).
- Garde anti-double-watchdog côté Cog (`_inbox_watcher_logged`, `corrections_watcher_running`, `is_running()`).

---

## 10. Évolutions notables

**Voir `BotGSTAR/CHANGELOG.md` pour l'historique complet** (Phases A → Y.23 + bugs corrigés + migrations).

Synthèse résumée :
- **A→D** : refonte v2 + watcher auto + backfill 156 corrections
- **E1, F1** : énoncés seuls + forums perso privés (5 commandes)
- **H** : auto-start watcher au boot
- **I** : fix multi-millésimes CC (`annee` argument propagé)
- **K** : `_auto_publish_cm` après arrivée `.txt` CM
- **L** : résumés via CLI subscription (0 € au lieu API), watcher `_publish_queue`
- **O+** : Mode A officiel (manifest canonique) vs Mode B freeform
- **R** : Cog `veille_rss.py` (4 salons digest)
- **R-F→R-H** : refonte rendu embeds RSS Style A v3
- **S** : tray watchdog (icône system, auto-restart)
- **X** : pipeline Arsenal (6 plateformes vidéo politique)
- **Y.5** : auto-archive Discord (>900 threads)
- **Y.6** : whitelist `CANONICAL_FORUMS` + `CLASSIFICATION_ALIASES` empêche forums orphelins
- **Y.17** : race condition auto-sync vs pipeline (`wait_if_busy=True`, `_forward_dossier_to_fil`)
- **Y.18** : platform fallback CSV pour parse_analysis (X/YT/Reddit/Threads)
- **Y.19** : auto-clear `weekly_throttled` quand live quota OK
- **Y.20** : catchup dedup étendu (download_timestamp + 6 plateformes)
- **Y.21** : fallback gallery-dl + OCR auto pour X / Threads / Reddit
- **Y.22** : `defer_dossier_forwards=True` met `Dossier indexé` du fil en queue
- **Y.23** : `find_mixed_media_for_source` scanne tous les `PLATFORM_DIR_PREFIXES`

Cleanup workspace 2026-04-26 : ~35 MB supprimés + ~9 MB archivés (`scrape_events.py` NosTale).

---

## 11. Cog `veille_rss.py` — Veille RSS tech

Cog autonome agrégeant des flux RSS et postant digests quotidiens dans 4 salons Discord (`📡 VEILLE` : `#cyber-veille`, `#ia-veille`, `#dev-veille`, `#tech-news`).

### Architecture
- **Sources** : `datas/rss_sources.yaml` (éditable + via `!veille sources add/remove/toggle`).
- **Mots-clés scoring** : `datas/rss_keywords.yaml` (boost + blacklist par catégorie).
- **État runtime** : `datas/rss_state.json` (atomic write, dédup MD5 guids, fetch_state, `last_digest_at`).
- **Logs** : embeds dans `#logs` (vert OK / orange warning / rouge erreur).

### Flux fetch
1. `_fetch_one_source` via `feedparser`, support `If-None-Match` / `If-Modified-Since`.
2. **Auto-désactivation** après `SOURCE_ERROR_THRESHOLD = 5` erreurs consécutives.
3. `_filter_and_select` : dédup MD5 guid, fenêtre fraîcheur, scoring, top N.

### Scoring (R-D)
```
score = (4 - priorité_source) × 1000
      + max(0, 1000 - âge_minutes)
      + nb_keywords_boost × 500
```
Article prio 2 + 3 boost (3500) bat prio 1 sans boost (3000). Volontaire.

### Fenêtres fraîcheur
| Catégorie | Fenêtre |
|---|---|
| `tech` | 24 h |
| `cyber`, `ia`, `dev` | 72 h |

### Scheduler
- `@tasks.loop(time=time(8, 0, tzinfo=ZoneInfo("Europe/Paris")))` — digest auto 8h00.
- Auto-start via `on_ready` (idempotent `is_running()`).
- **Catch-up** si bot démarre après 8h00 ET `last_digest_at` ≠ aujourd'hui.
- Garde anti-doublon `_digest_already_today`.

### Commandes (`!veille`, admin only)
| Commande | Effet |
|---|---|
| `fetch-now` | Cycle 'manual' (pas de récap) |
| `trigger-now` | Cycle 'auto' (récap dans `#logs`) |
| `status` | État sources avec compteurs erreurs |
| `reload` | Recharge YAML |
| `sources list/add/remove/toggle/test` | Gestion sources |
| `keywords` | Affiche boost + blacklist |

### Stack
`feedparser`, `aiohttp` (parallèle, timeout 15 s), `ruamel.yaml ≥ 0.18` (preserve-roundtrip), `discord.ext.tasks`, `ZoneInfo("Europe/Paris")`.

### Garde-fous
- User-Agent identifié (`BotGSTAR-VeilleRSS/1.0`).
- RSS/Atom uniquement (pas de scraping HTML).
- Atomic write JSON+YAML.
- Pruning `published[]` après 30 jours.
- Skip silencieux catégories à 0 article (`SKIP_EMPTY_CATEGORIES = True`).
- Split auto digests longs (cap 5 embeds, 3900 chars/embed).

---

## 12. Cog `veille_rss_politique.py` — Veille RSS politique (Option C)

Architecture jumelle de `veille_rss.py`. Migration 2026-04-29 : tourne sur ISTIC L1 G2 (au lieu du serveur Veille). 7 salons fusionnés dans la catégorie `📡 VEILLE` existante avec noms accessibles aux étudiants.

### Philosophie « Option C »
Catégories par **questions d'usage**, pas camps politiques.

| Clé interne | Salon Discord | Question d'usage |
|---|---|---|
| `actu-chaude` | `🔥・actu-chaude` | « Qu'est-ce qui se passe ce matin ? » |
| `arsenal-eco` | `💰・économie-veille` | « Quel chiffre/argument économique ? » |
| `arsenal-ecologie` | `🌱・écologie-veille` | « Quelle donnée climat/écologique ? » |
| `arsenal-international` | `🌍・international-veille` | « Que se passe-t-il sur Palestine, Russie, Sahel… ? » |
| `arsenal-social` | `✊・social-veille` | « Quelle lutte/syndicat/mobilisation ? » |
| `arsenal-attaques` | `🎯・débats-politiques` | « Quelle réplique pro-LFI ? » |
| `arsenal-medias` | `📺・médias-veille` | « Qui dit quoi, qui ment ? » |

**Pas de salon « droite »** : sources adverses pollueraient sans servir. Filtrées et critiquées via `arsenal-attaques`, `arsenal-medias`, fact-checks dans `actu-chaude`.

### Fichiers config
```
datas/rss_sources_politique.yaml    # 40 sources, 7 catégories
datas/rss_keywords_politique.yaml   # Boost + blacklist par catégorie
datas/rss_state_politique.json      # État runtime
```

### Différences notables vs `veille_rss.py`
- Permissions : `is_owner` OU rôle admin OU perms Discord (au lieu d'admin-only strict).
- Discovery salons par **nom** (`_normalize_channel_name`) au lieu d'IDs hardcodés.
- 100 % FR.
- Pas d'embed démarrage (silencieux depuis 2026-04-29 — `veille_rss` poste embed unifié).

### Commandes (alias `!vp`)
| Commande | Rôle |
|---|---|
| `setup-channels` | Crée catégorie + 7 salons si absents |
| `fetch-now`, `trigger-now`, `status`, `reload` | Idem `!veille` |
| `sources list/add/remove/toggle/test`, `keywords` | Idem |

### Test santé externe
`Arsenal_Arguments/_claude_logs/test_rss_sources.py` : lit YAML + teste chaque URL active en parallèle (10 workers). Reporte par catégorie. Toujours en sync avec YAML.
```bash
python Arsenal_Arguments/_claude_logs/test_rss_sources.py [--include-inactive]
```

### Garde-fous
Identiques à `veille_rss.py` + fenêtres fraîcheur ajustées (24h actu/intl, 48h écologie/social/attaques, 72h éco/medias).

---

## 13. Arsenal Intelligence Unit (`arsenal_pipeline.py` + `arsenal_publisher.py`)

Pipeline veille **vidéo politique** sur 6 plateformes (TikTok, Instagram, YouTube, X, Reddit, Threads) : download → normalize CSV → transcription Whisper GPU → résumé Claude (CLI subscription) → publication forum.

**Doc complète** : `Arsenal_Arguments/CLAUDE.md` (pipeline disque, conventions CSV, helpers config, scripts compagnons `whisper_supervisor.py`, `progress_monitor.py`, `post_whisper_orchestrator.py`).

### Salons et IDs clés (ISTIC L1 G2 depuis 2026-04-29)
| Élément | ID |
|---|---|
| Guild ISTIC L1 G2 | `1466806132998672466` |
| `🔗・liens` (auto URL drops) | `1498918445763268658` |
| `📋・logs` | `1493760267300110466` |
| Catégorie `ANALYSES POLITIQUES` (12 forums) | `1499086461297889330` |
| Catégorie `📡 VEILLE` (RSS tech + politique) | `1497581043086135478` |
| Rôle Admin | `1493905604241129592` |

⚠ `arsenal_publisher.category_name = "ANALYSES POLITIQUES"` (sans emoji `📂`).

### Workflow recommandé : GUI Tkinter
Double-clic sur `Arsenal_Arguments/start_summarize_gui.vbs` lance silencieusement `summarize_gui.py` (`pythonw.exe`).

GUI propose :
- **Options** : moteur (Claude Code CLI / API — persistant `_secrets/engine_pref.json`), filtres, robustesse.
- **📊 Quota Pro Max** : barres live Session 5h / Hebdo 7j / Hebdo Sonnet / Overage. Spinbox seuils, auto-stop, auto-resume, cookie chiffré DPAPI, reset throttle hebdo.
- **Contrôles** : ▶ Lancer / ⏸ Pause (psutil) / ⏹ Stop (Ctrl-Break + hard kill 10s).
- **Console** : tail 400 lignes, fond sombre.
- **Raccourcis** : 6 boutons (📂 summaries, 📊 audit, 🌐 #logs, 🩺 Status Anthropic, 💳 billing, 📈 Claude.ai usage).

Garde-fou quota persistant via `_secrets/quota_state.json` (atomic write).

### Module `claude_usage.py`
Lit quota Pro Max depuis endpoint privé `/api/organizations/{ORG_UUID}/usage` (DevTools Network). Cookie chiffré localement via **Windows DPAPI** dans `_secrets/claude_session.bin`. Imite Chrome (headers `sec-ch-ua-*`, `priority`, `sec-fetch-*`).

CLI : `--set-cookie`, `--fetch`, `--state`, `--clear`. GUI : refresh 60 s en thread daemon.

### Auto-sync temps réel (Phase X)
`arsenal_publisher` a `tasks.loop(seconds=15)` qui poll mtime CSV et lance `_sync_task` dès qu'une ligne `summary=SUCCESS, sync=PENDING` apparaît. Chaque résumé publié dans son forum dans 15 s.

**Anti-boucle** : ≥3 lignes `sync_timestamp` < 5 min ET `sync_status=FAILED` → cooldown.

**Sync silencieuse en l'absence de travail (Phase Y)** : `silent_if_no_work=True` supprime embeds `🚀 Synchronisation` et `🏁 Sync terminée` quand `synced=0` ET `failed=0`. Évite spam `📋・logs` quand `--re-summarize` rafraîchit items déjà SUCCESS. Manual sync (`!sync_arsenal`) garde les embeds inconditionnels.

### Whitelist forums canoniques (Phase Y.6)
`CANONICAL_FORUMS` (set de 14 slugs) + `CLASSIFICATION_ALIASES` (~35 entrées variantes → canonique). Helper `_normalize_forum_slug(raw_slug)` :
1. Si ∈ `CANONICAL_FORUMS` → tel quel.
2. Sinon si ∈ `CLASSIFICATION_ALIASES` → cible canonique.
3. Sinon → fallback `catégorie-libre`.

Empêche création de forums orphelins. Liste catégories en double : prompt `summarize.py` + `CANONICAL_FORUMS`. Si modif → mettre les deux à jour.

### Auto-archive Discord (Phase Y.5)
`tasks.loop(hours=1)` `_auto_archive_loop` archive vieux threads d'`ANALYSES POLITIQUES` quand guilde dépasse **900/1000 threads actifs** (limite Discord non-boost).

- Skip si `is_syncing`.
- Tri par snowflake ID (plus vieux d'abord), archive jusqu'à 800 (`ARCHIVE_THRESHOLD = 900`, `ARCHIVE_TARGET = 800`).
- Sleep 0.5 s entre PATCHs, abandon après 5 échecs.
- Embed `🗄️ Auto-archive Arsenal` dans `📋・logs` (silencieux si rien archivé).
- Threads archivés restent visibles, auto-unarchive si message posté.

Manuel : `!archive_arsenal [target]` (target override `ARCHIVE_TARGET`).

### Choix moteur summarize partagé GUI ↔ pipeline (Phase Y)
`arsenal_config.load_engine_pref()` / `save_engine_pref(engine)` lisent/écrivent `Arsenal_Arguments/_secrets/engine_pref.json` au format `{"engine": "claude_code" | "api"}`.
- GUI initialise radio « Moteur » depuis ce fichier, `trace_add` sauvegarde à chaque changement.
- `arsenal_pipeline.step_summarize` lit la pref, ajoute `--use-claude-code` que si engine == `claude_code`.

Cocher « API Anthropic » bascule **aussi** le pipeline `🔗・liens` sur l'API au prochain drop.

### Quota Pro Max appliqué au pipeline arsenal
`step_summarize` appelle `_check_quota_before_summarize()` avant spawn. Seuil dépassé → `{"ok": False, "quota_blocked": True}` → embed orange `⚠ Résumé sauté — quota atteint` + sync skip aussi.

Mode tolérant : si check fail (cookie missing, network), continue par défaut.

### Rattrapage FAILED — `arsenal_retry_failed.py`
Script standalone scanne `suivi_global.csv` pour `download_status=FAILED` (dédup par `(plateforme, id)` sur `download_timestamp` le plus récent), relance le bon downloader. Modes :
```
python arsenal_retry_failed.py                   # dry-run
python arsenal_retry_failed.py --apply [--limit N] [--platform X]
```
Appelle `csv_normalize.py` à la fin.

### Audit `🔗・liens` — `audit_liens_channel.py`
Script standalone `Arsenal_Arguments/_claude_logs/audit_liens_channel.py` qui scanne EXHAUSTIVEMENT `🔗・liens` et catégorise incohérences par drop. Avec `--apply`, fixe 9 catégories sur 11 (A, B, C, D, E, F, G, H, J, M).

Scan `#logs` cap à 12000-15000 messages. Pmap Y.15 (`datas/arsenal_published_threads.json`) sert de fallback. Idempotent.

```
python _claude_logs/audit_liens_channel.py             # dry-run
python _claude_logs/audit_liens_channel.py --apply     # fixes
python _claude_logs/audit_liens_channel.py --report    # JSON détaillé
```

Cas non-fixables (limitations amont) :
- Tweets text-only (`No video could be found`) — yt-dlp = vidéo only.
- Threads.com URLs — yt-dlp suit redirect `.net→.com` puis fail.

### Quota seuils — bypass
Pour outrepasser tous les checks quota côté bot et GUI : set `session_threshold_pct=100` et `weekly_threshold_pct=100` (et `weekly_throttled=false`) dans `_secrets/quota_state.json`.

---

**En cas de doute** : la source de vérité est toujours `cours_pipeline.py`, `veille_rss.py`, `veille_rss_politique.py`, `arsenal_pipeline.py`, `arsenal_publisher.py`. Chercher `@cours.command` / `@veille_group.command` pour la liste exhaustive, `@tasks.loop` pour les loops actifs.
