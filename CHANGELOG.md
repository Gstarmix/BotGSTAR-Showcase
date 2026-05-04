# Changelog BotGSTAR

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
versions par phases sémantiques.

Pour le pipeline correction côté COURS (Phase A → H), voir
`COURS/CHANGELOG.md`. Ce fichier documente les évolutions côté bot
Discord lui-même.

---

## Phase Y.23 — Médias X / Threads / Reddit attachés au thread forum (4 mai 2026)

Demande user après Y.21–Y.22 : « quand tu récupères une image ou vidéo
sur X et autres faut aussi le télécharger le poster dans le thread donc
tu vas devoir recommencer ».

### Bug

`arsenal_publisher.find_mixed_media_for_source(source_id)` cherchait
seulement dans `01_raw_images/IG_<id>/` (préfixe `IG_` hardcodé).
Conséquence : les médias téléchargés par le fallback gallery-dl Y.21
dans `01_raw_images/X_<id>/`, `01_raw_images/THREADS_<id>/`,
`01_raw_images/REDDIT_<id>/` étaient bien sur disque mais n'étaient
PAS postés en pièces jointes au thread forum lors de la sync.

### Fix Y.23

`find_mixed_media_for_source` scanne désormais TOUS les préfixes
plateforme connus (`PLATFORM_DIR_PREFIXES` de `arsenal_config`) :
IG_/X_/THREADS_/REDDIT_/YT_/TT_. Le premier dir qui existe et contient
des médias gagne. Mutuellement exclusifs car les IDs sont uniques par
plateforme. Filtre les fichiers `_*` (exclut `_post_text.txt` et
métadonnées Y.21).

Ordre de scan : IG_ d'abord pour ne pas casser le path Instagram
pré-Y.21.

### Re-publication des 7 X drops historiques

7 X drops avaient été synced sans média entre Y.21 et Y.23 (auto-sync
15s a gagné la course pendant le développement). Script one-shot
`_claude_logs/repush_x_with_media.py` :
- Supprime les 7 threads Discord via REST API.
- Retire les entrées pmap correspondantes (`x::<id>`).
- Reset `sync_status=PENDING` + `sync_timestamp` vide pour ces 7 IDs.
- L'auto-sync 15s les republie proprement avec médias attachés.

Les autres X drops futurs et les drops live passent désormais par le
flow correct dès le départ.

---

## Phase Y.22 — Dossier indexé en fin de fil + fix retry CSV (4 mai 2026)

Demande user après Y.21 : « pour les prochains drops fais en sorte que
l'embed `✅ Dossier indexé` apparaisse toujours à la fin et pas entre
`⚙️ Pipeline | ✅ Résumé Claude` et `⚙️ Pipeline | ✅ Publication Discord`,
mais après `⚙️ Pipeline | Pipeline terminé` ».

### Fix ordering

`_sync_task` du publisher a un nouveau param `defer_dossier_forwards:
bool = False`. Quand True (passé par `step_sync` côté pipeline live dès
qu'un `link_thread` existe) :
- Le post `📋・logs` du `Dossier indexé` reste immédiat (pas de
  régression côté logs).
- Le post dans le fil `🔗・liens` est mis en queue
  `self._deferred_thread_dossier_posts` au lieu d'être envoyé pendant
  l'étape Sync.
- `arsenal_pipeline.run_pipeline` appelle
  `publisher.flush_deferred_dossier_to_fil()` APRÈS l'embed
  `Pipeline terminé` → le Dossier indexé apparaît tout en bas du fil.

Couvre aussi les paths skip de `_sync_task` (race auto-sync vs pipeline,
Y.15 self-heal) qui appellent `_forward_dossier_to_fil` — le param
`defer=True` y est propagé pareil.

### Fixes secondaires (apparus pendant le retry Y.21)

**`already_in_csv` skip uniquement sur SUCCESS** (`dl_generic.py`) :
avant Y.22, un retry était short-circuité dès qu'une ligne (plat, id)
existait, même en FAILED → `arsenal_retry_failed.py --apply --platform X`
ne faisait rien. Désormais skip uniquement si une ligne SUCCESS existe.
Cohérent avec le besoin de retry et avec le comportement quand le user
re-drop une URL après une panne transitoire.

**`csv_normalize` colonnes download-related** : la dédup intelligente
faisait `choose_better_value` (= chaîne la plus longue) sur les colonnes
download. Conséquence : entre `video_direct_failed` (19 chars, ancienne
ligne FAILED) et `gallery_dl_x` (12 chars, nouvelle SUCCESS via Y.21),
c'est l'ancienne valeur qui gagnait. Désormais le bloc DOWNLOAD_COLS
(mode/error/filename/type/etc.) prend la valeur de la ligne au
`download_timestamp` le plus récent. `error_message` est aussi explicitement
vidée si le statut final est SUCCESS (sinon une stderr yt-dlp héritée
d'un ancien FAILED restait dans le row d'un retry réussi).

### Test sur backlog Y.21

Retry des 16 X drops historiquement FAILED → 9 récupérés via le
fallback gallery-dl (Y.21), 7 fail sur "No video could be found" + 1
timeout x.com. Les 9 récupérés ont produit `01_raw_images/X_<id>/` avec
images + `_post_text.txt`, OCR a généré 8 fichiers `<id>_ocr.txt`,
summarize batch est en cours sur ces 8 IDs.

---

## Phase Y.21 — Images X / Threads / Reddit + OCR auto (4 mai 2026)

Avant Y.21, le pipeline live ne savait gérer qu'**un seul média par drop**
côté X/Threads/Reddit : la vidéo téléchargée par yt-dlp. Tout le reste
échouait :
- Tweets text-only (`No video could be found in this tweet`) → script
  one-shot manuel `fix_text_tweets.py` après chaque cas.
- Tweets avec une ou plusieurs images sans vidéo → idem, FAILED en CSV
  + ❌ sur le drop côté `🔗・liens`.
- Posts Threads avec photos → idem (couplage à la limite amont
  `threads.com → threads.net` qui aggrave).
- Galeries Reddit → idem.

Conséquence : sur 76 drops politiques de fin avril, ~13 tweets X
text/image étaient bloqués FAILED en attente de re-traitement manuel.

### Solution

**1. Fallback gallery-dl dans `dl_generic.py`** :
- Nouvelle fonction `gallery_dl_fallback(url, platform, content_id)`
  appelée quand yt-dlp ne produit pas de média ET que la plateforme est
  X / Threads / Reddit.
- Lance `gallery-dl --dump-json --range 1-N` pour récupérer la liste de
  médias + métadonnées (content text, user, date) sans rien télécharger.
- Téléchargement séquentiel via `urllib` direct dans
  `01_raw_images/<PREFIX><id>/NN.ext` (préfixes par plateforme :
  `X_`, `THREADS_`, `REDDIT_`, `IG_` legacy).
- Texte du post écrit dans `_post_text.txt` du même dossier.
- CSV mis à jour : `download_status=SUCCESS`,
  `download_mode=gallery_dl_<platform>`, `type=Image|Video|Mixed|Text`
  selon ce qui a été récupéré.

**2. Élargissement `ocr_carousels.py`** :
- Scan multi-préfixes via `KNOWN_DIR_PREFIXES` (dérivé de
  `PLATFORM_DIR_PREFIXES` dans `arsenal_config.py`).
- `_post_text.txt` lu et prepend en bloc `[POST TEXT]` avant les blocs
  `[SLIDE N — NN.jpg]`. Permet aux tweets text-only (zéro image) de
  produire quand même un `<id>_ocr.txt` consommable par summarize.
- Pré-filtre des dossiers déjà OCR-isés AVANT l'init easyocr GPU (~5s
  économisées quand rien à faire — utile pour le step pipeline qui
  tourne à chaque drop).

**3. Step OCR dans `arsenal_pipeline.py`** :
- Nouvelle `step_ocr()` qui invoque `ocr_carousels.py` (idempotent).
- Insérée entre `step_transcribe` et `step_summarize` dans
  `run_pipeline`.
- Embed `📷 OCR images` posté dans le fil + `📋・logs` SEULEMENT si
  l'OCR a vraiment OCR-isé quelque chose (heuristique : `Succès: N` > 0
  dans la stderr du script). Un drop vidéo classique passe en silence.

**4. `fix_text_tweets.py` déprécié** : le script one-shot a été remplacé
par un stub qui pointe vers `arsenal_retry_failed.py --platform X
--apply` pour les rattrapages historiques.

### Fichiers touchés

- `Arsenal_Arguments/arsenal_config.py` : `PLATFORM_DIR_PREFIXES` map +
  helper `cfg.post_dir(platform, post_id)`.
- `Arsenal_Arguments/dl_generic.py` : `gallery_dl_fallback()` (~150
  lignes) + branchement dans `download_one()`.
- `Arsenal_Arguments/ocr_carousels.py` : `_strip_known_prefix()`,
  `_read_post_text()`, `[POST TEXT]` block + pré-filtre easyocr.
- `extensions/arsenal_pipeline.py` : `step_ocr()` + branchement dans
  `run_pipeline` après transcribe.
- `Arsenal_Arguments/_claude_logs/fix_text_tweets.py` : stub deprecation.

### Test E2E

Smoke test live sur `2050176446786940953` (tweet IATheYoker bloqué
historique) → 1 image téléchargée + 1769 chars de tweet text capturés +
OCR généré avec `[POST TEXT]` + `[SLIDE 1 — 01.jpg]` en 8.3s.

### Limitation persistante

Threads.com côté yt-dlp + gallery-dl : les deux suivent le redirect
`.net→.com` côté serveur, gallery-dl 1.32 fail aussi. Couvert par
`gallery_dl_fallback` quand le post a juste du texte (pas de média), mais
pour les posts Threads avec images réelles seulement, gallery-dl peut
échouer aussi — à monitorer dans les versions futures.

---

## Phase Y.17 → Y.20 — Audit complet `🔗・liens` + race fixes (3 mai 2026)

Quatre fixes structurels après un audit exhaustif du salon `🔗・liens` qui
a révélé que ~30 fils sur 68 avaient des incohérences (Dossier indexé
manquant, embeds pipeline absents, Dossier indexé pas en fin, ❌
résiduelles). Chaque incohérence a une cause racine dans le code, les 4
correctifs Y.17–Y.20 ferment les boucles. **Bilan** : 51 fils backfillés,
14 réorganisés, 21 réactions flippées, plus aucun re-enqueueing
parasite des FAILED au restart.

