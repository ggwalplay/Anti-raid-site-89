import discord
from discord.ext import commands

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    check_gerant,
)

LABELS_LOGS = {
    "commandes": "Logs des commandes",
    "sanctions": "Logs des sanctions",
    "tickets": "Logs des tickets",
}


def construire_embed_accueil(guild: discord.Guild) -> discord.Embed:
    config = charger_config().get(str(guild.id), {})
    roles_staff = config.get("staff_roles", [])
    logs_commandes = config.get("logs_commandes_channel")
    logs_sanctions = config.get("logs_sanctions_channel")
    logs_tickets = config.get("logs_tickets_channel")

    embed = discord.Embed(
        title="Panel de configuration",
        description="Choisissez une section à configurer ci-dessous.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Rôles staff",
        value=f"{len(roles_staff)} rôle(s) configuré(s)" if roles_staff else "Aucun rôle configuré",
        inline=True,
    )
    embed.add_field(
        name="Logs des commandes",
        value=f"<#{logs_commandes}>" if logs_commandes else "Non configuré",
        inline=True,
    )
    embed.add_field(
        name="Logs des sanctions",
        value=f"<#{logs_sanctions}>" if logs_sanctions else "Non configuré",
        inline=True,
    )
    embed.add_field(
        name="Logs des tickets",
        value=f"<#{logs_tickets}>" if logs_tickets else "Non configuré",
        inline=True,
    )
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


def construire_embed_staff(guild: discord.Guild, roles_ids: list[int]) -> discord.Embed:
    embed = discord.Embed(
        title="Rôles staff",
        description=(
            "Ces rôles auront accès aux commandes de modération du bot. "
            "Les rôles gérant définis en dur ne sont pas concernés par ce panel."
        ),
        color=discord.Color.blurple(),
    )

    roles_valides = [guild.get_role(r) for r in roles_ids]
    roles_valides = [r for r in roles_valides if r is not None]

    valeur = "\n".join(role.mention for role in roles_valides) if roles_valides else "Aucun rôle configuré pour le moment."
    embed.add_field(name="Rôles actuels", value=valeur, inline=False)
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


def construire_embed_logs_menu(guild: discord.Guild) -> discord.Embed:
    config = charger_config().get(str(guild.id), {})
    logs_commandes = config.get("logs_commandes_channel")
    logs_sanctions = config.get("logs_sanctions_channel")
    logs_tickets = config.get("logs_tickets_channel")

    embed = discord.Embed(
        title="Logs",
        description="Choisissez quel type de logs vous voulez configurer.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Logs des commandes",
        value=f"<#{logs_commandes}>" if logs_commandes else "Non configuré",
        inline=False,
    )
    embed.add_field(
        name="Logs des sanctions",
        value=f"<#{logs_sanctions}>" if logs_sanctions else "Non configuré",
        inline=False,
    )
    embed.add_field(
        name="Logs des tickets",
        value=f"<#{logs_tickets}>" if logs_tickets else "Non configuré",
        inline=False,
    )
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


def construire_embed_logs_detail(guild: discord.Guild, categorie: str, channel_id: int | None) -> discord.Embed:
    embed = discord.Embed(
        title=LABELS_LOGS[categorie],
        description="Choisissez le salon dans lequel ces logs seront envoyés.",
        color=discord.Color.blurple(),
    )
    valeur = f"<#{channel_id}>" if channel_id else "Aucun salon configuré pour le moment."
    embed.add_field(name="Salon actuel", value=valeur, inline=False)
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


