import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ["PYTHONIOENCODING"] = "utf-8"

"""
bot.py — Point d'entrée du bot Discord Arsenal Intelligence Unit.

Charge les extensions/cogs et lance le bot.
Ajoute Arsenal_Arguments au sys.path pour que les cogs puissent importer arsenal_config.

Usage :
    python bot.py
"""

import os
import sys
from dotenv import load_dotenv
import discord
from discord.ext import commands

# Charger .env (TOKEN, clés API)
load_dotenv()

# Ajouter Arsenal_Arguments au path pour l'import de arsenal_config
ARSENAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Arsenal_Arguments")
if os.path.isdir(ARSENAL_DIR) and ARSENAL_DIR not in sys.path:
    sys.path.insert(0, ARSENAL_DIR)

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    print("[ERREUR] DISCORD_BOT_TOKEN manquant dans le fichier .env")
    sys.exit(1)

PREFIX = "!"
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.members = True


class ArsenalBot(commands.Bot):
    async def setup_hook(self):
        extensions = [
            "extensions.arsenal_publisher",
            "extensions.arsenal_pipeline",
            "extensions.cours_pipeline",
            "extensions.veille_rss",
            "extensions.veille_rss_politique",
        ]

        disabled_extensions = [
            # "extensions.embed_logger",
            # "extensions.rules",
        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"[OK] Extension chargée → {ext}")
            except Exception as e:
                print(f"[ERREUR] Impossible de charger {ext} → {e}")

        for ext in disabled_extensions:
            print(f"[OFF] Extension désactivée → {ext}")


bot = ArsenalBot(command_prefix=PREFIX, intents=intents)


@bot.event
async def on_ready():
    print(f"[BOT] {bot.user.name} connecté à Discord")
    print(f"[BOT] Serveurs : {[g.name for g in bot.guilds]}")
    print(f"[BOT] Commandes : {[c.name for c in bot.commands]}")


bot.run(TOKEN)
