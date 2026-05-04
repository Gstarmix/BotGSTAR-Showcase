# Compagnon_Revision

> Outil personnel de révision orale active à la voix.
> Mode colle d'oral, vouvoiement strict, capture des points faibles vers SRS.
>
> **Phase A** (MVP texte pur) — démarrage 1-2 mai 2026.

---

## C'est quoi

Un compagnon de révision qui m'interroge à voix haute sur un exo de TD ou de CC, dans le style d'un colleur de prépa. Push-to-talk → Whisper → Claude → texte affiché. Capture les points où je galère, les écrit dans un JSON, à terme les pousse dans Anki pour révision espacée.

Projet sœur de BotGSTAR, autonome, réutilise le module quota d'Arsenal_Arguments et le moteur Whisper GPU.

---

## Lancer une session

```powershell
cd C:\Users\Gstar\OneDrive\Documents\BotGSTAR\Compagnon_Revision
python compagnon.py AN1 TD 5 3
```

→ ouvre le navigateur sur `http://127.0.0.1:5680/`, Claude pose la première question.

Arguments :
- **matière** : `AN1`, `EN1`, `PSI`, `ISE`, `PRG2`
- **type** : `TD`, `CC`, `Examen`
- **num** : numéro du TD/CC (ex : `5`)
- **exo** : numéro de l'exercice (ex : `3`) ou `full` pour toute la séance

Reprendre une session interrompue :
```powershell
python compagnon.py AN1 TD 5 3 --resume
```

---

## Push-to-talk

Dans le navigateur, **maintenir [Espace]** pour parler, **relâcher** pour envoyer. La transcription Whisper part vers Claude, sa réponse s'affiche en streaming.

L'hotkey est **global** (marche même si le navigateur n'a pas le focus), géré par la lib `keyboard` côté Python.

---

## Démarrer une session de dev (Claude Code)

```powershell
.\start_claude_code_session.ps1 -Task "Implémente le parser des balises selon ARCHITECTURE.md §3"
```

Lance Claude Code dans le projet avec :
- Vérification de la présence des fichiers de doctrine
- Affichage du quota Claude Max 5x au démarrage
- Préambule standard injecté (mode économe en tokens, règles absolues)
- Switch CLI subscription / API Anthropic selon `_secrets/engine_pref.json`

Voir le script lui-même pour les options (`-ExtraFile`, `-Verbose`).

---

## Arborescence

```
Compagnon_Revision/
├── CLAUDE.md                       Manuel pour Claude Code (ne pas modifier en dev)
├── ARCHITECTURE.md                 Spec technique détaillée
├── README.md                       Ce fichier
├── CHANGELOG.md                    Phases datées
├── compagnon.py                    Entry point
├── config.py                       Constantes, chemins
├── start_claude_code_session.ps1   Lanceur dev
│
├── _prompts/
│   └── PROMPT_SYSTEME_COMPAGNON.md Cœur pédagogique (sacré)
│
├── _scripts/                       Code Python, organisé par responsabilité
│   ├── audio/                      Capture micro + Whisper + TTS (Phase B)
│   ├── dialogue/                   Client Claude + parser + state machine
│   ├── watchers/                   photo_watcher.py (Phase B)
│   ├── web/                        Flask + SSE + front HTML
│   └── quota/                      Wrapper claude_usage.py (Arsenal)
│
├── _sessions/                      Logs JSON par séance
├── _points_faibles/                Agrégats CSV par matière (Phase B)
├── _photos_inbox/                  Drop Tailscale (Phase B)
├── _cache/tts/                     MP3 pré-générés (Phase B)
├── _secrets/                       Cookies, engine_pref.json (gitignore)
├── _logs/                          Logs rotation quotidienne
└── tests/                          Pytest
```

---

## Où trouver quoi

| Je cherche... | Je vais voir... |
|---|---|
| Comment Claude est censé me parler | `_prompts/PROMPT_SYSTEME_COMPAGNON.md` |
| Comment Claude Code est censé coder | `CLAUDE.md` §1 (séparation rôles), §3 (conventions) |
| Le schéma JSON d'une session | `ARCHITECTURE.md` §2 |
| Comment marche le parser SSE | `ARCHITECTURE.md` §3 (machine à états) |
| Mes points faibles AN1 | `_points_faibles/AN1_points_faibles.csv` (Phase B) |
| Pourquoi une session a été interrompue | Logs `_logs/compagnon_YYYY-MM-DD.log` + champ `interrupted_at` du JSON |
| Mon quota Claude Max 5x | Sidebar du front Flask, ou `python ../Arsenal_Arguments/claude_usage.py --fetch` |

