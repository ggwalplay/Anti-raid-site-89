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
        titre = f"📋 Membres sanctionnables — {guild.name}"
        entete = (
            f"Rôle du bot : {guild.me.top_role.mention} (position {guild.me.top_role.position})\n"
            f"Sanctionnables : **{len(sanctionnables)}** / {guild.member_count} membre(s)\n"
            f"Exclus : le propriétaire du serveur et les membres dont le rôle est égal ou "
            f"supérieur à celui du bot."
        )

        if not sanctionnables:
            return [
                discord.Embed(
                    title=titre,
                    description=entete + "\n\n*Aucun membre sanctionnable actuellement.*",
                    color=discord.Color.blurple(),
                )
            ]

        lignes = [
            f"{'🤖 ' if m.bot else ''}{m.mention} — {m.top_role.mention} (`{m}`)" for m in sanctionnables
        ]
        morceaux = decouper_lignes(lignes)
        total_morceaux = len(morceaux)

        # Discord limite un embed à 6000 caractères au total (titre + description
        # + tous les fields cumulés) ET à 25 fields. On construit donc les embeds
        # dynamiquement en suivant la taille réelle plutôt qu'un simple découpage
        # par nombre de fields, sous peine de "Embed size exceeds maximum size".
        LIMITE_TOTALE_EMBED = 5900  # marge de sécurité sous la limite réelle de 6000
        embeds: list[discord.Embed] = []
        embed: discord.Embed | None = None
        taille_courante = 0

        def nouvel_embed(avec_entete: bool) -> None:
            nonlocal embed, taille_courante
            embed = discord.Embed(title=titre, color=discord.Color.blurple())
            taille_courante = len(titre)
            if avec_entete:
                embed.description = entete
                taille_courante += len(entete)
            embeds.append(embed)

        nouvel_embed(avec_entete=True)
        for i, morceau in enumerate(morceaux, start=1):
            nom_field = f"Liste ({i}/{total_morceaux})"
            taille_field = len(nom_field) + len(morceau)
            if len(embed.fields) >= MAX_FIELDS_PAR_EMBED or taille_courante + taille_field > LIMITE_TOTALE_EMBED:
                nouvel_embed(avec_entete=False)
            embed.add_field(name=nom_field, value=morceau, inline=False)
            taille_courante += taille_field
        return embeds


async def setup(bot: commands.Bot):
    await bot.add_cog(ListRank(bot))
