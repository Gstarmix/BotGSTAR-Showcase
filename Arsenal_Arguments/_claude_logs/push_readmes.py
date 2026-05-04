"""Push READMEs vers les 6 repos archives via GitHub API.

Génère pour chaque repo :
- Description du projet (contexte historique)
- Stack technique
- Statut actuel (souvenir / abandonné / fonctionnel)
- Roadmap "comment je referais aujourd'hui avec LLM"

Utilise gh api pour le PUT contents (pas de clone local nécessaire).
"""
from __future__ import annotations
import base64
import io
import json
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

GH = r"C:\Program Files\GitHub CLI\gh.exe"
OWNER = "Gstarmix"

READMES = {
    "Pokeclicker": {
        "title": "Pokeclicker",
        "description": "Fork / version personnalisée du jeu open-source PokéClicker (idle clicker Pokémon).",
        "context": (
            "Petit projet de bidouille avant l'ère des LLM — j'avais cloné le jeu open-source "
            "[PokéClicker](https://github.com/pokeclicker/pokeclicker) et fait quelques mods "
            "personnels pour expérimenter le code (JavaScript, mécaniques de jeu, économie idle). "
            "Le repo héberge ma version locale, le contenu principal vit dans le sous-dossier "
            "`pokeclicker/`."
        ),
        "stack": ["JavaScript", "HTML", "CSS"],
        "roadmap": [
            "Refaire en TypeScript strict + Vite, isoler proprement la logique de jeu du DOM.",
            "Système de mods custom (chargement dynamique de packs Pokémon, événements scriptés).",
            "Sauvegarde dans IndexedDB au lieu de localStorage (volume + structuré).",
            "IA Claude / agent qui joue automatiquement selon une stratégie prédéfinie (auto-clicker intelligent, allocation de poké balls, leveling optimal).",
            "Mode multijoueur léger via WebSocket (échanges Pokémon entre joueurs amis).",
        ],
    },
    "BattleArena": {
        "title": "BattleArena",
        "description": "Jeu de combat dans le navigateur — JavaScript / HTML / Sass.",
        "context": (
            "Projet école L1 ISTIC (avril 2024). Conception d'un jeu de combat web en duel, "
            "avec barres de progression, effets visuels Sass et gameplay temps réel. C'était mon "
            "premier projet où j'expérimentais la séparation propre du code en modules JS, "
            "le pipeline Sass, et la logique d'animation."
        ),
        "stack": ["JavaScript", "HTML", "Sass", "npm"],
        "roadmap": [
            "Migrer le moteur vers [Phaser 3](https://phaser.io/) ou [Pixi.js](https://pixijs.com/) (gestion native du sprite-sheet, hitboxes, audio).",
            "Multijoueur en ligne via WebSocket (Socket.io ou Colyseus côté serveur).",
            "Adversaire IA piloté par un LLM (Claude API) — l'IA reçoit l'état du combat en JSON et choisit son action en fonction d'une persona configurable (agressif, défensif, opportuniste).",
            "Animations procédurales : Lottie pour les coups spéciaux, GreenSock pour le timing.",
            "Mode tournoi avec brackets dynamiques + ladder permanent.",
        ],
    },
    "Schmoulbrouk": {
        "title": "Schmoulbrouk",
        "description": "Projet WordPress personnel — site sur mesure (contenu local, repo metadata).",
        "context": (
            "Vieux site WordPress que j'avais bricolé en 2023. Le contenu vivant (médias, base de "
            "données, thème custom) est resté en local — ce repo contient principalement les "
            "fichiers de tracking Git. Plus maintenu, gardé en archive personnelle."
        ),
        "stack": ["WordPress", "PHP", "MySQL"],
        "roadmap": [
            "Migrer le contenu vers un **CMS headless** : [Strapi](https://strapi.io/) ou [Sanity](https://www.sanity.io/), avec API GraphQL.",
            "Front-end moderne : [Astro](https://astro.build/) (zéro JS par défaut, parfait pour blog) ou [Next.js](https://nextjs.org/) si interactivité riche.",
            "Hébergement statique sur Cloudflare Pages ou Vercel (gratuit, edge global, CI/CD intégré).",
            "Génération de contenu assistée par Claude pour les drafts, fact-check automatique des liens externes, tags suggérés.",
            "Migration progressive : exporter WordPress XML → script de transformation → import dans le nouveau CMS.",
        ],
    },
    "JeuPendu": {
        "title": "JeuPendu",
        "description": "Jeu du pendu en HTML/CSS/JavaScript — premier petit projet web.",
        "context": (
            "Anciennement hébergé sur https://gstarmix.github.io (GitHub Pages), c'était mon "
            "tout premier petit projet web — jeu du pendu en JS pur. Renommé `JeuPendu` pour "
            "cohérence avec ma nomenclature actuelle (PascalCase, sans points). L'URL devient "
            "donc une project page."
        ),
        "stack": ["HTML", "CSS", "JavaScript"],
        "roadmap": [
            "Refaire en [React](https://react.dev/) + Vite ou [SolidJS](https://www.solidjs.com/) (réactif, moins de boilerplate qu'à l'époque).",
            "Dictionnaire dynamique : API publique de mots français ([Lexique](http://www.lexique.org/) ou [Datamuse FR](https://www.datamuse.com/api/)) avec choix de difficulté (longueur, fréquence d'usage).",
            "**Mode IA Claude** : Claude génère un mot mystère adapté à un thème choisi par le joueur (« cuisine », « espace », « cinéma années 80 »…) et fournit éventuellement des indices stylisés.",
            "Mode multijoueur : 2 joueurs, l'un choisit le mot, l'autre devine (rooms via WebSocket).",
            "Skins / animations du pendu (SVG animés au lieu d'un dessin statique).",
            "Système de score persistant (cookie ou IndexedDB), leaderboard local par session.",
        ],
    },
    "SportApp": {
        "title": "SportApp",
        "description": "Application web de suivi sportif — backend Laravel (PHP) + frontend Blade.",
        "context": (
            "Projet d'apprentissage Laravel en 2023 (avant ISTIC). J'expérimentais l'écosystème "
            "PHP moderne : Eloquent ORM, migrations, routes RESTful, vues Blade. L'idée : "
            "tracker mes séances de sport, exercices, progression. Resté à l'état de prototype."
        ),
        "stack": ["Laravel", "PHP 8", "MySQL", "Blade", "Tailwind"],
        "roadmap": [
            "Refaire en **stack TypeScript moderne** : [SvelteKit](https://kit.svelte.dev/) ou [T3 stack](https://create.t3.gg/) (Next.js + tRPC + Prisma + Tailwind), backend Node/Bun.",
            "PWA mobile-first avec capteurs natifs (chronomètre, podomètre, vibration de feedback).",
            "**Conseiller IA** intégré : Claude API qui analyse les sessions précédentes et propose un programme adapté (objectif perte de poids / prise de masse / endurance), avec ajustement hebdomadaire selon la fatigue auto-déclarée.",
            "Intégration Strava / Apple Health / Google Fit pour synchro auto des cardios.",
            "Mode social léger : suivi d'amis, défis hebdomadaires, classement amical (sans gamification toxique).",
            "Génération automatique de plans nutritionnels par Claude en fonction des objectifs sportifs.",
        ],
    },
    "SpectreElectromagnetique": {
        "title": "SpectreElectromagnetique",
        "description": "Site pédagogique interactif sur le spectre électromagnétique — projet personnel 2023.",
        "context": (
            "Projet personnel (mars 2023) d'avant l'IA générative — j'avais conçu un site "
            "pédagogique pour vulgariser le spectre électromagnétique (ondes radio, "
            "micro-ondes, infrarouge, visible, UV, X, gamma). Le PDF "
            "`Projet_personnel_gaylord_aboeka.pdf` documente la démarche. Stack : "
            "JS / Sass / HTML, animations CSS pour la timeline du spectre."
        ),
        "stack": ["JavaScript", "Sass", "HTML", "CSS"],
        "roadmap": [
            "Refaire en [Next.js](https://nextjs.org/) + animations [Three.js](https://threejs.org/) (rendu 3D du spectre, navigation continue par molette/scroll).",
            "**Narration audio générée par IA** : pour chaque section du spectre, Claude rédige une explication adaptée au niveau choisi (collège / lycée / vulgarisation grand public), puis ElevenLabs ou OpenAI TTS la lit.",
            "Mode quiz interactif : questions générées dynamiquement par Claude sur le contenu, scoring, progression sauvegardée.",
            "Visualisations interactives : longueur d'onde modifiable en temps réel via slider, voir l'effet sur la perception (couleur visible, applications technologiques pour chaque bande).",
            "Version mobile native via [Tauri](https://tauri.app/) ou [Capacitor](https://capacitorjs.com/) si on veut sortir du navigateur.",
            "Mode comparatif : superposer le spectre EM avec des phénomènes connexes (bruit acoustique, vagues océaniques) pour montrer que les concepts d'onde sont universels.",
        ],
    },
}


