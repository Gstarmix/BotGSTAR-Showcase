# CLAUDE.md — Instructions Claude Code pour BotGSTAR
# Bot Discord multi-serveurs — Gaylord ABOEKA, 2025-2026
# Dernière mise à jour : 2026-05-04 (Phase Y.21–Y.23 — images X/Threads/Reddit
# + OCR auto + Dossier indexé en fin de fil + médias attachés au thread forum).
# Y.23 : `find_mixed_media_for_source` scanne tous les préfixes
# `PLATFORM_DIR_PREFIXES` (X_/THREADS_/REDDIT_/IG_/YT_/TT_), pas seulement
# IG_. Sans ce fix, les médias gallery-dl Y.21 étaient sur disque mais
# pas postés au thread forum. Script one-shot `repush_x_with_media.py`
# pour re-publier les 7 X drops historiques sans média.
# Y.22 : `_sync_task(defer_dossier_forwards=True)` met le post `Dossier
# indexé` du fil en queue `_deferred_thread_dossier_posts`, flush après
# `Pipeline terminé` via `flush_deferred_dossier_to_fil()`. Le post #logs
# reste immédiat. Fix retry : `already_in_csv` skip uniquement sur SUCCESS
# (avant Y.22, retry sur FAILED faisait rien). `csv_normalize` :
# DOWNLOAD_COLS prend la valeur de la ligne au timestamp le plus récent
# (avant, choose_better_value préférait la chaîne la plus longue → écrasait
# `gallery_dl_x` par `video_direct_failed`).
# Y.21 : avant, X/Threads/Reddit text-only ou image-only échouait (yt-dlp
# = vidéo only). Désormais, `dl_generic.gallery_dl_fallback()` télécharge
# images + vidéos + texte du post quand yt-dlp ne ramène rien, dans
# `01_raw_images/<PREFIX><id>/` (préfixes `X_`, `THREADS_`, `REDDIT_`).
# `ocr_carousels.py` scanne tous les préfixes plateforme et prepend
# `_post_text.txt` en bloc `[POST TEXT]` avant les slides. Nouveau step
# pipeline `step_ocr` (idempotent, silencieux quand rien à faire) inséré
# entre transcribe et summarize. `fix_text_tweets.py` déprécié → stub.
# Voir CHANGELOG Phase Y.21 pour le détail (4 fichiers touchés, ~250 LoC).
# Y.17 : `_sync_task` accepte `wait_if_busy=True` (utilisé par step_sync
# du pipeline live) + helper `_forward_dossier_to_fil` qui forwarde
# l'embed `✅ Dossier indexé` au fil même quand l'auto-sync 15s a gagné
# la race et publié sans `link_thread`. Couvre les 2 paths skip de
# `_sync_task` (`should_process` False, pmap déjà mappé). Y.18 :
# `parse_analysis` fallback platform depuis le CSV (`get_row_by_source_id`)
# pour les fichiers SRC_*.txt qui n'ont pas de header `PLATEFORME — …`
# (X / YouTube / Reddit / Threads). Avant Y.18, leurs entries pmap
# avaient une clé `::cid` à plateforme vide — inacessible via lookup
# `x::cid`. Y.19 : auto-clear de `state.weekly_throttled` quand le live
# `weekly_pct < seuil` dans `_check_quota_before_summarize`. Avant Y.19
# le flag persistant restait sticky tant que le user n'avait pas cliqué
# « Reset throttle hebdo » dans la GUI (qui auto-clear seulement quand
# elle tourne) → drops postés plusieurs jours après le throttle initial
# bloqués « quota atteint » alors que le quota live était à 5 %. Y.20 :
# (a) `_catchup_scan` considère « déjà vu » toute ligne CSV avec
# `download_timestamp` non vide (SUCCESS + FAILED + tout autre état),
# pas seulement SUCCESS — sinon chaque restart bot ré-enqueue les
# FAILED, re-spawn yt-dlp pour rien et re-poste ❌ par-dessus les ✅
# de l'audit ; (b) `extract_content_id_from_url` couvre les 6
# plateformes (avant : IG + TikTok seulement, X/YT/Reddit/Threads
# tombaient sur `cid=None` → enqueueing systématique) ; (c) skip
# explicite si cid non extractible (cas SSL handshake timeout sur
# `vm.tiktok.com` qui laisse l'URL en forme courte). Threads.com :
# regex `threads\.(?:net|com)` dans `arsenal_pipeline`,
# `audit_liens_channel`, `retrofit_link_threads`, et conversion
# silencieuse `.com → .net` dans `dl_generic.download_one` avant
# yt-dlp (yt-dlp follow le redirect Meta puis fail sur `.com` —
# limitation amont, pas un bug bot). Migration pmap one-shot :
# 6 entries `::cid` (X drops à plateforme vide) renommées
# `{plat}::cid` via lookup CSV. Audit `🔗・liens` : nouveau script
# `_claude_logs/audit_liens_channel.py` qui détecte 11 catégories
# d'incohérences (A→Z) et fixe 9 d'entre elles avec `--apply`. 51 fils
# backfillés au total (Dossier indexé + pipeline embeds + ❌→✅ flips
# + Dossier indexé re-ordonné en fin de fil + un-archive→delete→
# repost→re-archive), seuls 2 cas non-fixables côté bot persistent
# (X text-only `2050242458945024138`, Threads.com `DXzBaRyEzLk`).
# Phase Y antérieure — choix moteur summarize
# partagé GUI ↔ pipeline via _secrets/engine_pref.json, GUI scrollable
# (Canvas + Scrollbar), sync auto silencieuse quand 0 publié / 0 erreur,
# fix Y.1 : strip ponctuation finale dans parse_analysis pour éviter le
# bug de forum dupes "catégorie-libre" — 35 dupes nettoyés ; Y.2 : salon
# `liens` déplacé en public dans QG (anciennement BLABLA), écriture admin
# only pour éviter le burn de tokens par les camarades ; fix Y.3 :
# truncation-prefix matcher dans find_existing_thread_by_names — 29
# threads dupliqués nettoyés sur 8 forums ANALYSES POLITIQUES ; fix Y.4 :
# resolve_tiktok_short_url dans dl_tiktok.py pour empêcher 416 IDs sluggés
# du type `https_vm_tiktok_com_X` au lieu du vrai ID TikTok 19 chiffres,
# 416 rows marquées sync=SUCCESS+skip, 300 threads Discord archivés pour
# libérer la limite 1000/1000 atteinte, guilde redescendue à 700/1000 ;
# Y.5 : auto-archive loop horaire dans arsenal_publisher pour prévenir
# la re-saturation, seuils 900/800, commande !archive_arsenal pour
# trigger ad-hoc ; Y.6 : reclassif 4 threads orphelins (débats-et-
# rhétorique/éducation/social-et-médias/société-et-éducation) vers les
# 12 forums canoniques + ajout d'un whitelist + alias map dans
# parse_analysis pour empêcher la création de futurs forums orphelins
# (CANONICAL_FORUMS = 14 entrées, CLASSIFICATION_ALIASES ~35 variantes,
# fallback catégorie-libre) ; Y.7 : durcissement prompt summarize avec
# liste fermée des 14 thèmes, exemples explicites de classifications
# interdites, défense en profondeur avec le whitelist Y.6 ;
# fix Y.8 : timeout dynamique sur step_transcribe — calcul auto basé
# sur durée max via ffprobe, plancher 10min/plafond 1h/marge ×4 pour
# couvrir long Reels VP9 qui crashaient le timeout fixe 600s ;
# Y.9 : fil attaché au message d'origine dans 🔗・liens à chaque drop,
# tous les embeds Pipeline postés aussi dans le fil (vs juste #logs
# pollué) ; retrofit script pour les drops historiques. Y.10 :
# auto-delete des system messages "X a commencé un fil" dans 🔗・liens.
# Y.11/Y.12/Y.13 : embed Dossier indexé du publisher posté aussi dans
# le fil du drop, auto-archive du fil à la fin du pipeline,
# remove_user de l'auteur après création pour pas qu'il reçoive une
# notif Discord par embed (auto-follow par défaut). Y.14 :
# detect_anti_bot_pattern qui poste un embed orange avec procédure
# manuelle quand IG anti-bot / TikTok IP block / DNS transitoire
# détecté dans stderr du download. Y.15 : JSON map persistante
# datas/arsenal_published_threads.json source_id→thread_id pour empêcher
# les dupes en cas de race condition CSV — bootstrap des 1059 entries
# existantes via starter message des threads forum. Fix Y.16 : check
# quota Pro Max bypass si engine_pref = api — l'API Anthropic est
# facturée séparément, n'a rien à voir avec claude.ai weekly throttle.
# Pipeline + GUI ne bloquent plus.)

Ce document concerne les **cinq Cogs** de BotGSTAR (chargés par `bot.py`) :

| Cog | Serveur cible | Rôle | Section |
|---|---|---|---|
| `cours_pipeline.py` | ISTIC L1 G2 (1466806132998672466) | Workflow COURS (corrections, énoncés, perso) | §1 à §10 |
| `veille_rss.py` | ISTIC L1 G2 | Veille RSS technique (cyber/IA/dev/tech) | §11 |
| `arsenal_pipeline.py` | **ISTIC L1 G2** (depuis 2026-04-29) | Pipeline politique multi-plateforme (TikTok, IG, YT, X, Reddit, Threads → Whisper → résumé Claude → forum). Listener `🔗・liens` dans QG (public, écriture admin only). | §13 |
| `arsenal_publisher.py` | **ISTIC L1 G2** (depuis 2026-04-29) | Publication Discord côté Arsenal (catégorie `📂 ANALYSES POLITIQUES`, forums, tags, médias, transcriptions, résumés) | §13 |
| `veille_rss_politique.py` | **ISTIC L1 G2** (depuis 2026-04-29) | Veille RSS politique 7 catégories USAGE-orientées (Option C). Salons fusionnés dans la catégorie `📡 VEILLE` aux côtés des 4 salons tech (cyber/ia/dev/tech-news). | §12 |

Pour la philosophie du projet pédagogique côté disque, voir
`COURS/CLAUDE.md`. Pour le pipeline Arsenal côté disque, voir
`Arsenal_Arguments/CLAUDE.md`.

---

## 1. Pied à terre — où est quoi