class PanelBase(discord.ui.View):
    def __init__(self, guild_id: int, auteur_id: int, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message(
                "Seul l'auteur de la commande peut utiliser ce panel.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SelectRolesStaff(discord.ui.RoleSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(placeholder="Choisir les rôles staff", min_values=0, max_values=10)

    async def callback(self, interaction: discord.Interaction):
        roles_choisis = [role.id for role in self.values]

        config = charger_config()
        config.setdefault(str(self.guild_id), {})["staff_roles"] = roles_choisis
        sauvegarder_config(config)

        embed = construire_embed_staff(interaction.guild, roles_choisis)
        await interaction.response.edit_message(embed=embed, view=self.view)


class SelectChannelLogs(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int, categorie: str):
        self.guild_id = guild_id
        self.categorie = categorie
        super().__init__(
            placeholder=f"Choisir le salon pour {LABELS_LOGS[categorie].lower()}",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        salon = self.values[0]

        config = charger_config()
        config.setdefault(str(self.guild_id), {})[f"logs_{self.categorie}_channel"] = salon.id
        sauvegarder_config(config)

        embed = construire_embed_logs_detail(interaction.guild, self.categorie, salon.id)
        await interaction.response.edit_message(embed=embed, view=self.view)


class PanelStaff(PanelBase):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectRolesStaff(guild_id))

    @discord.ui.button(label="Réinitialiser", style=discord.ButtonStyle.secondary, row=1)
    async def reinitialiser(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = charger_config()
        config.setdefault(str(self.guild_id), {})["staff_roles"] = []
        sauvegarder_config(config)

        embed = construire_embed_staff(interaction.guild, [])
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil(interaction.guild)
        view = MenuPrincipal(self.guild_id, self.auteur_id)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=1)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class PanelLogsDetail(PanelBase):
    def __init__(self, guild_id: int, auteur_id: int, categorie: str):
        super().__init__(guild_id, auteur_id)
        self.categorie = categorie
        self.add_item(SelectChannelLogs(guild_id, categorie))

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_logs_menu(interaction.guild)
        view = PanelLogsMenu(self.guild_id, self.auteur_id)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=1)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class PanelLogsMenu(PanelBase):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)

    @discord.ui.button(label="Commandes", style=discord.ButtonStyle.primary, row=0)
    async def bouton_commandes(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = charger_config().get(str(self.guild_id), {})
        channel_id = config.get("logs_commandes_channel")

        embed = construire_embed_logs_detail(interaction.guild, "commandes", channel_id)
        view = PanelLogsDetail(self.guild_id, self.auteur_id, "commandes")
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Sanctions", style=discord.ButtonStyle.primary, row=0)
    async def bouton_sanctions(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = charger_config().get(str(self.guild_id), {})
        channel_id = config.get("logs_sanctions_channel")

        embed = construire_embed_logs_detail(interaction.guild, "sanctions", channel_id)
        view = PanelLogsDetail(self.guild_id, self.auteur_id, "sanctions")
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Tickets", style=discord.ButtonStyle.primary, row=0)
    async def bouton_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = charger_config().get(str(self.guild_id), {})
        channel_id = config.get("logs_tickets_channel")

        embed = construire_embed_logs_detail(interaction.guild, "tickets", channel_id)
        view = PanelLogsDetail(self.guild_id, self.auteur_id, "tickets")
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil(interaction.guild)
        view = MenuPrincipal(self.guild_id, self.auteur_id)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)


class MenuPrincipal(PanelBase):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)

    @discord.ui.button(label="Staff", style=discord.ButtonStyle.primary, row=0)
    async def bouton_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = charger_config().get(str(self.guild_id), {})
        roles_actuels = config.get("staff_roles", [])

        embed = construire_embed_staff(interaction.guild, roles_actuels)
        view = PanelStaff(self.guild_id, self.auteur_id)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.primary, row=0)
    async def bouton_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_logs_menu(interaction.guild)
        view = PanelLogsMenu(self.guild_id, self.auteur_id)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="setup", help="Ouvre le panel de configuration du bot (rôles staff, salons de logs).")
    @commands.guild_only()
    @check_gerant()
    async def setup_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil(ctx.guild)
        view = MenuPrincipal(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=view)
        view.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))