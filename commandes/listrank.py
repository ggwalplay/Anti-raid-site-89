import discord
from discord.ext import commands

from commandes._permissions import check_owner, decouper_lignes

# Un embed est limité à 25 fields par Discord : si la liste de membres est
# trop longue même après découpage par decouper_lignes, on répartit les
# fields sur plusieurs embeds (envoyés à la suite dans le même MP).
MAX_FIELDS_PAR_EMBED = 25


def _est_sanctionnable(guild: discord.Guild, membre: discord.Member) -> bool:
    """Un membre est sanctionnable si le rôle le plus haut du bot est
    strictement au-dessus du sien (même logique que _verifier_hierarchie
    dans sanction.py). Le propriétaire du serveur est toujours exclu :
    Discord empêche de le kick/ban/mute quelle que soit la hiérarchie de
    rôles.
    """
    if membre.id == guild.owner_id:
        return False
    return guild.me.top_role > membre.top_role


class ListRank(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="listrank",
        help="Envoie en MP la liste des membres que le bot peut actuellement sanctionner "
        "(rôle du bot au-dessus du leur, propriétaire exclu).",
    )
    @commands.guild_only()
    @check_owner()
    async def listrank(self, ctx: commands.Context):
        guild = ctx.guild
        message_statut = await ctx.send("⏳ Calcul de la liste en cours...")

        sanctionnables = [m for m in guild.members if _est_sanctionnable(guild, m)]
        sanctionnables.sort(key=lambda m: (-m.top_role.position, m.display_name.lower()))

        embeds = self._construire_embeds(guild, sanctionnables)

        try:
            for embed in embeds:
                await ctx.author.send(embed=embed)
        except discord.Forbidden:
            await message_statut.edit(
                content="❌ Impossible de vous envoyer la liste en MP (vérifiez vos paramètres de "
                "confidentialité, notamment 'Autoriser les messages privés des membres du serveur')."
            )
            return

        await message_statut.edit(
            content=f"✅ Liste envoyée en MP ({len(sanctionnables)} membre(s) sanctionnable(s))."
        )

    @staticmethod
    def _construire_embeds(guild: discord.Guild, sanctionnables: list[discord.Member]) -> list[discord.Embed]:
        entete = (
            f"Rôle du bot : {guild.me.top_role.mention} (position {guild.me.top_role.position})\n"
            f"Sanctionnables : **{len(sanctionnables)}** / {guild.member_count} membre(s)\n"
            f"Exclus : le propriétaire du serveur et les membres dont le rôle est égal ou "
            f"supérieur à celui du bot."
        )

        if not sanctionnables:
            return [
                discord.Embed(
                    title=f"📋 Membres sanctionnables — {guild.name}",
                    description=entete + "\n\n*Aucun membre sanctionnable actuellement.*",
                    color=discord.Color.blurple(),
                )
            ]

        lignes = [
            f"{'🤖 ' if m.bot else ''}{m.mention} — {m.top_role.mention} (`{m}`)" for m in sanctionnables
        ]
        morceaux = decouper_lignes(lignes)

        embeds = []
        for debut in range(0, len(morceaux), MAX_FIELDS_PAR_EMBED):
            groupe = morceaux[debut : debut + MAX_FIELDS_PAR_EMBED]
            embed = discord.Embed(
                title=f"📋 Membres sanctionnables — {guild.name}",
                color=discord.Color.blurple(),
            )
            if debut == 0:
                embed.description = entete
            for i, morceau in enumerate(groupe, start=1):
                embed.add_field(name=f"Liste ({debut + i}/{len(morceaux)})", value=morceau, inline=False)
            embeds.append(embed)
        return embeds


async def setup(bot: commands.Bot):
    await bot.add_cog(ListRank(bot))