def render_readme(spec: dict) -> str:
    out = [
        f"# {spec['title']}",
        "",
        f"> {spec['description']}",
        "",
        "## Contexte",
        "",
        spec["context"],
        "",
        "## Stack",
        "",
    ]
    for s in spec["stack"]:
        out.append(f"- {s}")
    out += [
        "",
        "## Statut",
        "",
        "Ancien projet personnel, conservé en archive. **Pas activement maintenu.** "
        "L'idée est de garder une trace des étapes d'apprentissage avant l'ère des LLM, "
        "et de planifier une refonte propre quand j'aurai du temps.",
        "",
        "## Roadmap (refonte si je devais le refaire aujourd'hui)",
        "",
    ]
    for r in spec["roadmap"]:
        out.append(f"- {r}")
    out += [
        "",
        "---",
        "",
        "_README généré le 2026-04-29 dans le cadre du cleanup général de mes anciens repos GitHub. "
        "Co-écrit avec [Claude Code](https://claude.com/claude-code) qui a rédigé le contenu "
        "à partir de l'inspection du repo._",
        "",
    ]
    return "\n".join(out)


def push_readme(repo: str, content: str) -> None:
    print(f"\n=== {repo} ===")
    # Récupère le SHA si README existe déjà
    sha = None
    res = subprocess.run([GH, "api", f"repos/{OWNER}/{repo}/contents/README.md"],
                         capture_output=True, text=True, encoding="utf-8")
    if res.returncode == 0:
        try:
            sha = json.loads(res.stdout).get("sha")
        except (json.JSONDecodeError, ValueError):
            pass
    # Encode contenu en base64
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    args = [GH, "api", "-X", "PUT", f"repos/{OWNER}/{repo}/contents/README.md",
            "-f", f"message=Refonte README — contexte historique + stack + roadmap LLM",
            "-f", f"content={b64}"]
    if sha:
        args += ["-f", f"sha={sha}"]
    res = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            print(f"  OK — commit {data.get('commit', {}).get('sha', '?')[:7]}")
        except Exception:
            print(f"  OK")
    else:
        print(f"  FAIL : {res.stderr[:300]}")


if __name__ == "__main__":
    for repo, spec in READMES.items():
        readme = render_readme(spec)
        push_readme(repo, readme)
    print("\nTerminé.")