---

## Règles que je dois respecter (note à moi-même)

1. **Ne pas modifier `CLAUDE.md`, `ARCHITECTURE.md`, `_prompts/PROMPT_SYSTEME_COMPAGNON.md` sans concertation Claude.ai.** Ce sont les fichiers de doctrine. Si une session révèle un problème de comportement, je remonte à Claude.ai qui édite.
2. **Ne pas éditer manuellement `_points_faibles/*.csv`.** C'est généré à partir de `_sessions/*.json` (Phase B).
3. **Ne pas commit `_secrets/`.** Vérifier qu'il est dans `.gitignore` si je versionne un jour.
4. **Mode économe en tokens** par défaut quand je dev avec Claude Code (cf. `CLAUDE.md` §6).
5. **Après chaque phase validée**, ajouter une entrée datée au `CHANGELOG.md`.

---

## Limites assumées (Phase A)

Ces limites sont **conscientes**, prévues pour évoluer en Phases B/C. Ne pas chercher à les contourner en Phase A.

- Pas de TTS — Claude répond en texte affiché uniquement.
- Pas de réception photo — il faudra dropper manuellement les photos dans `_photos_inbox/` plus tard (Phase B), et coder le watcher (Phase B).
- Pas de SRS Anki — les points faibles sont capturés en JSON mais pas exportés (Phase C).
- Pas de transfert auto téléphone → PC — j'utilise déjà Tailscale + drop manuel le temps de la Phase A. Le serveur Flask qui reçoit les photos depuis le téléphone arrivera en Phase C.
- Whisper non-streaming — la transcription se fait après que le WAV est complet (relâchement espace). En pratique, ça ajoute ~1-2 secondes de latence par tour de parole. Acceptable Phase A.
- Pas de mode multi-séances continues — une session = un TD/CC, point. Phase D.

---

## Stack technique

- **Python 3.12** sur Windows 11
- **faster-whisper large-v3** sur RTX 2060 (int8_float16 + VAD)
- **Flask** + Server-Sent Events pour le streaming Claude → front
- **`keyboard`** pour le push-to-talk global
- **`sounddevice`** pour la capture audio
- **Claude Opus 4.7** via CLI subscription (Max 5x) ou API Anthropic (switch dans `_secrets/engine_pref.json`)
- **`claude_usage.py`** d'Arsenal_Arguments pour le tracking quota live

---

## Quand quelque chose casse

| Symptôme | Vérifier d'abord... |
|---|---|
| Claude ne répond pas | `_logs/compagnon_YYYY-MM-DD.log` (erreur API ?), puis quota |
| Whisper transcrit mal | Niveau micro, langue forcée à `fr`, VAD `min_silence_duration_ms` |
| Le push-to-talk ne capte pas | `keyboard` peut nécessiter admin sur certains setups Windows |
| Session corrompue / json invalide | Dernière sauvegarde `.tmp` à côté du `.json` (atomic write a échoué) |
| Quota check toujours en erreur | Cookie `claude.ai` expiré, `python ../Arsenal_Arguments/claude_usage.py --set-cookie` |
| Le navigateur ne se connecte pas à Flask | Port 5680 déjà pris ? Pare-feu ? `netstat -ano \| findstr 5680` |

Si rien ne marche, je note le symptôme + extraits de logs et je remonte à Claude.ai (problème d'archi/pédagogie) ou Claude Code (bug de code).

---

## Pourquoi ce projet existe

J'avais déjà construit RoleplayOverlay (lecture passive de scripts oraux figés) et ça ne m'a pas aidé à apprendre — je lisais sans comprendre. La révision active à voix haute avec un interlocuteur exigeant me manquait : une vraie colle, pas un prof bienveillant qui valide tout.

Le compagnon résout ce trou : il connaît mon TD, il m'interroge sec, il refuse mes formulations floues, il capture les points où je galère pour que je les retravaille en SRS plus tard.

Cible : réussir CC3 en mai-juin 2026 sans avoir à aller à la BU pour me forcer à réviser.