| Fichier | Rôle |
|---|---|
| `bot.py` | Entry point — charge le Cog, gère le rate-limit, on_ready. |
| `extensions/cours_pipeline.py` | **Source de vérité** : commandes, helpers, watcher, JSON I/O. |
| `start_bot.bat` | Boucle de relance auto (10 s entre crashs). Log live console + `%TEMP%\BotGSTAR_startup.log`. |
| `datas/_published.json` | Tracking publications audio/transcription/résumé. |
| `datas/discord_published.json` | Tracking forums correction (schéma v2). |
| `datas/discord_perso_published.json` | Tracking forum perso (schéma v1). |
| `datas/embed_logs.json` | Cache embed last-state pour `#logs`. |

Le bot est **mono-serveur** : `ISTIC_GUILD_ID = 1466806132998672466`.
Toutes les commandes sont **admin only** : rôle `ADMIN_ROLE_ID = 1493905604241129592`
(check global via `cog_check`).

---

## 2. Commandes disponibles (24)

Toutes préfixées par `!cours`. Triées par usage.

### Setup infrastructure (5)

| Commande | Rôle |
|---|---|
| `!cours setup-channels` | Normalise les emojis des salons publics (🎧📝📌📋). |
| `!cours setup-forums` | Crée les 5 forums correction (`corrections-{mat}`) sous chaque catégorie matière. |
| `!cours setup-tags` | Crée les 7 tags forum public (4 type + 3 état). |
| `!cours setup-perso` | Crée la catégorie `🔒 PERSONNEL` + 5 forums `perso-{mat}` (admin only via permissions). |
| `!cours setup-tags-perso` | Crée les 8 tags forum perso (4 type + 4 matériel). |

### Publication corrections / énoncés (5)

| Commande | Rôle |
|---|---|
| `!cours publish <type> <mat> <num> <date>` | Pipeline classique : audio + transcription + résumé PDF (CM principalement). |
| `!cours publish correction <mat> <type> <num> <exo>` | Publie 1 PDF correction dans le thread TD/TP/CC. |
| `!cours publish enonce <mat> <type> <num> [annee]` | Publie un énoncé seul (crée le thread si absent). |
| `!cours backfill <mat>` | Rattrape tout le stock de corrections d'une matière (dry-run + confirm). |
| `!cours backfill-enonces <mat>` | Rattrape les énoncés manquants pour la matière. |
| `!cours republish <type> <mat> <num> <date>` | Re-poste le résumé seul (CM). |
| `!cours republish-correction <mat> <type> <num> <exo> [annee]` | Force republication v2 (delete + repost) d'une correction. |

### Publication matériel perso (3)

| Commande | Rôle |
|---|---|
| `!cours publish-perso <mat> <type> <num> [annee]` | Publie tout le matériel perso d'un TD/TP/CC dans `perso-{mat}` (TACHE + scripts + slides + vidéos). |
| `!cours backfill-perso <mat>` | Rattrape tout le matériel perso d'une matière. |
| `!cours purge-perso <mat> <type> <num> [annee]` | Vide l'entrée tracking perso (sans toucher Discord). |

### Watcher publication auto (1)

| Commande | Rôle |
|---|---|
| `!cours watcher <start\|stop\|status>` | Polling 60 s sur `COURS/{MAT}/**/corrections/*.pdf`. **Auto-start au boot** + contrôle manuel disponible. Récap quotidien dans `#logs` à ~23h Paris. |

### Maintenance (2)

| Commande | Rôle |
|---|---|
| `!cours purge-thread <mat> <type> <num> [annee]` | Réinitialise l'entrée JSON correction (ne touche pas Discord). |
| `!cours sync-absences` | Scanne 6 derniers mois pour détecter les messages d'absence et hydrater `_absences.json`. |

### Inspection / rapports (8)

| Commande | Rôle |
|---|---|
| `!cours status` | Liste les fichiers en attente dans `_INBOX`. |
| `!cours missing` | Liste les séances sans audio. |
| `!cours scan [mat]` | Liste les séances non publiées. |
| `!cours auto [mat]` | Publie en lot tous les CM non publiés (avec confirmation). |
| `!cours absent <type> <mat> <num> [date] [raison]` | Marque une séance absente. |
| `!cours absences` | Liste les absences enregistrées. |
| `!cours rapport [mat] [--deep]` | Inventaire + analyse IA optionnelle. |
| `!cours inbox` | Force un scan immédiat de `_INBOX`. |

---

## 3. Architecture des forums Discord

### Forums publics correction (Phase A → D)

5 forums `corrections-{matiere}` (un par catégorie matière) :
`corrections-an1`, `corrections-en1`, `corrections-prg2`,
`corrections-psi`, `corrections-ise`.

**7 tags** par forum (créés via `!cours setup-tags`) :
- **Type** (4) : `TD`, `TP`, `CC`, `Quiz` (sans emoji)
- **État** (3) : `📄 Énoncé seul`, `✍️ Corrections présentes`, `📄 Énoncé manquant`

**1 thread par TD/TP/CC** : énoncé en 1ᵉʳ post (embed + PDF si disponible),
corrections en posts suivants (1 par exercice), un seul tag d'état à la
fois (transitions automatiques).

**Versioning** : si une correction change de MD5, l'ancien message est
supprimé et un nouveau est posté avec préfixe `🔄 Version N`.

### Forum privé matériel perso (Phase F1)

Catégorie privée `🔒 PERSONNEL` (visible uniquement par `ADMIN_ROLE_ID`)
contenant 5 forums `perso-{matiere}`.

**8 tags** par forum perso :
- **Type** (4) : `TD`, `TP`, `CC`, `Quiz`
- **Matériel** (4) : `📋 TACHE`, `📝 Script oral`, `📊 Slides`, `🎬 Vidéo`

**1 thread par TD/TP/CC**, ordre des posts : TACHE → Script oral → Slides →
Vidéo (tri stable via `_PERSO_KIND_ORDER`). Vidéos > 25 Mo : embed
« trop lourd, conservé localement » sans fichier attaché.

### Salons publics existants (workflow CM)

`{type}-{audio,transcription,resume}-{matiere}` (15 salons : 3 types ×
5 matières). Format de message : **texte brut** (pas embed) pour
compatibilité mobile et listener `on_message`.

---

## 4. Storage JSON sous `datas/`

| Fichier | Schéma | Clé principale |
|---|---|---|
| `_published.json` | v1 | `{TYPE}{NUM}_{MAT}_{DATE}` (ex: `CM7_AN1_1602`) |
| `discord_published.json` | **v2** | `thread_key` (`AN1__TD__4`, `AN1__CC__4__2024-2025`) |
| `discord_perso_published.json` | v1 | `thread_key` (même format que v2) |
| `_absences.json` | v1 | `{TYPE}{NUM}_{MAT}` (ex: `TD2_PSI`) |

**Règle critique** : ces fichiers sont **écrits atomiquement**
(`.tmp` + `os.replace`) par le bot. **À ne jamais modifier à la main**
pendant que le bot tourne — risque de désynchro silencieuse.

Si une désynchro est suspectée :
- Côté correction : `!cours purge-thread <mat> <type> <num> [annee]`
- Côté perso : `!cours purge-perso <mat> <type> <num> [annee]`

Cela vide l'entrée JSON ; la prochaine publication recrée un thread
neuf (l'ancien thread Discord, s'il existe encore, peut être supprimé
manuellement).

### Structure d'une entry `discord_published.json` v2

```json
{
  "schema_version": 2,
  "threads": {
    "AN1__TD__4": {
      "matiere": "AN1", "type": "TD", "num": "4", "annee": null,
      "thread_id": "1234...", "forum_id": "5678...",
      "titre_td": "Étude globale de fonctions",
      "enonce": {
        "pdf_path": "AN1/TD/TD4/enonce_TD4_AN1.pdf",
        "md5": "...", "message_id": "111",
        "status": "present", "version": 1
      },
      "corrections": {
        "5": { "pdf_path": "...", "md5": "...", "message_id": "222",
                "version": 1, "versions": [...] },
        "6": { ... }
      },
      "state": "corrections_present",
      "tags_applied": ["TD", "Corrections présentes"],
      "created_at": "2026-04-...", "last_updated": "2026-04-..."
    }
  }
}
```

### Structure d'une entry `discord_perso_published.json` v1

```json
{
  "schema_version": 1,
  "threads": {
    "AN1__TD__4": {
      "matiere": "AN1", "type": "TD", "num": "4", "annee": null,
      "thread_id": "...", "forum_id": "...", "title": "[TD4] ...",
      "posts": {
        "tache:ex5": { "kind": "tache", "rel_key": "...",
                        "md5": "...", "message_id": "...",
                        "version": 1, "is_too_big": false },
        "script:ex5:md": { "kind": "script", ... },
        "slides:global": { "kind": "slides", ... },
        "video:global": { "kind": "video", "is_too_big": true, ... }
      },
      "tags_applied": ["TD", "TACHE", "Script oral", "Slides", "Vidéo"],
      "created_at": "...", "last_updated": "..."
    }
  }
}
```

Note : le `post_key` pour `script` inclut un suffixe d'extension
(`script:ex5:md`, `script:ex5:txt`, `script:global:json`) car
SCRIPT_*.md / script_oral_*.txt / project_*.json coexistent par
exercice et chacun mérite son propre post.

---

## 5. Watcher corrections (Phase B)

Polling **60 secondes** sur `COURS/{MAT}/**/corrections/*.pdf` (toutes
matières). Pour chaque PDF détecté :

1. `parse_correction_filename` extrait `(type, num, exo, annee)`.
2. `_do_publish_correction` est appelé (idempotent via MD5).
3. Si `status ∈ {ok, ok_v2}`, le compteur du jour est incrémenté.
4. Logué silencieusement côté fichier (pas dans Discord).

**Récap quotidien** dans `#logs` à **22h59 UTC** (~23h Paris hiver,
~24h Paris été). L'embed liste le total + détail par matière (nouveaux
vs mises à jour v2).

**Auto-start au boot** via listener `on_ready` (idempotent : double-start
prévenu par les gardes `corrections_watcher_running` et `task non-done`,
même pattern que `watcher_cmd("start")`). `on_ready` est aussi déclenché
au reconnect Discord — pas de risque de double-instance.

**Contrôle manuel** : `!cours watcher <start|stop|status>` reste
disponible (utile pour debug ou stop ponctuel). État stocké dans
`self.corrections_watcher_running`. Annulé proprement au `cog_unload`.

**Pas de watcher pour les énoncés ni le matériel perso** — publication
manuelle uniquement.

---

## 6. Watchdog `_INBOX` (existant, indépendant)

