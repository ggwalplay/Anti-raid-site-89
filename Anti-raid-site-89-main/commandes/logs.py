import discord
from discord.ext import commands

from commandes._permissions import get_logs_channel_id


def _tronquer(texte: str, longueur_max: int) -> str:
    if len(texte) <= longueur_max:
        return texte
    return texte[: longueur_max - 1].rstrip() + "…"


async def envoyer_log(guild: discord.Guild, categorie: str, embed: discord.Embed) -> None:
    """Envoie un embed dans le salon de logs configuré pour la catégorie donnée.

    categorie doit être 'commandes' ou 'sanctions'. Ne fait rien si aucun
    salon n'est configuré, et échoue silencieusement si le bot n'a plus accès
    au salon (supprimé, permissions retirées, etc).
    """
    channel_id = get_logs_channel_id(guild.id, categorie)
    if channel_id is None:
        return

    salon = guild.get_channel(channel_id)
    if salon is None:
        return

    try:
        await salon.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


def construire_embed_commande(ctx: commands.Context) -> discord.Embed:
    embed = discord.Embed(
        title="Commande utilisée",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Commande", value=f"&{ctx.command.qualified_name}", inline=True)
    embed.add_field(name="Auteur", value=f"{ctx.author.mention} ({ctx.author.id})", inline=True)
    embed.add_field(name="Salon", value=ctx.channel.mention, inline=True)

    if ctx.message.content and len(ctx.message.content) <= 1000:
        embed.add_field(
            name="Message complet",
            value=_tronquer(ctx.message.content, 1000),
            inline=False,
        )

    embed.set_footer(text=f"ID commande : {ctx.message.id}")
    return embed


def construire_embed_sanction(
    action: str,
    cible: discord.Member | discord.User,
    moderateur: discord.Member,
    raison: str | None = None,
    couleur: discord.Color | None = None,
    preuve: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=action,
        color=couleur or discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Membre", value=f"{cible.mention} ({cible.id})", inline=False)
    embed.add_field(name="Modérateur", value=f"{moderateur.mention} ({moderateur.id})", inline=False)
    embed.add_field(
        name="Raison",
        value=_tronquer(raison, 1000) if raison else "Aucune raison fournie",
        inline=False,
    )

    if preuve:
        embed.add_field(name="Preuve", value=_tronquer(preuve, 1000), inline=False)

    if isinstance(cible, (discord.Member, discord.User)):
        embed.set_thumbnail(url=cible.display_avatar.url)

    return embed


async def log_commande(ctx: commands.Context) -> None:
    if ctx.guild is None:
        return
    embed = construire_embed_commande(ctx)
    await envoyer_log(ctx.guild, "commandes", embed)


async def log_sanction(
    guild: discord.Guild,
    action: str,
    cible: discord.Member | discord.User,
    moderateur: discord.Member,
    raison: str | None = None,
    couleur: discord.Color | None = None,
    preuve: str | None = None,
) -> None:
    """À appeler depuis n'importe quel cog de modération.

    Exemple : await log_sanction(ctx.guild, "Kick", membre, ctx.author, raison)
    """
    embed = construire_embed_sanction(action, cible, moderateur, raison, couleur, preuve)
    await envoyer_log(guild, "sanctions", embed)


class Logs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        await log_commande(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(Logs(bot))