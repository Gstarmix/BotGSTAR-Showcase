# Guide veille RSS — ISTIC L1 G2

Ce guide explique comment utiliser et profiter de la veille
technologique automatique du serveur Discord ISTIC L1 G2.

Trois sections selon ton rôle :
- **§1 — Pour les lecteurs** (toute la promo)
- **§2 — Pour les administrateurs** (Gaylord et autres admins)
- **§3 — Pour les futurs mainteneurs** (toi qui reprend le code)

---

## 1. Pour les lecteurs (toute la promo)

### Où trouver les digests

Les digests sont postés automatiquement chaque matin **à 8h00**
(heure de Paris) dans 4 salons sous la catégorie `📡 VEILLE` :

| Salon | Contenu |
|---|---|
| `📰 cyber-veille` | Sécurité offensive/défensive : CVE, ransomware, APT, alertes ANSSI |
| `🤖 ia-veille` | IA, LLMs, recherche : annonces OpenAI / Anthropic / Hugging Face |
| `💻 dev-veille` | Développement : releases, breaking changes, security advisories |
| `📱 tech-news` | Tech grand public : actu Numerama, gadgets, réglementation numérique |

### Comment lire un digest

Chaque digest est un (ou plusieurs) embed(s) Style A « Magazine » :

- **Titre** : ex. `📰 Veille cybersécurité — samedi 25 avril 2026`,
  uniquement sur le **1er embed**. Si la catégorie est splittée en
  2 embeds, le 2e est une continuation visuelle sans titre (effet
  « magazine 2 pages »).
- **Liste d'articles** : 1 article = 1 carte aérée, jusqu'à 5 cartes
  par embed et 10 articles par catégorie au total. Tri par pertinence
  (priorité source + fraîcheur + mots-clés boost).
- **Format de chaque carte** (sur 2 lignes) :
  - Ligne 1 : `🔴`/`🟠`/`🟡` (priorité source) + **titre en gras
    cliquable** vers l'article original.
  - Ligne 2 : un emoji thématique + `` `source-id` `` (en backticks,
    pas cliquable) + 🇫🇷/🇬🇧 (langue) + _il y a X h_ (âge à
    l'instant du digest).
  - L'emoji thématique de la 2e ligne **change selon le salon** :
    - 📰 dans `#cyber-veille`
    - 🤖 dans `#ia-veille`
    - 💻 dans `#dev-veille`
    - 📱 dans `#tech-news`
  - Cela rend les digests immédiatement reconnaissables même hors
    contexte (par exemple en mention).
- **Footer + timestamp** : `N articles · M sources · 🌐 X FR · Y EN`
  + heure du digest, **uniquement sur le dernier embed du split**.
- **Largeur uniforme** : une image transparente 730×1 px est posée
  en bas de chaque embed pour forcer Discord à les afficher tous à
  la même largeur, éliminant l'effet « désaligné » entre embeds.
  Invisible à l'œil — c'est juste un trick structurel.

Si la catégorie a plus de 5 articles, le digest est splitté en
plusieurs embeds successifs (cap dur de 5 embeds par catégorie).
Chaque embed reste atomique : jamais de coupure au milieu d'un
article.

### Que se passe-t-il si une catégorie est vide ?

Aucun message n'est posté ce jour-là dans cette catégorie. C'est
volontaire : on préfère skipper plutôt que polluer avec un
« rien à dire aujourd'hui ». Les jours fastes, tu as 30+ articles
à lire ; les jours creux, tu as juste rien.

### Pourquoi tel article apparaît / pas un autre

Le bot applique 4 filtres dans cet ordre :

1. **Date** : l'article doit avoir été publié dans les dernières
   24 h (tech) ou 72 h (cyber/ia/dev). Plus vieux → ignoré.
2. **Dédoublonnage** : si l'article a déjà été posté dans un
   digest précédent, il n'est pas re-posté.
3. **Blacklist mots-clés** : certains termes (« test produit »,
   « bon plan », « black friday » sur tech-news par exemple)
   sont automatiquement filtrés.
4. **Top 10 par catégorie** : si plus de 10 articles passent les
   filtres, seuls les 10 mieux classés sont postés. Le score
   combine priorité de la source, fraîcheur et mots-clés boost.

### Proposer une source à ajouter

Si tu connais un blog/site qui devrait être surveillé :

1. Vérifie qu'il a un flux RSS (souvent dans le footer du site,
   ou en testant `https://lesite.com/feed/` ou `/rss.xml`).
