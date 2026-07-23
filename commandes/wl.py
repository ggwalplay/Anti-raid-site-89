import discord
from discord.ext import commands

from commandes._permissions import (
    est_whitelist,
    ajouter_whitelist,
    retirer_whitelist,
    check_gerant,
)


class Whitelist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="wl",
        help="Ajoute ou retire un membre de la whitelist anti-ban (déban automatique).",
    )
    @commands.guild_only()
    @check_gerant()
    async def wl(self, ctx: commands.Context, membre_id: int = None):
        if membre_id is None:
            await ctx.send("Utilisation : `&wl <id_membre>`")
            return

        if est_whitelist(ctx.guild.id, membre_id):
            retirer_whitelist(ctx.guild.id, membre_id)
            await ctx.send(f"❌ <@{membre_id}> a été retiré de la whitelist anti-ban.")
        else:
            ajouter_whitelist(ctx.guild.id, membre_id)
            await ctx.send(
                f"✅ <@{membre_id}> a été ajouté à la whitelist anti-ban.\n"
                "S'il se fait bannir, il sera automatiquement débanni."
            )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member):
        if not est_whitelist(guild.id, user.id):
            return

        try:
            await guild.unban(user, reason="[Whitelist] Membre protégé, déban automatique")
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Whitelist(bot))