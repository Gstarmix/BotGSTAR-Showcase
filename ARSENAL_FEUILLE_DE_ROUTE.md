# Arsenal Intelligence Unit — Feuille de route complète
## Dernière mise à jour : 28 avril 2026

---

# OÙ ON EN EST

## Ce qui est fait ✅

### Avril 2026 (mises à jour récentes)
- **Listener 6 plateformes** : `arsenal_pipeline.extract_urls()` étendu à TikTok / Instagram / YouTube / X / Reddit / Threads (auparavant TikTok+IG seulement). Routing auto vers `step_dl_generic` (yt-dlp universel) pour les non-tiktok/IG.
- **Bug 3/5 étapes corrigé** : `step_summarize` bascule sur CLI `claude --print` (subscription gratuite) au lieu de l'API Anthropic épuisée. Même bascule que COURS Phase L.
- **whisper_supervisor.py** : auto-restart si Whisper stalle > 15min, isolation auto des vidéos qui font hanger libav vers `_corrupted_videos/`. Posts Discord à chaque event.
- **post_whisper_orchestrator.py** : enchaîne OCR carrousels (`ocr_carousels.py`) + audit final après que Whisper a terminé. Posts récap final.
- **progress_monitor.py v2** : embeds Discord 1-par-fichier avec metadata Whisper (audio_duration, transcribe_time, ratio, segments, langue) extraites des `session.log`.
- **Veille politique** : nouveau cog `extensions/veille_rss_politique.py` (jumeau de `veille_rss.py` mais sur le serveur Veille/Arsenal). 7 catégories USAGE-orientées (Option C — pas idéologiques) : actu-chaude, arsenal-eco, arsenal-ecologie, arsenal-international, arsenal-social, arsenal-attaques, arsenal-medias. 40 sources françaises vérifiées. Digest matinal 8h Paris dans 7 salons sous catégorie `📡 VEILLE POLITIQUE`. Voir §12 de `CLAUDE.md`.
- **Traçabilité Claude Code** : sessions journalisées dans `Arsenal_Arguments/_claude_logs/session_YYYY-MM-DD.md` + sous-dossier `tasks/` pour les stdout/stderr des background scripts.
- **README portfolio** : réécrit pour expliquer le projet à des recruteurs / pairs (storytelling autour du parcours Gaylord, 26 ans, L1 ISTIC Rennes).

### Avant avril 2026
- **Migration Gemini → Claude** : summarize.py réécrit pour l'API Anthropic
- **Orchestrateur Discord** : arsenal_pipeline.py (cog) écoute #arsenal-liens et lance le pipeline auto
- **Audit & réparation** : arsenal_audit.py créé, CSV nettoyé (655 SUCCESS, 98 FAILED irrécupérables)
- **Nettoyage projet** : fichiers morts archivés dans _archived/, .env nettoyé, lock renommé, .gitignore à jour
- **README.md** à la racine de BotGSTAR
- **Bot Discord** opérationnel avec 8 commandes

## Ce qui reste à faire 🔲

### Étape 1 — Corriger le prompt (5 min)
Le prompt actuel dans summarize.py génère des tableaux markdown pour les sources. Discord ne les affiche pas.

**Action** : dans summarize.py, section 8 du SYSTEM_PROMPT, remplacer :
```
Format : Type | Organisme | Année | Description | URL ou "VÉRIFICATION NÉCESSAIRE"
```
par :
```
Lister chaque source sur une ligne avec un tiret, sous cette forme :
- Type — Organisme — Année — Description — URL ou "VÉRIFICATION NÉCESSAIRE"
Ne JAMAIS utiliser de tableau markdown. Toujours des tirets simples.
```

Ajouter aussi à la fin du SYSTEM_PROMPT :
```
RÈGLE DE FORMAT ABSOLUE : ne jamais utiliser de tableaux markdown (pas de |---|). 
Utiliser exclusivement des tirets (-) et des listes à puces (*) pour toute énumération.
```

### Étape 2 — Ajouter le mode Claude Code à summarize.py (à faire ensemble)
Réécrire summarize.py pour supporter deux modes :
- `--use-api` : appel API Anthropic (payant, rapide, images natives)
- `--use-claude-code` : appel `claude --print` via subprocess (gratuit Pro Max, plus lent)

Stratégie recommandée pour le batch de 655 contenus :
- Les 504 contenus TEXTE → claude --print (Pro Max, $0)
- Les 150 carrousels IMAGES → API Sonnet (~$8)
- Étaler sur 3-4 jours pour ne pas exploser le quota Pro Max

### Étape 3 — Lancer le re-résumé (étalé sur 3-4 jours)
```
Jour 1 : python summarize.py --re-summarize --text-only --use-claude-code
Jour 2 : suite si pas fini (quota Pro Max)
Jour 3 : python summarize.py --re-summarize --images-only --use-api --model claude-sonnet-4-20250514
```
Coût total estimé : ~$8 (images API uniquement)

### Étape 4 — Clear Discord + re-sync
1. Lancer le bot : `python bot.py`
2. Dans Discord : `!clear_arsenal` dans chaque forum pour vider les anciens résumés Gemini
3. Puis : `!sync_arsenal` pour republier les 655 résumés Claude

### Étape 5 — Configurer l'écoute automatique
Dans extensions/arsenal_pipeline.py, remplacer :
```python
LISTEN_CHANNEL_ID = None
```
par l'ID du salon #arsenal-liens (clic droit sur le salon → Copier l'identifiant).