`@tasks.loop(seconds=60)` sur `COURS/_INBOX/`. Range automatiquement les
fichiers selon le pattern `{TYPE}{NUM}_{MAT}_{DATE}.{txt|m4a|pdf|docx}`.
Contrôle de stabilité (taille inchangée entre 2 ticks). Doublons MD5
identiques supprimés ; différents gardés avec suffixe `_from_INBOX`.
Log embed par fichier traité dans `#logs`. Voir `COURS/CLAUDE.md` §8
pour les détails du pipeline CM.

### Phase L (2026-04-27) — Résumés LaTeX via CLI subscription

`generate_and_post_latex_summary` n'utilise **plus l'API Anthropic** (la
clé API a été épuisée). À la place, appel à **`call_claude_code`** qui
spawn `claude --print` en sub-process avec `ANTHROPIC_API_KEY` UNSET dans
l'env (force OAuth/keychain subscription). Coût : **0 €**, plafond
quota subscription. Pas de tracking tokens (le CLI ne les expose pas).

`call_claude_api` est conservée mais inutilisée dans le flow principal.

### Phase L — Watcher publish queue + forum hors-sujets

Watcher `_publish_queue_watcher` (`@tasks.loop(seconds=60)`) qui scanne
`COURS/_publish_queue/*.json`. Chaque manifest décrit une publication.
Le routage suit **deux modes** depuis Phase O+ (28/04/2026) — voir
ci-dessous.

#### Mode A — Pipeline officiel (Phase O+, recommandé pour tout thread canonique)

**À utiliser dès qu'on cible un thread canonique** (TD/TP/CC/Quiz d'une
matière). Le manifest porte les **champs canoniques** `type` + `num`
(+ `annee` si CC daté, + `exo` si correction par exo) :

```json
{
  "manifest_version": 1,
  "kind": "perso" | "correction" | "enonce",
  "matiere": "EN1",
  "type": "CC",            // requis : TD, TP, CC, Quiz
  "num": "2",              // requis (numérique ou textuel pour PSI : SHANNON, SGF…)
  "annee": "2024-25",      // optionnel — requis pour CC datés (multi-millésimes)
  "exo": "5",              // requis si kind=correction (ou "0" pour TP/CC global)
  "force_republish": false // optionnel — force le repost même si MD5 identique
}
```

Le watcher route vers la **méthode officielle correspondante** :
`_do_publish_perso`, `_do_publish_correction`, `_do_publish_enonce` —
**exactement** celles invoquées par `!cours publish-perso`,
`!cours publish correction`, `!cours publish enonce`. **Aucune logique
parallèle** : ce que fait Gaylord en CLI = ce que fait Claude via
manifest. Toutes les garanties officielles s'appliquent :
- Tracking JSON v2/perso v1 mis à jour atomiquement
- Idempotence MD5 — skip si fichier disque inchangé
- Versionning auto — delete ancien message + repost `🔄 Version N` quand
  MD5 change
- Tags appliqués automatiquement (TYPE + état/matériel)
- Réconciliation thread supprimé manuellement (404 → recrée)
- Le matériel posté vient du **disque** (`list_perso_material`,
  `resolve_correction_pdf`, `find_enonce_pdf`) — le champ `files` du
  manifest est ignoré dans ce mode (parce que le canonique est sur disque).

#### Mode B — Freeform (fallback ad-hoc, ex hors-sujets ou fichiers non-canoniques)

**À utiliser uniquement** quand on n'a pas de thread canonique cible
(`kind=off-topic`, ou contenu sans correspondance disque/tracking). Le
manifest n'a **pas** de `type`/`num` — le watcher tombe sur
`_publish_freeform` :

```json
{
  "manifest_version": 1,
  "kind": "perso" | "off-topic",
  "matiere": "EN1",
  "title": "Titre du thread",
  "description": "Intro du thread (optionnel)",
  "purge_existing": false,
  "target_thread_id": null,   // optionnel — pour cibler un thread par ID
  "files": [
    {"path": "C:\\...\\xxx.m4a", "label": "🎧 Audio", "kind": "audio"},
    {"path": "...", "label": "📝 Transcription", "kind": "transcription"},
    {"path": "...", "label": "📌 Résumé", "kind": "resume"}
  ],
  "created_by": "transcribe.py"
}
```

`_publish_freeform` poste les `files` listés tels quels, applique les
tags depuis le titre, mais **ne met pas à jour le tracking JSON officiel**
(le mode A le fait). Pas de versionning ni d'idempotence MD5.

Limitations connues du mode B (à éviter si possible) :
- Si on republie le même fichier deux fois, on aura **deux messages**
  dans le thread (pas de dédoublonnage MD5)
- Le tracking JSON ne reflète pas les posts faits via mode B → un
  `backfill-perso` ultérieur recréera des doublons

**Règle de routage** : si tu peux exprimer ta publication avec
(matiere, type, num, annee, exo), **utilise toujours le mode A**. Le mode
B est réservé aux contenus hors-sujets ou aux cas où le tracking n'a pas
de schema correspondant.

#### Logique commune aux deux modes

- `kind=perso` → forum `perso-{matiere}`
- `kind=correction` → forum `corrections-{matiere}`
- `kind=enonce` → forum `corrections-{matiere}` (l'énoncé est le 1ᵉʳ post)
- `kind=off-topic` → forum `hors-sujets` (mode B uniquement)
- Archivage manifest sous `_publish_queue/_done/<UTCstamp>__<original>.json`
- Embed récap dans `#logs` si `session_report` fourni dans le manifest

Producteur principal : `COURS/_scripts/transcribe.py` (post-step après
transcription pour les non-CMs et les `--off-topic`). `summarize.py` est
appelé en amont pour produire le PDF résumé.

### Auto-publication CM (Phase K, ajoutée 2026-04-27)

Quand le watchdog range un **`.txt` CM canonique** (ex : `CM7_AN1_1602.txt`)
vers `COURS/{MAT}/CM/`, il déclenche automatiquement
`_auto_publish_cm(matiere, num, date)` qui :

1. Charge `_published.json` et compte les étapes (`audio` / `transcription`
   / `resume`) déjà faites pour ce CM.
2. **Si 3/3** : skip silencieux.
3. **Si 1-2/3** : log d'avertissement dans `#logs` (re-publier doublonnerait,
   Gaylord doit faire `!cours publish` ou `!cours republish` à la main).
4. **Si 0/3** : invoque `_publish_classic` avec `_HeadlessCtx` (fake context
   qui no-ope `ctx.send`) → audio + transcription + résumé postés sur
   `cm-audio-{mat}` / `cm-transcription-{mat}` / `cm-résumé-{mat}`.

