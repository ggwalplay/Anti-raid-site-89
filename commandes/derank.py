import discord
from discord.ext import commands

from commandes._permissions import est_owner


class Derank(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="derank", hidden=True)
    async def derank(self, ctx: commands.Context, membre_id: int = None, role_id: int = None):
        # Utilisable uniquement en message privé.
        if ctx.guild is not None:
            return

        # Utilisable uniquement par le(s) propriétaire(s) défini(s) dans .env (OWNER=...).
        if not est_owner(ctx.author.id):
            return

        if membre_id is None or role_id is None:
            await ctx.send("Utilisation : `&derank <id_membre> <id_role>`")
            return

        # L'ID d'un rôle est unique à un serveur : on s'en sert pour retrouver
        # le bon serveur parmi tous ceux où le bot est présent.
        guild_cible = None
        role_cible = None
        for guild in self.bot.guilds:
            role = guild.get_role(role_id)
            if role is not None:
                guild_cible = guild
                role_cible = role
                break

        if guild_cible is None:
            await ctx.send("Rôle introuvable sur aucun serveur où je suis présent.")
            return

        membre = guild_cible.get_member(membre_id)
        if membre is None:
            try:
                membre = await guild_cible.fetch_member(membre_id)
            except discord.NotFound:
                membre = None

        if membre is None:
            await ctx.send("Membre introuvable sur ce serveur.")
            return

        if role_cible not in membre.roles:
            await ctx.send("Ce membre n'a pas ce rôle.")
            return

        try:
            await membre.remove_roles(role_cible)
        except discord.Forbidden:
            await ctx.send("Permissions insuffisantes pour retirer ce rôle.")
            return
        except discord.HTTPException as e:
            await ctx.send(f"Erreur Discord : {e}")
            return

        await ctx.send(f"✅ Rôle **{role_cible.name}** retiré à **{membre}** sur **{guild_cible.name}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Derank(bot))