Redémarrer le bot. Désormais, tout lien TikTok/Instagram posté dans ce salon déclenche automatiquement le pipeline complet.

### Étape 6 — Test pipeline complet
Poster un lien TikTok ou Instagram dans #arsenal-liens. Vérifier que le bot :
1. Réagit avec 🔄
2. Télécharge
3. Transcrit
4. Résume
5. Publie dans le bon forum
6. Réagit avec ✅

---

# COMMANDES DISPONIBLES

| Commande | Ce qu'elle fait |
|----------|----------------|
| `!pipeline <url>` | Pipeline complet sur une URL |
| `!pipeline_batch` | Traite tous les PENDING (transcribe → summarize → sync) |
| `!pipeline_resummarize` | Re-résume TOUS les contenus (migration batch) |
| `!pipeline_status` | État du pipeline (actif ou inactif) |
| `!sync_arsenal` | Publie les résumés non publiés sur Discord |
| `!stats_arsenal` | Statistiques du CSV |
| `!clear_arsenal` | Vide un forum Discord |

---

# USAGE QUOTIDIEN (une fois tout configuré)

## Ajouter un contenu
Poste un lien TikTok ou Instagram dans #arsenal-liens. C'est tout. Le bot fait le reste.

## Ajouter plusieurs contenus
Poste plusieurs liens (un par ligne ou dans le même message). Le bot les traite séquentiellement.

## Vérifier l'état
`!pipeline_status` ou `!stats_arsenal`

## Si le bot plante
```
cd "C:\Users\Gstar\OneDrive\Documents\BotGSTAR"
python bot.py
```

## Lancer manuellement une étape
```
cd Arsenal_Arguments
python dl_tiktok.py --url "https://..."     # download seul
python csv_normalize.py                      # normaliser le CSV
python summarize.py --id "ABC123"            # résumer un seul contenu
python arsenal_audit.py                      # vérifier la santé des données
python arsenal_audit.py --full-repair        # tout réparer
```

---

# PROJETS FUTURS

## 1. Support YouTube / X (Twitter) / Reddit
yt-dlp supporte déjà ces plateformes. Il faut :
- Ajouter les regex de détection dans arsenal_pipeline.py
- Créer un dl_generic.py qui wrappera yt-dlp pour toute plateforme
- Le reste du pipeline (transcription, résumé, publication) ne change pas
Difficulté : faible. Temps estimé : 1 session.

## 2. arsenal_search.py — Exploiter la base de connaissances
Le fichier tous_les_resumes.txt (3+ Mo) est trop gros pour le contexte Claude.
Solution : un outil de recherche locale qui envoie uniquement les extraits pertinents.

Commandes envisagées :
```
python arsenal_search.py "arguments contre la flat tax"
python arsenal_search.py --roleplay "débat avec un libéral sur la fiscalité"
python arsenal_search.py --quiz "teste-moi sur les sophismes"
```

Ou intégré au bot Discord :
```
!arsenal search "justice fiscale Zucman"
!arsenal quiz économie
!arsenal debate "un RN me dit que l'immigration coûte cher"
```

Difficulté : moyenne. Temps estimé : 2-3 sessions.

## 3. Intégration projet COURS
Le même bot Discord (BotGSTAR) servira pour le pipeline COURS :
- Cog séparé : extensions/cours_pipeline.py
- Commande : `!cours publish cm an1 7 1602`
- Utilise Claude Code subprocess (Pro Max), pas l'API
- Pipeline : audio → transcription → résumé → publication Discord
Les deux systèmes (Arsenal + COURS) cohabitent dans le même bot.

---

# ARCHITECTURE FICHIERS (état actuel)

```
BotGSTAR/
├── bot.py                              # Point d'entrée
├── .env                                # DISCORD_BOT_TOKEN
├── README.md                           # Documentation
├── extensions/
│   ├── arsenal_publisher.py            # Publication forums Discord
│   ├── arsenal_pipeline.py             # Orchestrateur auto (écoute liens)
│   ├── embed_logger.py                 # Mudae (hors scope)
│   └── rules.py                        # Règlement (hors scope)
├── Arsenal_Arguments/
│   ├── arsenal_config.py               # Config centralisée
│   ├── dl_instagram.py                 # Téléchargeur IG
│   ├── dl_tiktok.py                    # Téléchargeur TT
│   ├── csv_normalize.py                # Nettoyage CSV
│   ├── arsenal_transcribe.ps1          # Wrapper Whisper
│   ├── whisper_engine.ps1              # Moteur Whisper GPU
│   ├── summarize.py                    # Résumeur Claude
│   ├── arsenal_audit.py                # Audit et réparation
│   ├── suivi_global.csv                # Source de vérité (753 lignes)
│   └── yt-dlp.exe                      # Binaire
├── _archived/                          # Fichiers morts
│   ├── arsenal_sync.py
│   └── OLD/ (26 anciens scripts)
└── CLAUDE.md                           # Contexte pour Claude Code
```

---

# RÉSUMÉ EN UNE PHRASE

Reviens, dis "on reprend Arsenal", et on fait dans l'ordre : correction prompt → mode Claude Code dans summarize.py → re-résumé étalé sur 3 jours → clear Discord → re-sync → configurer l'écoute auto → c'est fini.