### Y.20 — Catchup dedup étendu (download_timestamp + 6 plateformes)

**Bugs cumulés** observés au boot bot :
1. À chaque restart, le `_catchup_scan` ré-enqueueait 8–9 drops dont
   des FAILED → re-failent → re-postent ❌ sur les messages user de
   `🔗・liens` (annulant les ✅ posées manuellement par l'audit) +
   re-polluent `📋・logs` avec des embeds Pipeline | Download échoués.
2. Idem pour les drops dont le cid n'est pas extractible (résolution
   TikTok short URL timeout SSL → URL reste en forme courte).

**Causes** :
- Critère « déjà vu » trop strict : `download_status == SUCCESS` seul.
  Les FAILED échappent au filtre et reviennent à chaque scan.
- `extract_content_id_from_url` ne gérait que IG + TikTok. Pour X /
  YouTube / Reddit / Threads, retournait `None` → la dédup `cid in
  known_ids` est toujours False → enqueue systématique.
- En cas de timeout SSL sur `vm.tiktok.com`, l'URL reste courte, ne
  match pas la regex `tiktok.com/@user/video/N`, cid=None, enqueue.

**Fix Y.20** dans `arsenal_pipeline._catchup_scan` :
- `seen = df[df["download_timestamp"].astype(str).str.strip() != ""]`
  → couvre SUCCESS + FAILED + tout autre état avec un timestamp.
  Le rattrapage explicite des FAILED se fait via
  `arsenal_retry_failed.py` (qui a `--limit`, `--platform`, etc.).
- `extract_content_id_from_url` étendu aux 6 plateformes (X/YouTube/
  Reddit/Threads avec leurs regex respectives + alias `reels` pour IG).
- Skip explicite si `cid is None` (log info, pas d'enqueue).
- `known_urls` set en fallback de `known_ids` pour couvrir les rares
  cas où la dédup-cid échoue mais l'URL exacte est en CSV.

**Avant Y.20** : restart → "Rattrapage : 50 messages scannés, 9 lien(s)
enqueue".
**Après Y.20** : restart → "Rattrapage : 50 messages scannés, 0
lien(s) enqueue".

### Y.19 — Auto-clear `weekly_throttled` quand quota redescend

**Bug observé** : user drop un lien à 19h26 (`DX4OaXxMGe1`), le
pipeline répond "⚠ Résumé sauté — quota atteint". Mais le user a ses
tokens reset depuis longtemps (live `weekly_pct < 5 %`). Le pipeline
bloque alors que le quota est OK.

**Cause** : `state.weekly_throttled` est un flag persistant posé par
la GUI summarize ou le pipeline lui-même quand le seuil est dépassé.
La GUI auto-clear le flag quand elle tourne en foreground et que
`weekly_pct < seuil` (ligne 884). Mais quand la GUI est fermée, le
flag reste sticky tant que le user n'a pas cliqué « Reset throttle
hebdo ». `_check_quota_before_summarize` early-return sur le flag
SANS vérifier le live quota → blocage indéfini.

**Fix Y.19** dans `arsenal_pipeline._check_quota_before_summarize` :
fetch la live `quota = fetch_usage()` AVANT le check du flag
persistant. Auto-clear si `state.weekly_throttled and
live_quota.weekly_pct < state.weekly_threshold_pct` (save state, log
info). Si live check fail (cookie expired, network), fallback sur le
flag persistant comme avant. Effet de bord : si live ≥ seuil, on POSE
le flag persistant (symétrique à la GUI) en plus de bloquer ce run.

Le pipeline n'attend plus que le user ouvre la GUI pour redémarrer.

### Y.18 — Plateforme fallback CSV pour parse_analysis (SRC_*.txt)

**Bug observé** : 6 entries dans `datas/arsenal_published_threads.json`
avec une clé `::cid` à plateforme vide pour des drops X. Le retrofit
ou l'audit cherche par `x::cid` → miss → impossible de forwarder le
Dossier indexé au fil.

**Cause** : `parse_analysis` lit `^PLATEFORME — (.+)$` dans le résumé.
Mais `summarize.py` ne génère ce header que pour les fichiers
`IG_*.txt` et `TT_*.txt`. Les `SRC_*.txt` (X / YouTube / Reddit /
Threads) n'ont pas ce header. La fallback de `parse_analysis` ne
reconnaît que les préfixes `IG_/TT_` du nom de fichier. Pour SRC_,
`metadata["platform"]` reste `None` → `_published_key(None, sid)` =
`f"::sid"` → écrit dans la pmap avec une clé inexploitable.

**Fix Y.18** dans `arsenal_publisher._sync_task` après `parse_analysis` :
si `platform is None`, lookup CSV via `self.get_row_by_source_id(
source_id)` et utiliser `csv_row["plateforme"]`. Set aussi
`metadata["platform"]` pour la suite du flow.

**Migration one-shot** : script Python inline qui scanne
`arsenal_published_threads.json`, pour chaque clé `::cid` lookup le
CSV pour la plateforme, renomme en `{plat.lower()}::cid`. Backup auto
avec timestamp avant écriture. **6 entries migrées, 0 collision.**

Le retrofit `_claude_logs/retrofit_link_threads.py` v1.4 et le script
`audit_liens_channel.py` ont aussi un fallback de lookup pmap qui
essaie `{plat}::cid` puis `::cid` puis `unknown::cid` pour couvrir les
drops historiques avant la migration.

### Y.17 — Race condition auto-sync vs pipeline (link_thread orphelin)

**Bug observé** : drops postés dans `🔗・liens` parfois sans
`✅ Dossier indexé` dans le fil, alors que l'embed est bien dans
`📋・logs`. Discrepance visible sur ~25 fils sur 65.

**Cause** : race entre le `tasks.loop(seconds=15)` `_auto_sync_loop`
du publisher et `step_sync` du pipeline live.
- Auto-sync : appelle `_sync_task(silent_if_no_work=True)` SANS
  `link_thread`. Quand il publie, l'embed Dossier indexé va dans
  `📋・logs` uniquement.
- Pipeline live : appelle `_sync_task(only_source_id=cid,
  link_thread=fil)` APRÈS summarize. Mais si l'auto-sync 15s a déjà
  publié entre-temps (CSV `summary=SUCCESS, sync=PENDING` détecté à
  son tick), `_sync_task` du pipeline tombe sur :
  - Soit `is_syncing=True` early-return (auto-sync encore en cours).
  - Soit `should_process=False` skip (CSV déjà SUCCESS).
  - Soit pmap entry trouvée skip (Y.15).
  Dans les 3 cas, le `link_thread` est jeté à la poubelle, le fil
  reste sans Dossier indexé.

**Fix Y.17** dans `arsenal_publisher` :
- `_sync_task(wait_if_busy=False)` : nouveau param. Si True, attend
  jusqu'à 5 min la fin du sync en cours (poll `is_syncing` toutes
  les 1s) au lieu de l'early-return. Utilisé par `step_sync` du
  pipeline live, jamais par l'auto-sync 15s.
- `_link_thread_has_dossier(link_thread) -> bool` : helper qui
  fetch les 50 derniers messages du fil et cherche un embed
  `✅ Dossier indexé`. Évite les double-posts.
- `_forward_dossier_to_fil(metadata, link_thread)` : helper qui
  forwarde l'embed Dossier indexé au fil pour une source déjà
  publiée (pmap lookup → fetch_channel → post embed via
  `send_log(link_thread=...)`). Idempotent.
- Branchements dans les 2 paths skip de `_sync_task` :
  - Après `should_process=False` : si pipeline live (`only_source_id +
    link_thread` matchent), `await self._forward_dossier_to_fil(
    metadata, link_thread)` puis `continue`.
  - Après pmap entry trouvée (Y.15 self-heal) : idem.

**Fix côté pipeline** (`arsenal_pipeline.step_sync`) : passe
`wait_if_busy=link_thread is not None` au `_sync_task`. Plus d'early-
return sur `is_syncing` côté caller.

Audit complet de `🔗・liens` ensuite : 25 fils ont reçu leur Dossier
indexé manquant via le retrofit + `audit_liens_channel.py` (voir
script).

### Audit `🔗・liens` — `audit_liens_channel.py`

Nouveau script standalone `Arsenal_Arguments/_claude_logs/
audit_liens_channel.py` qui scanne EXHAUSTIVEMENT le salon `🔗・liens`
(tous les messages user, pas juste les 50 derniers du `_catchup_scan`)
et catégorise les incohérences par drop. 11 catégories (A→Z), 9
fixables avec `--apply`. Voir `BotGSTAR/CLAUDE.md` §13 pour le tableau
complet.

Particularités :
- Scan #logs cap 12000–15000 messages (≈1 semaine). Cap configurable
  via `LOGS_SCAN_CAP`.
- pmap fallback (Y.15) quand l'embed n'est plus dans la fenêtre #logs.
  Reconstruit un embed minimal `✅ Dossier indexé` pointant vers le
  thread forum existant.
- Catégorie M (Dossier indexé pas en dernier) gère le cas où mes
  fixes audit (B + C/D) ont posté Pipeline terminé après Dossier
  indexé : un-archive → DELETE → re-POST → re-archive (Discord refuse
  DELETE dans un fil archivé, 400).
- Catégorie J (réaction ❌ → ✅) : DELETE puis PUT sur l'API reactions
  avec emoji URL-encoded.
- Idempotent : tout fix vérifie l'état avant d'agir.

### Threads.com support (partiel — limitation amont)

Meta a unifié les domaines Threads sur `threads.com` mi-2025, mais
yt-dlp 2026.03.17 (stable) et 2026.04.30 (nightly) gardent leur
extracteur sur `threads.net`. Drop d'une URL `.com` → yt-dlp suit le
redirect HTTP `.net→.com` côté serveur, puis "Unsupported URL" sur le
`.com`.

**Fix partiel** appliqué côté bot :
- Regex `threads\.(?:net|com)` dans `arsenal_pipeline.
  ALL_PLATFORM_PATTERNS`, `audit_liens_channel.py PLATFORM_PATTERNS`,
  `retrofit_link_threads.py PLATFORM_PATTERNS`.
- `dl_generic.detect_platform` reconnaît aussi `.com`.
- `dl_generic.download_one` convertit silencieusement `.com → .net`
  avant yt-dlp.

**Limitation amont** : yt-dlp suit le redirect même quand on passe
`.net`. Tant que yt-dlp ne supporte pas `.com` upstream, les Threads
en `.com` ne sont pas téléchargeables automatiquement. gallery-dl 1.32
non plus (Unsupported URL). À surveiller dans les versions futures.

