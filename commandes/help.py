import discord
from discord.ext import commands

from commandes._permissions import est_owner

# Du plus restrictif au plus permissif : détermine à la fois le label affiché
# et l'ordre d'affichage des sections dans le help.
NIVEAUX = [
    ("check_owner", "🔴", "Owner"),
    ("check_gerant", "🟠", "Gérant"),
    ("check_admin", "🟡", "Administrateur"),
    ("check_staff", "🟢", "Staff"),
]

# Commandes vérifiant leur permission manuellement dans le corps de la fonction
# (ex: &derank, volontairement silencieuse pour ne pas révéler son existence
# à un non-owner) plutôt que via un décorateur @check_xxx(). Indétectable
# automatiquement depuis command.checks : déclaré ici pour un audit exact.
NIVEAUX_MANUELS = {
    "derank": ("🔴", "Owner"),
}

EMOJI_PAR_DEFAUT = "⚪"
LABEL_PAR_DEFAUT = "Public"


def determiner_niveau(command: commands.Command) -> tuple[str, str]:
    """Retourne (emoji, label) du niveau de permission requis par la commande."""
    if command.qualified_name in NIVEAUX_MANUELS:
        return NIVEAUX_MANUELS[command.qualified_name]

    for check in command.checks:
        qualname = getattr(check, "__qualname__", "")
        for prefixe, emoji, label in NIVEAUX:
            if qualname.startswith(f"{prefixe}."):
                return emoji, label
    return EMOJI_PAR_DEFAUT, LABEL_PAR_DEFAUT


class HelpPersonnalise(commands.HelpCommand):
    """Help command listant TOUTES les commandes, regroupées par niveau de
    permission requis (plutôt que par cog), pour un audit rapide et lisible.

    Les commandes hidden=True (ex: &derank) restent masquées, sauf pour le(s)
    owner(s) du bot.
    """

    def get_command_signature(self, command: commands.Command) -> str:
        return f"{self.context.clean_prefix}{command.qualified_name} {command.signature}".strip()

    async def _est_visible(self, command: commands.Command) -> bool:
        if not command.hidden:
            return True
        return est_owner(self.context.author.id)

    async def _est_autorise(self, command: commands.Command) -> bool:
        if command.qualified_name in NIVEAUX_MANUELS:
            return est_owner(self.context.author.id)
        try:
            return await command.can_run(self.context)
        except commands.CommandError:
            return False

    async def send_bot_help(self, mapping):
        ctx = self.context

        toutes_commandes = [cmd for cmds in mapping.values() for cmd in cmds]
        visibles = [cmd for cmd in toutes_commandes if await self._est_visible(cmd)]

        embed = discord.Embed(
            title="📖 Commandes du bot",
            description=f"Préfixe : `{ctx.clean_prefix}`  •  ✅ accessible pour vous  •  🔒 refusé",
            color=discord.Color.blurple(),
        )
        if ctx.guild:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        if not visibles:
            embed.description += "\n\n*Aucune commande trouvée.*"
            await self.get_destination().send(embed=embed)
            return

        groupes: dict[tuple[str, str], list[commands.Command]] = {}
        for commande in visibles:
            niveau = determiner_niveau(commande)
            groupes.setdefault(niveau, []).append(commande)

        ordre_labels = [f"{emoji} {label}" for _, emoji, label in NIVEAUX] + [f"{EMOJI_PAR_DEFAUT} {LABEL_PAR_DEFAUT}"]

        for emoji, label in sorted(groupes.keys(), key=lambda n: ordre_labels.index(f"{n[0]} {n[1]}")):
            commandes_du_niveau = groupes[(emoji, label)]
            lignes = []
            for commande in sorted(commandes_du_niveau, key=lambda c: c.name):
                autorise = await self._est_autorise(commande)
                icone = "✅" if autorise else "🔒"
                description = commande.help or "Aucune description."
                lignes.append(f"{icone} `{ctx.clean_prefix}{commande.name}` — {description}")

            embed.add_field(name=f"{emoji}  {label}", value="\n".join(lignes), inline=False)

        embed.set_footer(text=f"{ctx.clean_prefix}help <commande> pour le détail d'une commande")
        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command: commands.Command):
        ctx = self.context

        if not await self._est_visible(command):
            await self.get_destination().send("Cette commande n'existe pas.")
            return

        autorise = await self._est_autorise(command)
        emoji, label = determiner_niveau(command)

        embed = discord.Embed(
            title=f"{ctx.clean_prefix}{command.qualified_name}",
            description=command.help or "Aucune description.",
            color=discord.Color.blurple() if autorise else discord.Color.dark_grey(),
        )
        embed.add_field(name="Utilisation", value=f"`{self.get_command_signature(command)}`", inline=False)
        embed.add_field(name="Niveau requis", value=f"{emoji} {label}", inline=True)
        embed.add_field(name="Votre accès", value="✅ Autorisé" if autorise else "🔒 Refusé", inline=True)

        if command.aliases:
            embed.add_field(name="Alias", value=", ".join(f"`{a}`" for a in command.aliases), inline=False)

        if command.hidden:
            embed.set_footer(text="⚠️ Commande cachée (masquée du help pour tout le monde sauf le(s) owner(s))")

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
