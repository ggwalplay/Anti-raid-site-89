import discord
from discord.ext import commands

from commandes._permissions import check_staff


class Publication(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="pub",
        help="Publie un message en annonce propre dans le salon actuel et supprime la commande d'origine.",
    )
    @commands.guild_only()
    @check_staff()
    async def pub(self, ctx: commands.Context, *, message: str = None):
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        if not message:
            await ctx.send("Utilisation : `&pub <message>`", delete_after=10)
            return

        embed = discord.Embed(
            description=message,
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=ctx.guild.name,
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
        )
        embed.set_footer(text=f"Publié par {ctx.author.display_name}")

        try:
            await ctx.send(embed=embed)
        except discord.Forbidden:
            # Le message de commande est déjà supprimé à ce stade ; on ne peut
            # prévenir que si le bot peut encore écrire dans le salon.
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Publication(bot))