---

## Phase Y — Choix moteur partagé + GUI scrollable + sync silencieuse (30 avril 2026)

Trois ajustements UX/wiring après usage de la GUI summarize en prod.

### Fix Y.16 — Quota Pro Max ne bloque plus quand engine = api

**Bug observé** : user veut traiter ses 25 résumés PENDING via l'API
Anthropic (engine=api), mais le throttle hebdo Pro Max est actif et
bloque le pipeline + le bouton Lancer de la GUI.

**Cause** : le check quota Pro Max (claude_usage `weekly_throttled`) est
appliqué inconditionnellement, alors qu'il ne concerne **que le CLI
subscription Claude Code**. L'API Anthropic est facturée séparément
(crédits API) et n'a aucun lien avec le quota hebdo claude.ai.

**Fix** :
- `arsenal_pipeline._check_quota_before_summarize()` : early-return
  `can_proceed=True` avec reason `engine_api` si `load_engine_pref() == 'api'`,
  AVANT tout check claude_usage.
- `summarize_gui._can_spawn()` : early-return `(True, "")` si
  `var_engine.get() == 'api'`.
- `summarize_gui._set_state()` : le bouton ▶ Lancer reste activé même
  si `weekly_throttled` quand engine=api (la check de désactivation
  ignore le throttle si engine_is_api).

**Conséquence** : avec engine=api, l'user peut spawn summarize via le
pipeline `🔗・liens` ou la GUI sans aucun blocage Pro Max. Si engine
revient à `claude_code`, les checks reprennent leur cours normal.

### Y.15 — JSON map source_id → thread_id pour stopper les dupes (race condition CSV)

**Bug observé** : 6 threads `TotalEnergies` créés dans `économie-et-social`
pour le même drop `DXwTXqbDZXj`. Cause profonde : 6 rows CSV existaient
pour le même source_id (résidu de manips manuelles), summarize les a
toutes traitées (overwrite du même fichier `IG_DXwTXqbDZXj.txt`), Claude
a généré 6 titres légèrement différents au fil des runs, et l'auto-sync
(15s) a publié à chaque nouvelle version du fichier — 6 titres
différents → matcher Y.6 ne match pas → 6 threads créés.

