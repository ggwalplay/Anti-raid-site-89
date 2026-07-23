import discord
from discord.ext import commands


class HelpPersonnalise(commands.HelpCommand):
    """Help command qui ne liste que les commandes que l'auteur a le droit d'utiliser.

    Le filtrage repose sur les checks discord.py (@commands.check, @check_gerant(),
    @commands.guild_only(), etc.) posés sur chaque commande : on teste chaque
    commande avec `command.can_run(ctx)` et on ne garde que celles qui passent.
    """

    def get_command_signature(self, command: commands.Command) -> str:
        return f"{self.context.clean_prefix}{command.qualified_name} {command.signature}".strip()

    async def _commandes_autorisees(self, commandes) -> list[commands.Command]:
        ctx = self.context
        autorisees = []
        for commande in commandes:
            if commande.hidden:
                continue
            try:
                if await commande.can_run(ctx):
                    autorisees.append(commande)
            except commands.CommandError:
                continue
        return autorisees

    async def send_bot_help(self, mapping):
        ctx = self.context
        embed = discord.Embed(
            title="📖 Commandes disponibles",
            description=(
                f"Voici les commandes que **{ctx.author.display_name}** peut utiliser ici.\n"
                f"Préfixe : `{ctx.clean_prefix}`"
            ),
            color=discord.Color.blurple(),
        )
        if ctx.guild:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        au_moins_une_commande = False

        for cog, commandes in mapping.items():
            commandes_autorisees = await self._commandes_autorisees(commandes)
            if not commandes_autorisees:
                continue

            au_moins_une_commande = True
            nom_categorie = cog.qualified_name if cog else "Général"

            lignes = []
            for commande in sorted(commandes_autorisees, key=lambda c: c.name):
                description = commande.help or "Aucune description."
                lignes.append(f"**`{ctx.clean_prefix}{commande.name}`** — {description}")

            embed.add_field(name=f"__{nom_categorie}__", value="\n".join(lignes), inline=False)

        if not au_moins_une_commande:
            embed.description += "\n\n*Aucune commande disponible pour vous sur ce serveur.*"

        embed.set_footer(text=f"Tapez {ctx.clean_prefix}help <commande> pour plus de détails sur une commande.")
        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command: commands.Command):
        ctx = self.context
        try:
            autorise = await command.can_run(ctx)
        except commands.CommandError:
            autorise = False

        if not autorise or command.hidden:
            await self.get_destination().send(
                "Cette commande n'existe pas ou vous n'avez pas la permission de l'utiliser."
            )
            return

        embed = discord.Embed(
            title=f"{ctx.clean_prefix}{command.qualified_name}",
            description=command.help or "Aucune description.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Utilisation", value=f"`{self.get_command_signature(command)}`", inline=False)

        if command.aliases:
            embed.add_field(name="Alias", value=", ".join(f"`{a}`" for a in command.aliases), inline=False)

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
