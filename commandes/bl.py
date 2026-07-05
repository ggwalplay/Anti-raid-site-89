import discord
from discord.ext import commands

from commandes._permissions import (
    est_blacklist,
    obtenir_entree_blacklist,
    ajouter_blacklist,
    retirer_blacklist,
    check_gerant,
)
from commandes.logs import log_sanction


class Blacklist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="bl", help="Ajoute un membre à la blacklist et le bannit immédiatement.")
    @commands.guild_only()
    @check_gerant()
    async def bl(self, ctx: commands.Context, membre: discord.User, *, raison: str = "Aucune raison fournie"):
        """Ajoute un membre à la blacklist et le bannit immédiatement."""
        if membre.id == ctx.author.id or membre.id == self.bot.user.id:
            await ctx.send("Vous ne pouvez pas blacklist cette personne.")
            return

        if est_blacklist(ctx.guild.id, membre.id):
            await ctx.send(f"{membre.mention} est déjà sur la blacklist.")
            return

        ajouter_blacklist(ctx.guild.id, membre.id, ctx.author.id, raison)

        try:
            await ctx.guild.ban(membre, reason=f"[Blacklist] {raison}")
            ban_ok = True
        except discord.Forbidden:
            ban_ok = False
        except discord.HTTPException:
            ban_ok = False

        if ban_ok:
            await ctx.send(f"🔨 {membre.mention} a été **blacklist et banni**.")
        else:
            await ctx.send(
                f"⚠️ {membre.mention} a été ajouté à la blacklist, mais le bannissement a échoué "
                "(permissions insuffisantes ou membre introuvable). Il sera banni automatiquement "
                "s'il tente de rejoindre le serveur."
            )

        await log_sanction(
            guild=ctx.guild,
            action="⛔ Blacklist",
            cible=membre,
            moderateur=ctx.author,
            raison=raison,
            couleur=discord.Color.dark_red(),
        )

    @commands.command(name="unbl", help="Retire un membre de la blacklist et le débannit si besoin.")
    @commands.guild_only()
    @check_gerant()
    async def unbl(self, ctx: commands.Context, membre: discord.User):
        """Retire un membre de la blacklist et le débannit si besoin."""
        if not est_blacklist(ctx.guild.id, membre.id):
            await ctx.send(f"{membre.mention} n'est pas sur la blacklist.")
            return

        retirer_blacklist(ctx.guild.id, membre.id)

        try:
            await ctx.guild.unban(membre, reason=f"[Unblacklist] Par {ctx.author}")
            unban_ok = True
        except discord.NotFound:
            # Le membre n'était pas banni, rien à faire de plus.
            unban_ok = None
        except discord.HTTPException:
            unban_ok = False

        if unban_ok:
            await ctx.send(f"✅ {membre.mention} a été retiré de la blacklist et débanni.")
        elif unban_ok is None:
            await ctx.send(f"✅ {membre.mention} a été retiré de la blacklist (il n'était pas banni).")
        else:
            await ctx.send(
                f"⚠️ {membre.mention} a été retiré de la blacklist, mais le débannissement a échoué "
                "(permissions insuffisantes)."
            )

        await log_sanction(
            guild=ctx.guild,
            action="✅ Retrait de la Blacklist",
            cible=membre,
            moderateur=ctx.author,
            raison="Retrait manuel de la blacklist",
            couleur=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Re-bannit automatiquement un membre qui tente de rejoindre alors qu'il est blacklist."""
        if not est_blacklist(member.guild.id, member.id):
            return

        entree = obtenir_entree_blacklist(member.guild.id, member.id)
        raison = entree.get("raison", "Aucune raison fournie") if entree else "Aucune raison fournie"

        try:
            await member.guild.ban(
                member, reason=f"[Blacklist] Tentative de retour sur le serveur - {raison}"
            )
        except discord.Forbidden:
            return
        except discord.HTTPException:
            return

        await log_sanction(
            guild=member.guild,
            action="⛔ Blacklist : Ban Automatique (nouvelle tentative)",
            cible=member,
            moderateur=member.guild.me,
            raison=f"Membre blacklist ayant tenté de rejoindre le serveur. Raison originale : {raison}",
            couleur=discord.Color.dark_red(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Blacklist(bot))