Sous-cause : **race condition CSV entre summarize.py et arsenal_publisher**.
Les deux processus écrivent `suivi_global.csv` en parallèle, sans lock.
Le publisher set sync_status=SUCCESS pour le row, summarize re-charge le
CSV (peut lire l'ancien état avec sync=PENDING) puis re-sauvegarde,
écrasant le SUCCESS du publisher → cycle de re-publication.

**Fix Y.15** : map JSON persistante `datas/arsenal_published_threads.json`
indépendante du CSV, gérée par le publisher. Format :
```json
{"<platform>::<source_id>": {"thread_id": "...", "forum_id": "...",
                              "title": "...", "created_at": "..."}}
```
- Helpers `_load_published_threads()`, `_save_published_threads(data)`,
  `_published_key(platform, source_id)` au top-level du module.
- `_sync_task` AVANT `should_process` : check map → si entrée existe
  ET le thread Discord est encore reachable (`bot.fetch_channel`) →
  set sync=SUCCESS + skip. Si fetch retourne 404 (thread supprimé
  manuellement) → retire de la map et continue le flux normal.
- Branch `find_existing_thread_by_names` (self-heal) : ajoute aussi à
  la map quand un thread existant est trouvé par titre.
- Branch création nouvelle : enregistre dans la map juste après
  `set_sync_status(SUCCESS)`. Recharge la map avant écriture pour
  éviter les écrasements concurrents.

**Bootstrap** : script standalone qui scanne les 1064 threads existants
dans ANALYSES POLITIQUES (active + archived) et fetch leur starter
message (`GET /channels/{thread_id}/messages/{thread_id}` car forum
threads ont starter.id == thread.id), extrait `🆔 **ID** — \`<sid>\``
+ `🏷️ **Plateforme** — <plat>` du contenu. **1059 entries mappées**
(5 skip = threads sans starter conforme).

**Cleanup hors commit** : 5 threads `TotalEnergies` dupes supprimés
(garde le plus ancien `Manon Aubry dénonce les superprofits…`),
`csv_normalize` dédupé les 6 rows CSV en 1.

### Y.14 — Embed procédure manuelle quand anti-bot détecté

**Demande user** : "quand y'a des mesures anti bot comme ça faut préciser
dans les embeds et logs et aussi préciser dans l'embed ou créer un autre
embed la procédure pour le déposer ou et comment créer le dossier etc."

Cas réel : le post Insta `DXwTXqbDZXj` (TotalEnergies par Manon Aubry,
score 17/20) bloqué par anti-bot Instagram. Visible dans le navigateur
de l'user, refusé pour yt-dlp/gallery-dl. L'embed `❌ Download` est
opaque : ni explication, ni voie de récup.

**Fix** : nouvelle méthode `detect_anti_bot_pattern(stderr, platform)`
dans `arsenal_pipeline.py` qui détecte 3 cas :
- **Instagram anti-bot** : patterns `redirect to home`, `redirect to login`,
  `HTTP Error 404`, `Page Not Found`, `échec malgré fallback`. Procédure
  carrousel image (création dossier `01_raw_images/IG_<ID>/` + nommage
  `01.jpg, 02.jpg…`) ET reel vidéo (`01_raw_videos/<ID>_<user>_<date>.mp4`).
- **TikTok IP block per-post** : `ip address is blocked`. Procédure
  attente / VPN / download manuel.
- **DNS / network transient** : `could not resolve host`, `transporterror`.
  Procédure : re-drop dans 5 min.

`run_pipeline` pose un embed orange après le `❌ Download` quand un de
ces patterns est détecté. Embed posté dans `📋・logs` ET dans le fil
du drop (Y.9). Donne immédiatement à l'user le mode d'emploi pour
récupérer le contenu sans avoir à demander.

**Cas vérifié en prod** : DXwTXqbDZXj récupéré manuellement (8 images
JPEG 1080×1350 déposées par user dans `01_raw_images/IG_DXwTXqbDZXj/`),
OCR easyocr → résumé Claude Code CLI → publication forum
`économie-et-social` ("TotalEnergies, profiteur de guerre : 5 Md€ au
T1 selon Manon Aubry", 17/20). Fil dans `🔗・liens` mis à jour avec
embeds verts post-récup + Dossier indexé. Réaction swap ❌→✅.

### Y.11 / Y.12 / Y.13 — Embed Dossier indexé dans le fil + auto-archive + no-ping

Trois améliorations sur la feature fil par drop (Y.9), suite à feedback user.

**Y.11 — Forward "Dossier indexé" dans le fil** :
- L'embed `✅ Dossier indexé` (Note/Forum/Média/Titre) qui sortait
  uniquement dans `📋・logs` après publication apparaît maintenant aussi
  dans le fil du drop. Cohérence avec les autres embeds Pipeline.
- `arsenal_publisher.send_log()` reçoit param `link_thread`.
- `_sync_task()` reçoit param `link_thread`. Quand `only_source_id`
  matche le source en cours de sync, l'embed est dupliqué dans
  `link_thread`.
- `arsenal_pipeline.step_sync()` reçoit `link_thread` et le propage.
- `run_pipeline()` passe le `thread` du worker comme `link_thread` à
  `step_sync`.

**Y.12 — Auto-archive du fil à la fin du pipeline** :
- Bloc `finally` de `run_pipeline` : si `thread is not None`,
  `await thread.edit(archived=True)`. Le fil reste accessible (clic
  sur l'icône fil du message d'origine) mais n'encombre plus la liste
  des fils actifs de `🔗・liens`.
- Aussi : `auto_archive_duration` du fil descendu de 4320 (3j) à 60
  (1h) — cohérent avec Y.12, et au cas où Y.12 échoue, Discord
  finit par archiver de toute façon.
- Si quelqu'un poste dans le fil archivé, Discord le ré-ouvre
  automatiquement — pas de perte de fonctionnalité.

**Y.13 — Pas de ping pour l'auteur du drop** :
- Quand le bot crée un fil sur le message d'un user, Discord
  auto-follow l'auteur → notif par embed posté (= 5-10 pings par
  drop). Très dérangeant.
- Fix : `await thread.remove_user(message.author)` juste après
  création. L'auteur reste dans la liste "membres du fil" via le
  message d'origine mais n'est plus auto-follow → silence radio.
- Le ping initial du système message thread_created est déjà esquivé
  par Y.10 (auto-delete sys msg).

### Y.10 — Auto-delete des messages système "X a commencé un fil" dans `🔗・liens`

Conséquence directe de Y.9 : Discord poste automatiquement un message
système (`MessageType.thread_created`, type=18) dans le salon parent à
chaque création de fil, du genre *"Gaylord Test a commencé un fil :
📱 Instagram · DXuQxSOCMxj"*. Pollue `🔗・liens` qui devient illisible
quand on multiplie les drops.

Fix dans `arsenal_pipeline.on_message` : early-return + `await
message.delete()` pour `message.type == MessageType.thread_created`
quand `message.channel.id == LISTEN_CHANNEL_ID`. Le fil reste
parfaitement accessible côté UI Discord (clic sur l'icône fil du
message d'origine), juste sans le message système redondant.

Cleanup hors commit : 2 messages système existants supprimés via API.

### Y.9 — Fil par drop dans `🔗・liens` + retrofit historique

**Demande user** : `📋・logs` mélange beaucoup d'embeds (sync Arsenal,
RSS politique, RSS tech, summarize, auto-archive, pipeline) → impossible
de retrouver facilement les étapes d'un drop spécifique.

**Solution** : chaque drop dans `🔗・liens` ouvre maintenant un **fil
attaché** au message (`message.create_thread()`, auto-archive 3j). Tous
les embeds Pipeline qui vont dans `📋・logs` sont **aussi postés dans
le fil**. Info en double mais accessible par lien.

**Code (`arsenal_pipeline.py`)** :
- `send_log()` reçoit un nouveau param `thread: Optional[discord.Thread]`.
  Si fourni, l'embed est dupliqué dans le fil (try/except silencieux).
- `run_pipeline()` reçoit aussi `thread` et le propage à toutes ses
  calls `send_log` (Démarrage, chaque étape, Pipeline terminé, erreur).
- `_worker()` (queue consumer) crée le fil sur le `message` d'origine
  juste avant d'appeler `run_pipeline` :
  ```python
  thread_name = f"📱 {platform.title()} · {content_id[:60]}"[:100]
  thread = await message.create_thread(name=..., auto_archive_duration=4320)
  ```
  Try/except autour pour rester robuste si perms manquantes ou fil
  déjà existant — dans ce cas pipeline tourne sans fil, embeds vont
  juste dans `📋・logs` comme avant.

**Retrofit historique** : script standalone
`Arsenal_Arguments/_claude_logs/retrofit_link_threads.py` qui :
1. Scanne `📋・logs` (jusqu'à 5100 msgs) pour les embeds Pipeline,
   regroupe par `content_id` (extrait depuis l'URL dans l'embed
   "Démarrage").
2. Scanne `🔗・liens` pour les messages utilisateur avec URL.
3. Pour chaque message sans fil existant et avec un run pipeline trouvé :
   crée le fil + re-poste les embeds chronologiquement.

Mode `--dry-run` pour audit avant exécution. Rate-limit safety : 0.4 s
entre PATCHs, retry sur 429.

**Cas d'usage** : 4 drops historiques observés (`DXumw9HDMYW`,
`DXuQxSOCMxj`, `DXwTXqbDZXj`, `DXjm3rEjJub`). 2 retrofittés (chacun
avec 6 embeds), 1 a déjà un fil créé par la feature live au restart
bot, 1 trop ancien (embeds dans le `#logs` Veille pré-migration W).

**Limitation connue** : la swap automatique des réactions ❌→✅ après
retraitement manuel n'est pas implémentée (cf TODO Y.10). Pour l'instant,
si un drop ❌ est retraité avec succès, je swap manuellement la réaction.

### Fix Y.8 — Timeout dynamique sur step_transcribe (long Reels VP9)

**Bug** : drop user `DXuQxSOCMxj` (Instagram Reel de 4min17s en codec
VP9) → pipeline échoue à l'étape Transcription Whisper après 624 s.
Cause : `TIMEOUTS["transcribe"] = 600` (10 min fixes) dans
`arsenal_pipeline.py`. Sur RTX 2060 + int8_float16 + VAD, Whisper prend
~2-3× la durée audio en H.264 et davantage en VP9 (libav VP9 plus lent).
Pour 257 s de VP9, on est à ~10-13 min effectif → cut juste avant la fin.

**Fix** : nouveau helper `_probe_max_video_duration_seconds()` qui scanne
`01_raw_videos/` pour les vidéos sans transcription correspondante,
extrait la durée via `ffprobe`, retourne le max. `step_transcribe`
calcule un timeout dynamique = `max(600, min(4 × max_dur, 3600))` :
plancher 10 min, plafond 1h, marge ×4 (couvre VP9 + petit buffer
sécurité libav). Log info à chaque calcul pour traçabilité.

Applicable uniquement à la pipeline live (drops `🔗・liens`). La
transcription batch via `arsenal_transcribe.ps1` ou `whisper_supervisor.py`
n'a pas de timeout (peut tourner des heures sans souci).

### Y.7 — Durcissement prompt summarize : liste fermée des 14 thèmes

Le `SYSTEM_PROMPT` de `summarize.py` (lignes 119-145) listait les 14
catégories thématiques mais sans contrainte explicite : Claude promouvait
parfois un sous-thème entre parenthèses en thème général, ou faisait des
variations orthographiques (`Social` vs `Société`). Cause des 4 forums
orphelins observés Phase Y.6.

**Fix** : ajout d'une RÈGLE STRICTE en tête de la section "Thématiques de
classification" qui :
- Impose les 14 libellés exacts (recopier au mot près, accents inclus)
  comme valeurs autorisées pour `[Thème Général]`.
- Précise que les éléments entre parenthèses sont des **exemples** de
  sous-thèmes pour `[Thème Spécifique]`, jamais comme thème général.
- Liste 6 exemples de classifications INTERDITES (avec → la version
  correcte) basés sur les dérives observées en prod : `Éducation` →
  `Culture et Éducation`, `Société et Éducation` → `Culture et Éducation`,
  `Social et Médias` → `Société et Médias`, `Débats et Rhétorique` →
  `Politique Française > Débats et Rhétorique`, `Médias` →
  `Société et Médias`, `Économie` → `Économie et Social`.
- Indique `Catégorie Libre` comme fallback de dernier recours.

**Défense en profondeur** : combiné avec le whitelist + alias map de
Phase Y.6 dans `arsenal_publisher._normalize_forum_slug`, on a deux
filets de sécurité — (a) Claude est moins susceptible de dévier grâce
au prompt strict, (b) si une dérive arrive, l'alias map la route vers
le forum canonique. Plus jamais de forum orphelin en prod.

Note : la nouvelle version du prompt ne prend effet que pour les
**futures** générations de résumés (pas rétroactif). Les résumés déjà
sur disque gardent leur classification originale, mais le whitelist
au sync les route correctement.

### Y.6 — Reclassif 4 threads orphelins + whitelist forums anti-orphelins

**Audit user** : sur 18 forums dans `ANALYSES POLITIQUES`, **4 forums
orphelins à 1 thread chacun** créés par variations de classification
Claude (au lieu de matcher les 12 forums voulus) :

| Forum orphelin | Thread (1) | Reclassif vers |
|---|---|---|
| `débats-et-rhétorique` | "Réponse chroniqueur TV productivité" (`IG_DV5rJ8rjOve`) | `société-et-médias` |
| `éducation` | "Boyard réforme 9h-15h" (`TT_7593427650119519510`) | `culture-et-éducation` |
| `social-et-médias` | "Débat priorités communautaires" (`TT_7628674155738565910`) | `société-et-médias` |
| `société-et-éducation` | "Universités sous-financement" (`SRC_aLAQC_em4dw`) | `culture-et-éducation` |

**Migration manuelle** : édit ligne `**Classification**` du résumé,
reset CSV `sync_status=PENDING`, suppression thread orphelin, auto-sync
(15s) recrée dans le bon forum, suppression forum orphelin vide.
Backup CSV `_backups/suivi_global_pre_y6_*.csv`. État final : **14
forums** (12 thématiques voulus + `catégorie-libre` fallback +
`campagne-2027` thématique cohérent à 6 threads).

**Garde-fou parser (`arsenal_publisher.py`)** : nouveau helper
`_normalize_forum_slug(raw_slug)` appelé dans `parse_analysis` après
slugification. Whitelist `CANONICAL_FORUMS` (14 entrées) + alias map
`CLASSIFICATION_ALIASES` (~35 variantes : `éducation` →
`culture-et-éducation`, `débats-et-rhétorique` → `société-et-médias`,
`économie` → `économie-et-social`, `présidentielle-2027` →
`campagne-2027`, etc.). Tout slug hors whitelist et alias map →
fallback sur `catégorie-libre` avec `log.info` (pour repérer les
nouveaux patterns Claude à ajouter).

Conséquence : **plus jamais de forum orphelin créé** par variation de
formulation Claude. Si Claude écrit `Économie > X` au lieu de
`Économie et Social > X`, le slug `économie` est aliasé →
`économie-et-social`, le thread va dans le bon forum, l'historique
reste cohérent.

### Y.5 — Auto-archive Arsenal : boucle horaire pour libérer le quota Discord

Loop interne au cog `arsenal_publisher.py` qui prévient la saturation des
1000 threads actifs guilde-wide (limite Discord serveurs non-boostés).
Sans ça, l'auto-sync se met à échouer en 160006 dès que la limite est
atteinte (cas vu Phase Y.4 avec 86 erreurs spam #logs avant cleanup
manuel des 300 vieux threads).

**Mécanisme** (`@tasks.loop(hours=1)`) :
1. Skip si `is_syncing` (évite conflits PATCH concurrents).
2. Fetch fresh `guild.active_threads()` (REST, pas cache).
3. Si total ≤ `ARCHIVE_THRESHOLD` (900) → return silencieux.
4. Sinon : trier les threads d'`ANALYSES POLITIQUES` du plus vieux au
   plus récent (par snowflake ID), archiver jusqu'à descendre à
   `ARCHIVE_TARGET` (800).
5. Sleep 0.5s entre chaque PATCH (rate limit safety) ; abandon ce tick
   après 5 échecs consécutifs.
6. Embed récap dans `📋・logs` avec breakdown par forum (top 5).

**Constantes** (modifiables dans `__init__`) :
- `ARCHIVE_THRESHOLD = 900` : seuil de déclenchement
- `ARCHIVE_TARGET = 800` : cible post-archivage (200 slots libres)
- `ARCHIVE_INTERVAL_HOURS = 1` : périodicité du loop

**Comportement** : threads archivés restent visibles dans Discord et
auto-unarchivent si quelqu'un poste dedans. Pas de perte de contenu.

**Commande manuelle** : `!archive_arsenal [target]` force un cycle
d'archivage immédiat (sans attendre le tick horaire). `target` optionnel
override `ARCHIVE_TARGET` pour ce run uniquement (ex `!archive_arsenal 600`
pour archiver plus agressivement). Utile si la guilde sature avant le
prochain tick.

**Démarrage** : auto-start dans `on_ready`, cancel dans `cog_unload`.
Idempotent via flag `_auto_archive_started` (même pattern que
`_auto_sync_started`).

### Fix Y.4 — TikTok short URLs sluggés + spam #logs sur quota Discord saturé

**Bug 1** : `dl_tiktok.py` (modes `extract_from_urls`, `extract_from_html`,
`extract_from_single_url`) ne résolvait pas les liens courts
`vm.tiktok.com/X` ou `vt.tiktok.com/X` avant l'extraction d'ID. La regex
`/video/(\\d+)/` ne match pas sur ces URLs, et le fallback
`re.sub(r"\\W+", "_", url)[-40:]` produit un slug type
`https_vm_tiktok_com_ZNRxxxx` au lieu de l'ID numérique 19 chiffres
réel TikTok. Ces slugs deviennent ensuite l'`id` CSV, le nom du fichier
résumé (`TT_https_vm_tiktok_com_ZNRxxxx.txt`), et la valeur du source_id
postée dans la description du thread Discord.

Note : `arsenal_pipeline.py` (listener `🔗・liens`) a déjà sa propre
fonction `resolve_tiktok_short_url` (cf Phase X), donc les drops
manuels passaient bien. Le bug ne touchait que les imports batch via
`dl_tiktok.py` (input file `dl_tiktok_video.txt` ou `--url` direct).
Conséquence en prod : **416 lignes CSV** (sur 1549) avec ID slugé.

**Fix** : ajout de `resolve_tiktok_short_url(url)` dans `dl_tiktok.py`
(HEAD redirect, cache mémoire, fallback sur URL d'origine si le réseau
est down). Appelé dans les 3 fonctions d'extraction avant la regex.

**Bug 2** : symptôme observé par l'user — 86× embed
`❌ Erreur — 400 Bad Request (error code: 160006): Maximum number of
active threads reached` dans `📋・logs`. Cause : la guilde ISTIC est à
la **limite Discord 1000 threads actifs** (serveurs non-boostés). Les
225 lignes PENDING avec ID slugé tentent d'être publiées à chaque
auto-sync, échouent toutes avec 160006, postent un embed erreur, et
l'anti-loop suspend après 3 fails sans empêcher le burst initial.

**Fix** : (a) marquer les 416 lignes malformées en `sync_status=SUCCESS`
avec `sync_error="malformed_legacy_id_skip (Y.4)"` pour stopper les
retries (le bug fix prévient les futures occurrences) ; (b) archiver
manuellement 300 vieux threads via Discord API pour libérer du quota
(politique-française : 200 archivés, 312→112 ; économie-et-social : 100
archivés, 243→143). Total guilde : 1000 → 700 threads actifs.

**Cleanup hors commit** :
- 416 lignes CSV marquées sync=SUCCESS+skip (backup
  `_backups/suivi_global_pre_y4_*.csv` créé avant modif).
- 300 threads Discord archivés (visibles mais hors quota actif).

**Migration future possible (non urgent)** : 329 lignes ont leur vrai ID
TikTok 19 chiffres extractible depuis le filename
(`<slug>_<19_digits>_<date>.mp4` — yt-dlp avait bien résolu au download,
juste pas propagé au CSV). Une migration ID-correct nécessite renommer
les summaries `TT_<slug>.txt` → `TT_<real_id>.txt` et gérer 48 groupes
de clashes (plusieurs short URLs vers même vidéo). Reporté tant que les
threads existants restent fonctionnels (titres descriptifs corrects, ID
slugé seulement dans la description = cosmétique).

### Fix Y.3 — Threads dupes via truncation-prefix matcher (`arsenal_publisher.py`)

`find_existing_thread_by_names` ne détectait pas le cas où le préfixe
tronqué d'un candidate suffixé (`build_thread_name` génère `"...préca · X"`
quand le titre complet est trop long avec le suffixe) est un préfixe
strict d'un thread existant non-suffixé (`"...précarité"`). Le matcher
gérait :
1. Match exact (`t_name in want`)
2. `t_name in want_prefixes` (ex want `"Foo · X"`, thread `"Foo"`)
3. Inverse : want `"Foo"`, thread `"Foo · X"` (`startswith(w + " · ")`)

