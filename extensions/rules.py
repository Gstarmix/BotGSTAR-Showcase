import discord
from discord.ext import commands

class RulesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="reglement")
    async def reglement(self, ctx):
        banniere = "https://www.zupimages.net/up/24/25/58iw.png"
        separator = "https://www.zupimages.net/up/24/24/stl1.png"
        color = discord.Color(0x2b2d31)

        embeds = [
            discord.Embed(color=color).set_image(url=banniere),
            discord.Embed(
                description=(
                    "En plus de respecter certaines règles, vous devez respecter les [**Guidelines**](https://discord.com/guidelines) "
                    "ainsi que les [**ToS**](https://discord.com/terms) de Discord."
                ),
                color=color
            ).set_image(url=separator),
            discord.Embed(
                title="I. Conditions Générales d'Utilisation",
                description=(
                    "> ➔ **Comportement Respectueux** : Adoptez un comportement courtois et respectueux envers tous les membres, même en cas de désaccord.\n\n"
                    "> ➔ **Interdiction de Contenus Inappropriés** : Toute diffusion de contenu pornographique, violent ou autrement offensant est strictement prohibée.\n\n"
                    "> ➔ **Adéquation au Salon** : Veuillez respecter la thématique de chaque salon et déplacer les discussions hors sujet vers les salons appropriés.\n\n"
                ),
                color=color
            ).set_image(url=separator),
            discord.Embed(
                title="II. Commerce et Échanges",
                description=(
                    "> ➔ **Vente de Possessions Virtuelles** : Il est interdit de vendre des objets virtuels contre de l'argent réel ou de les échanger contre des items d'autres jeux.\n\n"
                    "> ➔ **Achats Non Autorisés** : L'achat d'or ou d'items du jeu avec de l'argent réel via des moyens non approuvés par GameForge est interdit.\n\n"
                    "> ➔ **Promotion et Utilisation de Contenus Illégaux** : La promotion de serveurs privés, hacks, cheats ou toute autre activité illégale est strictement interdite.\n\n"
                ),
                color=color
            ).set_image(url=separator),
            discord.Embed(
                title="III. Règles Spécifiques au Salon Discussion",
                description=(
                    "> ➔ **Pertinence des Sujets** : Vous pouvez poser des questions pertinentes en lien avec la discussion en cours ou une annonce récente (de moins de 48 heures).\n\n"
                    "> ➔ **Redirection des Questions** : Les questions concernant NosTale, mais non liées à la discussion en cours ou à une annonce récente, doivent être posées dans "
                    "[**Questions**](https://discord.com/channels/684734347177230451/1055993732505284690).\n\n"
                    "> ➔ **Anti-trolling** : Les messages de trolling, perturbateurs ou hors sujet seront supprimés. Les récidivistes seront sanctionnés et ne pourront poster que dans [**Trashtalk**](https://discord.com/channels/684734347177230451/860165680576724992).\n\n"
                ),
                color=color
            ).set_image(url=separator),
            discord.Embed(
                title="IV. Mesures Disciplinaires",
                description=(
                    "> ➔ En cas de comportement inapproprié, un avertissement initial sera donné.\n\n"
                    "> ➔ Selon la gravité de l'infraction, des sanctions pourront être appliquées, allant d'un mute temporaire à une exclusion définitive du serveur.\n\n"
                    "> ➔ Dans les fils, le bot ajoute automatiquement l'emoji :warning: pour signaler un message, tandis qu'en dehors, vous pouvez l'ajouter manuellement. Dans les deux cas, l'emoji vous permet de contextualiser votre signalement par message privé.\n\n"
                ),
                color=color
            ).set_image(url=separator),
            discord.Embed(
                title="V. Attribution des Rôles",
                description=(
                    "> ➔ <@&684742675726991436> : Administrateur du serveur.\n\n"
                    "> ➔ <@&730357227784896564> : Membres ayant boosté le serveur.\n\n"
                    "> ➔ <@&1020023040794427432> : Membres du mois ayant été les plus récompensés dans [**Questions**](https://discord.com/channels/684734347177230451/1055993732505284690).\n\n"
                    "> ➔ <@&1020028607491493939> : Membres du mois ayant été les plus récompensés dans [**Estimations**](https://discord.com/channels/684734347177230451/1028316725457981440).\n\n"
                    "> ➔ <@&711308286556635238> : Membres du mois ayant été les plus drôles dans [**Memes**](https://discord.com/channels/684734347177230451/724265897794994186).\n\n"
                    "> ➔ <@&1116784777392042116> : Membres du mois ayant attrapé les Pokémon les plus rares dans [**Pokétwo-pokéslot**](https://discord.com/channels/684734347177230451/1122921340354179129).\n\n"
                    "> ➔ <@&1116784776481869944> : Membres du mois s'étant mariés avec un personnage dans [**Mudae-rolls-1**](https://discord.com/channels/684734347177230451/1122922261180071976).\n\n"
                    "> ➔ <@&684745331602096134> : Membres du serveur.\n\n"
                    "> ➔ <id:customize> : Vous avez la possibilité de sélectionner les rôles de raid pour être mentionné et pour mentionner dans [**Recherche-raids**](https://discord.com/channels/684734347177230451/719104975665496115).\n\n"
                    "> ➔ Les rôles attribués peuvent être consultés et les classements mensuels des membres les plus actifs sont affichés dans [**Classements-mois**](https://discord.com/channels/684734347177230451/1248412336042283129).\n\n"
                ),
                color=color
            ).set_image(url=separator),
        ]

        for embed in embeds:
            await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(RulesCog(bot))