Restriction : **CM uniquement** (pas TD/TP — leur transcription va dans
`_A_TRIER/transcriptions/` ou `{MAT}/TP/`, sans pipeline Discord standard).
L'audio est cherché par `build_audio_path` dans `AUDIO_ROOT`
(`C:\Users\Gstar\Music\Enregistrement\`). S'il manque → message
« trop lourd / disponible sur demande » classique.

Le déclenchement vit aussi dans `!cours inbox` (scan forcé) — pour permettre
le rattrapage manuel sur un fichier déjà passé silencieusement.

---

## 7. Helpers clés

À connaître pour tout debug ou évolution. Localisés dans
`cours_pipeline.py`.

| Helper | Type | Rôle |
|---|---|---|
| `thread_key(matiere, type, num, annee)` | module | Clé canonique des threads (`AN1__TD__4`, `AN1__CC__4__2024-2025`, `PRG2__quiz__1`). |
| `parse_correction_filename(path)` | module | Inverse `correction_*` → `{type, num, exo, annee}`. |
| `parse_enonce_filename(path)` | module | Inverse `enonce_*` → `{type, num, annee}`. |
| `list_perso_material(matiere)` | module | Scan disque → liste matériel perso classifié (6 catégories : `tache`, `script`, `script_print`, `slides`, `slides_src`, `video`). `script_print` = `script_imprimable_*.pdf` (Phase J COURS), inséré entre `script` et `slides` dans l'ordre de publication, taggué comme "Script oral" (pas de tag distinct). |
| `find_correction_forum(guild, matiere)` | module | Forum public d'une matière. |
| `find_perso_forum(guild, matiere)` | module | Forum privé (Phase F1). |
| `load_titres_threads()` | module | Charge `COURS/_titres_threads.yaml` (rechargé à chaque appel). |
| `load_discord_published_v2()` / `save_discord_published_v2(data)` | module | I/O atomique tracking correction. |
| `load_discord_perso_published()` / `save_discord_perso_published(data)` | module | I/O atomique tracking perso. |
| `resolve_correction_pdf(matiere, type, num, exo, annee=None)` | module | Trouve le PDF correction. **Pour CC, `annee` est crucial** : sans elle, l'ordre lexical décroissant retourne le millésime le plus récent et publie le mauvais PDF dans les threads des années antérieures (cf. Phase I). |
| `find_enonce_pdf(folder, type, num, matiere, annee=None)` | module | Trouve le PDF énoncé. **Si `annee` fournie, exigée même dans le match partiel** (étape 2) — sinon CC1 2025-26 sans PDF tomberait sur `enonce_CC1_2023-24_*` (cf. Phase I). |
| `_ensure_td_thread(guild, mat, type, num, annee, titre_td)` | méthode | Crée/réconcilie un thread correction. Retourne `(thread, entry, was_created, data)`. |
| `_ensure_perso_thread(...)` | méthode | Idem pour le forum perso. |
| `_do_publish_correction(...)` | méthode | Publie 1 correction (idempotent MD5). |
| `_do_publish_enonce(...)` | méthode | Publie / met à jour 1 énoncé seul. |
| `_publish_enonce_into_thread(...)` | méthode | Helper bas niveau : attache énoncé à un thread existant. |
| `_do_publish_perso(...)` | méthode | Publie tout le matériel d'un thread perso. |
| `_publish_perso_post(...)` | méthode | Publie 1 post perso (TACHE / script / slides / vidéo). |
| `_apply_perso_thread_tags(...)` | méthode | Applique tags type + matériel à un thread perso. |
| `_corrections_watcher_loop()` / `_corrections_watcher_tick()` | méthode | Watcher Phase B. |
| `_auto_publish_cm(matiere, num, date)` | méthode | Phase K — invoque `_publish_classic` avec `_HeadlessCtx` après l'arrivée d'un `.txt` CM dans `_INBOX`. Gating sur `_published.json` (skip si 3/3, warn si 1-2/3, full publish si 0/3). |
| `_HeadlessCtx` | classe | Faux ctx (no-op `send`) pour invoquer les commandes en interne. |

---

## 8. Règles de comportement

### Sécurité

- **Ne jamais modifier `datas/*.json` à la main pendant que le bot tourne.**
- Avant un `!cours backfill*`, **toujours faire le dry-run / lire le
  preview embed avant de réagir ✅** (impossible de revenir en arrière
  sans purge-thread + recréation manuelle).
- Avant de relancer le bot, vérifier qu'**une seule instance** tourne
  (`tasklist /FI "IMAGENAME eq python.exe"` Windows).

### Idempotence

Tous les `_do_publish_*` sont **idempotents par MD5** : relancer la
même publication n'a aucun effet si le fichier n'a pas changé. Cela
permet de relancer un `backfill` sans crainte de doublons Discord.

### Versioning

Quand un fichier change (correction, énoncé, post perso) :
- Ancien message Discord supprimé via `thread.fetch_message().delete()`.
- Nouveau message posté avec préfixe `🔄 Version N` dans le titre.
- Numéro `version` incrémenté dans le JSON.
- Historique des versions conservé dans le champ `versions[]` (côté
  correction).

### Tags forum

Les tags `discord.ForumTag` ont **`name` et `emoji` séparés** (Discord
les stocke séparément). Coller l'emoji dans `name` casse l'idempotence
de `setup-tags*`. Toujours utiliser :
```python
discord.ForumTag(name="Énoncé seul", emoji=discord.PartialEmoji(name="📄"))
```
Le helper `get_forum_tag(forum, label)` cherche par `tag.name` uniquement.

### RÈGLE — Application automatique des tags (sans demande explicite)

**À chaque création OU mise à jour d'un thread forum**, Claude applique
**systématiquement et sans qu'on ait à le demander** les tags qui
correspondent au contenu posté. Pas besoin que Gaylord dise « applique
les tags » — c'est par défaut.

S'applique à tous les chemins qui touchent aux threads forum :
- `_do_publish_correction` / `_do_publish_enonce` → tag Type (TD/TP/CC/Quiz)
  + tag État (`📄 Énoncé seul` / `✍️ Corrections présentes` / `📄 Énoncé manquant`)
- `_do_publish_perso` → tag Type + tags Matériel (`📋 TACHE`, `📝 Script oral`,
  `📊 Slides`, `🎬 Vidéo`)
- `_publish_queue_watcher` (Phase L, manifestes JSON) — pour `kind=perso`,
  appliquer les mêmes tags que `_do_publish_perso` ; pour `kind=off-topic`
  dans le forum `hors-sujets`, appliquer les tags présents si le forum en
  expose.
- Toute commande de maintenance qui touche un thread (republish, backfill,
  reconcile, etc.) doit re-appeler `_apply_*_tags` à la fin.

Le tracking `tags_applied[]` dans les JSON v2/perso reflète l'état que le
bot **croit** avoir appliqué — ce n'est pas une vérification côté Discord.
En cas de doute (api a renvoyé OK mais visuellement les tags manquent),
relancer la publication ou la commande de tagging idempotente force la
réapplication.

**Conséquence concrète** : si Claude crée un thread via un script ad-hoc
ou une nouvelle branche de pipeline, il **doit** ajouter l'appel
`_apply_*_tags` au moment de la création — ne pas reporter à plus tard,
ne pas laisser ça à une commande manuelle de rattrapage.

### Réconciliation thread supprimé

Si un thread Discord est supprimé manuellement (pas via `purge-thread`),
le prochain appel à `_ensure_*_thread` détecte le 404 lors du
`fetch_channel`, purge l'entrée JSON, et recrée le thread. **Important** :
le check MD5 se fait **après** `_ensure_td_thread` dans
`_do_publish_correction`, sinon une suppression manuelle laisserait le
JSON désynchronisé et la branche `skip_same_md5` empêcherait toute
republication.

---

## 9. Démarrage automatique

Deux modes coexistent. **Une seule instance à la fois** sinon double
watcher / double digest / double on_ready.

### Mode tray (recommandé) — `bot_tray.py` + `start_tray.vbs`

- `bot_tray.py` : process parent qui spawne `python -u bot.py` en
  subprocess (sans console via `CREATE_NO_WINDOW`). Capture
  stdout/stderr → `%TEMP%\BotGSTAR_startup.log` + buffer mémoire
  `deque(maxlen=4000)` pour la fenêtre logs Tk.
- Auto-restart 10 s après crash (`RESTART_DELAY_SECONDS = 10`),
  toast Windows à chaque crash + redémarrage manuel.
- Icône tray colorée selon `BotState` :
  vert RUNNING / orange PAUSED / rouge CRASHED_WAITING / bleu RESTARTING.
- Menu clic droit (8 entrées) : voir logs (Tk auto-scroll) / ouvrir
  dossier logs / ouvrir dossier datas / pause-reprise / redémarrer /
  démarrer avec Windows / quitter.
- `start_tray.vbs` : lanceur silencieux qui détecte `pythonw.exe`
  (PATH ou chemin par défaut `Python312`) et lance `bot_tray.py` sans
  console (`Run …, 0, False`).
- Auto-démarrage Windows : géré par le menu tray lui-même
  (`action_toggle_startup`), qui écrit `BotGSTAR_Tray.vbs` dans
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`. Pas
  besoin de raccourci manuel.
- `taskkill /F /T /PID` pour tuer proprement (arbre de processus).

### Mode console (debug) — `start_bot.bat`

- Boucle de relance (10 s entre crashs), `python -u bot.py` via
  PowerShell `Tee-Object` → console + fichier log simultanément.
- **Sémantique « click = restart »** : au lancement, tue toute autre
  instance (ancien watchdog par `WINDOWTITLE`, ancien `python.exe`
  par cmdline `bot.py`) avant de prendre la main. La fenêtre courante
  est protégée car elle n'a pas encore le `title` cible au moment du
  `taskkill`.
- Titre fenêtre `BotGSTAR - Pipeline COURS` posé après le kill →
  permet aux relances suivantes de la cibler.

### Communs aux deux modes

- Rattrapage des commandes manquées via `on_ready` (< 24 h, admin only).
- Auto-start des deux watchers (corrections + digest RSS) via leurs
  listeners `on_ready` respectifs (idempotents).
- Garde anti-double-watchdog côté Cog (`_inbox_watcher_logged`,
  `corrections_watcher_running`, `is_running()` côté `tasks.loop`).

---

## 10. Évolutions notables (synthèse — voir `COURS/CHANGELOG.md` pour le détail)

- **Phase A** (refonte v2) : 1 thread par TD/TP/CC au lieu de 1 par exo.
- **Phase B** : watcher auto polling 60 s + récap quotidien.
- **Phase D** : 156 corrections rattrapées via backfill × 3 matières.
- **Phase E1** : pipeline énoncés seuls + commande `publish enonce`.
- **Phase F1** : forum privé `🔒 PERSONNEL` + 5 forums perso + 5 commandes.
- **Phase G** : nettoyage 57 Mo (script `cleanup_2026-04-25.py` côté
  COURS, manifeste JSON dans `_archives/`).
- **Phase H** : refresh complet de la doc (ce fichier) + auto-start du
  watcher au boot via listener `on_ready`.
- **Phase R** : nouveau Cog autonome `veille_rss.py` (agrégation RSS,
  4 salons digest matinal, scoring mots-clés). Voir §11 + `BotGSTAR/CHANGELOG.md`.
- **Phase I** (2026-04-25) : 3 fixes sur le résolveur multi-millésimes des
  CC. `resolve_correction_pdf` et `find_enonce_pdf` acceptent désormais un
  argument `annee` et filtrent dessus pour les CC, sinon le tri lexical
  décroissant publiait le PDF le plus récent dans tous les threads d'années
  antérieures. `_do_publish_correction` propage `annee` au resolver. Causé
  4 threads EN1 CC corrompus (CC1 ×3 + CC2 2023-24), réparés via
  `!cours backfill en1` après bot restart. Voir `COURS/CHANGELOG.md` pour
  le diagnostic complet.
- **Phase S** (2026-04-26) : tray watchdog `bot_tray.py` + lanceur
  silencieux `start_tray.vbs`. Remplace `start_bot.bat` pour l'usage
  normal (icône system tray, auto-restart, fenêtre logs Tk, toggle
  autostart Windows depuis le menu). `start_bot.bat` reste utilisable
  pour debug, avec sémantique « click = restart » qui tue toute autre
  instance avant de prendre la main. Voir §9.
- **Phase R-F** (2026-04-26) : refonte du rendu embed Style A v2
  (titres cliquables via lien Markdown dans `field.value`, name
  `​` pour aération inter-articles, timestamp + footer
  uniquement sur le dernier embed du split). Voir `BotGSTAR/CHANGELOG.md`.
- **Phase R-G** (2026-04-26) : Style A v3 final — titre uniquement
  sur le 1er embed (suppression des `(1/N)` `(2/N)`), image spacer
  730×1 transparente sur chaque embed pour uniformiser la largeur
  côté Discord. Constante `EMBED_SPACER_URL`.
- **Phase R-H** (2026-04-26) : emoji source par catégorie. Constante
  `CATEGORY_SOURCE_EMOJI` mappe chaque catégorie à l'emoji du salon
  Discord correspondant (cyber→📰, ia→🤖, dev→💻, tech→📱).
  `_format_article_field` lit `CATEGORY_SOURCE_EMOJI[article.category]`
  pour la 2e ligne de chaque carte. Reset complet `rss_state.json`
  au déploiement (backup `rss_state.json.bak.YYYYMMDD-HHMMSS`).
  **Checklist pour ajouter une catégorie** : alimenter à la fois
  `VALID_CATEGORIES`, `VEILLE_CHANNELS`, `CATEGORY_TITLES`,
  `CATEGORY_COLORS`, `DIGEST_WINDOW_HOURS_BY_CAT` ET
  `CATEGORY_SOURCE_EMOJI` (sinon fallback 📰).
- **Cleanup workspace** (2026-04-26) : ~35 MB supprimés (vieux logs
  racine, junk Mudae `embed_logs.json`, `constants.py`, `dumps/`,
  vieux backups RSS), ~9 MB archivés (`scrape_events.py` NosTale +
  outputs vers `_archived/nostale_events/`).

---

## 11. Cog `veille_rss.py` — Système de veille RSS Discord

Cog autonome (indépendant de `cours_pipeline.py`) qui agrège des flux
RSS et poste des digests quotidiens dans 4 salons Discord dédiés
(`#cyber-veille`, `#ia-veille`, `#dev-veille`, `#tech-news` sous la
catégorie `📡 VEILLE`).

### Architecture

- **Sources** : `datas/rss_sources.yaml` (éditable manuellement OU via
  `!veille sources add/remove/toggle`).
- **Mots-clés de scoring** : `datas/rss_keywords.yaml` (boost +
  blacklist par catégorie).
- **État runtime** : `datas/rss_state.json` (atomic write, dédup MD5
  des guids, fetch_state par source, `last_digest_at`).
- **Logs** : embeds dans `#logs` (couleurs codées : vert OK, orange
  warning, rouge erreur, gris info).

### Flux de fetch

1. `_fetch_one_source(session, source, fetch_state)` — récupère un
   flux via `feedparser`, supporte `If-None-Match` /
   `If-Modified-Since` pour le cache HTTP, incrémente
   `consecutive_errors` en cas d'échec.
2. **Auto-désactivation** : après `SOURCE_ERROR_THRESHOLD = 5` erreurs
   consécutives, la source est désactivée en mémoire (le YAML reste
   `active: true` mais le runtime skip + log embed).
3. `_filter_and_select` — applique
   - dédoublonnage via `state["published"]` (hash MD5 du guid)
   - fenêtre de fraîcheur par catégorie (`DIGEST_WINDOW_HOURS_BY_CAT`)
   - scoring mots-clés (blacklist = filtre dur, boost = +500/match)
   - tri par score décroissant + cap `DIGEST_MAX_ARTICLES = 10`.

### Scoring formule (R-D)

```
score = (4 - priorité_source) × 1000
      + max(0, 1000 - âge_minutes)
      + nb_keywords_boost × 500
```

Pour comparaison : prio 1 = 3000, prio 2 = 2000. Un article prio 2
avec 3 mots-clés boost (3500) bat un article prio 1 sans boost
(3000). C'est intentionnel : un sujet brûlant d'une source secondaire
prime.

### Fenêtre de fraîcheur par catégorie

| Catégorie | Fenêtre | Justification |
|---|---|---|
| `tech` | 24 h | Numerama publie 5-10 articles/jour |
| `cyber` | 72 h | CERT-FR publie ~3-5 fois/semaine |
| `ia` | 72 h | OpenAI/Anthropic ~5-10/semaine |
| `dev` | 72 h | GitHub Blog ~1-3/semaine |

### Scheduler quotidien

- `@tasks.loop(time=time(8, 0, tzinfo=ZoneInfo("Europe/Paris")))` —
  digest auto à 8h00 Paris.
- Auto-start au boot via listener `on_ready` (idempotent via
  `is_running()`, même pattern que `cours_pipeline.py` Phase H).
- **Catch-up** : si le bot démarre après 8h00 ET `last_digest_at` ≠
  aujourd'hui, exécution immédiate.
- Garde anti-doublon `_digest_already_today` (évite 2 digests le même
  jour si reconnect Discord).
- `cog_unload` cancel le loop pour shutdown propre.

### Commandes Discord (10 commandes, admin only)

| Commande | Effet |
|---|---|
| `!veille fetch-now` | Cycle manuel mode 'manual' (pas de récap logs) |
| `!veille trigger-now` | Cycle 'auto' (avec récap dans `#logs`) |
| `!veille status` | Liste l'état des sources avec compteurs d'erreurs |
| `!veille reload` | Recharge `rss_sources.yaml` et `rss_keywords.yaml` |
| `!veille sources list` | Embed groupé par catégorie |
| `!veille sources add <id> <url> <cat> [prio]` | Ajout avec test d'URL préalable |
| `!veille sources remove <id>` | Suppression avec confirmation 30s |
| `!veille sources toggle <id>` | Active/désactive |
| `!veille sources test <url>` | Validation d'URL sans persistance |
| `!veille keywords` | Embed des mots-clés boost + blacklist |

### Stack technique

- `feedparser` — parsing RSS/Atom standard
- `aiohttp` — fetchs asynchrones (timeout 15 s, parallèle sur sources actives)
- `ruamel.yaml` (≥ 0.18) — preserve-roundtrip du YAML lors des
  `!veille sources add/remove/toggle` (commentaires + ordre conservés)
- `discord.ext.tasks` — scheduler (déjà utilisé par `cours_pipeline.py`)
- `ZoneInfo("Europe/Paris")` — timezone-aware

### Fichiers de configuration

```
BotGSTAR/datas/
  rss_sources.yaml      # Sources actives, éditable à la main ou via Discord
  rss_keywords.yaml     # Mots-clés boost + blacklist par catégorie
  rss_state.json        # État runtime (NE PAS éditer à la main)
  rss_state.json.bak.*  # Backups auto avant modifs lourdes
```

### Garde-fous

- User-Agent identifié : `BotGSTAR-VeilleRSS/1.0 (+gaylordaboeka@gmail.com)`
- Pas de scraping HTML (RSS/Atom uniquement)
- Atomic write JSON et YAML (`.tmp` + `os.replace`)
- Pruning des entrées `published[]` après 30 jours
- Skip silencieux des catégories à 0 article en mode auto
  (constante `SKIP_EMPTY_CATEGORIES = True`)
- Split automatique des digests longs (cap dur 5 embeds par catégorie,
  description ≤ 3900 chars/embed pour rester sous la limite Discord 4096)

### Helpers clés

| Helper | Type | Rôle |
|---|---|---|
| `_make_yaml()` | module | Factory ruamel.yaml configurée (preserve_quotes, indent 2) |
| `_load_sources_raw()` / `_save_sources_raw(data)` | module | I/O brut ruamel (CommentedSeq) |
| `_load_sources()` | module | Validation + conversion en dataclass `Source` |
| `_load_keywords()` | module | Charge `rss_keywords.yaml`, structure {cat → boost/blacklist} |
| `_apply_keyword_scoring(art, kw)` | module | Modifie `art.keyword_boost`, retourne `(kept, boost_matches, blacklist_matches)` |
| `_filter_and_select(articles, state)` | module | Le filtre principal (dédup + fenêtre + scoring + top N) |
| `_fetch_one_source(session, src, state)` | module | Fetch RSS d'1 source, gère 304/erreurs/etag |
| `_run_daily_cycle(source)` | méthode | Cycle complet (fetch → filtre → post → récap), `source ∈ {auto, manual}` |
| `_test_source_url(url)` | méthode | Valide une URL avant `!veille sources add` |

---

**En cas de doute** : la source de vérité est toujours
`cours_pipeline.py` ou `veille_rss.py` (chercher `@cours.command` /
`@veille_group.command` pour la liste exhaustive, `@tasks.loop` pour
les loops actifs).

---

## 12. Cog `veille_rss_politique.py` — Veille RSS politique (Option C)

> **Migration 2026-04-29** : ce cog tourne maintenant sur **ISTIC L1 G2**
> (`1466806132998672466`) et non plus sur le serveur Veille. Les 7 salons
> ont été déplacés dans la catégorie `📡 VEILLE` existante (qui contient
> aussi les 4 salons RSS tech), et renommés avec des noms accessibles aux
> étudiants : `arsenal-eco` → `économie-veille`, `arsenal-attaques` →
> `débats-politiques`, etc. Le `cog_check` accepte le rôle `ADMIN_ROLE_ID`
> ISTIC (`1493905604241129592`) en plus de `is_owner` / admin Discord.

Cog autonome (architecture jumelle de `veille_rss.py`). Agrège des sources
politiques françaises et poste un digest matinal à 8h00 Paris dans
**7 salons USAGE-orientés** dans la catégorie `📡 VEILLE` (côté tech +
politique fusionnés depuis 2026-04-29).

### Philosophie « Option C » (vs. classification idéologique)

Les catégories ne sont pas des camps politiques mais des **questions
d'usage** : « pour quel besoin précis vais-je consulter ce salon ? ».

| Clé interne (YAML/state) | Salon Discord ISTIC | Question d'usage |
|---|---|---|
| `actu-chaude` | `🔥・actu-chaude` | « Qu'est-ce qui se passe ce matin ? » |
| `arsenal-eco` | `💰・économie-veille` | « Quel chiffre / argument économique pour répondre ? » |
| `arsenal-ecologie` | `🌱・écologie-veille` | « Quelle donnée climat / écologique pour étayer ? » |
| `arsenal-international` | `🌍・international-veille` | « Que se passe-t-il sur Palestine, Russie, Sahel… ? » |
| `arsenal-social` | `✊・social-veille` | « Quelle lutte / syndicat / mobilisation a bougé ? » |
| `arsenal-attaques` | `🎯・débats-politiques` | « Quelle réplique pro-LFI à un argument adverse ? » |
| `arsenal-medias` | `📺・médias-veille` | « Qui dit quoi, qui ment, qui critique le récit dominant ? » |

Note : les **clés internes** (gauche) restent stables (`arsenal-*`) car
elles indexent les YAML `rss_sources_politique.yaml`,
`rss_keywords_politique.yaml` et le state JSON. Les **noms Discord** (droite)
ont été rendus accessibles aux étudiants ISTIC en avril 2026.

**Pas de salon « droite » ou « extrême droite » dédié** : les sources
adverses pollueraient sans servir d'usage clair. Leur narratif est
filtré et critiqué via `arsenal-attaques` (analyses pro-LFI),
`arsenal-medias` (Acrimed, Arrêt sur Images) et les fact-checks
(CheckNews, Décodeurs) de `actu-chaude`.

### Fichiers de configuration

```
BotGSTAR/datas/
  rss_sources_politique.yaml    # 40 sources, 7 catégories (toutes vérifiées HTTP 200 + entries > 0)
  rss_keywords_politique.yaml   # Mots-clés boost + blacklist par catégorie
  rss_state_politique.json      # État runtime (NE PAS éditer à la main)
  rss_state_politique.json.bak.* # Backups auto
```

### Différences notables avec `veille_rss.py`

| Aspect | `veille_rss.py` (tech) | `veille_rss_politique.py` |
|---|---|---|
| Serveur | **ISTIC L1 G2** (1466806132998672466) | **ISTIC L1 G2** (depuis 2026-04-29) |
| Catégorie Discord | `📡 VEILLE` (partagée) | `📡 VEILLE` (partagée, fusion 2026-04-29) |
| Salons | 4 tech (cyber/ia/dev/tech-news) | 7 politiques (Option C) |
| Permissions | rôle ADMIN_ROLE_ID ISTIC | `is_owner` OU rôle admin OU perms Discord |
| Discovery salons | IDs hardcodés (`VEILLE_CHANNELS`) | Auto-discovery par nom (`_normalize_channel_name`) |
| Sources | Mix FR/EN | 100% FR |
| Usage cible | étudiants ISTIC L1 G2 | Gaylord (Arsenal Intelligence Unit) + étudiants curieux |
| Démarrage embed | poste l'embed unique pour les 2 cogs | (silencieux depuis 2026-04-29) |

### Commandes (alias `!vp`)

| Commande | Rôle |
|---|---|
| `!veille_pol setup-channels` | Crée la catégorie + 7 salons s'ils n'existent pas |
| `!veille_pol fetch-now` (`!vp fetch-now`) | Cycle manuel sans récap logs |
| `!veille_pol trigger-now` | Cycle 'auto' avec récap dans `#logs` |
| `!veille_pol status` | État détaillé sources + erreurs |
| `!veille_pol reload` | Recharge YAML sources + keywords |
| `!veille_pol sources list/add/remove/toggle/test` | Gestion sources (test URL préalable) |
| `!veille_pol keywords` | Affiche mots-clés boost + blacklist par catégorie |

### Test santé externe

Script standalone `Arsenal_Arguments/_claude_logs/test_rss_sources.py`
qui lit le YAML et teste chaque URL active en parallèle (10 workers).
Reporte par catégorie, identifie les défaillantes, propose la commande
de désactivation. **Toujours en sync avec le YAML** (pas de liste
hardcodée).

```bash
python Arsenal_Arguments/_claude_logs/test_rss_sources.py
python Arsenal_Arguments/_claude_logs/test_rss_sources.py --include-inactive
```

### Helpers spécifiques

| Helper | Rôle |
|---|---|
| `_resolve_channels(guild)` | Auto-discovery des 7 salons par nom (insensible aux emojis/special chars), cache les IDs dans `state.channels` |
| `_find_category(guild, name)` / `_find_text_channel(...)` | Lookup par nom normalisé (`_normalize_channel_name`) |
| `_normalize_channel_name(name)` | `re.sub(r"[^a-z0-9]", "", name.lower())` — strippe emojis et special chars |
| `_post_morning_summary(state, posted, stats)` | Récap matinal stylé dans `#logs` (couleur orange si erreurs, vert sinon, stats : doublons / expirés / blacklistés / boostés) |

### Garde-fous (identiques à `veille_rss.py` pour cohérence)

- Atomic write JSON et YAML
- Dédoublonnage MD5 sur 30 jours
- Fenêtres de fraîcheur ajustées par catégorie (24h actu/intl, 48h écologie/social/attaques, 72h éco/medias)
- Auto-désactivation source après 5 erreurs consécutives
- Catch-up au boot si après 8h00 Paris et pas de digest aujourd'hui
- User-Agent identifié : `BotGSTAR-VeillePolitique/1.0 (+gaylordaboeka@gmail.com)`

---

## 13. Cogs Arsenal Intelligence Unit (`arsenal_pipeline.py` + `arsenal_publisher.py`)

Pipeline complet de veille **vidéo politique** sur 6 plateformes
(TikTok, Instagram, YouTube, X, Reddit, Threads), avec download →
normalize CSV → transcription Whisper GPU → résumé Claude (via CLI
subscription depuis le bug 3/5 corrigé 2026-04-28) → publication forum.

**Pour la doc complète** : voir `Arsenal_Arguments/CLAUDE.md` qui décrit
le pipeline disque, les conventions CSV, les helpers config, et les
scripts compagnons (`whisper_supervisor.py`, `progress_monitor.py`,
`post_whisper_orchestrator.py`).

### Salons et IDs clés (ISTIC L1 G2 depuis 2026-04-29)

| Élément | ID |
|---|---|
| Guild ISTIC L1 G2 | `1466806132998672466` |
| `🔗・liens` dans QG (listener auto URL drops, public lecture, écriture admin only) | `1498918445763268658` |
| `📋・logs` dans QG (embeds couleur-codés) | `1493760267300110466` |
| Catégorie `ANALYSES POLITIQUES` (12 forums politiques thématiques fins) | `1499086461297889330` |
| Catégorie `📡 VEILLE` (RSS tech + politique fusionnés) | `1497581043086135478` |
| Rôle Admin | `1493905604241129592` |

⚠ **Note migration Phase X** : `arsenal_publisher.category_name = "ANALYSES POLITIQUES"`
(sans emoji `📂`). Une catégorie `📂 ANALYSES POLITIQUES` avec emoji existait
brièvement comme orpheline de migration et a été supprimée. Le user a
créé manuellement la cat finale `ANALYSES POLITIQUES` avec 12 forums
thématiques (économie-et-social, culture-et-éducation, justice-et-libertés,
féminisme-et-luttes, histoire-et-géopolitique, etc.) — c'est la cat cible
définitive pour `arsenal_publisher`.

### Workflow recommandé : GUI Tkinter `summarize_gui.py`

Pour piloter le batch summarize sans CLI : **double-clic sur
`Arsenal_Arguments/start_summarize_gui.vbs`** qui lance silencieusement la
GUI Tkinter (PID `pythonw.exe` sans console).

La GUI propose :
- Frame **Options** : moteur (Claude Code CLI / API — choix persistant
  dans `_secrets/engine_pref.json` partagé avec `arsenal_pipeline.step_summarize`,
  donc cocher API bascule aussi le pipeline `🔗・liens` au prochain drop),
  filtres (--text-only, --re-summarize avec confirmation modale, --no-wait,
  --id), robustesse (auto-restart + plafond max).
- Frame **📊 Quota Pro Max** : barres live Session 5h / Hebdo 7j / Hebdo
  Sonnet / Overage, deux Spinbox seuils (défauts 70%/80%), auto-stop +
  auto-resume, bouton dialog cookie chiffré DPAPI, bouton reset throttle hebdo.
- Frame **Contrôles** : ▶ Lancer / ⏸ Pause (psutil suspend récursif) /
  ⏹ Stop (Ctrl-Break → embed Discord "Interrompu" / hard kill 10s).
- Frame **Console** : tail 400 lignes, fond sombre, auto-scroll.
- Frame **Raccourcis** : 6 boutons (📂 dossier summaries, 📊 audit, 🌐 #logs,
  🩺 Status Anthropic, 💳 Console billing, 📈 Claude.ai usage).

Le **garde-fou quota** est persistant via `_secrets/quota_state.json`
(atomic write). Si seuil session dépassé → auto-stop + reprise quand reset
ou quota redescend. Si seuil hebdo dépassé → auto-stop **persistant**
(survit aux restarts GUI), bouton ▶ Lancer désactivé jusqu'à reset
automatique ou clic « Reset throttle hebdo » (avec confirmation modale).

### Module `claude_usage.py` (scraping endpoint privé Claude.ai)

Module standalone qui lit le quota Pro Max depuis l'endpoint privé interne
`/api/organizations/{ORG_UUID}/usage` (découvert via DevTools Network).
Cookie de session chiffré localement via **Windows DPAPI** (pas de mot de
passe, lié à la session Windows) dans `_secrets/claude_session.bin`.
Imite Chrome pour passer Cloudflare (headers `sec-ch-ua-*`, `priority`,
`sec-fetch-*`). Mode CLI standalone : `--set-cookie`, `--fetch`, `--state`,
`--clear`. Utilisé par la GUI pour rafraîchir le quota toutes les 60 s
dans un thread daemon (pas de freeze UI).

### Auto-sync temps réel (Phase X)

`arsenal_publisher` a un `tasks.loop(seconds=15)` qui poll le mtime du
CSV et lance `_sync_task` dès qu'une ligne `summary=SUCCESS, sync=PENDING`
apparaît. Conséquence : chaque résumé créé par `summarize.py` est publié
dans son forum dans les 15 s qui suivent, **automatiquement**, sans LLM.

**Anti-boucle** : si ≥3 lignes ont un `sync_timestamp` < 5 min ET
`sync_status=FAILED`, suspension du tick (cooldown). Évite le spam quand
un bug structurel pète sur les mêmes IDs.

**Sync silencieuse en l'absence de travail (Phase Y)** :
`_sync_task(silent_if_no_work=True)` (passé par `_auto_sync_loop`) supprime
les embeds `🚀 Synchronisation` et `🏁 Sync terminée` quand `synced=0` ET
`failed=0`. Évite le spam `📋・logs` quand un batch `--re-summarize`
rafraîchit des items déjà `sync=SUCCESS`. Les manual sync
(`!sync_arsenal`) gardent les embeds inconditionnels (par défaut
`silent_if_no_work=False`). Le compteur `⏭️ N ignorés` agrège tout ce
qui n'a rien à faire (déjà SUCCESS, thread auto-healed, parse fail) —
pas un compteur d'erreurs.

### Whitelist forums canoniques (Phase Y.6)

`arsenal_publisher.py` définit `CANONICAL_FORUMS` (set de 14 slugs) et
`CLASSIFICATION_ALIASES` (~35 entrées variantes → canonique). Le helper
`_normalize_forum_slug(raw_slug)` est appelé dans `parse_analysis` après
la slugification du `display_theme`. Comportement :
1. Si `raw_slug` ∈ `CANONICAL_FORUMS` → retourne tel quel.
2. Sinon si `raw_slug` ∈ `CLASSIFICATION_ALIASES` → retourne la cible
   canonique (log info).
3. Sinon → fallback `catégorie-libre` (log info, pour repérer les
   nouveaux patterns Claude à ajouter à l'alias map).

Empêche la création de futurs forums orphelins. Cas observé Y.6 : Claude
écrit parfois `Éducation > Politique éducative` au lieu de
`Culture et Éducation > Politique éducative` (sous-thème promu en thème
principal sous l'effet du contenu de la vidéo). Sans le whitelist, ça
crée un forum `éducation` orphelin à 1 thread. Avec, l'alias map
`éducation` → `culture-et-éducation` route le thread dans le bon forum.

Note : la liste de catégories est définie en double, dans le prompt
système de `summarize.py` (lignes 117-133) ET dans `CANONICAL_FORUMS`
de `arsenal_publisher.py`. Si tu modifies l'une, mets l'autre à jour.

### Auto-archive Discord (Phase Y.5)

`arsenal_publisher` a un `tasks.loop(hours=1)` `_auto_archive_loop` qui
archive les vieux threads d'`ANALYSES POLITIQUES` quand la guilde dépasse
**900/1000 threads actifs** (limite Discord serveurs non-boostés). Évite
la saturation et les erreurs 160006 sur l'auto-sync.

Comportement :
- Skip si `is_syncing` (évite conflits PATCH concurrents).
- Trie threads par snowflake ID (plus vieux d'abord), archive jusqu'à
  redescendre à 800 (seuils dans `__init__` : `ARCHIVE_THRESHOLD = 900`,
  `ARCHIVE_TARGET = 800`).
- Sleep 0.5 s entre PATCHs (rate limit), abandon après 5 échecs.
- Embed `🗄️ Auto-archive Arsenal` dans `📋・logs` avec breakdown par
  forum (silencieux si rien archivé).
- Threads archivés restent visibles, auto-unarchive si message posté.

Commande manuelle `!archive_arsenal [target]` force un cycle immédiat
(target optionnel override `ARCHIVE_TARGET` pour ce run, ex
`!archive_arsenal 600` pour archivage agressif).

### Choix moteur summarize partagé GUI ↔ pipeline (Phase Y)

`arsenal_config.load_engine_pref()` / `save_engine_pref(engine)` lisent /
écrivent `Arsenal_Arguments/_secrets/engine_pref.json` au format
`{"engine": "claude_code" | "api"}`. Source de vérité unique :
- La GUI `summarize_gui.py` initialise le radio button « Moteur » depuis
  ce fichier (au lieu du défaut hardcodé) et `trace_add` sauvegarde à
  chaque changement.
- `arsenal_pipeline.step_summarize` lit la pref et n'ajoute
  `--use-claude-code` que si engine == `claude_code`. En mode `api`,
  `summarize.py` utilise son model par défaut (nécessite
  `ANTHROPIC_API_KEY`).

Donc cocher le radio « API Anthropic » dans la GUI bascule **aussi** le
pipeline `🔗・liens` sur l'API au prochain drop. Avant Phase Y, la GUI
ne persistait rien et `step_summarize` avait `--use-claude-code` hardcodé.

### Quota Pro Max appliqué au pipeline arsenal (Phase X)

`arsenal_pipeline.step_summarize` appelle `_check_quota_before_summarize()`
avant de spawn `summarize.py`. Si le seuil session ou hebdo est dépassé
(seuils partagés avec la GUI summarize via `_secrets/quota_state.json`),
le step retourne `{"ok": False, "quota_blocked": True, ...}` → embed orange
`⚠ Résumé sauté — quota atteint` dans `📋・logs`, étape Sync skip aussi.

Mode tolérant : si check fail (cookie missing, network), on continue par
défaut pour ne pas bloquer le pipeline sur une panne du quota watcher.

### Bugs corrigés 2026-04-28 / 2026-04-29

- **Bug listener 6 plateformes** (28/04) : `extract_urls()` était limité
  TikTok/IG. Réécrit comme wrapper de `extract_urls_all_platforms()`.
- **Bug 3/5 étapes summarize** (28/04) : clé API Anthropic épuisée →
  `step_summarize` utilise désormais `--use-claude-code` (CLI subscription).
  Depuis Phase Y (30/04), le choix CLI vs API est switchable via la GUI
  summarize (radio button persistant dans `_secrets/engine_pref.json`).
- **Bug Carrousels d'images IG** (29/04) : yt-dlp 2026.02 ne récupérait
  plus les `entries` des carrousels d'images IG. Fix double : update
  `yt-dlp -U` + ajout d'un fallback **`download_via_gallery_dl()`** dans
  `dl_instagram.py:760` qui dump le manifest gallery-dl en JSON puis
  télécharge via `download_binary_url`. Probe `?img_index=N` élargi à
  `type=Auto`/`Image` (avant : `Carrousel` only).
- **Bug Unicode console cp1252** (29/04) : log emojis (✅ ⚠️ 🎬) crashaient
  sur stdout cp1252 par défaut Windows. Fix global dans `arsenal_config.py`
  qui force `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` au
  module load.
- **Spam DM admin supprimé** (29/04) : `arsenal_publisher.py` et
  `arsenal_pipeline.py` envoyaient des DM redondants après chaque sync /
  pipeline en erreur. Supprimés — tout est déjà dans `#logs`.

### Migration 2026-04-29 — Veille → ISTIC L1 G2

Les 3 cogs Arsenal et la veille politique ont été migrés du serveur Veille
(1475846763909873727) vers ISTIC L1 G2 (1466806132998672466) pour unifier
sur un seul serveur. Actions Discord :
- 7 salons RSS politiques fusionnés dans la catégorie `📡 VEILLE` existante
  (renommés user-friendly : `arsenal-eco` → `économie-veille`,
  `arsenal-attaques` → `débats-politiques`, etc.).
- Catégorie `📂 ANALYSES POLITIQUES` créée avec les 8 forums politiques
  (tags copiés du serveur Veille).
- Salon `🔗・liens` créé (public, lecture pour tous, écriture admin only ;
  initialement créé dans `🔒 PERSONNEL` puis déplacé en `QG` le 30/04 — voir
  Phase Y.2).
- Côté Veille : catégories `ARSENAL`, `📡 VEILLE POLITIQUE` et salon
  `#liens` supprimés (cleanup destructif validé par l'utilisateur).

Scripts de migration archivés dans `Arsenal_Arguments/_claude_logs/` :
`audit_discord_structure.py`, `migrate_setup_istic.py`,
`migrate_cleanup_veille.py`.

### Embed démarrage unifié veille (2026-04-29)

Auparavant `veille_rss` et `veille_rss_politique` postaient chacun un
embed `🟢 ... — Démarrage` au boot → doublon dans `#logs`. Désormais
`veille_rss_politique` ne poste rien au démarrage et `veille_rss` poste
un embed unique `🟢 Veille — Démarrage` qui couvre les deux loops.

### Rattrapage des FAILED — `arsenal_retry_failed.py`

Script standalone qui scanne `suivi_global.csv` pour les lignes
`download_status=FAILED` (déduplication par `(plateforme, id)` sur le
download_timestamp le plus récent) et relance le bon downloader pour
chacune. Profite du fix gallery-dl + yt-dlp 2026.03. Modes :

```
python arsenal_retry_failed.py                   # dry-run + stats
python arsenal_retry_failed.py --apply           # exécute toutes
python arsenal_retry_failed.py --apply --limit 10
python arsenal_retry_failed.py --platform Instagram --apply
```

Appelle `csv_normalize.py` à la fin pour dédupliquer (les nouvelles lignes
SUCCESS éclipsent les anciennes FAILED).

### Audit `🔗・liens` — `audit_liens_channel.py` (Phase Y.17+)

Script standalone `Arsenal_Arguments/_claude_logs/audit_liens_channel.py`
qui scanne EXHAUSTIVEMENT le salon `🔗・liens` (tous messages, pas
seulement les 50 derniers comme `_catchup_scan`) et catégorise les
incohérences par drop. Avec `--apply`, fixe 9 catégories sur 11.

| Code | Catégorie | Fix appliqué |
|---|---|---|
| **A** | User msg avec URL mais sans fil | Crée fil + post pipeline embeds depuis #logs scan |
| **B** | Fil sans `✅ Dossier indexé` | Forward depuis #logs scan OU pmap Y.15 fallback |
| **C** | Fil sans `⚙️ Pipeline | Démarrage` | Post pipeline embeds manquants |
| **D** | Fil sans `⚙️ Pipeline terminé` | Post pipeline embeds manquants |
| **E** | Auteur encore membre du fil | DELETE thread-members (Y.13 silence ping) |
| **F** | Fil non archivé après pipeline complet | PATCH archived=true (Y.12) |
| **G** | System msg "X a commencé un fil" | DELETE (Y.10) |
| **H** | Fil vide (zéro embed) | Traité comme A |
| **I** | 🔄 sans ✅/❌ (= pipeline jamais terminé) | Log only |
| **J** | Réaction ❌ posée par le bot | Flip en ✅ (suite aux fixes audit) |
| **K** | Msg URL posté par un bot | Log only |
| **M** | `Dossier indexé` n'est pas le DERNIER embed | un-archive → DELETE → re-POST → re-archive |

Le scan #logs est cap à 12000–15000 messages (≈1 semaine de logs).
La pmap Y.15 (`datas/arsenal_published_threads.json`) sert de fallback
quand l'embed n'est plus dans la fenêtre #logs : `build_dossier_from_pmap`
essaie `{plat}::cid` puis `::cid` puis `unknown::cid` (couvre les drops
historiques avant Y.18). Idempotent : tout fix vérifie l'état avant de
poster, donc relancer le script est sûr.

Usage :
```
python _claude_logs/audit_liens_channel.py             # dry-run récap
python _claude_logs/audit_liens_channel.py --apply     # exécute fixes
python _claude_logs/audit_liens_channel.py --report    # JSON détaillé
```

Cas non-fixables côté bot (limitations amont) :
- Tweets text-only (`No video could be found`) — yt-dlp ne supporte que les tweets vidéo.
- Threads.com URLs — yt-dlp suit le redirect `.net→.com` puis fail (extracteur Threads hardcodé sur `.net`). Le fix `dl_generic` convertit `.com→.net` mais yt-dlp re-redirige côté serveur.

### Race condition auto-sync vs pipeline (Phase Y.17)

**Bug observé** : drops postés dans `🔗・liens` parfois sans
`✅ Dossier indexé` dans le fil. Cause : race entre l'auto-sync
`tasks.loop(seconds=15)` du publisher et `step_sync` du pipeline live.
Quand l'auto-sync gagne, il publie le thread forum et poste l'embed
Dossier indexé dans `📋・logs` SANS `link_thread` — le fil reste
orphelin. Quand le pipeline appelle ensuite `_sync_task(only_source_id,
link_thread=thread)`, le `is_syncing=True` early-return (ou la source
SUCCESS dans le CSV) empêche le forward.

**Fix Y.17** :
- `_sync_task(wait_if_busy=False)` : nouveau param. Si True (utilisé
  par `step_sync` côté pipeline live, jamais par l'auto-sync), attend
  jusqu'à 5 min la fin du sync en cours au lieu de l'early-return.
- `_forward_dossier_to_fil(metadata, link_thread)` : helper qui forwarde
  l'embed `✅ Dossier indexé` au fil pour une source déjà publiée
  (lookup pmap → fetch_channel → post embed via `send_log`). Idempotent
  via `_link_thread_has_dossier(link_thread)`.
- Branchements dans les 2 paths skip de `_sync_task` :
  - Après `should_process=False` (CSV sync_status=SUCCESS).
  - Après pmap entry trouvée (Y.15 self-heal).
- `step_sync` côté `arsenal_pipeline` passe `wait_if_busy=link_thread is not None`.

### Y.18 — Plateforme fallback CSV pour parse_analysis

**Bug observé** : 6 entries pmap `::cid` (plateforme vide) pour des
drops X — inaccessibles via lookup `x::cid` côté retrofit ou audit.

**Cause** : `parse_analysis` lit `PLATEFORME — …` depuis le résumé.
Mais `summarize.py` ne génère ce header que pour IG_/TT_*.txt. Les
SRC_*.txt (X / YouTube / Reddit / Threads) n'ont pas de header
plateforme, et la fallback de `parse_analysis` ne reconnaît que les
préfixes `IG_/TT_` du nom de fichier. Pour SRC_, `metadata["platform"]`
reste `None` → `_published_key(None, sid)` = `f"::sid"`.

**Fix Y.18** : dans `_sync_task` après `parse_analysis`, si
`platform is None`, lookup CSV via `self.get_row_by_source_id(source_id)`
et utiliser `csv_row["plateforme"]`. Set aussi `metadata["platform"]`
pour le reste du flow.

**Migration one-shot** : script Python inline qui scanne
`datas/arsenal_published_threads.json`, pour chaque clé `::cid` lookup
le CSV pour la plateforme, renomme en `{plat.lower()}::cid`. 6 entries
migrées, 0 collision. Backup auto avec timestamp.

### Y.19 — Auto-clear weekly_throttled

**Bug observé** : user drop un lien vers 19h26, le pipeline répond
"⚠ Résumé sauté — quota atteint". Mais le user a ses tokens reset
depuis longtemps (live `weekly_pct < 5 %`). Cause : `state.weekly_throttled`
posé le 30/04 par la GUI, jamais clearé car la GUI n'auto-clear que
quand elle tourne en foreground.

**Fix Y.19** dans `arsenal_pipeline._check_quota_before_summarize` :
fetch la live `quota = fetch_usage()` AVANT le check du flag
persistant. Si `state.weekly_throttled and live_quota.weekly_pct <
state.weekly_threshold_pct` → clear le flag, save state, log info,
continue normalement. Si live check fail (cookie expired, network),
fallback sur le flag persistant comme avant.

Effet de bord : si le live quota est ≥ seuil, on POSE le flag persistant
(symétrique à la GUI) en plus de bloquer ce run. Cohérent.

### Y.20 — Catchup dedup étendu (download_timestamp + 6 plateformes)

**Bug observé** : à chaque restart bot, 8–9 drops re-enqueued en
catchup, dont des FAILED qui re-failent et ré-affichent ❌ sur les
messages user de `🔗・liens` (annulant les ✅ posés par l'audit).

**Causes multiples** :
1. `_catchup_scan` considère « déjà vu » seulement les rows SUCCESS
   → toutes les FAILED sont enqueued à chaque restart, repassent par
   le pipeline qui re-fail le download, re-poste ❌, re-pollue #logs.
2. `extract_content_id_from_url` ne gère que IG + TikTok. X / YouTube /
   Reddit / Threads tombent sur `cid=None` → la dédup `cid in known_ids`
   est False → enqueue systématique.
3. Quand la résolution TikTok short URL timeout (`SSL handshake operation
   timed out`), l'URL reste en forme courte `vm.tiktok.com/Xxxx` qui
   ne match pas la regex `tiktok.com/@user/video/N` → cid=None →
   enqueue.

**Fix Y.20** :
- Critère « déjà vu » : `download_timestamp` non vide, peu importe le
  status. Couvre SUCCESS + FAILED + tout autre.
- `extract_content_id_from_url` étendu aux 6 plateformes (X/YouTube/
  Reddit/Threads avec leurs regex respectives, plus `reels` ajouté à
  IG comme alias).
- Skip explicite si `cid is None` (log info, pas d'enqueue) — le user
  re-droppera plus tard si vraiment nouveau.
- Dédup-URL fallback : `known_urls` set en plus de `known_ids`. Couvre
  le cas où le CSV a la forme courte (rare) ou la forme longue d'une
  même URL.

Avec Y.20 chargé, restart bot → catchup `0 lien(s) enqueue`.
Le rattrapage explicite des FAILED se fait via `arsenal_retry_failed.py`
(qui a sa propre logique de retry avec `--limit`, `--platform`, etc.).

### Threads.com support (Phase Y.20)

Meta a unifié les domaines Threads sur `threads.com` mi-2025, mais
l'extracteur yt-dlp est resté sur `threads.net`. Conséquence : tout drop
en `.com` → yt-dlp suit le redirect `.net→.com`, puis "Unsupported URL"
sur le `.com`.

**Fix partiel** appliqué :
- Regex `threads\.(?:net|com)` dans :
  - `arsenal_pipeline.ALL_PLATFORM_PATTERNS` (line 101)
  - `_claude_logs/audit_liens_channel.py PLATFORM_PATTERNS`
  - `_claude_logs/retrofit_link_threads.py PLATFORM_PATTERNS`
- `dl_generic.detect_platform` reconnaît aussi `.com`.
- `dl_generic.download_one` convertit silencieusement `.com → .net`
  avant yt-dlp.

**Limitation amont** : yt-dlp suit le redirect côté serveur même quand
on lui passe `.net`. Tant que yt-dlp ne supporte pas `.com` upstream,
les Threads dropés ne pourront pas être téléchargés automatiquement.
gallery-dl 1.32 idem.

### Y.21 — Fallback gallery-dl + OCR auto pour X / Threads / Reddit

**Problème** : avant Y.21, `dl_generic.py` ne savait gérer que les drops
vidéo via yt-dlp. Tout post text-only ou image-only sur X / Threads /
Reddit échouait en CSV (`download_status=FAILED`,
`error_message="No video could be found in this tweet"` ou
`"Unsupported URL"`), ce qui annulait toute la chaîne pipeline.

**Trigger fallback** : après l'appel yt-dlp, si `not ok or not media_path`
ET plateforme ∈ {X, Threads, Reddit}, on appelle
`gallery_dl_fallback(url, platform, content_id)` qui :

1. Lance `python -m gallery_dl --dump-json --range 1-N` (sans
   téléchargement, juste la liste des opérations + métadonnées) avec un
   timeout de 90s. Threads bénéficie des cookies IG (même ownership
   Meta) ; X et Reddit pas besoin de cookies (gallery-dl gère son
   propre auth).
2. Parse le JSON : marker `2` = directory metadata (`content`, `user`,
   `date`), marker `3` = URL média + métadata (incluant `extension`).
3. Crée le dossier `01_raw_images/<PREFIX><id>/` via `cfg.post_dir()`
   (PLATFORM_DIR_PREFIXES = `IG_`/`X_`/`THREADS_`/`REDDIT_`/`YT_`/`TT_`).
4. Écrit le `content` du post dans `_post_text.txt` (UTF-8) si présent.
5. Télécharge chaque média via `urllib.request` direct dans
   `<dir>/NN.ext` (numérotation séquentielle, extension réelle conservée
   parmi `jpg/jpeg/png/webp/gif/heic` pour images et
   `mp4/mov/webm/mkv/m4v` pour vidéos).
6. CSV : `download_status=SUCCESS`,
   `download_mode=gallery_dl_<platform>`, `type ∈ {Image, Video, Mixed,
   Text}` selon le contenu, `filename=<dir name>` (pas un fichier
   unique).

**OCR pipeline** : `ocr_carousels.py` étendu :
- Scan multi-préfixes via `KNOWN_DIR_PREFIXES` dérivé de
  `PLATFORM_DIR_PREFIXES`.
- `_strip_known_prefix(name)` extrait le post_id en stripping le
  préfixe matchant le plus long.
- `_read_post_text(dir)` lit `_post_text.txt` si présent et le prepend
  comme bloc `[POST TEXT]\n<content>` AVANT les blocs `[SLIDE N — NN.jpg]`
  dans le `<id>_ocr.txt`. Permet aux tweets text-only (zéro image) de
  produire un transcript valide.
- Pré-filtre des dossiers déjà OCR-isés AVANT init easyocr GPU. Coût
  ~0s quand rien à faire (utile pour le step pipeline qui tourne sur
  chaque drop).

**Step pipeline** : `step_ocr()` ajoutée à `arsenal_pipeline.py`,
insérée entre `step_transcribe` et `step_summarize` dans
`run_pipeline`. Embed `📷 OCR images` posté dans le fil + `📋・logs`
SEULEMENT si l'OCR a vraiment OCR-isé quelque chose (heuristique :
`Succès: N` > 0 dans la stderr du script). Drop vidéo classique = step
silencieux.

**Pickup côté summarize** : `match_known_id("<id>_ocr", known_ids)` du
summarize.py reconnaît déjà le naming `<id>_ocr.txt` via
`startswith(kid + "_")`. Comme `image_idx` ne scanne que les dossiers
`IG_*` (les dossiers `X_*` ne sont pas indexés comme images), summarize
tombe naturellement dans le branch `transcript_path` (text-only) ce qui
est exactement ce qu'on veut pour `--use-claude-code` (CLI text-only).

**Test E2E** : smoke test sur `2050176446786940953` (tweet IATheYoker
historique FAILED) → 1 image téléchargée + 1769 chars de tweet text
captés + OCR généré en 8.3s. Bout-en-bout fonctionnel.

**`fix_text_tweets.py` déprécié** : stub d'arrêt qui pointe vers
`arsenal_retry_failed.py --platform X --apply` pour les rattrapages
historiques.

**Limitation persistante** : Threads.com côté yt-dlp + gallery-dl 1.32
suivent tous les deux le redirect `.net→.com` côté serveur. Pour les
posts Threads qui ont vraiment des images (pas du texte seul),
gallery-dl peut quand même fail upstream — à monitorer.

### Quota seuils — bypass via `_secrets/quota_state.json`

Pour outrepasser tous les checks quota côté bot et GUI, set
`session_threshold_pct=100` et `weekly_threshold_pct=100` (et
`weekly_throttled=false`) dans `_secrets/quota_state.json`. Y.19 +
Y.16 garantissent que le pipeline passe sans blocage.