Mais pas le cas 4 : want `"Foo bar préca · X"` (issu d'une troncature
mid-word à 89 chars), thread existant `"Foo bar précarité"` (plus long).
Le `want_prefixes = {"Foo bar préca"}` n'était jamais comparé en
`startswith` contre les noms de threads existants.

**Conséquence en prod** : 28 paires de threads dupliqués observées
(récap audit, par forum) :
- politique-française : 9 paires
- économie-et-social : 6 paires
- société-et-médias : 6 paires
- religions-et-philosophie : 2 paires
- social-et-logement : 2 paires
- écologie-et-climat / ia-et-technologie / international-et-solidarités : 1 chacun

Plus 3 paires `[identical]` (même nom exact) probablement dues au cache
`forum.threads` stale après les restarts du bot pendant la session
(cf Phase Y.1). Total : **29 threads dupliqués supprimés**.

**Fix** : ajouter le cas `truncation-prefix` au matcher. Si
`len(w_prefix) >= 80` ET `t_name.startswith(w_prefix)` ET le caractère
suivant dans `t_name` est alphanumérique (= continue un mot tronqué),
on match. Le seuil de 80 chars évite les faux positifs sur préfixes
génériques courts ; le check alphanum confirme que c'est une troncature
mid-word et pas juste un préfixe lexical commun.

### Y.2 — Salon `liens` rendu public + rename catégorie BLABLA → QG (Discord)

Côté Discord (pas de code) :
- `#liens` (id `1498918445763268658`) déplacé de `🔒 PERSONNEL` (privé) vers
  la catégorie générale (anciennement `BLABLA`). Public en **lecture pour
  tous**, **écriture restreinte aux admins** via permissions de salon — ça
  évite que les camarades ISTIC postent des liens en boucle qui brûleraient
  les tokens API ou le quota Pro Max.
- Renommage du salon : `liens` → `🔗・liens` pour s'harmoniser avec la
  convention emoji + `・` + nom des autres salons de la catégorie
  (`💬・général`, `🔧・tests`, `📋・logs`).
- Renommage de la catégorie : `BLABLA` → `QG` (Quartier Général). Style
  3-lettres compact aligné avec les matières (AN1, EN1, PSI, ISE…). Pas
  d'emoji sur les catégories (convention serveur).

Le bot route par ID (`LISTEN_CHANNEL_ID`, `LOG_CHANNEL_ID`,
`category_name="ANALYSES POLITIQUES"`), donc le rename n'impacte pas le
code. Meta files (CLAUDE.md, README.md, Arsenal_Arguments/CLAUDE.md)
mis à jour pour refléter la nouvelle position du salon.

### Fix Y.1 — Forum dupes via ponctuation finale (`arsenal_publisher.py`)

`parse_analysis` ne stripait pas la ponctuation finale du segment Classification
avant slugification. Conséquence avec `Classification: Catégorie Libre.` :
- `forum_name = "catégorie-libre."` (slug avec point).
- Discord normalise / strip le point à la création → forum stocké comme
  `"catégorie-libre"` (15 chars, pas de point).
- Cycle suivant : `discord.utils.get(forums, name="catégorie-libre.")` ne
  matche plus la version sans point → `create_forum` → nouveau dupe.
