import uuid

import discord
from discord.ext import commands

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    est_admin,
    check_admin,
    get_logs_channel_id,
)
from commandes.logs import envoyer_log

MAX_PANELS = 25
MAX_OPTIONS_PAR_PANEL = 25  # limite Discord d'un menu déroulant


# ============================================================
#  CONFIGURATION (stockée dans data/config.json, clé "roles_panels")
# ============================================================

def get_role_panels(guild_id: int) -> dict:
    config = charger_config()
    return config.get(str(guild_id), {}).get("roles_panels", {})


def sauvegarder_role_panels(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["roles_panels"] = data
    sauvegarder_config(config)


def get_panel(guild_id: int, panel_id: str) -> dict | None:
    return get_role_panels(guild_id).get(panel_id)


def options_valides(panel: dict) -> dict:
    """Ne garde que les options auxquelles un rôle Discord a réellement été assigné."""
    return {oid: info for oid, info in panel.get("options", {}).items() if info.get("role_id")}


# ============================================================
#  MENU PUBLIC (vue persistante envoyée dans le salon choisi)
# ============================================================

class SelectRolePublic(discord.ui.Select):
    def __init__(self, guild_id: int, panel_id: str, options_info: dict):
        options = [
            discord.SelectOption(
                label=(info.get("nom") or "?")[:100],
                description=(info.get("description") or "")[:100] or None,
                emoji=info.get("emoji") or None,
                value=option_id,
            )
            for option_id, info in list(options_info.items())[:MAX_OPTIONS_PAR_PANEL]
        ]
        super().__init__(
            placeholder="Choisissez vos rôles...",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id=f"role_select|{guild_id}|{panel_id}",
        )
        self.guild_id = guild_id
        self.panel_id = panel_id

    async def callback(self, interaction: discord.Interaction):
        panel = get_panel(self.guild_id, self.panel_id)
        if panel is None:
            await interaction.response.send_message("Ce panel de rôles n'existe plus.", ephemeral=True)
            return

        selection = set(self.values)
        ajoutes, retires, echecs = [], [], []

        for option_id, info in options_valides(panel).items():
            role = interaction.guild.get_role(info["role_id"])
            if role is None:
                continue

            a_le_role = role in interaction.user.roles
            veut_le_role = option_id in selection

            try:
                if veut_le_role and not a_le_role:
                    await interaction.user.add_roles(role, reason="[RoleReaction] Sélection via menu déroulant")
                    ajoutes.append(role)
                elif not veut_le_role and a_le_role:
                    await interaction.user.remove_roles(role, reason="[RoleReaction] Désélection via menu déroulant")
                    retires.append(role)
            except discord.Forbidden:
                echecs.append(role)

        morceaux = []
        if ajoutes:
            morceaux.append("✅ Ajouté(s) : " + ", ".join(r.mention for r in ajoutes))
        if retires:
            morceaux.append("➖ Retiré(s) : " + ", ".join(r.mention for r in retires))
        if echecs:
            morceaux.append(
                "⚠️ Impossible de modifier (rôle du bot trop bas dans la hiérarchie) : "
                + ", ".join(r.mention for r in echecs)
            )
        if not morceaux:
            morceaux.append("Aucun changement.")

        await interaction.response.send_message("\n".join(morceaux), ephemeral=True)

        if ajoutes or retires:
            description = f"{interaction.user.mention} a mis à jour ses rôles via **{panel.get('titre', 'Panel de rôles')}**."
            if ajoutes:
                description += "\n✅ " + ", ".join(r.mention for r in ajoutes)
            if retires:
                description += "\n➖ " + ", ".join(r.mention for r in retires)
            await envoyer_log(
                interaction.guild,
                "roles",
                discord.Embed(
                    title="🎭 Rôles auto mis à jour",
                    description=description,
                    color=discord.Color.blurple(),
                    timestamp=discord.utils.utcnow(),
                ),
            )


class RolePublicView(discord.ui.View):
    def __init__(self, guild_id: int, panel_id: str, options_info: dict):
        super().__init__(timeout=None)
        self.add_item(SelectRolePublic(guild_id, panel_id, options_info))


def construire_vue_role_panel(guild_id: int, panel_id: str, options_info: dict) -> RolePublicView:
    return RolePublicView(guild_id, panel_id, options_info)


# ============================================================
#  PANEL DE GESTION (&role) — configuration par les admins
# ============================================================

def construire_embed_accueil_roles(guild: discord.Guild) -> discord.Embed:
    panels = get_role_panels(guild.id)
    embed = discord.Embed(
        title="🎭 Panel de gestion des rôles auto-attribués",
        description=(
            "Créez des panels de rôles (un menu déroulant par panel) que les membres pourront utiliser "
            "pour s'attribuer eux-mêmes des rôles.\n"
            "Le salon de logs se configure via `&setup` → *Logs* → *Rôles*."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Panels configurés", value=f"{len(panels)} / {MAX_PANELS}", inline=True)
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


class PanelBaseRole(discord.ui.View):
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


# --- ECRAN 1 : MENU PRINCIPAL ---

class SelectPanelExistant(discord.ui.Select):
    def __init__(self, guild_id: int, panels: dict):
        options = [
            discord.SelectOption(
                label=(info.get("titre") or "?")[:100],
                description=f"{len(options_valides(info))} rôle(s) configuré(s)",
                value=panel_id,
            )
            for panel_id, info in list(panels.items())[:25]
        ]
        super().__init__(placeholder="Gérer un panel existant...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        view: MenuPrincipalRole = self.view
        panel_id = self.values[0]
        embed = construire_embed_panel_detail(interaction.guild, panel_id)
        vue = PanelDetail(self.guild_id, view.auteur_id, panel_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class ModalNouveauPanel(discord.ui.Modal, title="Créer un panel de rôles"):
    titre = discord.ui.TextInput(label="Titre du panel", max_length=100, placeholder="Choix des notifications")
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        max_length=300,
        required=False,
        placeholder="Sélectionnez les notifications que vous souhaitez recevoir.",
    )

    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id

    async def on_submit(self, interaction: discord.Interaction):
        panels = get_role_panels(self.guild_id)
        panel_id = uuid.uuid4().hex[:8]
        panels[panel_id] = {
            "titre": self.titre.value.strip(),
            "description": self.description.value.strip(),
            "salon_id": None,
            "message_id": None,
            "options": {},
        }
        sauvegarder_role_panels(self.guild_id, panels)

        embed = construire_embed_panel_detail(interaction.guild, panel_id)
        vue = PanelDetail(self.guild_id, self.auteur_id, panel_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class MenuPrincipalRole(PanelBaseRole):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        panels = get_role_panels(guild_id)
        if panels:
            self.add_item(SelectPanelExistant(guild_id, panels))

    @discord.ui.button(label="Créer un panel", style=discord.ButtonStyle.success, row=1, emoji="➕")
    async def creer(self, interaction: discord.Interaction, button: discord.ui.Button):
        panels = get_role_panels(self.guild_id)
        if len(panels) >= MAX_PANELS:
            await interaction.response.send_message(f"Limite de {MAX_PANELS} panels atteinte.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalNouveauPanel(self.guild_id, self.auteur_id))

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=1)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# --- ECRAN 2 : DETAIL D'UN PANEL (salon, options, envoi) ---

def construire_embed_panel_detail(guild: discord.Guild, panel_id: str) -> discord.Embed:
    panel = get_panel(guild.id, panel_id) or {}
    salon = guild.get_channel(panel.get("salon_id")) if panel.get("salon_id") else None
    options = panel.get("options", {})

    embed = discord.Embed(
        title=f"🎭 {panel.get('titre', '?')}",
        description=panel.get("description") or "Aucune description.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Salon", value=salon.mention if salon else "Non configuré", inline=True)
    embed.add_field(name="Envoyé", value="Oui" if panel.get("message_id") else "Non", inline=True)

    if not options:
        embed.add_field(name="Rôles", value="Aucun rôle ajouté pour l'instant.", inline=False)
    else:
        for info in list(options.values())[:25]:
            role_mention = f"<@&{info['role_id']}>" if info.get("role_id") else "⚠️ Rôle non assigné"
            embed.add_field(
                name=f"{info.get('emoji', '🎭')} {info.get('nom', '?')}",
                value=f"{role_mention}\n{info.get('description') or '—'}",
                inline=False,
            )

    embed.set_footer(text=f"ID interne : {panel_id}")
    return embed


class SelectSalonRole(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int, panel_id: str):
        self.guild_id = guild_id
        self.panel_id = panel_id
        super().__init__(
            placeholder="Choisir le salon où sera envoyé ce panel",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        panels = get_role_panels(self.guild_id)
        panel = panels.get(self.panel_id)
        if panel is None:
            await interaction.response.send_message("Ce panel n'existe plus.", ephemeral=True)
            return

        panel["salon_id"] = self.values[0].id
        sauvegarder_role_panels(self.guild_id, panels)

        embed = construire_embed_panel_detail(interaction.guild, self.panel_id)
        await interaction.response.edit_message(embed=embed, view=self.view)


class SelectOptionExistante(discord.ui.Select):
    def __init__(self, guild_id: int, panel_id: str, options: dict):
        select_options = [
            discord.SelectOption(
                label=(info.get("nom") or "?")[:100],
                emoji=info.get("emoji") or None,
                value=option_id,
            )
            for option_id, info in list(options.items())[:25]
        ]
        super().__init__(placeholder="Modifier un rôle existant...", options=select_options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.panel_id = panel_id

    async def callback(self, interaction: discord.Interaction):
        view: PanelDetail = self.view
        option_id = self.values[0]
        embed = construire_embed_option_detail(interaction.guild, self.guild_id, self.panel_id, option_id)
        vue = PanelOptionDetail(self.guild_id, view.auteur_id, self.panel_id, option_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class ModalNouvelleOption(discord.ui.Modal, title="Ajouter un rôle au panel"):
    nom = discord.ui.TextInput(label="Nom affiché", max_length=80, placeholder="Annonces mises à jour")
    emoji = discord.ui.TextInput(label="Emoji (optionnel)", max_length=10, required=False, placeholder="📢")
    description = discord.ui.TextInput(
        label="Description (optionnel)",
        style=discord.TextStyle.paragraph,
        max_length=100,
        required=False,
        placeholder="Reçoit une notification à chaque mise à jour.",
    )

    def __init__(self, guild_id: int, auteur_id: int, panel_id: str):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        panels = get_role_panels(self.guild_id)
        panel = panels.get(self.panel_id)
        if panel is None:
            await interaction.response.send_message("Ce panel n'existe plus.", ephemeral=True)
            return

        option_id = uuid.uuid4().hex[:8]
        panel.setdefault("options", {})[option_id] = {
            "nom": self.nom.value.strip(),
            "emoji": self.emoji.value.strip() or "🎭",
            "description": self.description.value.strip(),
            "role_id": None,
        }
        sauvegarder_role_panels(self.guild_id, panels)

        embed = construire_embed_option_detail(interaction.guild, self.guild_id, self.panel_id, option_id)
        embed.add_field(
            name="⚠️ À faire",
            value="Sélectionnez le rôle Discord à assigner à cette option ci-dessous.",
            inline=False,
        )
        vue = PanelOptionDetail(self.guild_id, self.auteur_id, self.panel_id, option_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class PanelDetail(PanelBaseRole):
    def __init__(self, guild_id: int, auteur_id: int, panel_id: str):
        super().__init__(guild_id, auteur_id)
        self.panel_id = panel_id
        self.add_item(SelectSalonRole(guild_id, panel_id))
        options = get_role_panels(guild_id).get(panel_id, {}).get("options", {})
        if options:
            self.add_item(SelectOptionExistante(guild_id, panel_id, options))

    @discord.ui.button(label="Ajouter un rôle", style=discord.ButtonStyle.success, row=2, emoji="➕")
    async def ajouter(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = get_panel(self.guild_id, self.panel_id) or {}
        if len(panel.get("options", {})) >= MAX_OPTIONS_PAR_PANEL:
            await interaction.response.send_message(
                f"Limite de {MAX_OPTIONS_PAR_PANEL} rôles par panel atteinte (limite Discord).", ephemeral=True
            )
            return
        await interaction.response.send_modal(ModalNouvelleOption(self.guild_id, self.auteur_id, self.panel_id))

    @discord.ui.button(label="Modifier titre / description", style=discord.ButtonStyle.primary, row=2, emoji="✏️")
    async def modifier(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = get_panel(self.guild_id, self.panel_id) or {}
        await interaction.response.send_modal(ModalModifierPanel(self.guild_id, self.auteur_id, self.panel_id, panel))

    @discord.ui.button(label="Envoyer / Mettre à jour", style=discord.ButtonStyle.primary, row=3, emoji="📤")
    async def envoyer(self, interaction: discord.Interaction, button: discord.ui.Button):
        panels = get_role_panels(self.guild_id)
        panel = panels.get(self.panel_id)
        if panel is None:
            await interaction.response.send_message("Ce panel n'existe plus.", ephemeral=True)
            return

        salon_id = panel.get("salon_id")
        options = options_valides(panel)

        if not salon_id:
            await interaction.response.send_message("Configurez d'abord un salon ci-dessus.", ephemeral=True)
            return
        if not options:
            await interaction.response.send_message(
                "Ajoutez au moins un rôle (avec un rôle Discord assigné) avant d'envoyer le panel.", ephemeral=True
            )
            return

        salon = interaction.guild.get_channel(salon_id)
        if salon is None:
            await interaction.response.send_message("Le salon configuré est introuvable.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🎭 {panel.get('titre', '?')}",
            description=panel.get("description") or "Choisissez vos rôles dans le menu ci-dessous.",
            color=discord.Color.blurple(),
        )
        for info in options.values():
            embed.add_field(
                name=f"{info.get('emoji', '🎭')} {info.get('nom', '?')}",
                value=info.get("description") or "—",
                inline=False,
            )

        vue_publique = construire_vue_role_panel(self.guild_id, self.panel_id, options)

        message_final = None
        ancien_id = panel.get("message_id")
        if ancien_id:
            try:
                ancien_message = await salon.fetch_message(ancien_id)
                await ancien_message.edit(embed=embed, view=vue_publique)
                message_final = ancien_message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message_final = None

        if message_final is None:
            try:
                message_final = await salon.send(embed=embed, view=vue_publique)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Permissions insuffisantes pour envoyer un message dans ce salon.", ephemeral=True
                )
                return

        panel["message_id"] = message_final.id
        sauvegarder_role_panels(self.guild_id, panels)

        await interaction.response.send_message(f"✅ Panel envoyé/mis à jour dans {salon.mention}.", ephemeral=True)

    @discord.ui.button(label="Supprimer ce panel", style=discord.ButtonStyle.danger, row=3, emoji="🗑️")
    async def supprimer(self, interaction: discord.Interaction, button: discord.ui.Button):
        panels = get_role_panels(self.guild_id)
        panels.pop(self.panel_id, None)
        sauvegarder_role_panels(self.guild_id, panels)

        embed = construire_embed_accueil_roles(interaction.guild)
        vue = MenuPrincipalRole(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=4)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_roles(interaction.guild)
        vue = MenuPrincipalRole(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class ModalModifierPanel(discord.ui.Modal, title="Modifier le panel"):
    def __init__(self, guild_id: int, auteur_id: int, panel_id: str, panel: dict):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.panel_id = panel_id

        self.titre = discord.ui.TextInput(label="Titre du panel", max_length=100, default=panel.get("titre", ""))
        self.description = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            max_length=300,
            required=False,
            default=panel.get("description", ""),
        )
        self.add_item(self.titre)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        panels = get_role_panels(self.guild_id)
        panel = panels.get(self.panel_id)
        if panel is None:
            await interaction.response.send_message("Ce panel n'existe plus.", ephemeral=True)
            return

        panel["titre"] = self.titre.value.strip()
        panel["description"] = self.description.value.strip()
        sauvegarder_role_panels(self.guild_id, panels)

        embed = construire_embed_panel_detail(interaction.guild, self.panel_id)
        vue = PanelDetail(self.guild_id, self.auteur_id, self.panel_id)
        await interaction.response.edit_message(embed=embed, view=vue)


# --- ECRAN 3 : DETAIL D'UNE OPTION (rôle assigné, nom/emoji/description, suppression) ---

def construire_embed_option_detail(guild: discord.Guild, guild_id: int, panel_id: str, option_id: str) -> discord.Embed:
    panel = get_panel(guild_id, panel_id) or {}
    info = panel.get("options", {}).get(option_id, {})
    role = guild.get_role(info["role_id"]) if info.get("role_id") else None

    embed = discord.Embed(
        title=f"{info.get('emoji', '🎭')} {info.get('nom', '?')}",
        description=info.get("description") or "Aucune description.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Rôle Discord assigné", value=role.mention if role else "⚠️ Non configuré", inline=False)
    embed.set_footer(text=f"Panel : {panel.get('titre', '?')}")
    return embed


class SelectRoleOption(discord.ui.RoleSelect):
    def __init__(self, guild_id: int, panel_id: str, option_id: str):
        self.guild_id = guild_id
        self.panel_id = panel_id
        self.option_id = option_id
        super().__init__(placeholder="Choisir le rôle Discord à assigner", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        panels = get_role_panels(self.guild_id)
        panel = panels.get(self.panel_id)
        info = panel.get("options", {}).get(self.option_id) if panel else None
        if info is None:
            await interaction.response.send_message("Cette option n'existe plus.", ephemeral=True)
            return

        role = self.values[0]
        if role.is_default() or role.managed:
            await interaction.response.send_message(
                "Impossible d'utiliser @everyone ou un rôle géré automatiquement (bot/intégration).", ephemeral=True
            )
            return

        info["role_id"] = role.id
        sauvegarder_role_panels(self.guild_id, panels)

        embed = construire_embed_option_detail(interaction.guild, self.guild_id, self.panel_id, self.option_id)
        await interaction.response.edit_message(embed=embed, view=self.view)


class ModalModifierOption(discord.ui.Modal, title="Modifier ce rôle"):
    def __init__(self, guild_id: int, auteur_id: int, panel_id: str, option_id: str, info: dict):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.panel_id = panel_id
        self.option_id = option_id

        self.nom = discord.ui.TextInput(label="Nom affiché", max_length=80, default=info.get("nom", ""))
        self.emoji = discord.ui.TextInput(
            label="Emoji (optionnel)", max_length=10, required=False, default=info.get("emoji", "")
        )
        self.description = discord.ui.TextInput(
            label="Description (optionnel)",
            style=discord.TextStyle.paragraph,
            max_length=100,
            required=False,
            default=info.get("description", ""),
        )
        self.add_item(self.nom)
        self.add_item(self.emoji)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        panels = get_role_panels(self.guild_id)
        panel = panels.get(self.panel_id)
        info = panel.get("options", {}).get(self.option_id) if panel else None
        if info is None:
            await interaction.response.send_message("Cette option n'existe plus.", ephemeral=True)
            return

        info["nom"] = self.nom.value.strip()
        info["emoji"] = self.emoji.value.strip() or "🎭"
        info["description"] = self.description.value.strip()
        sauvegarder_role_panels(self.guild_id, panels)

        embed = construire_embed_option_detail(interaction.guild, self.guild_id, self.panel_id, self.option_id)
        vue = PanelOptionDetail(self.guild_id, self.auteur_id, self.panel_id, self.option_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class PanelOptionDetail(PanelBaseRole):
    def __init__(self, guild_id: int, auteur_id: int, panel_id: str, option_id: str):
        super().__init__(guild_id, auteur_id)
        self.panel_id = panel_id
        self.option_id = option_id
        self.add_item(SelectRoleOption(guild_id, panel_id, option_id))

    @discord.ui.button(label="Modifier nom / emoji / description", style=discord.ButtonStyle.primary, row=2, emoji="✏️")
    async def modifier(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = get_panel(self.guild_id, self.panel_id) or {}
        info = panel.get("options", {}).get(self.option_id, {})
        await interaction.response.send_modal(
            ModalModifierOption(self.guild_id, self.auteur_id, self.panel_id, self.option_id, info)
        )

    @discord.ui.button(label="Supprimer ce rôle", style=discord.ButtonStyle.danger, row=2, emoji="🗑️")
    async def supprimer(self, interaction: discord.Interaction, button: discord.ui.Button):
        panels = get_role_panels(self.guild_id)
        panel = panels.get(self.panel_id)
        if panel is not None:
            panel.get("options", {}).pop(self.option_id, None)
            sauvegarder_role_panels(self.guild_id, panels)

        embed = construire_embed_panel_detail(interaction.guild, self.panel_id)
        vue = PanelDetail(self.guild_id, self.auteur_id, self.panel_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=3)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_panel_detail(interaction.guild, self.panel_id)
        vue = PanelDetail(self.guild_id, self.auteur_id, self.panel_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# ============================================================
#  COG
# ============================================================

class Role(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._vues_enregistrees = False

    @commands.Cog.listener()
    async def on_ready(self):
        # Ré-enregistre les menus déroulants persistants à chaque (re)connexion,
        # une seule fois par démarrage.
        if self._vues_enregistrees:
            return
        self._vues_enregistrees = True

        config = charger_config()
        for guild_id_str, data in config.items():
            panels = data.get("roles_panels")
            if not panels:
                continue
            try:
                guild_id = int(guild_id_str)
            except ValueError:
                continue
            for panel_id, panel in panels.items():
                options = options_valides(panel)
                if not options:
                    continue
                self.bot.add_view(construire_vue_role_panel(guild_id, panel_id, options))

    @commands.command(
        name="role",
        aliases=["roles", "autorole"],
        help="Ouvre le panel de gestion des rôles auto-attribués (panels à menu déroulant).",
    )
    @commands.guild_only()
    @check_admin()
    async def role_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil_roles(ctx.guild)
        vue = MenuPrincipalRole(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=vue)
        vue.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Role(bot))