2. Envoie un message à un admin du serveur (Gaylord) avec :
   - L'URL du flux RSS
   - La catégorie où le mettre (`cyber` / `ia` / `dev` / `tech`)
   - Pourquoi cette source mériterait d'être ajoutée
3. L'admin teste l'URL avec `!veille sources test <url>` et
   l'ajoute si pertinente.

---

## 2. Pour les administrateurs (admin only)

Toutes les commandes sont préfixées par `!veille` et limitées au
rôle `ADMIN_ROLE_ID` sur le serveur ISTIC L1 G2.

### Commandes principales

```
!veille
```
Affiche l'aide complète des commandes disponibles.

```
!veille status
```
Affiche un message listant l'état de chaque source : actives,
nombre d'erreurs récentes, dernier fetch.

```
!veille fetch-now
```
Lance un cycle manuel immédiatement. Mode « manual » → pas de
récap dans `#logs`. Idempotent : un 2e appel rapide ne reposte
rien (les articles sont déjà dans `published[]`).

```
!veille trigger-now
```
Identique à `fetch-now` mais en mode « auto » : poste un récap
matinal dans `#logs` avec compteurs et état des sources. Utile
pour simuler le digest 8h sans attendre. **Garde anti-doublon** :
si le digest a déjà été posté aujourd'hui, le cycle est skippé
(édite `last_digest_at` à `null` dans `rss_state.json` pour
forcer un re-test).

```
!veille reload
```
Recharge `rss_sources.yaml` ET `rss_keywords.yaml` depuis le
disque. À utiliser après une édition manuelle des YAML.

### Gestion des sources

```
!veille sources list
```
Embed groupé par catégorie. Pour chaque source : ✅/⛔ actif,
🔴/🟠/🟡 priorité, 🇫🇷/🇬🇧 langue, identifiant.

```
!veille sources test <url>
```
Teste une URL RSS sans la persister. Affiche le nombre d'articles
détectés ou un message d'erreur. **Toujours faire ça avant un
add.**

```
!veille sources add <id> <url> <cat> [prio]
```
Ajoute une source. Exemples :

- `!veille sources add wired https://www.wired.com/feed/rss tech 2`
- `!veille sources add krebs https://krebsonsecurity.com/feed/ cyber 1`
- `!veille sources add hf https://huggingface.co/blog/feed.xml ia 2`

Règles :
- `id` : minuscules, chiffres, tirets, underscores, 50 chars max,
  unique.
- `cat` : `cyber` / `ia` / `dev` / `tech`
- `prio` : `1` (top) / `2` (medium, défaut) / `3` (low)
- L'URL est testée automatiquement avant l'ajout. Si le test
  échoue, l'ajout est annulé.
- La langue est détectée à partir du domaine (`.fr` → français,
  sinon anglais). Modifiable manuellement dans le YAML après coup.
- L'entrée est insérée dans la bonne section catégorielle du YAML
  (ordre : cyber → ia → dev → tech), commentaires conservés.

```
!veille sources remove <id>
```
Demande confirmation (réponds `oui` dans les 30 s). Supprime
définitivement la source du YAML.

```
!veille sources toggle <id>
```
Active ↔ désactive une source sans la supprimer. Utile pour les
sources problématiques temporairement.

### Mots-clés de scoring

```
!veille keywords
```
Affiche les mots-clés actuels (boost + blacklist par catégorie).

Pour les modifier : éditer manuellement
`BotGSTAR/datas/rss_keywords.yaml`, puis `!veille reload`.

