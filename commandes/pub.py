import os

import discord
from discord.ext import commands

from commandes._permissions import check_admin

INVITE_LINK = "https://discord.gg/mzFywAEYup"
LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logo.png")

ANNOUNCE_KEYWORDS = ["annonce", "announcement", "announcements"]


def build_announcement() -> tuple[discord.Embed, discord.File | None]:
    embed = discord.Embed(
        description=f"*Bot créé et hébergé par Eternal Hosting*\n{INVITE_LINK}",
        color=discord.Color.blurple(),
    )
    fichier = None
    if os.path.exists(LOGO_PATH):
        fichier = discord.File(LOGO_PATH, filename="logo.png")
        embed.set_image(url="attachment://logo.png")
    return embed, fichier


async def send_announcement(channel: discord.abc.Messageable) -> None:
    embed, fichier = build_announcement()
    if fichier is not None:
        await channel.send(embed=embed, file=fichier)
    else:
        await channel.send(embed=embed)


def error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.red())


def find_target_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Cherche un salon adapté : un salon nommé 'annonce(s)', sinon le salon système, sinon le premier salon writable."""
    me = guild.me

    for channel in guild.text_channels:
        if any(mot in channel.name.lower() for mot in ANNOUNCE_KEYWORDS):
            if channel.permissions_for(me).send_messages:
                return channel

    if guild.system_channel and guild.system_channel.permissions_for(me).send_messages:
        return guild.system_channel

    for channel in guild.text_channels:
        if channel.permissions_for(me).send_messages:
            return channel

    return None


class Pub(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Poste automatiquement l'annonce dès que le bot rejoint un nouveau serveur (n'importe lequel)."""
        channel = find_target_channel(guild)
        if channel is None:
            return
        try:
            await send_announcement(channel)
        except discord.Forbidden:
            pass

    @commands.command(
        name="pubauto",
        help="Simule ce qui se passe automatiquement quand le bot rejoint un serveur (détection du salon cible).",
    )
    @commands.guild_only()
    @check_admin()
    async def pubauto(self, ctx: commands.Context):
        channel = find_target_channel(ctx.guild)
        if channel is None:
            await ctx.send(embed=error_embed(
                "Aucun salon trouvé",
                "Aucun salon nommé 'annonce', aucun salon système, et aucun salon où le bot peut écrire.",
            ))
            return

        try:
            await send_announcement(channel)
        except discord.Forbidden:
            await ctx.send(embed=error_embed(
                "Erreur",
                f"Le salon détecté est {channel.mention}, mais le bot n'a pas la permission d'y envoyer de message.",
            ))
            return

        await ctx.send(embed=discord.Embed(
            title="Test réussi",
            description=f"Salon détecté automatiquement : {channel.mention}",
            color=discord.Color.green(),
        ))

    @commands.command(
        name="pub",
        help="Publie l'annonce du bot (crédit d'hébergement) dans le salon actuel, ou un salon précisé.",
    )
    @commands.guild_only()
    @check_admin()
    async def pub(self, ctx: commands.Context, channel: discord.TextChannel = None):
        target = channel or ctx.channel

        try:
            await send_announcement(target)
        except discord.Forbidden:
            await ctx.send(embed=error_embed(
                "Erreur",
                f"Permissions insuffisantes pour envoyer un message dans {target.mention}.",
            ))
            return

        if target != ctx.channel:
            await ctx.send(embed=discord.Embed(
                title="Annonce envoyée",
                description=f"Message publié dans {target.mention}.",
                color=discord.Color.green(),
            ))

    @pub.error
    async def pub_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.ChannelNotFound):
            await ctx.send(embed=error_embed("Erreur", "Salon introuvable."))
        elif isinstance(error, commands.CheckFailure):
            raise error  # laisse le handler global de main.py afficher le message de permission
        else:
            await ctx.send(embed=error_embed("Erreur", "Une erreur est survenue lors de l'exécution de la commande."))
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Pub(bot))
