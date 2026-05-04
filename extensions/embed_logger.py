import discord
from discord.ext import commands
import json
import os
from datetime import datetime

USER_ID = 200750717437345792
MUDAE_BOT_ID = 432610292342587392
CHANNEL_ID = 1191348379406577704
LOG_FILE_PATH = "embed_logs.json"

KAKERA_L_EMOJI_ID = 1097914945699581973
KAKERA_R_EMOJI_ID = 1097914903915925716
KAKERA_W_EMOJI_ID = 1097914914498150431
KAKERA_P_EMOJI_ID = 1097914822462545951

KAKERA_ICONS = {
    KAKERA_R_EMOJI_ID: "<:kakeraR:1270430307346022430>",
    KAKERA_W_EMOJI_ID: "<:kakeraW:1270430305882341377>",
    KAKERA_L_EMOJI_ID: "<:kakeraL:1270430612888621067>",
    KAKERA_P_EMOJI_ID: "<:kakeraP:1270448786442948639>"
}

SLASH_COMMANDS = {"ha"}
TEXT_COMMANDS = {"$ha"}

class EmbedLogger(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.ensure_log_file_exists()

    def ensure_log_file_exists(self):
        if not os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, 'w') as f:
                json.dump([], f, indent=4)

    def log_embed(self, embed, author, command, buttons_info=None):
        log_entry = {
            "user_id": author.id,
            "username": author.name,
            "command": command,
            "timestamp": datetime.now().isoformat(),
            "embed": {
                "title": embed.title,
                "description": embed.description,
                "fields": [{"name": field.name, "value": field.value} for field in embed.fields],
                "footer": embed.footer.text if embed.footer else None,
                "image": embed.image.url if embed.image else None,
                "thumbnail": embed.thumbnail.url if embed.thumbnail else None,
                "author": {
                    "name": embed.author.name,
                    "url": embed.author.url,
                    "icon_url": embed.author.icon_url
                } if embed.author else None,
                "buttons": buttons_info,
                "url": embed.url
            }
        }

        try:
            with open(LOG_FILE_PATH, 'r') as f:
                logs = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Erreur de décodage JSON: {e}")
            logs = []  # Réinitialiser les logs en cas d'erreur

        logs.append(log_entry)

        with open(LOG_FILE_PATH, 'w') as f:
            json.dump(logs, f, indent=4)

        # Commented out to avoid flooding the console
        # print("Embed logged:", log_entry)

    def extract_buttons_info(self, message):
        view = discord.ui.View.from_message(message)
        buttons_info = [{"label": b.label, "emoji": str(b.emoji) if b.emoji else None} for b in view.children if isinstance(b, discord.ui.Button)]
        # Commented out to avoid flooding the console
        # print("Extracted buttons info:", buttons_info)
        return buttons_info

    async def send_private_message(self, user, content, icon, image_url):
        try:
            embed_message = discord.Embed(description=f"{icon} {content}")
            if image_url:
                embed_message.set_image(url=image_url)
            await user.send(embed=embed_message)
        except Exception as e:
            print(f"Erreur lors de l'envoi du message privé: {e}")

    def contains_specific_kakera(self, message, emoji_id):
        view = discord.ui.View.from_message(message)
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.emoji and item.emoji.id == emoji_id:
                return True
        return False

    def get_footer_value(self, footer_text):
        # Extract value from footer in the format (🔑value) or (⭐value)
        import re
        match = re.search(r'[(⭐🔑](\d+)[)]', footer_text)
        return int(match.group(1)) if match else None

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.bot.user} est prêt.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id != CHANNEL_ID:
            return

        if message.author.id == MUDAE_BOT_ID:
            # Commented out to avoid flooding the console
            # print(f"Message de Mudae capturé : {message.content}")
            command_name = None
            if message.interaction:
                command_name = message.interaction.name
            # Commented out to avoid flooding the console
            # print(f"Command name: {command_name}")

            if message.embeds:
                for embed in message.embeds:
                    buttons_info = self.extract_buttons_info(message)
                    self.log_embed(embed, message.author, command_name, buttons_info)
                    footer_text = embed.footer.text if embed.footer else ""
                    description = embed.description if embed.description else ""

                    # Définir l'utilisateur avant les vérifications
                    user = self.bot.get_user(USER_ID)

                    # Extraire la valeur
                    value = self.get_footer_value(footer_text)

                    # Fonction pour envoyer le MP
                    async def send_kakera_message(kakera_type, emoji_id):
                        if self.contains_specific_kakera(message, emoji_id):
                            if value:
                                await self.send_private_message(user, f"Un kakera {kakera_type} de valeur {value} a été détecté ! [Lien vers le message]({message.jump_url})", KAKERA_ICONS[emoji_id], embed.image.url)
                            else:
                                await self.send_private_message(user, f"Un kakera {kakera_type} avec ❌ a été détecté ! [Lien vers le message]({message.jump_url})", KAKERA_ICONS[emoji_id], embed.image.url)

                    # Vérification pour les kakeraP
                    # if "❌" in description and "<:sw:1163913219782492220>" in description and "Appartient à gstar" in footer_text:
                    #     await send_kakera_message("P", KAKERA_P_EMOJI_ID)
                    # elif "Appartient à gstar" in footer_text and "⭐" in footer_text:
                    #     await send_kakera_message("P", KAKERA_P_EMOJI_ID)

                    # Vérification pour les kakeraL
                    # if "❌" in description and "<:sw:1163913219782492220>" in description and "Appartient à gstar" in footer_text:
                    #     await send_kakera_message("L", KAKERA_L_EMOJI_ID)
                    # elif "Appartient à gstar" in footer_text and "⭐" in footer_text:
                    #     await send_kakera_message("L", KAKERA_L_EMOJI_ID)

                    # Conditions spécifiques pour kakera R en fonction de la valeur
                    if "Appartient à gstar" in footer_text and self.contains_specific_kakera(message, KAKERA_R_EMOJI_ID):
                        if value and value >= 200:
                            await self.send_private_message(user, f"Un kakera R de valeur {value} a été détecté ! [Lien vers le message]({message.jump_url})", KAKERA_ICONS[KAKERA_R_EMOJI_ID], embed.image.url)
                        elif "❌" in description:
                            await self.send_private_message(user, f"Un kakera R avec ❌ a été détecté ! [Lien vers le message]({message.jump_url})", KAKERA_ICONS[KAKERA_R_EMOJI_ID], embed.image.url)

                    # Autres conditions pour envoyer un MP kakeraW
                    if "Appartient à gstar" in footer_text and ("🔑" in footer_text or "⭐" in footer_text):
                        if self.contains_specific_kakera(message, KAKERA_W_EMOJI_ID):
                            if value:
                                await self.send_private_message(user, f"Un kakera W de valeur {value} a été détecté ! [Lien vers le message]({message.jump_url})", KAKERA_ICONS[KAKERA_W_EMOJI_ID], embed.image.url)
                            else:
                                await self.send_private_message(user, f"Un kakera W avec ❌ a été détecté ! [Lien vers le message]({message.jump_url})", KAKERA_ICONS[KAKERA_W_EMOJI_ID], embed.image.url)
                # Commented out to avoid flooding the console
                # else:
                #     await message.channel.send(f"{message.author.mention}, aucun embed trouvé dans la réponse du bot.")
                #     print("Aucun embed trouvé dans la réponse du bot.")

async def setup(bot):
    await bot.add_cog(EmbedLogger(bot))