Format YAML :
```yaml
cyber:
  boost:
    - vulnérabilité critique
    - 0-day
    - RCE
  blacklist:
    - sponsored
```

- **Boost** : +500 points au score par match (article remonte dans
  le tri). Additif (3 matches = +1500).
- **Blacklist** : article rejeté du digest si match.
- Matching insensible à la casse, sur titre + summary, substring
  exact (multi-mots = consécutifs dans cet ordre).
- Section `all` : s'applique à toutes les catégories. Utile pour
  un mot transversal (ex. ajouter `Anthropic` dans `all.boost`
  pour le booster partout).

### Cas problématiques

**Une source affiche « désactivée automatiquement » dans `#logs`** :
elle a accumulé 5 erreurs consécutives. Vérifier l'URL avec
`!veille sources test <url>`. Si elle remarche, réactiver via
`!veille sources toggle <id>` (ou éditer manuellement le YAML +
reload, le compteur d'erreurs étant en `rss_state.json`).

**Le digest auto n'est pas sorti à 8h** :
- Le bot était-il offline à 8h ? → catch-up déclenche au boot,
  vérifier `#logs` pour un embed `🔁 Catch-up digest`.
- `last_digest_at` est-il déjà à aujourd'hui ? → la garde
  anti-doublon a sauté, normal. Vérifier dans `rss_state.json`.

**Une catégorie est constamment vide** :
- Vérifier les sources de cette catégorie avec `!veille sources list`.
- Élargir la fenêtre de fraîcheur dans `veille_rss.py`
  (constante `DIGEST_WINDOW_HOURS_BY_CAT`).
- Ajouter une source supplémentaire via `!veille sources add`.

### Édition manuelle des fichiers

Tous les fichiers `BotGSTAR/datas/rss_*.yaml` peuvent être édités à
la main (commentaires + ordre conservés grâce à ruamel.yaml côté
écriture programmatique), mais :

- Toujours faire `!veille reload` après édition pour propager.
- `rss_state.json` ne doit **pas** être édité à la main sauf cas
  précis (debug, reset published d'une catégorie pour tester un
  re-post). Préférer laisser le bot le gérer. Si tu modifies, le
  bot fait un backup automatique au prochain reset script
  (`rss_state.json.bak.YYYYMMDD-HHMMSS`).

---

## 3. Pour les futurs mainteneurs

Voir `BotGSTAR/CLAUDE.md` §11 pour l'architecture détaillée et les
conventions de code (helpers, scoring, scheduler, garde-fous).

### Ajouter une nouvelle catégorie de digest (ex. `science`)

1. Créer le salon Discord `📚 science-veille` sous la catégorie
   `📡 VEILLE` et noter son ID.
2. Modifier `extensions/veille_rss.py` :
   - Ajouter `"science"` dans `VALID_CATEGORIES`
   - Ajouter l'entrée `"science": <channel_id>` dans `VEILLE_CHANNELS`
   - Ajouter une fenêtre dans `DIGEST_WINDOW_HOURS_BY_CAT`
   - Ajouter une couleur dans `CATEGORY_COLORS`
   - Ajouter un titre dans `CATEGORY_TITLES`
3. Mettre à jour `datas/rss_keywords.yaml` (ajouter une section
   `science:` avec `boost: []` et `blacklist: []`).
4. Mettre à jour `_post_morning_summary` (cat_emojis) pour avoir
   l'emoji dans le récap matinal.
5. Ajouter au moins 1 source via `!veille sources add ... science`.

C'est ~10 lignes de code Python + 1 commande Discord.

### Ajuster le scoring

- **Augmenter l'effet du boost mots-clés** : modifier
  `KEYWORD_BOOST_POINTS` (actuellement 500). Mettre 1000 fait
  qu'un seul mot-clé booste un prio 2 (2000+1000=3000) au niveau
  d'un prio 1 sans match.
- **Changer le poids de la fraîcheur** : modifier la formule dans
  `Article.score`. Actuellement `max(0, 1000 - âge_minutes)` →
  bonus dégressif sur ~16 h.
- **Plus de 10 articles par digest** : modifier
  `DIGEST_MAX_ARTICLES` + ajuster `DIGEST_MAX_EMBEDS_PER_CATEGORY`
  si tu veux laisser plus d'embeds.

### Migrer la stack RSS

`feedparser` est en mode maintenance depuis longtemps mais
fonctionne. Si tu veux passer à `aiosonic` + parser XML manuel
pour gain de perf, refactor à isoler dans `_fetch_one_source` —
le reste du code reste agnostique tant que `_fetch_one_source`
retourne `(list[Article], err)`.

### Tests

Tous les tests offline ont été faits via Python en CLI dans le
workspace BotGSTAR. Pas de framework de tests automatisés (pas
nécessaire vu la taille). Les principaux scenarios à tester
manuellement après modif :

- AST parse : `python -c "import ast; ast.parse(open('extensions/veille_rss.py', encoding='utf-8').read())"`
- Import du module : `python -c "from extensions.veille_rss import VeilleRSS; print('OK')"`
- Round-trip YAML : charger `rss_sources.yaml` via `_load_sources_raw`,
  re-dumper, vérifier que les `# === CYBER ===` sont conservés.
- Fetch live : appeler `_fetch_one_source` sur 1-2 sources, vérifier
  `(articles, None)` non vide.
- Filtrage : injecter des `Article` synthétiques dans
  `_filter_and_select`, vérifier le tri.

### Évolutions futures envisagées (non implémentées)

- **R-F** — proposition de sources par la promo via commande
  `!veille suggest <url>` (limitée non-admin).
- Indicateurs visuels supplémentaires (réactions Discord pour
  bookmark, etc.).
- Support OPML pour import/export en masse.
- Notifications individuelles ciblées (DM si matche un filtre
  perso).

---

## 4. Cog jumeau — Veille politique (`veille_rss_politique.py`)

Depuis la **Phase V (28 avril 2026)**, un second cog jumeau existe :
`extensions/veille_rss_politique.py`. Il sert le serveur **Veille / Arsenal**
(`1475846763909873727`) et applique la même architecture (digest 8h Paris,
auto-désactivation, embeds Style Magazine) mais sur 7 catégories
USAGE-orientées (« Option C »), pas idéologiques :

| Catégorie | Salon Discord | Question d'usage |
|---|---|---|
| `actu-chaude` | `🔥・actu-chaude` | « Qu'est-ce qui se passe ce matin ? » |
| `arsenal-eco` | `💰・arsenal-eco` | « Quel chiffre / argument économique ? » |
| `arsenal-ecologie` | `🌱・arsenal-ecologie` | « Quelle donnée climat / écologique ? » |
| `arsenal-international` | `🌍・arsenal-international` | « Que se passe-t-il sur Palestine, Russie ? » |
| `arsenal-social` | `✊・arsenal-social` | « Quelle lutte / syndicat / mobilisation ? » |
| `arsenal-attaques` | `🎯・arsenal-attaques` | « Quelle réplique pro-LFI ? » |
| `arsenal-medias` | `📺・arsenal-medias` | « Qui dit quoi, qui ment ? » |

Préfixe de commandes : `!veille_pol …` ou alias `!vp …` (mêmes commandes
que `!veille …` côté tech : `fetch-now`, `trigger-now`, `status`,
`reload`, `sources list/add/remove/toggle/test`, `keywords`).

Fichiers de config :
```
BotGSTAR/datas/
  rss_sources_politique.yaml    # 40 sources françaises vérifiées
  rss_keywords_politique.yaml   # boost + blacklist par catégorie
  rss_state_politique.json      # état runtime (NE PAS éditer à la main)
```

Pour la doc dev complète : voir §12 de `CLAUDE.md`. Pour tester la
santé des sources sans Discord :

```bash
python Arsenal_Arguments/_claude_logs/test_rss_sources.py
python Arsenal_Arguments/_claude_logs/test_rss_sources.py --include-inactive
```
