import discord
from discord.ext import commands

from commandes._permissions import (
    est_whitelist,
    ajouter_whitelist,
    retirer_whitelist,
    charger_whitelist,
    check_gerant,
)


class Whitelist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="wl",
        help="Ajoute/retire un membre de la whitelist anti-ban, ou `&wl liste` pour voir tous les membres whitelistés.",
    )
    @commands.guild_only()
    @check_gerant()
    async def wl(self, ctx: commands.Context, argument: str = None):
        if argument is None:
            await ctx.send("Utilisation : `&wl <id_membre>` ou `&wl liste`")
            return

        if argument.lower() in ("liste", "list"):
            await self._afficher_liste(ctx)
            return

        if not argument.isdigit():
            await ctx.send("ID de membre invalide. Utilisation : `&wl <id_membre>` ou `&wl liste`")
            return

        membre_id = int(argument)

        if est_whitelist(ctx.guild.id, membre_id):
            retirer_whitelist(ctx.guild.id, membre_id)
            await ctx.send(f"❌ <@{membre_id}> a été retiré de la whitelist anti-ban.")
        else:
            ajouter_whitelist(ctx.guild.id, membre_id)
            await ctx.send(
                f"✅ <@{membre_id}> a été ajouté à la whitelist anti-ban.\n"
                "S'il se fait bannir, il sera automatiquement débanni."
            )

    async def _afficher_liste(self, ctx: commands.Context) -> None:
        whitelist = charger_whitelist()
        ids = whitelist.get(str(ctx.guild.id), [])

        embed = discord.Embed(title="📋 Whitelist anti-ban", color=discord.Color.blurple())

        if not ids:
            embed.description = "Aucun membre whitelisté sur ce serveur."
            await ctx.send(embed=embed)
            return

        lignes = [f"<@{membre_id}> (`{membre_id}`)" for membre_id in ids]

        # Découpe en blocs de 1024 caractères max (limite Discord par champ d'embed)
        blocs: list[str] = []
        bloc_actuel = ""
        for ligne in lignes:
            candidat = f"{bloc_actuel}\n{ligne}" if bloc_actuel else ligne
            if len(candidat) > 1024:
                blocs.append(bloc_actuel)
                bloc_actuel = ligne
            else:
                bloc_actuel = candidat
        if bloc_actuel:
            blocs.append(bloc_actuel)

        for i, bloc in enumerate(blocs):
            nom_champ = "Membres" if i == 0 else "Membres (suite)"
            embed.add_field(name=nom_champ, value=bloc, inline=False)

        embed.set_footer(text=f"{len(ids)} membre(s) whitelisté(s)")
        await ctx.send(embed=embed)

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
