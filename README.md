# BotGSTAR — Écosystème de bots Discord

> _« LFI n'est pas assez à gauche à mon goût, mais c'est le parti qui représente le mieux mes convictions et le moins soumis aux intérets des ultra riches. »_
>
> — **Gaylord ABOEKA** ([@gaylordaboeka](https://www.instagram.com/gaylordaboeka/))

---

## TL;DR

**BotGSTAR** est un **écosystème de trois bots Discord interconnectés** que je développe et opère seul, en Python 3.12 + GPU CUDA, sur ma machine personnelle. Le projet répond à trois besoins très concrets :

1. **Automatiser le suivi de mes cours** à l'ISTIC (Université de Rennes 1) — pipeline transcription audio + résumés IA + publication forum.
2. **Bâtir un arsenal d'arguments politiques** — scraping multi-plateforme (TikTok, Instagram, YouTube, X, Reddit, Threads), transcription Whisper, OCR carrousels, résumé Claude, indexation Discord.
3. **Surveiller le paysage politique français** — agrégation RSS quotidienne sur 7 salons USAGE-orientés (actu-chaude, économie-veille, écologie-veille, international-veille, social-veille, débats-politiques, médias-veille), avec scoring par mots-clés et digest matinal.

Le tout tourne **24/7** sur un mini-tower Windows 11 avec une RTX 2060, supervisé par un système de tray Windows custom et auto-restartable.

**Pourquoi ?** Pour montrer que l'engagement politique n'est pas qu'un slogan : c'est un travail d'analyse, de veille, d'archivage. Et que l'informatique, bien utilisée, peut industrialiser cette analyse.

---

## Qui je suis

Gaylord ABOEKA, **26 ans**, Rennais (35000), étudiant en **L1 Informatique et Électronique à l'ISTIC** — la composante informatique de l'Université de Rennes 1. Avant cette reprise d'études en sciences dures, j'avais entamé une licence Informatique-Électronique en 2021 puis une **prépa digitale chez Buroscope**, où j'ai consolidé mes bases en HTML/CSS/PHP/JS, en outils Adobe (Photoshop, Illustrator, Premiere Pro), et en ingénierie WordPress.

Mon **bac S** au Lycée René Descartes m'a donné le socle scientifique. Mais ce sont surtout mes années d'**administration de serveurs Discord communautaires** (notamment autour du MMORPG NosTale), et mon **bénévolat associatif** (Union Étudiante pour l'Intégration, voyages scolaires de Bruz) qui ont forgé ma méthode : observer un besoin réel, proposer un outil, le maintenir dans le temps.

À côté de ça : musculation (Fitness Park Liberté), engagement politique à gauche radicale, et beaucoup, beaucoup, **beaucoup** de code Python.

---

## Pourquoi BotGSTAR existe

### Le déclic pédagogique
Première année à l'ISTIC, je rate des cours, mes camarades aussi. Le contenu est éparpillé entre Moodle, mails, slides, mes propres notes. Solution : un bot qui range mon disque dur, transcrit mes audios de CM, génère des résumés LaTeX, et poste tout dans un Discord par matière. **C'est devenu le Cog `cours_pipeline.py`** — 8 353 lignes, 24 commandes, un watcher de fichiers temps réel, un pipeline LaTeX → PDF, un système de forums Discord avec tags d'état (`📄 Énoncé seul`, `✍️ Corrections présentes`, etc.).

### Le déclic politique
Deuxième moteur : **comprendre LFI sans tomber dans la bulle**. Je suis sympathisant Insoumis, mais je veux pouvoir répondre intelligemment aux contradicteurs — pas en répétant des éléments de langage. Il me faut des **arguments**, **chiffrés**, **sourcés**, **vidéo à l'appui**.

D'où **Arsenal Intelligence Unit** : pipeline qui aspire les vidéos politiques que je trouve sur les réseaux (TikTok, Insta, YouTube), les transcrit, les OCR, les résume avec Claude, et les indexe dans des forums Discord thématiques. À la moindre prise de parole d'un contradicteur, je peux retrouver la bonne séquence en 30 secondes.

### Le déclic veille
Troisième besoin : **ne pas rater l'actu**. Je crée un Cog `veille_rss.py` qui agrège des flux RSS techniques (cyber/IA/dev/tech) pour mes études. Puis je le décline en **`veille_rss_politique.py`** pour la politique — 9 catégories, 45 sources françaises curatées, scoring par mots-clés boostés (LFI, Mélenchon, retraites, climat, etc.), digest matinal à 8h00 dans le salon Discord adéquat.

---

## Les trois modules

### Module 1 — `cours_pipeline.py` : automatisation pédagogique

**Salon cible** : serveur Discord de l'ISTIC L1 G2 (`ISTIC_GUILD_ID = 1466806132998672466`).

**Ce que ça fait** :
- **Watchdog `_INBOX`** (polling 60s) : range automatiquement les fichiers déposés dans `COURS/_INBOX/` selon le pattern `{TYPE}{NUM}_{MAT}_{DATE}.{txt|m4a|pdf|docx}` (ex : `CM7_AN1_1602.m4a`).
- **Pipeline complet CM** : audio + transcription Whisper + résumé LaTeX (généré via le CLI Claude Code en mode subscription, gratuit) → publication automatique dans 15 salons texte (`cm-{audio,transcription,résumé}-{matière}`).
- **Forums correction** : 1 thread par TD/TP/CC, énoncé en 1ᵉʳ post, corrections en posts suivants (1 par exercice), avec versionning automatique en cas de changement de MD5 (suppression ancien message + repost `🔄 Version N`).
- **Forum privé matériel perso** : catégorie `🔒 PERSONNEL` (visible admin only) avec scripts oraux, slides, vidéos d'entraînement par exercice.
- **24 commandes admin** (`!cours setup-channels`, `!cours publish`, `!cours backfill`, `!cours rapport [--deep]`, etc.).

**Phase notable — Phase L (avril 2026)** : suite à l'épuisement de ma clé API Anthropic, j'ai **basculé tout le système de résumés vers le CLI `claude --print`** (sub-process avec OAuth subscription). Coût : 0 €. Plafond : quota subscription. C'est la même bascule que j'ai répliquée pour Arsenal (`step_summarize` du Cog `arsenal_pipeline.py`).

### Module 2 — `arsenal_pipeline.py` + `arsenal_publisher.py` : Arsenal Intelligence Unit

**Salon cible** (depuis avril 2026) : serveur Discord ISTIC L1 G2 (`ISTIC_GUILD_ID = 1466806132998672466`), salon `🔗・liens` (catégorie `QG`, public en lecture, écriture admin only pour ne pas brûler mes tokens) pour les drops d'URLs.

**Pipeline en 5 étapes** :
1. **Download** — scripts dédiés (`dl_tiktok.py`, `dl_instagram.py`) ou `dl_generic.py` (yt-dlp universel) pour YouTube/X/Reddit/Threads.
2. **Normalize** — consolidation CSV (`csv_normalize.py`) avec backup auto.
3. **Transcribe** — Whisper large-v3 sur GPU CUDA RTX 2060 (`faster-whisper`, `int8_float16`, VAD seuil 0.35, beam 5).
4. **Summarize** — résumé Claude via CLI (subscription) ou API selon configuration.
5. **Publish** — sync vers forums Discord par catégorie (TikTok / IG / YT / etc.).

**Données** : CSV central `suivi_global.csv` (25 colonnes, 1500+ lignes), 1300+ vidéos téléchargées, 1000+ transcriptions. Les carrousels Instagram passent par un OCR easyocr (fr+en, GPU) qui agrège le texte de chaque slide en `02_whisper_transcripts/<id>_ocr.txt`.

**Architecture forum** : chaque type de contenu va dans son forum, taggé par plateforme et thème (immigration, retraites, écologie, etc.). À chaque drop d'URL dans `#liens`, le bot :
- Détecte la plateforme automatiquement (regex 6 plateformes).
- Lance le pipeline et logge chaque étape via embeds dans `#logs` (couleur verte/orange/rouge selon succès).
- Poste le résultat dans le forum approprié.

**Robustesse** : le `whisper_supervisor.py` que j'ai écrit ce mois-ci détecte automatiquement les **vidéos qui font hanger libav** (problème connu de `faster-whisper` avec certains MP4 corrompus), kill le process, isole le fichier dans `_corrupted_videos/`, et relance Whisper. Pas une seule intervention manuelle nécessaire pour traiter 500+ vidéos.

Côté Discord, l'`arsenal_publisher` a une boucle horaire (`_auto_archive_loop`) qui archive les vieux threads d'`ANALYSES POLITIQUES` dès que la guilde dépasse **900/1000 threads actifs** (limite Discord serveurs non-boostés). Empêche les erreurs `400 ... 160006: Maximum number of active threads reached` qui bloqueraient toute publication de nouveau contenu. Threads archivés restent visibles dans Discord et auto-unarchivent si quelqu'un poste dedans — pas de perte de contenu, juste libération du quota actif. Commande manuelle `!archive_arsenal [target]` pour trigger ad-hoc.

### Module 3 — `veille_rss.py` + `veille_rss_politique.py` : agrégation RSS

**Deux Cogs jumeaux**, deux serveurs cibles :

#### `veille_rss.py` — veille technique (serveur ISTIC)
- 4 catégories : **cyber** (CERT-FR, Krebs, BleepingComputer, ZATAZ, etc.), **ia** (Anthropic, OpenAI, HuggingFace, ActuIA), **dev** (GitHub Blog…), **tech** (Numerama).
- Scoring par mots-clés (`vulnérabilité critique`, `0-day`, `RCE`, `GPT-5`, `Claude`, etc.) + blacklist anti-pubs.
- Digest matinal à 8h00 Paris dans 4 salons dédiés (Style A v3 Magazine — embeds avec spacer image 730×1 transparent pour uniformiser la largeur).
- Auto-désactivation des sources qui plantent en boucle (5 erreurs consécutives).

#### `veille_rss_politique.py` — veille politique (serveur ISTIC L1 G2 depuis 2026-04-29, fusionnée avec la veille tech dans `📡 VEILLE`)
- **7 catégories USAGE-orientées**, **40 sources françaises** vérifiées (HTTP 200 + entries > 0).
- Conception : **Option C — Arsenal Intelligence Unit**. Chaque salon répond à une question concrète d'usage, pas à un positionnement idéologique flou. Pas de salon « droite » ou « extrême droite » dédié (qui devient vite un bottin toxique) — leur narratif est filtré et critiqué via `arsenal-attaques` (analyses pro-LFI), `arsenal-medias` (critique média) et les fact-checks (CheckNews, Décodeurs) de `actu-chaude`.

| Catégorie | Emoji | Question d'usage | Sources représentatives |
|---|---|---|---|
| **actu-chaude** | 🔥 | « Qu'est-ce qui se passe ce matin ? » | Le Monde, Mediapart, Libération, France Info, Le Parisien |
| **économie-veille** | 💰 | « Quel chiffre pour répondre sur la fiscalité, les retraites, les inégalités ? » | Alt. Économiques, Contretemps, Frustration, Inégalités, Attac, RFI Éco |
| **écologie-veille** | 🌱 | « Quelle donnée climat / écologique pour étayer ? » | Reporterre, Bon Pote, Vert, Basta!, EcoloObs |
| **international-veille** | 🌍 | « Que se passe-t-il sur Palestine, Russie, Sahel… ? » | Le Monde Diplo, Courrier Int., Le Monde Int., France 24, RFI Monde |
| **social-veille** | ✊ | « Quelle lutte / syndicat / mobilisation a bougé ? » | L'Humanité, StreetPress, Regards, Lundi Matin, Révolution Permanente, Paris-luttes, Rebellyon, Contre Attaque |
| **débats-politiques** | 🎯 | « Quelle réplique argumentée à un narratif adverse ? » | L'Insoumission, LVSL, Mélenchon Blog, LFI officiel, Libération Politique |
| **médias-veille** | 📺 | « Qui dit quoi, qui ment, qui critique le récit dominant ? » | Acrimed, Arrêt sur Images, CheckNews (Libé), Décodeurs (Le Monde), AOC |

> **Pourquoi pas de salon « extrême droite » dédié ?**
>
> Parce qu'agréger Valeurs Actuelles + Causeur + Boulevard Voltaire + Salon Beige + CNEWS chaque matin pollue le mental sans servir de mission claire. Pour comprendre la concurrence, il vaut mieux lire les **analyses critiques** de ces médias (Acrimed, Arrêt sur Images, Le Vent Se Lève) que les médias eux-mêmes en brut. C'est la philosophie « Option C » : on ne classe pas par idéologie, on classe par USAGE.

- **Scoring** : prio source (1000-3000 pts) + fraîcheur (jusqu'à 1000 pts dégressif sur 1000 min) + mots-clés boost (+500 pts par match). Un article de prio 2 fortement matchant peut battre un article de prio 1 sans match. C'est intentionnel : un sujet brûlant prime sur la hiérarchie de sources.
- **Fenêtres de fraîcheur ajustées** par catégorie : 24h pour les généralistes (Le Monde, Le Figaro), 72h pour les sources nichées (Lundi Matin, Contretemps).
- **Digest auto à 8h00 Paris** + catch-up si bot relancé après 8h00 + dédoublonnage MD5 sur 30 jours.
- **9 commandes admin** (`!veille_pol setup-channels`, `!vp fetch-now`, `!vp trigger-now`, `!vp status`, `!vp reload`, `!vp sources list/add/remove/toggle/test`, `!vp keywords`).

---

## Stack technique

| Couche | Outils |
|---|---|
| **OS** | Windows 11 Home (mini-tower perso, 24/7) |
| **Runtime** | Python 3.12 |
| **Discord** | discord.py (cogs/extensions architecture) |
| **GPU** | NVIDIA RTX 2060 (6 Go VRAM) — CUDA |
| **Transcription** | faster-whisper (large-v3, int8_float16, VAD) |
| **OCR** | easyocr (fr + en, GPU) |
| **LLM** | Anthropic Claude (API + CLI subscription en fallback) |
| **Téléchargement** | yt-dlp + cookies Netscape (Instagram, TikTok) |
| **RSS** | feedparser, aiohttp (fetch parallèle, ETag/If-Modified-Since) |
| **YAML** | ruamel.yaml (preserve-roundtrip, commentaires + ordre conservés) |
| **Données** | CSV utf-8-sig (Excel-compatible) + JSON atomic (`.tmp` + `os.replace`) |
| **Supervision** | tray Windows custom (`bot_tray.py` + `start_tray.vbs`) avec auto-restart 10 s, fenêtre logs Tk, toggle autostart Windows |
| **Logs** | Discord embeds couleur-codés dans `#logs` + fichiers locaux dans `_claude_logs/tasks/` |

---

## Architecture (vue d'ensemble)

```
┌─────────────────────────────────────────────────────────────────┐
│                    BotGSTAR (un seul Python process)            │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │   COURS      │  │   Arsenal    │  │   Veille RSS          │  │
│  │ cours_       │  │ arsenal_     │  │ veille_rss + politique│  │
│  │ pipeline     │  │ pipeline +   │  │ (2 cogs jumeaux)      │  │
│  │              │  │ publisher    │  │                       │  │
│  │  - INBOX     │  │  - DL multi- │  │  - 4 cat tech         │  │
│  │  - LaTeX     │  │   plateforme │  │  - 9 cat politique    │  │
│  │  - Forums    │  │  - Whisper   │  │  - Digest 8h00 Paris  │  │
│  │  - 24 cmds   │  │  - OCR       │  └───────────────────────┘  │
│  └──────────────┘  │  - Claude    │                             │
│                    │  - Forums    │                             │
│                    └──────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
        │                     │                       │
        ▼                     ▼                       ▼
   Disque local          GPU CUDA              Discord API
  (COURS/, datas/,      (Whisper +              (3 serveurs:
   Arsenal_Arguments/)   easyocr)                ISTIC, Veille)
        │
        ▼
   ┌─────────────────┐
   │ whisper_        │  ← supervise Whisper, isole les corrompus
   │ supervisor.py   │     auto-restart sur stall (15 min)
   └─────────────────┘
        │
        ▼
   ┌─────────────────┐
   │ post_whisper_   │  ← chaîne OCR + audit final après Whisper
   │ orchestrator.py │
   └─────────────────┘
        │
        ▼
   ┌─────────────────┐
   │ progress_       │  ← surveille les .txt produits, poste
   │ monitor.py      │     sur Discord avec embeds (1/file)
   └─────────────────┘
```

---

## Layout du repo

```
BotGSTAR/
├── bot.py                          # Point d'entrée — charge 5 cogs
├── bot_tray.py                     # Tray Windows watchdog (auto-restart)
├── start_tray.vbs                  # Lanceur silencieux Windows
├── start_bot.bat                   # Mode debug (console visible)
├── extensions/                     # 5 Cogs Discord
│   ├── cours_pipeline.py           # COURS — 8353 lignes
│   ├── arsenal_pipeline.py         # Arsenal — 1100+ lignes
│   ├── arsenal_publisher.py        # Arsenal — publication Discord
│   ├── veille_rss.py               # Veille tech ISTIC — 1654 lignes
│   └── veille_rss_politique.py     # Veille politique — 700+ lignes
├── datas/                          # État + config (atomic write)
│   ├── rss_sources.yaml            # Sources veille tech
│   ├── rss_keywords.yaml           # Mots-clés boost/blacklist tech
│   ├── rss_state.json              # Tracking dédup + erreurs tech
│   ├── rss_sources_politique.yaml  # Sources veille politique (45)
│   ├── rss_keywords_politique.yaml # Mots-clés politique (200+)
│   ├── rss_state_politique.json
│   ├── discord_published.json      # Tracking corrections COURS (v2)
│   ├── discord_perso_published.json
│   └── _published.json             # Tracking pipeline CM COURS
├── COURS/                          # Disque local pédagogique
│   ├── _INBOX/                     # Dépôt fichiers à ranger
│   ├── _scripts/                   # Watcher publish queue
│   ├── _titres_threads.yaml        # Mapping TD/TP → titre humain
│   └── {AN1,EN1,PRG2,PSI,ISE}/     # Une matière par dossier
└── Arsenal_Arguments/              # Pipeline politique
    ├── arsenal_config.py           # Module centralisé chemins/CSV
    ├── dl_{tiktok,instagram,generic}.py
    ├── csv_normalize.py
    ├── summarize.py                # Claude API + CLI fallback
    ├── ocr_carousels.py            # easyocr GPU
    ├── arsenal_audit.py            # Audit santé du pipeline
    ├── progress_monitor.py         # Discord embeds par fichier traité
    ├── whisper_supervisor.py       # Auto-restart + isolation corrompus
    ├── post_whisper_orchestrator.py # OCR + audit chaîné
    ├── whisper_engine.ps1          # Wrapper PowerShell Whisper
    ├── 01_raw_videos/              # Vidéos téléchargées (~1300)
    ├── 02_whisper_transcripts/     # Transcriptions (~1000)
    ├── 03_ai_summaries/            # Résumés Claude
    ├── _corrupted_videos/          # Isolés par supervisor
    ├── _claude_logs/               # Traçabilité de mes sessions Claude Code
    │   ├── session_YYYY-MM-DD.md
    │   └── tasks/                  # Stdout/stderr des bg tasks
    └── suivi_global.csv            # Source de vérité (25 colonnes)
```

---

## Démos & scénarios concrets

### Scénario 1 — Je rate un CM
1. Mon camarade enregistre l'audio (`.m4a`) et me l'envoie.
2. Je le drop dans `COURS/_INBOX/CM7_AN1_1602.m4a`.
3. Le watcher détecte → range dans `COURS/AN1/CM/`.
4. Pipeline auto : audio publié dans `#cm-audio-an1`, Whisper → `#cm-transcription-an1`, résumé LaTeX → `#cm-résumé-an1` (PDF généré via `summarize.py`).
5. Tout est dans Discord avant que j'aie eu le temps de me faire un café.

### Scénario 2 — Je trouve une vidéo TikTok intéressante d'un opposant
1. Je copie-colle l'URL dans `#liens`.
2. Le bot détecte (regex), réagit avec 🔄, enqueue.
3. Pipeline : `dl_tiktok.py` → `csv_normalize.py` → Whisper → `summarize.py --use-claude-code` → publication dans le forum thématique.
4. Embed `#logs` pour chaque étape : ✅ Download (3.2s) · ✅ Normalize · ✅ Whisper (12s) · ✅ Résumé · ✅ Publication.
5. Si l'étape Summarize plante (ex : clé API épuisée), je vois le rouge dans `#logs` et je sais où agir.

### Scénario 3 — Je veux des arguments économiques pour répondre à un débat
1. Je regarde `#💰・économie-veille` à 8h05.
2. Je vois 10 articles : Alt. Économiques sur les inégalités de patrimoine, Frustration sur les milliardaires, Inégalités.fr sur le SMIC vs cadres, Contretemps analyse marxiste de l'inflation, etc.
3. Je clique sur celui qui m'intéresse (titre cliquable dans l'embed Discord).
4. Si je veux creuser un sujet précis, je tape `!vp keywords` pour voir les mots-clés boostés et ajuster.

### Scénario 4 — Une source RSS plante
1. CERT-FR a 5 erreurs HTTP consécutives.
2. Le bot la désactive automatiquement.
3. Embed orange dans `#logs` : « La source `cert-fr` a été désactivée après 5 erreurs consécutives. »
4. Le récap matinal du lendemain mentionne la source en panne.
5. Je vais voir le YAML, je modifie l'URL, je tape `!veille reload`, c'est reparti.

### Scénario 6 — J'ouvre la GUI et je vois en un coup d'œil l'état complet du pipeline

La frame `📥 Drops récents (#liens)` de la GUI summarize affiche les 10
derniers drops avec, pour chacun : ID source, plateforme, emoji par étape
(DL ✅, Summarize ⏳, Sync ❌), timestamp. Refresh toutes les 30 s par
lecture directe du CSV. Si je vois une ligne ❌ rouge sur DL ou Sync, je
sais immédiatement où regarder dans `📋・logs`.

Couplé à la frame `📊 Quota Pro Max`, j'ai sur un seul écran : quotas live
+ état du pipeline Arsenal complet. Et si mon batch summarize est en
auto-stop suite à un seuil, je vois le label cookie passer en `⚠ Cookie
expiré` (rouge) ou `🌐 Cookie OK, réseau down` (orange) et je sais
exactement quoi faire.

Le pipeline Arsenal (les drops dans `🔗・liens`) est aussi soumis au
même quota via le bot Discord : si je drop un lien quand ma session 5h
est saturée, le bot poste un embed orange `⚠ Résumé sauté — quota
atteint` au lieu de consommer. Le download/whisper sont quand même
exécutés et conservés, je peux relancer le summarize quand le quota
redescend.

### Scénario 5 — Mon batch Claude bouffe mon quota Pro Max et je bloque mes autres usages

C'est le problème classique du « pipeline qui tourne la nuit pendant 8 h » :
le matin je veux ouvrir Claude Code pour autre chose, et là, **plus de quota
disponible**. Solution maison :

1. J'ouvre la GUI Tkinter `summarize_gui.py` (double-clic VBS, sans console).
2. Frame **📊 Quota Pro Max** affiche en live : `Session 5h : 30 % (reset 3h50m)`,
   `Hebdo 7j : 45 %`, `Hebdo Sonnet : 1 %`, `Overage : 94 %`.
3. Comment la GUI lit ces chiffres ? L'API `claude.ai/settings/usage` n'est
   pas publique — j'ai inspecté les requêtes Network DevTools, identifié
   l'endpoint privé `/api/organizations/{ORG}/usage`, mimétisé Chrome côté
   headers (`sec-ch-ua-*`, `priority`, `sec-fetch-*`) pour passer Cloudflare.
4. Le **cookie de session** est stocké chiffré localement via **Windows DPAPI**
   (`win32crypt.CryptProtectData`, lié à la session Windows — déchiffrable
   uniquement par mon compte sur cette machine, pas de mot de passe à retenir).
5. La GUI propose **deux Spinbox de seuils** : « Session : 70 % » et
   « Hebdo : 80 % ». Quand le batch dépasse, **auto-stop** propre (Ctrl-Break
   → embed Discord « Pause session — quota 5h atteint » → release lock).
6. Le throttle hebdo est **persistant** (`_secrets/quota_state.json`), donc
   il survit à un redémarrage de la GUI. Le bouton ▶ Lancer est désactivé
   tant qu'il n'est pas levé (auto-reset quand le quota redescend, ou clic
   manuel « Reset throttle hebdo » avec confirmation).
7. Auto-resume session quand le compteur 5h passe sous le seuil ou après
   reset. Pas besoin de surveiller, je laisse tourner et je récupère mon
   quota intact pour mes autres usages le matin.

C'est typiquement le genre de chose qu'on me dirait « impossible / API non
documentée / faut un proxy ». En pratique : 200 lignes de Python +
DevTools + DPAPI = problème résolu.

---

## Robustesse & ops

- **Watchdog tray** : si le bot crash, restart auto en 10 s. Il a planté 77 fois en 3 mois sans perte de service.
- **Atomic write** : tous les JSON et YAML sont écrits via `.tmp` + `os.replace` — pas de corruption en cas de crash en plein write.
- **Dédoublonnage MD5** : sur 30 jours pour les RSS, sur fichier pour les corrections COURS, sur `(plateforme, id)` pour le CSV Arsenal.
- **Versionning** : les corrections qui changent de MD5 sont republiées avec préfixe `🔄 Version N`, l'historique est gardé dans `versions[]`.
- **Catch-up** : si le bot est démarré après 8h00, le digest RSS du jour est exécuté immédiatement, en respectant le dédoublonnage.
- **Auto-isolation** : les vidéos qui font hanger libav sont déplacées dans `_corrupted_videos/` par `whisper_supervisor.py` — Whisper ne se bloque jamais plus de 15 minutes.
- **Auto-désactivation** : les sources RSS qui plantent en boucle (5 erreurs) sont désactivées en mémoire (le YAML reste `active: true` pour qu'on les remette manuellement après fix).

---

## Ce que ça démontre

**Compétences techniques** :
- Architecture asynchrone Python (asyncio, aiohttp, discord.py).
- Pipelines ETL multi-étapes avec checkpoints CSV/JSON et idempotence MD5.
- Intégration LLM (API + CLI subscription) avec fallback automatique.
- GPU computing (CUDA, faster-whisper, easyocr).
- Discord API avancée (forums, tags, embeds Style Magazine, REST direct hors discord.py).
- Ops Windows native (PowerShell, services tray, auto-restart, **DPAPI** pour secrets locaux).
- Configuration as code (YAML preserve-roundtrip via ruamel).
- Observabilité (embeds couleur-codés, fichiers de session datés, audit scripts).
- **Reverse-engineering d'APIs privées** (DevTools Network, mimétisme Chrome, bypass Cloudflare, scraping de quotas).
- **GUI desktop Python** (Tkinter, threads daemon pour I/O réseau, signaux propres pour stop graceful).
- **Multi-stratégie de download** quand un outil principal lâche (ex : `dl_instagram.py` chaîne yt-dlp → manifest → probe `?img_index=N` → **gallery-dl** → thumbnail).

**Méthode** :
- **Besoin réel d'abord, code ensuite**. Aucun de ces bots n'est un « projet jouet » — ils tournent tous les jours, je les utilise tous les jours.
- **Itérations courtes, traçabilité totale**. Chaque session de développement est journalisée dans `_claude_logs/session_YYYY-MM-DD.md` avec décisions, anomalies, transitions de phase.
- **Qualité défensive**. Atomic writes, idempotence, watchdogs, supervisors. Je préfère investir 1 h de plus pour ne plus jamais avoir à réparer manuellement.
- **Documentation vivante**. Les `CLAUDE.md` à la racine sont mis à jour à chaque phase (A → S à ce jour) et servent de spec exécutable pour l'IA d'assistance.

---

## Lancement

### Mode tray (recommandé)

`bot_tray.py` est un watchdog avec icône system tray :

- Spawne `python -u bot.py` en subprocess sans console.
- Auto-restart 10 s après crash (toast Windows à chaque crash).
- Icône colorée (vert running / orange pause / rouge crash / bleu restart).
- Menu clic droit : voir logs / pause / redémarrer / autostart on/off / quitter.
- Fenêtre Tk de logs en direct avec auto-scroll, ouvrable depuis le menu.

Lancement silencieux : double-clic sur `start_tray.vbs` (utilise `pythonw.exe`, pas de console). Le menu tray peut installer/retirer le raccourci `BotGSTAR_Tray.vbs` dans le dossier Startup Windows pour auto-démarrage au login.

### Mode console (debug)

`start_bot.bat` reste utilisable :

- Boucle de relance (10 s entre crashs).
- Logs live console + `%TEMP%\BotGSTAR_startup.log`.
- **Sémantique « click = restart »** : au lancement, tue toute autre instance (ancien watchdog par titre, ancien `bot.py` par cmdline) puis prend la main. Garantit qu'une seule instance tourne.

Une seule des deux modes doit tourner à la fois — le tray watchdog gère lui-même le subprocess `bot.py`, le pousser en parallèle de `start_bot.bat` produirait deux instances concurrentes.

---

## Variables d'environnement

| Variable | Emplacement | Usage |
|---|---|---|
| `DISCORD_BOT_TOKEN` | `.env` racine | Token du bot Discord |
| `ANTHROPIC_API_KEY` | env Windows | Clé Claude (résumés CM, optionnel — fallback CLI subscription) |

---

## Documentation détaillée

| Fichier | Audience |
|---|---|
| `CLAUDE.md` | Architecture détaillée des cogs (référence dev, utilisée par Claude Code lui-même). |
| `CHANGELOG.md` | Évolutions par phase (R-A → R-H côté veille, S côté tray). |
| `GUIDE_VEILLE.md` | Manuel utilisateur veille RSS (lecteurs / admins / mainteneurs). |
| `Arsenal_Arguments/CLAUDE.md` | Architecture pipeline Arsenal. |
| `Arsenal_Arguments/_claude_logs/session_YYYY-MM-DD.md` | Journaux de session Claude Code par jour. |
| `COURS/CLAUDE.md` | Conventions disque côté projet COURS. |
| `COURS/CHANGELOG.md` | Évolutions du pipeline cours (Phase A → I). |

---

## Roadmap

- [ ] **Forum politique automatisé** — sur le modèle des forums correction COURS, créer 1 thread par sujet politique majeur (retraites, immigration, écologie) et y poster les vidéos+transcriptions+résumés agrégés.
- [ ] **Dashboard web** — Streamlit ou FastAPI + HTMX pour visualiser le CSV Arsenal, filtrer par mots-clés, exporter des décompositions.
- [ ] **Détection thèmes** — clustering sémantique (sentence-transformers) sur les transcriptions Whisper pour découvrir automatiquement les thèmes émergents.
- [ ] **Multi-tenant** — adapter le bot pour qu'un autre étudiant ISTIC puisse l'auto-héberger pour son groupe.
- [ ] **Veille internationale anglais/espagnol** — étendre le système RSS politique aux médias étrangers de gauche radicale.

---

## Contact

- **Email** : [gaylordaboeka@gmail.com](mailto:gaylordaboeka@gmail.com)
- **Instagram** : [@gaylordaboeka](https://www.instagram.com/gaylordaboeka/)
- **Localisation** : Rennes (35000)

> Si tu veux discuter politique, code, ou les deux : je suis à Rennes, salle de sport au Fitness Park Liberté, ou sur Discord. N'hésite pas.

---

_Ce README a été co-écrit avec Claude Code dans une session de pair-programming nocturne d'avril 2026, pendant que Whisper transcrivait 447 vidéos politiques en arrière-plan. Méta._