- Boucle observée en prod : **35 forums `catégorie-libre` dupliqués**, chacun
  contenant un seul thread `"Fragment audio incompréhensible sans contexte
  exploitable"` (résumé poubelle d'un audio de 10 s incohérent).

Fix dans `parse_analysis` : strip ponctuation finale (`.,:;!?`) avant slug
+ collapse double-tirets + strip `-.` aux bouts. Le slug est désormais
stable et matche ce que Discord stocke réellement.

Cleanup déclenché manuellement après le fix : 35 forums dupes supprimés via
Discord API, fichiers locaux IG_DWUc_qVjFtk (summary 2 KB, video 1.3 MB,
transcription 479 B) supprimés, ligne CSV correspondante retirée
(1549 → 1548 lignes). Le résumé étant `note 1/20` et la transcription juste
"le coran il faut un barbecue tranquille voilà voilà", aucune perte d'info
analytique.

### Préférence moteur partagée GUI ↔ pipeline (`arsenal_config.py`)

Nouveau couple `load_engine_pref()` / `save_engine_pref(engine)` dans
`arsenal_config.py`. Lecture/écriture atomique de
`Arsenal_Arguments/_secrets/engine_pref.json` au format
`{"engine": "claude_code" | "api"}`. Source de vérité unique partagée
entre :
- `summarize_gui.py` : initialise le radio button « Moteur » depuis ce
  fichier au lancement (`var_engine = tk.StringVar(value=load_engine_pref())`),
  et `trace_add` sauvegarde à chaque changement.
- `arsenal_pipeline.step_summarize` : lit la pref et n'ajoute
  `--use-claude-code` à `summarize.py` que si engine == `claude_code`.
  En mode `api`, summarize.py utilise son model par défaut (nécessite
  `ANTHROPIC_API_KEY` dans l'env).

**Avant** : la préférence GUI vivait en mémoire (reset à `claude_code`
à chaque ouverture) et `step_summarize` avait `--use-claude-code`
hardcodé → impossible de basculer le pipeline `🔗・liens` sur l'API
Anthropic sans éditer le code.

### GUI scrollable (`summarize_gui.py`)

Helper `_build_scrollable_root()` qui wrappe tout `_build_ui` dans un
`tk.Canvas` + `ttk.Scrollbar` verticale. Comportement :
- Fenêtre plus petite que le contenu → scrollbar verticale active.
- Fenêtre plus grande que le contenu → la frame interne s'étire à la
  hauteur du canvas, la console (`expand=True`) prend l'espace
  excédentaire (préserve le comportement d'origine).
- Roulette de souris : `bind_all("<MouseWheel>")` qui skip pour
  `Text`/`Treeview`/`Listbox`/`TCombobox` afin que la console et le
  Treeview drops gardent leur scroll natif.

**Avant** : avec les 3 frames Options + Quota Pro Max + Drops récents,
le contenu débordait la fenêtre par défaut 780×720 → les contrôles
inférieurs (▶ Lancer, console, raccourcis) étaient inaccessibles sans
agrandir manuellement.

### Sync auto silencieuse quand rien n'est publié (`arsenal_publisher.py`)

`_sync_task` reçoit un paramètre `silent_if_no_work: bool = False`.
Quand `True` (utilisé par `_auto_sync_loop` 15 s) :
- L'embed `🚀 Synchronisation` n'est pas posté.
- L'embed `🏁 Sync terminée` n'est posté que si `synced > 0` OU
  `failed > 0`.

Les manual sync (`!sync_arsenal`) gardent 🚀 et 🏁 inconditionnels (le
flag par défaut est `False`) pour la transparence admin. Les erreurs
(`❌ Erreur lecture`, `⚠️ Dossier résumés vide`, `❌ Erreur sync globale`)
restent toujours loggées.

**Avant** : quand un batch `summarize.py --re-summarize` rafraîchissait
des résumés sur des items déjà `sync=SUCCESS`, le mtime CSV changeait
à chaque résumé ré-écrit → l'auto-sync se déclenchait toutes les 15 s
avec `🚀 Synchronisation` + `🏁 Sync terminée : 0 publiés, 0 erreurs,
N ignorés` → spam continu de `📋・logs` sans information utile (le
compteur `ignorés` agrège les déjà-publiés, pas de vrais skips).

### Note sur le compteur « ignorés »

Le label `⏭️ N ignorés` du récap `🏁 Sync terminée` agrège **tout ce
qui n'a rien à faire** (item déjà `sync=SUCCESS`, thread déjà existant
auto-healed → SUCCESS, résumé sans ligne CSV correspondante, parse
fail). Comportement existant inchangé — c'est juste plus rare de le
voir maintenant (silencieux en auto-sync sans travail).

---

## Phase X — Auto-sync temps réel + quota pour le pipeline arsenal (29 avril 2026, soir)

Ajustements post-Phase W après remontée user d'erreurs en prod.

### Auto-sync temps réel (`arsenal_publisher.py`)

`tasks.loop(seconds=15)` ajouté au cog `ArsenalPublisher` :
- Poll mtime CSV → si changé, recharge et compte les lignes
  `summary=SUCCESS, sync=PENDING`. Si ≥1, lance `_sync_task` automatiquement.
- Le `is_syncing` lock empêche les doubles. Idempotent grâce au filtre
  `sync=PENDING` interne.
- **Anti-boucle** : si ≥3 lignes ont un `sync_timestamp` < 5 min ET
  `sync_status=FAILED`, suspension du tick (cooldown). Évite le spam quand
  un bug structurel pète sur les mêmes IDs en boucle.
- Embed Discord bleu `🔄 Auto-sync Arsenal — démarré` au boot pour
  signaler l'activation.
- **Conséquence** : chaque résumé créé par `summarize.py` est publié dans
  son forum dans les 15 s qui suivent, sans intervention humaine ni LLM.

### Fix bug `40061 Tag names must be unique` (`arsenal_publisher.py`)

`get_or_create_tags` était fragile : Discord rejette le PATCH
`forum.edit(available_tags=...)` quand 2 tags collisionnent après
normalisation interne. Cas vu en prod : tags tronqués à 20 chars qui
touchent un tag existant après strip.

**Fix double** :
1. **Dedup strict** par `name.lower().strip()` juste avant le PATCH (pas
   seulement avant l'append).
2. **Fallback HTTPException 40061** : si Discord rejette quand même, on
   log un warning + on garde les tags actuels (les nouveaux ne sont pas
   ajoutés pour ce thread, mais le sync continue au lieu de planter
   toute la sync sur un seul tag conflictuel).

### Pointage de la bonne catégorie (`arsenal_publisher.py`)

`self.category_name` était `"📂 ANALYSES POLITIQUES"` (avec emoji,
catégorie créée par ma migration et qui contenait 3 forums orphelins).
L'utilisateur a entre-temps créé manuellement une **catégorie thématique
fine** `"ANALYSES POLITIQUES"` (sans emoji) avec **12 forums** :
économie-et-social, culture-et-éducation, justice-et-libertés,
féminisme-et-luttes, histoire-et-géopolitique, etc.

Correctif : `self.category_name = "ANALYSES POLITIQUES"`. Cleanup
Discord : la cat `📂 ANALYSES POLITIQUES` + ses 3 forums orphelins
supprimés. 198 threads créés au mauvais endroit également supprimés.

### Quota Pro Max appliqué au pipeline arsenal (`arsenal_pipeline.py`)

Nouvelle fonction `_check_quota_before_summarize()` appelée avant chaque
subprocess `summarize.py --use-claude-code` du pipeline. Importe
`claude_usage`, charge le state JSON `_secrets/quota_state.json` (mêmes
seuils que la GUI summarize), check :
- Throttle hebdo persistant → bloque
- Quota hebdo > seuil → bloque
- Quota session 5h > seuil → bloque

Si bloqué, retourne `{"ok": False, "quota_blocked": True, ...}` au lieu
de spawn le subprocess. Le caller (`cmd_pipeline`, `pipeline_batch`)
détecte et poste un embed orange `⚠ Résumé sauté — quota atteint` dans
`📋・logs`. L'étape Sync est skip aussi (rien à publier).

**Mode tolérant** : si check fail (cookie missing, network down,
endpoint changé), on autorise par défaut pour ne pas bloquer le pipeline
sur une panne du quota watcher.

### Améliorations GUI summarize

- **Indicateur visuel cookie expiré** : la frame Quota Pro Max distingue
  désormais 3 états — `✅ Configuré` (vert) / `⚠ Cookie expiré` (rouge,
  si erreur 401/403/expir) / `🌐 Cookie OK, réseau down` (orange, si
  network/timeout).
- **Frame `📥 Drops récents (#liens)`** : Treeview qui affiche les 10
  derniers drops du CSV (id source, plateforme, ✅/❌/⏳ par étape DL/Sum/Sync,
  timestamp). Refresh toutes les 30 s. Lecture CSV directe (pas de réseau).

### Embed `[Summarize] X/Y` enrichi avec le titre

`summarize.py` parse désormais la section `**Titre**` du résumé Claude
fraîchement créé et l'inclut en **description** de l'embed SUCCESS sur
`📋・logs`. Le user voit immédiatement de quoi parle le contenu sans
attendre la publication forum.

Fonction helper `extract_summary_title()` dans `summarize.py:288`.

### Cleanup `.gitignore` et push

`.gitignore` étendu : exclusion `.venv/`, `*.exe`, `_secrets/`, cookies
yt-dlp, `02_whisper_logs/`, `04_exports/`. Le binaire
`Arsenal_Arguments/yt-dlp.exe` (18 MB) retiré du tracking. Squash des 3
derniers commits locaux (qui contenaient des images attachments massives
de l'historique Discord) pour réduire le pack git lors du push (HTTP 500
résolu par cette voie).

Push final : `83eebff..6e0395b master -> master` sur
`https://github.com/Gstarmix/BotGSTAR` (privé).

### Cleanup GitHub global

7 repos audités via `gh`. Convention nommage : **PascalCase**, pas de `_`
ni `.`, jamais tout minuscule. Renames : `Battle_arena → BattleArena`,
`Gstarmix.github.io → JeuPendu`, `spectre_electromagnetique →
SpectreElectromagnetique`. 6 repos passés en **public** (sauf BotGSTAR
privé pour cause de cookies / secrets locaux). Chaque repo reçoit un
README avec contexte historique, stack, statut, et **roadmap "comment je
referais ça aujourd'hui avec LLM/IA"**. Aucune suppression — tous des
souvenirs personnels conservés.

---

## Phase W — Migration Arsenal → ISTIC L1 G2 + GUI Quota Pro Max (29 avril 2026)

### Migration unifiée vers ISTIC L1 G2 (`1466806132998672466`)

Les 3 cogs Arsenal (`arsenal_pipeline`, `arsenal_publisher`,
`veille_rss_politique`) tournaient sur le serveur Veille
(`1475846763909873727`). Tout migré sur ISTIC pour avoir un seul serveur.

**Côté Discord (via API REST)** :
- Nouvelle catégorie `📂 ANALYSES POLITIQUES` (`1498918425584603168`) avec
  **8 forums politiques** (tags copiés du serveur Veille).
- 7 salons RSS politiques fusionnés dans `📡 VEILLE` existante (renommée
  depuis `VEILLE`), aux côtés des 4 salons tech (cyber/ia/dev/tech-news).
  Renommages user-friendly :
  - `arsenal-eco` → `💰・économie-veille`
  - `arsenal-ecologie` → `🌱・écologie-veille`
  - `arsenal-international` → `🌍・international-veille`
  - `arsenal-social` → `✊・social-veille`
  - `arsenal-attaques` → `🎯・débats-politiques` ("attaques" sonnait mal)
  - `arsenal-medias` → `📺・médias-veille`
  - `actu-chaude` (inchangé)
- Nouveau salon `🔗・liens` (`1498918445763268658`) dans `🔒 PERSONNEL`.
- Topics descriptifs posés sur les 11 salons veille.

**Côté code (8 fichiers)** :
- `extensions/arsenal_pipeline.py` : `LISTEN_CHANNEL_ID` + `LOG_CHANNEL_ID` ISTIC.
- `extensions/arsenal_publisher.py` : `guild_id`, `log_channel_id`,
  `category_name = "📂 ANALYSES POLITIQUES"`.
- `extensions/veille_rss_politique.py` : `ARSENAL_GUILD_ID` →
  `ISTIC_GUILD_ID`, fusion catégorie, valeurs `VEILLE_POL_CHANNEL_NAMES`
  renommées (clés internes inchangées pour compat YAML/state),
  `CATEGORY_TITLES` neutralisés.
- 5 scripts annexes (`progress_monitor`, `whisper_supervisor`,
  `post_whisper_orchestrator`, `summarize`, `summarize_gui`) :
  `LOGS_CHANNEL_ID` ISTIC.

**Cleanup côté Veille** : 18 éléments supprimés (catégories `ARSENAL` +
`📡 VEILLE POLITIQUE` + salon `#liens`). Le serveur garde TRAVAUX,
LOGICIELS, PROMPTS et salons annexes.

**Embed démarrage unifié** : `veille_rss_politique` est silencieux au
boot, `veille_rss` poste un seul `🟢 Veille — Démarrage` qui couvre les 2
loops.

**Spam DM admin supprimé** : `arsenal_publisher` et `arsenal_pipeline`
envoyaient des DM redondants après chaque sync / pipeline en erreur.
Supprimés — tout est dans `📋・logs`.

**Reset CSV** : 52 lignes `sync_status=SUCCESS` (publiées sur l'ancienne
catégorie ARSENAL côté Veille, désormais supprimée) remises en `PENDING`
pour republication automatique dans `📂 ANALYSES POLITIQUES` ISTIC au
prochain `!sync_arsenal`. Backup CSV dans `Arsenal_Arguments/_backups/`.

Scripts de migration archivés dans `Arsenal_Arguments/_claude_logs/` :
`audit_discord_structure.py`, `migrate_setup_istic.py`,
`migrate_cleanup_veille.py`.

### GUI Tkinter `summarize_gui.py` + garde-fous quota Pro Max

Lancement silencieux via `start_summarize_gui.vbs` (double-clic).

**Frame 📊 Quota Pro Max** : 4 barres live (Session 5h / Hebdo 7j / Hebdo
Sonnet / Overage), 2 Spinbox seuils (défauts 70 % / 80 %), bouton dialog
cookie chiffré DPAPI, bouton "Reset throttle hebdo".

**Garde-fous** :
- `session_pct ≥ seuil` → auto-stop + flag volatile + embed Discord
  orange. Auto-resume quand quota redescend (reset 5h ou baisse organique).
- `weekly_pct ≥ seuil` → auto-stop + flag persistant
  (`_secrets/quota_state.json`, atomic write) qui survit aux reboots GUI.
  Bouton ▶ Lancer désactivé tant que flag levé. Auto-reset si quota
  redescend ou clic manuel.

**Pre-check `_can_spawn()`** appelé avant chaque spawn (start manuel,
auto-restart, auto-resume) pour bloquer si quota déjà dépassé — évite la
fenêtre de timing entre un Lancer et le prochain refresh quota.

### Module `claude_usage.py` (scraping endpoint privé Claude.ai)

Lit le quota Pro Max depuis l'endpoint privé
`/api/organizations/{ORG_UUID}/usage` (découvert via DevTools Network).

**Cookie chiffré Windows DPAPI** (`win32crypt.CryptProtectData`, lié à la
session Windows). Stocké dans
`Arsenal_Arguments/_secrets/claude_session.bin`.

**Mimétisme Chrome HTTP** pour passer Cloudflare : headers `sec-ch-ua-*`,
`priority`, `sec-fetch-*`, User-Agent réel. Pas de TLS fingerprinting
(`curl_cffi`) — un cookie `__cf_bm` / `cf_clearance` frais suffit.

**Dataclass `Quota`** (session_pct, weekly_pct, weekly_sonnet_pct,
extra_used_credits, etc.) + persistance `QuotaState` + exceptions typées
(`CookieMissingError`, `CookieExpiredError`, `EndpointChangedError`,
`NetworkError`).

**Mode CLI standalone** : `--set-cookie / --fetch / --state / --clear`.

### Fix download Instagram — fallback `gallery-dl`

yt-dlp 2026 ne récupère plus les `entries` des carrousels d'images IG.
**Fix double** :
1. Update `yt-dlp -U` (2026.02.21 → 2026.03.17).
2. Ajout `download_via_gallery_dl()` (`dl_instagram.py:760`) — fallback
   qui dump le manifest gallery-dl en JSON puis télécharge via
   `download_binary_url`. Validé sur 2 carrousels (1 / 5 / 20 slides).
3. Probe `?img_index=N` élargi à `type=Auto/Image` (avant : `Carrousel`
   only).

**Ordre des fallbacks** : video direct → manifest entries → probe
img_index → **gallery-dl** → thumbnail.

### Script `arsenal_retry_failed.py`

Rattrapage des `download_status=FAILED` (309 lignes uniques après
migration : 76 TikTok, 212 IG, 15 X, 6 YouTube). Modes
`--dry-run / --apply / --limit / --platform`.

### Fix Unicode console cp1252 (Windows)

`arsenal_config.py` force `sys.stdout.reconfigure(encoding="utf-8")` au
top du module. Tous les scripts CLI Arsenal en bénéficient.

---

## Phase V — Veille politique Option C (28 avril 2026)

Création d'un **second cog veille jumeau** `extensions/veille_rss_politique.py`
pour le serveur **Veille / Arsenal** (`1475846763909873727`), distinct de
`veille_rss.py` qui sert le serveur ISTIC.

**Itération en 3 temps** dans la même journée :

1. **V1 (16:45)** — 5 catégories politiques (lfi, gauche, économie, écologie,
   international), 25 sources françaises vérifiées, scheduler 8h Paris,
   auto-discovery des salons par nom.
2. **V2 (17:05)** — sur demande user "sortir de la bulle LFI", ajout de 4
   catégories adversaires : droite, extrême-droite, centre, extrême-gauche.
   Total 9 catégories, 45 sources.
3. **V3 / Option C (17:50)** — refonte complète après critique honnête :
   les 9 catégories mélangent positionnement politique et thématique
   → conflits de classification. Refonte vers **7 catégories USAGE-orientées** :

| Catégorie | Question d'usage |
|---|---|
| 🔥 actu-chaude | « Qu'est-ce qui se passe ce matin ? » |
| 💰 arsenal-eco | « Quel chiffre / argument économique ? » |
| 🌱 arsenal-ecologie | « Quelle donnée climat / écologique ? » |
| 🌍 arsenal-international | « Que se passe-t-il sur Palestine / Russie ? » |
| ✊ arsenal-social | « Quelle lutte / syndicat / mobilisation ? » |
| 🎯 arsenal-attaques | « Quelle réplique pro-LFI ? » |
| 📺 arsenal-medias | « Qui dit quoi, qui ment ? » |

**Migration Discord** : 5 salons renommés (lfi/gauche/eco/écolo/intl →
arsenal-X), 4 supprimés (centre, droite, extreme-droite, extreme-gauche),
2 créés (actu-chaude, arsenal-medias). Total final : 40 sources, 7 salons.

**Sources droite/ED supprimées** : Le Figaro x3, L'Express, Contrepoints,
Valeurs Actuelles, Causeur, Boulevard Voltaire, Salon Beige, CNEWS. Le
narratif adverse est désormais filtré via `arsenal-attaques` (analyses
pro-LFI), `arsenal-medias` (Acrimed, Arrêt sur Images) et les fact-checks
(CheckNews, Décodeurs).

**Sources médias ajoutées (5)** : Acrimed, Arrêt sur Images (API publique
`https://api.arretsurimages.net/api/public/rss/all-content`), CheckNews
(Libération), Décodeurs (Le Monde), AOC.

**Frustration Magazine déplacée** : depuis `lfi` (V1) → `arsenal-eco` (V3),
sa thématique principale étant l'inégalité économique de classe.

**Doc** : nouvelle §12 dans `CLAUDE.md`. Script de health check
`Arsenal_Arguments/_claude_logs/test_rss_sources.py` lit le YAML
directement (always-in-sync).

### Bugs corrigés en parallèle (cogs Arsenal)

- **Listener 6 plateformes** : `arsenal_pipeline.extract_urls()` était
  limité TikTok/IG, réécrit comme wrapper de `extract_urls_all_platforms()`.
  `step_download` route non-tiktok/IG vers `step_dl_generic` (yt-dlp).
- **Pipeline 3/5 étapes (summarize)** : clé API Anthropic épuisée → 1535
  summaries en PENDING. Fix : `step_summarize` ajoute `--use-claude-code`
  pour basculer sur CLI subscription gratuite (même bascule que COURS
  Phase L 2026-04-27).

### Outillage Whisper renforcé (Arsenal)

- `Arsenal_Arguments/whisper_supervisor.py` : auto-restart si stall > 15min
  + isolation auto des vidéos qui font hanger libav (vers `_corrupted_videos/`).
  Posts Discord à chaque event (start/stall/isolement/restart/done).
- `Arsenal_Arguments/post_whisper_orchestrator.py` : enchaîne OCR carrousels
  + audit final après que le supervisor a terminé Whisper. Posts récap final.
- `Arsenal_Arguments/progress_monitor.py` v2 : embeds Discord 1-par-fichier
  avec metadata Whisper (audio_duration, transcribe_time, ratio, segments,
  langue) extraites des `session.log`. Stall detection + milestone embeds
  + completion auto si `--total` atteint.

---

## Phase R — Système de veille RSS Discord (avril 2026)

Nouveau Cog autonome `extensions/veille_rss.py` qui agrège des flux
RSS externes et poste des digests quotidiens dans 4 salons Discord
dédiés (`#cyber-veille`, `#ia-veille`, `#dev-veille`, `#tech-news`)
sous la nouvelle catégorie `📡 VEILLE`.

Architecture **indépendante** de `cours_pipeline.py` — partage
uniquement le bot, le rôle admin et le salon `#logs`.

### Phase R-A — MVP fetch + post manuel

- Cog `veille_rss.py` créé, indépendant de `cours_pipeline.py`.
- 4 sources de test (CERT-FR, Anthropic via RSSHub, GitHub Blog,
  Numerama), 1 par catégorie.
- Stockage runtime atomique dans `datas/rss_state.json` (dédup MD5
  des guids, fetch_state par source, prune 30 j).
- Commandes : `!veille fetch-now`, `!veille status`, `!veille reload`.
- Auto-désactivation des sources après 5 erreurs consécutives.
- Catégories : `cyber`, `ia`, `dev`, `tech` (4 salons Discord créés
  dans la catégorie `📡 VEILLE`).
- Stack initial : `feedparser` + `aiohttp` + `PyYAML`.

### Phase R-A.1 — Embeds FR + split anti-rogne

- Dates des digests en français (`samedi 25 avril 2026` au lieu de
  `Saturday 25 April 2026`) via helper `_format_date_fr` sans
  dépendance locale.
- Split automatique en plusieurs embeds si la description dépasse
  3900 chars (cap dur Discord à 4096).
- Footer `X articles · Y sources` uniquement sur le dernier embed
  du split.
- Catégorie `cyber` renommée en `📰 Veille cybersécurité`.
- Cap dur de 5 embeds par digest pour éviter le spam.

### Phase R-A.2 — Fenêtre par catégorie

- Remplacement de `DIGEST_WINDOW_HOURS = 24` par
  `DIGEST_WINDOW_HOURS_BY_CAT = {cyber: 72, ia: 72, dev: 72, tech: 24}`.
- Justification : sources cyber/ia/dev publient ~3-5/semaine, fenêtre
  24 h trop stricte. Tech (Numerama) reste à 24 h car ~5-10/jour.
- Fallback `DIGEST_WINDOW_HOURS_DEFAULT = 24` si catégorie inconnue.

### Phase R-B — Scheduler 8h auto + catch-up + logs en embeds

- `@tasks.loop(time=time(8, 0, tzinfo=ZoneInfo("Europe/Paris")))` —
  digest quotidien à 8h Paris (timezone-aware).
- Auto-start au boot via listener `on_ready` (idempotent via
  `is_running()`).
- **Catch-up** : si le bot démarre après 8h00 ET que le digest du
  jour n'a pas été posté, exécution immédiate.
- Garde anti-doublon `_digest_already_today` (compare la date Paris
  de `last_digest_at` à aujourd'hui).
- Skip silencieux des catégories à 0 article (`SKIP_EMPTY_CATEGORIES = True`).
- Refonte de `_log_to_channel` : tous les logs `#logs` deviennent des
  embeds Discord (signature étendue avec `title`, `color`, `fields`).
  Couleurs codées : vert (succès), orange (warning), rouge (erreur),
  gris (info), bleu (défaut).
- Récap matinal : embed structuré avec 3 fields (Articles par
  catégorie, Sources, Stats globales). Couleur dynamique vert ↔
  orange selon présence d'erreurs.
- Nouvelle commande `!veille trigger-now` : déclenche manuellement
  le cycle 'auto' pour tester sans attendre 8h.
- `cog_unload` cancel le loop pour shutdown propre.

### Phase R-C — Config externe + commandes sources

- Migration de `PyYAML` vers `ruamel.yaml` (≥ 0.18) pour préservation
  des commentaires et de l'ordre lors des écritures programmatiques
  du YAML.
- Helpers module : `_make_yaml`, `_load_sources_raw`,
  `_save_sources_raw`, `_find_source_index`, `_is_valid_url`,
  `_validate_source_id`.
- Méthode `_test_source_url(url)` : fetch d'1 URL via session
  isolée, retourne `(ok, msg, count)`.
- Nouveau sous-groupe `!veille sources` :
  - `list` — embed groupé par catégorie, avec emoji priorité (🔴🟠🟡)
    + drapeau langue (🇫🇷🇬🇧) + ✅/⛔ actif
  - `add <id> <url> <cat> [prio]` — avec test d'URL préalable,
    insertion ordonnée par catégorie dans le YAML
  - `remove <id>` — avec confirmation 30 s
  - `toggle <id>` — active/désactive sans suppression
  - `test <url>` — validation sans persistance
- Validation stricte : `id` en kebab-case (`[a-z0-9][a-z0-9\-_]*`,
  ≤ 50 chars), catégorie ∈ {cyber, ia, dev, tech}, priorité ∈
  {1, 2, 3}, URL http/https.
- Détection automatique de la langue (heuristique sur `.fr` dans
  l'URL).
- 7 sources configurées au final (cyber × 1, ia × 4, dev × 1, tech × 1).

### Phase R-D — Scoring mots-clés (boost + blacklist)

- Nouveau fichier `datas/rss_keywords.yaml` avec sections par
  catégorie (`cyber`, `ia`, `dev`, `tech`) + section `all` qui
  s'applique à toutes.
- Champ `keyword_boost: int = 0` ajouté au dataclass `Article`.
- Score étendu : `prio + freshness + keyword_boost`.
- **Boost** : article qui matche un mot-clé reçoit +500 points par
  match (constante `KEYWORD_BOOST_POINTS`). Additif.
- **Blacklist** : article qui matche est rejeté du digest (filtre
  dur, court-circuit avant boost).
- Matching : insensible à la casse, substring exact, sur titre +
  summary. Multi-mots = ces mots consécutifs dans cet ordre.
- Helpers module : `_load_keywords`, `_match_keywords`,
  `_apply_keyword_scoring`.
- Stats loggées : `R-D scoring : X boostés, Y blacklistés` à chaque
  cycle.
- Nouvelle commande `!veille keywords` : embed listant les mots-clés
  par catégorie, avec compteurs.
- `!veille reload` recharge maintenant aussi les keywords (via
  `_load_keywords` rappelé à chaque `_filter_and_select`).
- Configuration initiale : 39 mots-clés boost, 7 blacklist (focus
  cyber sur CVE/RCE/0-day, ia sur GPT-5/Claude/jailbreak, tech
  blacklist anti-promo).

### Phase R-F — Refonte rendu embeds Style A v2 (2026-04-26)

Itération sur le Style A « Magazine » de la Phase R-A.1 pour rendre
les titres cliquables et aérer le rendu visuel.

- **Titre cliquable** : Discord ne supporte pas les liens Markdown
  dans `field.name`, seulement dans `field.value`. Donc tout déplacé
  dans `value` :
  ```
  🟠 [**Titre cliquable**](url)
  📰 `source-id` · 🇫🇷 · _il y a 10 h_
  ```
  Le source-id reste mis en évidence avec backticks mais n'est plus
  cliquable (le titre suffit, le doublon serait redondant).
- **Aération** : `field.name = "​"` (zero-width space).
  Discord rend l'espace réservé au header du field même si visuellement
  vide → séparation naturelle entre articles sans gros gap.
- **Fix timestamp** : le timestamp Discord (« Aujourd'hui à HH:MM »)
  n'apparaît plus que sur le **dernier embed du split**, pour éviter
  qu'il s'affiche aussi sur les embeds intermédiaires (visuellement
  parasite quand on a 2 embeds enchaînés). Le footer suivait déjà
  cette règle.
- Reset complet de `rss_state.json` avant le test pour avoir un
  fetch frais sur les 20 sources (backup auto
  `rss_state.json.bak.YYYYMMDD-HHMMSS`).

### Phase R-G — Refonte rendu embeds Style A v3 (2026-04-26)

Itération finale sur le Style A pour traiter les 2 derniers points
visuels qui posaient encore souci.

- **Titre uniquement sur le 1er embed du split** : si un digest est
  splitté en 2 embeds (10 articles → 5+5), seul le 1er porte
  « 📰 Veille X — date ». Le 2e n'a plus de titre. Plus de
  suffixes `(1/N)` / `(2/N)` — visuellement plus propre, effet
  « magazine 2 pages » naturel.
- **Image spacer 730×1 transparente** sur chaque embed via
  `embed.set_image(url=EMBED_SPACER_URL)`. Discord force la largeur
  d'un embed à correspondre à celle de l'image attachée → tous les
  embeds d'un même message (ou de tous les digests sur le serveur)
  ont désormais la même largeur, éliminant l'effet « désaligné ».
  L'image étant transparente avec alpha=0, elle est invisible mais
  a un effet structurel.
- Nouvelle constante `EMBED_SPACER_URL` en haut du module. URL hébergée
  publiquement (zupimages) ; fallback documenté = uploader
  `datas/embed_spacer.png` (PNG 83 octets généré pour l'occasion) sur
  Discord et utiliser l'URL CDN.
- Cas 0 article (mode manual) inclut aussi l'image spacer pour
  cohérence visuelle.

### Phase R-H — Emoji source par catégorie (2026-04-26)

- Nouvelle constante `CATEGORY_SOURCE_EMOJI` qui mappe chaque
  catégorie à un emoji cohérent avec le salon Discord :
  - cyber → 📰 (`#cyber-veille`)
  - ia → 🤖 (`#ia-veille`)
  - dev → 💻 (`#dev-veille`)
  - tech → 📱 (`#tech-news`)
- `_format_article_field` utilise désormais
  `CATEGORY_SOURCE_EMOJI[article.category]` au lieu d'un 📰 fixe
  pour la 2e ligne de chaque carte article.
- Cohérence visuelle entre l'emoji du salon (à gauche dans la liste
  Discord) et l'emoji des cartes articles (devant le source-id).
- Reset complet du `rss_state.json` au déploiement pour permettre
  un re-fetch frais et observer immédiatement le nouveau rendu.

### Phase R-E — Documentation

- `BotGSTAR/CLAUDE.md` étendu avec section §11 « Cog veille_rss.py »
  (architecture, scoring, commandes, garde-fous, helpers).
  Préambule mis à jour pour refléter les 2 Cogs (Cours + Veille).
- `BotGSTAR/CHANGELOG.md` créé (ce fichier).
- `BotGSTAR/GUIDE_VEILLE.md` créé (guide utilisateur 3 niveaux :
  lecteurs / admins / mainteneurs).
- `COURS/README.md` §5bis étendu avec sous-section
  « Espace veille technologique » + suppression de l'item « à venir »
  qui mentionnait le projet RSS.
- `COURS/CHANGELOG.md` enrichi d'une nouvelle entrée datée renvoyant
  vers ce CHANGELOG pour le détail.

---

## Phase S — Tray watchdog (avril 2026)

Nouveau lanceur principal `bot_tray.py` + `start_tray.vbs`. Remplace
`start_bot.bat` pour l'usage normal. Le `.bat` reste utilisable pour
debug en console.

### `bot_tray.py` — Watchdog avec icône system tray

- Spawne `python -u bot.py` en subprocess sans console
  (`subprocess.Popen` + `CREATE_NO_WINDOW = 0x08000000`).
- Tee de stdout/stderr vers `%TEMP%\BotGSTAR_startup.log` + buffer
  mémoire `deque(maxlen=4000)` pour la fenêtre logs Tk.
- Auto-restart 10 s après crash (`RESTART_DELAY_SECONDS = 10`),
  avec compteur `_crash_count` exposé dans le tooltip.
- Icône `pystray` 64×64 (disque coloré + B blanc), couleurs codées
  via `BotState` enum :
  - 🟢 vert : `RUNNING`
  - 🟠 orange : `PAUSED`
  - 🔴 rouge : `CRASHED_WAITING` (countdown affiché dans tooltip)
  - 🔵 bleu : `RESTARTING` (kill manuel, respawn immédiat)
- Toast Windows à chaque crash + redémarrage manuel (via
  `icon.notify`).
- Menu clic droit (8 entrées) :
  - Voir logs en direct (fenêtre Tk dédiée, auto-scroll, font
    Consolas, fond sombre VS Code-like)
  - Ouvrir dossier logs / dossier datas
  - Pause / Reprendre (toggle)
  - Redémarrer
  - ✅/⬜ Démarrer avec Windows (toggle, écrit/supprime
    `BotGSTAR_Tray.vbs` dans le dossier Startup)
  - Quitter
- Kill propre : `taskkill /F /T /PID` (arbre de processus, au cas
  où le bot spawn des enfants).
- 3 threads : watchdog principal (boucle `_watchdog_loop`),
  refresher tooltip (30 s), pump stdout par subprocess.

### `start_tray.vbs` — Lanceur silencieux

- Détection auto de `pythonw.exe` : PATH d'abord, sinon chemin par
  défaut `C:\Users\Gstar\AppData\Local\Programs\Python\Python312\pythonw.exe`.
- Lance `bot_tray.py` via `WshShell.Run …, 0, False` (fenêtre cachée,
  non bloquant). Double-clic = bot lancé silencieusement.
- L'auto-démarrage Windows est géré par le menu tray
  (`action_toggle_startup`), qui écrit `BotGSTAR_Tray.vbs` dans
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`. Pas
  besoin de raccourci manuel.

### `start_bot.bat` — Sémantique « click = restart » (rétrofit)

Pour rester compatible avec le mode tray, le `.bat` historique tue
maintenant toute autre instance avant de prendre la main :

- `tasklist /FI "WINDOWTITLE eq BotGSTAR - Pipeline COURS"` détecte
  un ancien `.bat` et le `taskkill /F`.
- `Get-CimInstance Win32_Process … bot.py` détecte le subprocess
  bot (qu'il vienne du `.bat` OU du tray) et le `Stop-Process`.
- La fenêtre courante est protégée car elle n'a pas encore le
  `title` cible au moment du `taskkill`.
- Garantit qu'**une seule instance bot** tourne, même en cas de
  bascule tray ↔ console répétée.

---

## Avant Phase R

Pour les évolutions du Cog Cours (Phase A → H, refonte forums,
watcher, backfill, énoncés seuls, perso, cleanup, doc), voir
`COURS/CHANGELOG.md`.
