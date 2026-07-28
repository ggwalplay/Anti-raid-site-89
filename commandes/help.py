import discord
from discord.ext import commands

from commandes._permissions import est_owner

# Ordre du plus restrictif au plus permissif, utilisé pour le tri et la légende.
NIVEAUX = [
    ("check_owner", "🔴 Owner"),
    ("check_gerant", "🟠 Gérant"),
    ("check_admin", "🟡 Administrateur"),
    ("check_staff", "🟢 Staff"),
]

# Certaines commandes vérifient la permission manuellement dans leur corps
# (ex: &derank, volontairement silencieuse pour les non-owners plutôt que de
# renvoyer une erreur qui révélerait son existence) plutôt que via un
# décorateur @check_xxx(). Ces cas ne peuvent pas être détectés automatiquement
# depuis command.checks : on les déclare ici explicitement pour que l'audit
# des permissions reste exact.
NIVEAUX_MANUELS = {
    "derank": "🔴 Owner (vérifié manuellement dans le code, sans message d'erreur)",
}


def determiner_niveau(command: commands.Command) -> str:
    """Déduit le niveau de permission requis à partir des checks posés sur la commande.

    Repose sur le nommage des fonctions internes de _permissions.py (check_owner,
    check_gerant, check_admin, check_staff enveloppent toutes une fonction
    `predicate`) : leur __qualname__ ressemble à "check_admin.<locals>.predicate".
    Aucune modification des autres fichiers de commandes n'est nécessaire, sauf
    pour les cas listés dans NIVEAUX_MANUELS (vérification faite en dur dans le code).
    """
    if command.qualified_name in NIVEAUX_MANUELS:
        return NIVEAUX_MANUELS[command.qualified_name]

    for check in command.checks:
        qualname = getattr(check, "__qualname__", "")
        for prefixe, label in NIVEAUX:
            if qualname.startswith(f"{prefixe}."):
                return label
    return "⚪ Public"


class HelpPersonnalise(commands.HelpCommand):
    """Help command qui liste TOUTES les commandes (pour audit des permissions),
    avec pour chacune : le niveau requis, et si l'auteur y a accès ou non.

    Les commandes marquées hidden=True (ex: &derank) restent masquées, sauf
    pour le(s) owner(s) du bot, qui peuvent ainsi auditer l'intégralité des
    commandes existantes.
    """

    def get_command_signature(self, command: commands.Command) -> str:
        return f"{self.context.clean_prefix}{command.qualified_name} {command.signature}".strip()

    async def _est_visible(self, command: commands.Command) -> bool:
        if not command.hidden:
            return True
        return est_owner(self.context.author.id)

    async def _est_autorise(self, command: commands.Command) -> bool:
        if command.qualified_name in NIVEAUX_MANUELS:
            # Ces commandes ne posent pas de check décorateur : command.can_run()
            # renverrait toujours True. On retombe sur la même règle que le code
            # réel de la commande (ici : réservé au(x) owner(s)).
            return est_owner(self.context.author.id)
        try:
            return await command.can_run(self.context)
        except commands.CommandError:
            return False

    async def send_bot_help(self, mapping):
        ctx = self.context
        embed = discord.Embed(
            title="📖 Commandes du bot",
            description=(
                f"Liste complète des commandes, avec leur niveau de permission requis.\n"
                f"Préfixe : `{ctx.clean_prefix}`\n\n"
                f"✅ = vous y avez accès  •  🔒 = accès refusé pour vous\n"
                f"🔴 Owner  •  🟠 Gérant  •  🟡 Administrateur  •  🟢 Staff  •  ⚪ Public (aucun check)"
            ),
            color=discord.Color.blurple(),
        )
        if ctx.guild:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        au_moins_une_commande = False

        for cog, commandes in mapping.items():
            commandes_visibles = [c for c in commandes if await self._est_visible(c)]
            if not commandes_visibles:
                continue

            au_moins_une_commande = True
            nom_categorie = cog.qualified_name if cog else "Général"

            lignes = []
            for commande in sorted(commandes_visibles, key=lambda c: c.name):
                autorise = await self._est_autorise(commande)
                icone = "✅" if autorise else "🔒"
                niveau = determiner_niveau(commande)
                description = commande.help or "Aucune description."
                lignes.append(
                    f"{icone} **`{ctx.clean_prefix}{commande.name}`** — {description} *({niveau})*"
                )

            embed.add_field(name=f"__{nom_categorie}__", value="\n".join(lignes), inline=False)

        if not au_moins_une_commande:
            embed.description += "\n\n*Aucune commande trouvée.*"

        embed.set_footer(text=f"Tapez {ctx.clean_prefix}help <commande> pour plus de détails sur une commande.")
        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command: commands.Command):
        ctx = self.context

        if not await self._est_visible(command):
            await self.get_destination().send("Cette commande n'existe pas.")
            return

        autorise = await self._est_autorise(command)
        niveau = determiner_niveau(command)

        embed = discord.Embed(
            title=f"{ctx.clean_prefix}{command.qualified_name}",
            description=command.help or "Aucune description.",
            color=discord.Color.blurple() if autorise else discord.Color.dark_grey(),
        )
        embed.add_field(name="Utilisation", value=f"`{self.get_command_signature(command)}`", inline=False)
        embed.add_field(name="Niveau requis", value=niveau, inline=True)
        embed.add_field(name="Votre accès", value="✅ Autorisé" if autorise else "🔒 Refusé", inline=True)

        if command.aliases:
            embed.add_field(name="Alias", value=", ".join(f"`{a}`" for a in command.aliases), inline=False)

        if command.hidden:
            embed.set_footer(text="⚠️ Commande cachée (masquée du help pour tout le monde sauf le(s) owner(s)).")

        await self.get_destination().send(embed=embed)

    async def send_group_help(self, group: commands.Group):
        await self.send_command_help(group)

    async def send_error_message(self, error: str):
        await self.get_destination().send(error)


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ancien_help_command = bot.help_command
        bot.help_command = HelpPersonnalise()
        bot.help_command.cog = self

    def cog_unload(self):
        self.bot.help_command = self._ancien_help_command


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
