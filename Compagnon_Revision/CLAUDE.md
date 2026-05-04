# CLAUDE.md — Compagnon_Revision

> **Manuel d'instructions permanent pour Claude Code.**
> Lu au début de chaque session de développement.
> Ne touche pas à ce fichier sans validation explicite de Gstar.

---

## 0. PRÉSENTATION RAPIDE

`Compagnon_Revision/` est un projet sœur de BotGSTAR, autonome dans son runtime mais qui réutilise des briques d'`Arsenal_Arguments/` (notamment le scraper de quota Pro Max `claude_usage.py` et le moteur `faster-whisper` GPU).

**Objectif fonctionnel** : permettre à Gstar de réviser à voix haute un TD ou CC universitaire en dialogue avec Claude (mode colle d'oral, vouvoiement strict). Push-to-talk → Whisper → Claude → texte affiché + TTS sélectif sur passages clés. Capture des points faibles vers SRS Anki. Réception de photos de brouillon papier via watcher de dossier.

**Public** : Gstar uniquement, étudiant L1 Informatique-Électronique ISTIC Rennes, en préparation des CC3 (mai-juin 2026). Pas de logique multi-utilisateur.

**Non-objectif** : ce n'est pas un produit. C'est un outil personnel. Pas de packaging pip, pas d'installeur, pas de doc utilisateur grand public. Le `README.md` est pour Gstar, point.

---

## 1. SÉPARATION DES RÔLES IA (RÈGLE FONDATRICE)

C'est le pattern habituel de Gstar (cf. COURS/, Arsenal_Arguments/). À respecter strictement :

### 1.1 Claude.ai (chat web) — la conception
- Rédige les fichiers de doctrine : `CLAUDE.md`, `README.md`, `ARCHITECTURE.md`, `_prompts/PROMPT_SYSTEME_COMPAGNON.md`, `CHANGELOG.md`
- Décide l'archi, les noms, les conventions, les phases
- Audit de code à la demande, propositions de refactor
- **N'écrit jamais de code à exécuter.** Si Claude.ai produit du code, c'est à titre d'exemple ou de spec, jamais à coller dans un fichier `.py` du projet.

### 1.2 Claude Code (CLI) — l'exécution
- Code l'orchestrateur, les watchers, les helpers, les wrappers, l'intégration API
- Code les tests
- Lit `CLAUDE.md` + `ARCHITECTURE.md` + `_prompts/PROMPT_SYSTEME_COMPAGNON.md` au début de chaque session pour se calibrer
- **Ne touche jamais à `_prompts/`**, jamais à `CLAUDE.md`, jamais à `README.md`, jamais à `ARCHITECTURE.md`. Ce sont des artefacts de Claude.ai.
- Si Claude Code détecte une incohérence ou un manque dans la doctrine, il demande à Gstar avant de coder un workaround. **Mieux vaut s'arrêter et clarifier que coder en zone grise.**

### 1.3 Gstar — l'arbitrage
- Décide les pivots d'archi
- Valide ou rejette les propositions
- Teste en conditions réelles (sessions de révision)
- Reporte les bugs ou frictions vers le bon canal (Claude.ai pour archi/pédagogie, Claude Code pour bug de code)

### 1.4 Le prompt système du compagnon — sacré
`_prompts/PROMPT_SYSTEME_COMPAGNON.md` est le cœur pédagogique. Il définit *comment* Claude (en runtime, dans le compagnon) interroge Gstar. **Aucune des trois IA ne le modifie sans concertation explicite avec Gstar.** Si une session révèle un problème de comportement (Claude trop bavard, trop tendre, etc.), Gstar le remonte à Claude.ai qui édite le prompt système, pas Claude Code.

---

## 2. ARBORESCENCE DU PROJET

```
Compagnon_Revision/
├── CLAUDE.md                       # ce fichier
├── README.md                       # guide utilisateur (pour Gstar)
├── ARCHITECTURE.md                 # spec technique détaillée
├── CHANGELOG.md                    # phases datées
├── compagnon.py                    # orchestrateur principal (entry point)
├── config.py                       # constantes, chemins, racines
├── requirements.txt                # dépendances Python
│
├── _prompts/
│   └── PROMPT_SYSTEME_COMPAGNON.md # cœur pédagogique, sacré
│
├── _scripts/
│   ├── audio/
│   │   ├── listener.py             # capture micro + push-to-talk
│   │   ├── transcribe_stream.py    # wrapper faster-whisper
│   │   └── tts.py                  # Edge TTS primary + Piper fallback
│   ├── dialogue/
│   │   ├── claude_client.py        # wrapper API/CLI Claude
│   │   ├── prompt_builder.py       # assemble le contexte par session
│   │   ├── parser.py               # extrait les balises <<<...>>> du stream
│   │   └── session_state.py        # état machine d'une séance
│   ├── watchers/
│   │   └── photo_watcher.py        # surveille _photos_inbox/
│   ├── web/
│   │   ├── app.py                  # Flask + SSE
│   │   ├── templates/index.html    # UI du dialogue
│   │   └── static/                 # CSS, JS
│   └── quota/
│       └── quota_check.py          # wrapper sur Arsenal_Arguments/claude_usage.py
│
├── _sessions/                      # logs JSON par séance (atomic write)
│   └── YYYY-MM-DD_{MAT}_{TYPE}{N}_ex{n}.json
│
├── _points_faibles/                # agrégats par matière (rebuild idempotent)
│   ├── AN1_points_faibles.csv
│   ├── EN1_points_faibles.csv
│   └── PSI_points_faibles.csv
│
├── _photos_inbox/                  # drop Tailscale/Syncthing depuis téléphone
│
├── _cache/
│   └── tts/                        # MP3 pré-générés des relances types
│
├── _secrets/                       # cookies, configs sensibles (gitignore)
│   └── engine_pref.json            # CLI subscription vs API Anthropic
│
├── _logs/
│   └── compagnon_YYYY-MM-DD.log
│
└── tests/
    ├── test_parser.py
    ├── test_session_state.py
    └── test_prompt_builder.py
```

---

## 3. CONVENTIONS DE CODE

### 3.1 Python
- Python 3.12 sur Windows 10/11
- PEP 8, sans dogmatisme — lignes 100 chars autorisées (comme dans Arsenal_Arguments/)
- Type hints partout sur les signatures publiques. Pas obligatoire dans le corps.
- Docstrings courts en français pour les fonctions publiques. Format triple-quote, une ligne de résumé suivie d'un bloc paramètres si pertinent.
- Logging via `logging` standard, pas de `print()` en code prod (sauf entry point CLI). Logger nommé par module : `logger = logging.getLogger(__name__)`
- Pas de chemins absolus en dur. Tout passe par `config.py` qui expose les constantes :
  - `COURS_ROOT = Path(r"C:\Users\Gstar\OneDrive\Documents\COURS")`
  - `PROJECT_ROOT = Path(__file__).parent`
  - `SESSIONS_DIR = PROJECT_ROOT / "_sessions"`
  - etc.

### 3.2 Imports
- Imports standard d'abord, puis tiers, puis locaux. Une ligne vide entre les groupes.
- Pour réutiliser `claude_usage.py` d'Arsenal_Arguments, en Phase A on fait un `sys.path.insert` minimal en tête de `quota_check.py` :
  ```python
  import sys
  from pathlib import Path
  ARSENAL_PATH = Path(__file__).resolve().parents[2] / "Arsenal_Arguments"
  if str(ARSENAL_PATH) not in sys.path:
      sys.path.insert(0, str(ARSENAL_PATH))
  from claude_usage import fetch_usage  # noqa: E402
  ```
  En Phase B, on transformera Arsenal_Arguments en vrai package importable. Pas urgent.

### 3.3 Nommage
- `snake_case` pour fonctions et variables
- `PascalCase` pour classes
- `UPPER_SNAKE` pour constantes
- Préfixe `_` pour privé interne au module
- Fichiers `.py` en `snake_case`, jamais d'espaces ni de tirets

### 3.4 Atomic writes obligatoires
Toute écriture vers `_sessions/`, `_points_faibles/`, `_secrets/`, `_cache/` (hors MP3) doit être atomique.

Pattern standard, à reproduire :
```python
import json
import os
from pathlib import Path

def atomic_write_json(path: Path, data: dict) -> None:
    """Écriture atomique d'un JSON via .tmp + os.replace()."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
```

Helper centralisé dans `_scripts/dialogue/session_state.py` ou un futur `_scripts/utils.py`. Pas de `f.write(json.dumps(...))` direct sur le fichier final.

### 3.5 Idempotence
- Un script relancé deux fois doit produire le même résultat. Pas de duplication de session, pas de réécriture si rien n'a changé.
- Le rebuild de `_points_faibles/{MAT}_points_faibles.csv` doit être 100% reproductible à partir de `_sessions/*.json`. Aucune édition manuelle de ce fichier autorisée — il est généré.
- Pour les fichiers MP3 du cache TTS, dédup par SHA1 du texte d'entrée. Cf. `_cache/tts/<sha1>.mp3`.

### 3.6 Schémas JSON versionnés
Tout fichier JSON persistant inclut un champ `schema_version: int` à la racine.

Versions actuelles :
- `_sessions/*.json` : `schema_version: 1`
- `_secrets/engine_pref.json` : `schema_version: 1`

Toute modification de schéma incrémente la version et inclut une migration douce (lecture des deux schémas pendant une période, normalisation à l'écriture).

---

## 4. CONTRAT DES BALISES (PARSER)

Le `_scripts/dialogue/parser.py` extrait des balises spéciales du stream Claude avant affichage. Cf. `_prompts/PROMPT_SYSTEME_COMPAGNON.md` §7 pour la spec côté prompt.

### 4.1 Balises supportées
| Balise | Format | Action côté Python |
|--------|--------|-------------------|
| `<<<TTS>>>...<<<END>>>` | texte libre, ≤50 mots | extrait, envoie au moteur TTS, retourne dans le flux affiché |
| `<<<WEAK_POINT>>>{...}<<<END>>>` | JSON minifié sur une ligne | extrait, valide le schéma, écrit dans la session courante, **retire** du flux affiché |
| `<<<END_SESSION>>>` | balise nue | déclenche la finalisation propre de la session, **retire** du flux affiché |

### 4.2 Streaming SSE — accumulation de buffer
Les balises peuvent arriver coupées en plusieurs chunks SSE. Le parser doit :
1. Accumuler le buffer reçu jusqu'à reconnaître une balise complète (ouverture **et** fermeture pour `<<<TTS>>>` et `<<<WEAK_POINT>>>`)
2. Pour `<<<END_SESSION>>>`, reconnaître la balise complète (pas de fermeture séparée)
3. Diffuser au front Flask **uniquement** les portions de texte qui sont "stables" (pas en train d'être à l'intérieur d'une balise potentielle)

Pattern recommandé : machine à états (`OUTSIDE`, `INSIDE_TTS`, `INSIDE_WEAK_POINT`, `INSIDE_END_SESSION_PROBE`).

### 4.3 Validation du schéma `cm_anchor`
Le JSON `<<<WEAK_POINT>>>` contient un champ `cm_anchor` qui peut être :
- `null`
- Un objet `{transcription?, poly?, section?}` avec **au moins un** sous-champ présent

Le parser valide à la lecture et logge un warning si malformé, **mais ne fait pas planter la session**. La donnée est écrite avec `cm_anchor: null` et un flag `cm_anchor_malformed: true` à côté pour audit ultérieur. Tolérance > rigidité en runtime.

### 4.4 Validation des chemins
Les chemins dans `cm_anchor.transcription` et `cm_anchor.poly` sont **relatifs à `COURS_ROOT`**. Le parser vérifie qu'ils ne commencent pas par `C:\` ou `/` ou `\\`. Si oui, warning + normalisation (extraction de la portion relative quand possible, sinon `null`).

---

## 5. INTÉGRATION QUOTA CLAUDE MAX 5x

### 5.1 Réutilisation de `claude_usage.py`
Le module est dans `Arsenal_Arguments/claude_usage.py`. Pattern d'import : cf. §3.2.

`_scripts/quota/quota_check.py` est un **wrapper mince** qui :
- Importe `fetch_usage()` depuis Arsenal
- Expose deux fonctions au reste du compagnon :
  - `can_start_session() -> tuple[bool, str]` — retourne `(True, "")` si quota OK, `(False, "raison humaine")` sinon
  - `get_usage_snapshot() -> dict` — snapshot pour affichage live dans le front Flask

### 5.2 Seuils par défaut
- **Démarrage de session** : refusé si `five_hour.utilization > 85` ou `seven_day.utilization > 90`
- **En cours de session** : warning visuel si `five_hour > 90`, mais pas d'arrêt forcé (la session a déjà commencé, on ne casse pas le flow)

### 5.3 Switch CLI subscription / API Anthropic
Persistance dans `_secrets/engine_pref.json` :
```json
{
  "schema_version": 1,
  "engine": "cli_subscription",
  "updated_at": "2026-05-01T16:30:00+02:00"
}
```

Valeurs possibles pour `engine` :
- `"cli_subscription"` : appel via `subprocess` du CLI `claude` avec `ANTHROPIC_API_KEY` unset dans l'env (force OAuth/keychain). Mode par défaut.
- `"api_anthropic"` : appel via SDK `anthropic` Python avec clé API à la consommation. Pour les cas où Gstar veut réviser malgré quota tendu.

Le radio button de switch est exposé dans le front Flask. Toute écriture de ce fichier passe par atomic write (cf. §3.4).

### 5.4 Affichage live dans le front
Endpoint Flask `/api/quota` qui retourne le snapshot toutes les 60 secondes côté client (poll, pas SSE — pas besoin de temps réel à la seconde). Affiche 4 barres : session 5h, hebdo 7j Opus, hebdo Sonnet, overage credits.

---

## 6. MODE ÉCONOME EN TOKENS (PHASE DE CONSTRUCTION)

Pendant que Claude Code construit le projet, on bosse en **mode économe** pour préserver le quota Max 5x de Gstar pendant les 8 semaines avant CC3.

### 6.1 Règles côté Claude Code
- **Specs courtes** : Gstar te passe une spec ciblée, tu codes ce qui est demandé, point. Pas d'extension de scope.
- **Code par bouts** : un module à la fois, validation par Gstar, puis module suivant. Pas de génération de 10 fichiers d'un coup.
- **Contexte minimal** : ne charge en lecture que les fichiers nécessaires pour la tâche en cours. Si tu as besoin de comprendre un module pour en coder un autre, demande à Gstar de te le pointer plutôt que d'explorer.
- **Pas de refactor préventif** : si un fichier marche, tu n'y touches pas même si tu trouves le style sous-optimal. Tu signales à Gstar et il décide.
- **Pas de tests exhaustifs en Phase A** : un test par fichier critique (parser, session_state, prompt_builder), pas plus. La couverture viendra en Phase B.

### 6.2 Quand basculer en mode verbeux
Sur autorisation explicite de Gstar uniquement, et seulement pour :
- Pivot d'archi majeur (changement de stack, restructuration globale)
- Bug profond qui nécessite de comprendre le système entier
- Refactor de fin de phase planifié

### 6.3 Commande de session Claude Code recommandée
Au début de chaque session de dev, Gstar lance Claude Code avec une commande type :
```
Lis CLAUDE.md, ARCHITECTURE.md, et le fichier que je vais te pointer.
Mode économe en tokens.
Tâche : [description courte de ce qu'il faut coder].
Ne touche pas à _prompts/, CLAUDE.md, README.md, ARCHITECTURE.md.
Demande avant de coder en cas de doute.
```

---

## 7. NOMMAGE DES SESSIONS

Format de fichier : `_sessions/YYYY-MM-DD_{MAT}_{TYPE}{N}_ex{n}.json`

Exemples :
- `_sessions/2026-05-02_AN1_TD5_ex3.json`
- `_sessions/2026-05-08_EN1_CC2_ex1.json`
- `_sessions/2026-05-15_PSI_TD7_full.json` (toute la séance, pas un seul exo)

Champs :
- `MAT` : code matière sur 3-4 lettres (`AN1`, `EN1`, `PSI`, `ISE`, `PRG2`)
- `TYPE` : `TD` ou `CC` ou `Examen`
- `N` : numéro du TD/CC (entier)
- `n` : numéro de l'exercice traité (entier), ou `full` si la session a couvert tous les exos

### 7.1 Sessions multiples le même jour
Si Gstar fait deux sessions sur le même exo le même jour (rare mais possible : reprise après pause longue), le second fichier prend un suffixe `_b` : `2026-05-02_AN1_TD5_ex3_b.json`.

### 7.2 Sessions interrompues
Si une session s'arrête sans `<<<END_SESSION>>>` propre (crash, fermeture brutale), le fichier garde l'extension `.json` mais inclut un champ racine `"interrupted": true` et `"interrupted_at": ISO`.

À la reprise (flag `[RESUME_SESSION]` envoyé au prompt), le système charge cette session, marque `"resumed_at": ISO`, et continue. À la fin propre, `"interrupted": false` est écrit.

---

## 8. LOGGING

### 8.1 Fichier
- `_logs/compagnon_YYYY-MM-DD.log` (rotation quotidienne)
- Niveau par défaut : `INFO` en prod, `DEBUG` si variable d'env `COMPAGNON_DEBUG=1`
- Format : `%(asctime)s [%(name)s] %(levelname)s: %(message)s`

### 8.2 Console
- Le front Flask redirige les logs `WARNING+` vers une mini-console visible dans une sidebar repliable (utile pour debug en session sans ouvrir de terminal)

### 8.3 Ce qu'on log toujours
- Démarrage/fin de session (timestamp, matière, exo)
- Appels Claude (durée, tokens consommés si dispo, succès/échec)
- Capture de point faible (concept, score)
- Erreurs Whisper, Edge TTS, Piper
- Quota check au démarrage de session

### 8.4 Ce qu'on log jamais
- Le contenu détaillé des transcriptions audio (privacy + verbosité)
- Les prompts complets envoyés à Claude (verbosité — on logge juste la longueur en tokens)
- Les chemins absolus contenant `Gstar` ou `OneDrive` quand on peut log un chemin relatif

---

## 9. PHASES DE CONSTRUCTION (ROADMAP)

### Phase A — MVP boucle dialogue texte pure
**Date prévue** : démarrage 1-2 mai 2026.
**Scope** : la boucle minimale qui démontre que tout le pipeline tourne, sans fioritures.

Inclut :
- `compagnon.py` entry point
- `_scripts/audio/listener.py` push-to-talk (touche espace globale via `keyboard` ou `pynput`, fallback navigateur)
- `_scripts/audio/transcribe_stream.py` wrapper faster-whisper (réutilise la stack RTX 2060 large-v3 int8_float16 + VAD)
- `_scripts/dialogue/claude_client.py` wrapper appels Claude (CLI subscription par défaut)
- `_scripts/dialogue/prompt_builder.py` assemble le contexte (énoncé + transcription CM + prompt système + état session)
- `_scripts/dialogue/parser.py` extraction balises `<<<...>>>` (les 3 balises supportées dès Phase A)
- `_scripts/dialogue/session_state.py` état d'une session, capture des points faibles dans `_sessions/*.json`
- `_scripts/quota/quota_check.py` wrapper Arsenal `claude_usage.py`
- `_scripts/web/app.py` Flask minimal + SSE pour le streaming Claude
- `_scripts/web/templates/index.html` UI minimale (zone dialogue, indicateur push-to-talk, sidebar quota)
- `tests/test_parser.py` couverture basique de l'extraction des balises

Exclut :
- TTS (Edge ou Piper) — Phase B
- Watcher photos — Phase B
- Rebuild `_points_faibles/*.csv` — Phase B
- Export Anki — Phase C
- Mode reprise de session — Phase B

**Critère de validation Phase A** : Gstar peut faire une session de révision de 30 min, AN1 TD5, dialogue texte propre, points faibles capturés en JSON, quota tracké en live dans la sidebar. Pas de TTS, pas de photo, pas de SRS — juste la boucle.

### Phase B — TTS, photos, reprise, agrégat points faibles
**Date prévue** : 2-3 semaines après validation Phase A.
**Scope** :
- `_scripts/audio/tts.py` Edge TTS primary + Piper fallback (cf. ARCHITECTURE.md pour le détail)
- Pré-génération du cache TTS pour les relances types
- `_scripts/watchers/photo_watcher.py` watchdog sur `_photos_inbox/`
- Mode reprise de session (`[RESUME_SESSION]`)
- Script `rebuild_weak_points.py` qui re-scanne tous les `_sessions/*.json` et génère les `_points_faibles/{MAT}_points_faibles.csv`
- Tests étendus

### Phase C — SRS Anki, transfert photo téléphone→PC
**Date prévue** : après les CC3.
**Scope** :
- Export `.apkg` Anki à partir des `_points_faibles/*.csv`
- Mini serveur Flask exposé via Tailscale pour réception des photos depuis le téléphone (POST direct depuis l'app Android "Partager")
- Calendrier de révision (J+1, J+3, J+7, J+14 selon score)

### Phase D et au-delà
À définir selon retour d'usage. Pistes :
- Mode multi-séances continues (revoir un point faible historique en cours de séance courante)
- Stats personnelles (progression dans le temps, matières où Gstar avance le mieux/le moins)
- Intégration avec le cog `cours_pipeline.py` de BotGSTAR pour pousser les récaps de session dans Discord

---

## 10. RÈGLES ABSOLUES (À NE JAMAIS ENFREINDRE)

1. **Claude Code ne touche pas à `_prompts/`, `CLAUDE.md`, `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`.** Ce sont des artefacts de doctrine, propriété de Claude.ai et Gstar.

2. **Pas de chemin absolu en dur dans le code.** Tout passe par `config.py`. Exception tolérée : le `sys.path.insert` vers Arsenal_Arguments en Phase A (cf. §3.2), à supprimer en Phase B.

3. **Atomic writes obligatoires** sur `_sessions/`, `_points_faibles/`, `_secrets/`. Cf. §3.4.

4. **Idempotence** : un script relancé donne le même résultat. Pas de duplication, pas de side-effects cumulatifs. Cf. §3.5.

5. **Mode économe par défaut** pendant la phase de construction. Cf. §6.

6. **Pas de modification du prompt système sans concertation Gstar.** Le prompt système est sacré. Cf. §1.4.

7. **Pas d'upload du dossier `_secrets/` nulle part.** Il doit être dans `.gitignore` à vie.

8. **Pas de log des chemins absolus contenant des infos identifiantes** (`Gstar`, `OneDrive`, etc.) quand un chemin relatif suffit. Cf. §8.4.

9. **Tolérance en runtime, rigidité en schéma** : le parser accepte les malformations Claude (et logge), mais le schéma JSON est strict en lecture-écriture côté code Python.

10. **Pas de scope creep en cours de phase.** Si Claude Code ou Gstar voit une amélioration potentielle qui n'est pas dans la phase courante, ça va dans `TODO_GLOBAL.md` ou en CHANGELOG (note "reporté Phase suivante"), pas dans le code.

---

## 11. POINTERS UTILES

- Spec pédagogique : `_prompts/PROMPT_SYSTEME_COMPAGNON.md` (382 lignes, v0.2)
- Spec technique : `ARCHITECTURE.md` (à venir, livré juste après ce CLAUDE.md)
- Guide utilisateur : `README.md` (à venir, livré après ARCHITECTURE.md)
- Module quota réutilisé : `../Arsenal_Arguments/claude_usage.py`
- Stack Whisper réutilisée : `../Arsenal_Arguments/` (à identifier le module exact en Phase A)
- Racine des cours : `C:\Users\Gstar\OneDrive\Documents\COURS\` exposée via `config.COURS_ROOT`

---

## 12. RAPPEL FINAL

Tu es Claude Code. Tu codes ce qu'on te demande, dans le périmètre de la phase courante, en mode économe en tokens, sans toucher aux fichiers de doctrine.

Si tu hésites, demande. Si tu détectes une incohérence dans la doctrine, signale-la à Gstar. Si une phrase de ce CLAUDE.md te paraît contradictoire avec une autre, **arrête-toi** et demande arbitrage.

L'objectif n'est pas de coder vite. L'objectif est de coder juste, dans un cadre que Gstar peut auditer et faire évoluer pendant 6 mois sans perdre le fil.
