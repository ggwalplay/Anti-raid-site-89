import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    charger_absences,
    sauvegarder_absences,
    est_admin,
    check_admin,
)

MAX_POLES = 15
DUREE_MAX_JOURS = 90
INTERVALLE_VERIFICATION_MINUTES = 30


# ============================================================
#  CONFIGURATION (stockée dans data/config.json, clé "absence")
# ============================================================

def get_config_absence(guild_id: int) -> dict:
    config = charger_config()
    return config.get(str(guild_id), {}).get(
        "absence", {"poles": {}, "salon_id": None, "message_id": None}
    )


def sauvegarder_config_absence(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["absence"] = data
    sauvegarder_config(config)


def get_pole(guild_id: int, pole_id: str) -> dict | None:
    return get_config_absence(guild_id).get("poles", {}).get(pole_id)


def est_gerant_pole(membre: discord.Member, pole: dict) -> bool:
    if est_admin(membre):
        return True
    roles_membre = {r.id for r in membre.roles}
    return bool(roles_membre.intersection(pole.get("roles_gerants", [])))


# ============================================================
#  EMBED D'UNE DEMANDE (public, sert d'historique dans le salon dédié)
# ============================================================

def construire_embed_demande(guild: discord.Guild, demande: dict, pole: dict | None) -> discord.Embed:
    statut = demande.get("statut", "en_attente")
    couleurs = {
        "en_attente": discord.Color.orange(),
        "acceptee": discord.Color.green(),
        "refusee": discord.Color.red(),
        "terminee": discord.Color.dark_grey(),
    }
    labels = {
        "en_attente": "🟠 En attente",
        "acceptee": "🟢 Acceptée",
        "refusee": "🔴 Refusée",
        "terminee": "⚪ Terminée",
    }

    embed = discord.Embed(
        title=f"📋 Demande d'absence — {pole['nom'] if pole else '?'}",
        color=couleurs.get(statut, discord.Color.blurple()),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Demandeur", value=f"<@{demande['demandeur_id']}>", inline=True)
    embed.add_field(name="Durée", value=f"{demande['duree_jours']} jour(s)", inline=True)
    embed.add_field(name="Statut", value=labels.get(statut, statut), inline=True)
    embed.add_field(name="Raison", value=demande.get("raison") or "—", inline=False)

    if demande.get("traite_par"):
        embed.add_field(name="Traité par", value=f"<@{demande['traite_par']}>", inline=True)
    if statut == "acceptee" and demande.get("date_expiration"):
        date_exp = datetime.fromisoformat(demande["date_expiration"])
        embed.add_field(name="Fin prévue", value=f"<t:{int(date_exp.timestamp())}:F>", inline=True)

    embed.set_footer(text=f"ID demande : {demande.get('id', '?')}")
    return embed


# ============================================================
#  VUE PUBLIQUE : ACCEPTER / REFUSER UNE DEMANDE (persistante)
# ============================================================

class BoutonDecisionAbsence(discord.ui.Button):
    def __init__(self, guild_id: int, demande_id: str, accepter: bool):
        super().__init__(
            label="Accepter" if accepter else "Refuser",
            style=discord.ButtonStyle.success if accepter else discord.ButtonStyle.danger,
            emoji="✅" if accepter else "❌",
            custom_id=f"absence_{'accepter' if accepter else 'refuser'}|{guild_id}|{demande_id}",
        )
        self.guild_id = guild_id
        self.demande_id = demande_id
        self.accepter = accepter

    async def callback(self, interaction: discord.Interaction):
        absences = charger_absences()
        demande = absences.get(str(self.guild_id), {}).get(self.demande_id)
        if demande is None:
            await interaction.response.send_message("Cette demande n'existe plus.", ephemeral=True)
            return
        if demande.get("statut") != "en_attente":
            await interaction.response.send_message("Cette demande a déjà été traitée.", ephemeral=True)
            return

        pole = get_pole(self.guild_id, demande["pole_id"])
        if pole is None:
            await interaction.response.send_message("Le pôle associé à cette demande n'existe plus.", ephemeral=True)
            return

        if not est_gerant_pole(interaction.user, pole):
            await interaction.response.send_message(
                "Seul un gérant de ce pôle (ou un admin) peut traiter cette demande.", ephemeral=True
            )
            return

        guild = interaction.guild
        demandeur = guild.get_member(int(demande["demandeur_id"]))

        if self.accepter:
            role = guild.get_role(pole.get("role_absence")) if pole.get("role_absence") else None
            if role is not None and demandeur is not None:
                try:
                    await demandeur.add_roles(role, reason=f"[Absence] Acceptée par {interaction.user}")
                except discord.Forbidden:
                    pass
            demande["statut"] = "acceptee"
            demande["date_expiration"] = (
                datetime.now(timezone.utc) + timedelta(days=demande["duree_jours"])
            ).isoformat()
        else:
            demande["statut"] = "refusee"

        demande["traite_par"] = str(interaction.user.id)
        sauvegarder_absences(absences)

        for item in self.view.children:
            item.disabled = True
        embed = construire_embed_demande(guild, demande, pole)
        await interaction.response.edit_message(embed=embed, view=self.view)

        if demandeur is not None:
            try:
                if self.accepter:
                    await demandeur.send(
                        f"✅ Votre demande d'absence (**{pole['nom']}**) a été acceptée par {interaction.user.mention}, "
                        f"pour {demande['duree_jours']} jour(s)."
                    )
                else:
                    await demandeur.send(
                        f"❌ Votre demande d'absence (**{pole['nom']}**) a été refusée par {interaction.user.mention}."
                    )
            except discord.Forbidden:
                pass


class VueDecisionAbsence(discord.ui.View):
    def __init__(self, guild_id: int, demande_id: str):
        super().__init__(timeout=None)
        self.add_item(BoutonDecisionAbsence(guild_id, demande_id, accepter=True))
        self.add_item(BoutonDecisionAbsence(guild_id, demande_id, accepter=False))


# ============================================================
#  FLUX PUBLIC : FAIRE UNE DEMANDE
# ============================================================

class ModalNouvelleDemande(discord.ui.Modal, title="Demande d'absence"):
    def __init__(self, guild_id: int, pole_id: str):
        super().__init__()
        self.guild_id = guild_id
        self.pole_id = pole_id

        self.raison = discord.ui.TextInput(
            label="Raison de l'absence",
            style=discord.TextStyle.paragraph,
            max_length=300,
        )
        self.duree = discord.ui.TextInput(
            label=f"Durée en jours (1 à {DUREE_MAX_JOURS})",
            max_length=3,
            placeholder="7",
        )
        self.add_item(self.raison)
        self.add_item(self.duree)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.duree.value.strip().isdigit():
            await interaction.response.send_message("La durée doit être un nombre entier de jours.", ephemeral=True)
            return

        duree_jours = int(self.duree.value.strip())
        if not (1 <= duree_jours <= DUREE_MAX_JOURS):
            await interaction.response.send_message(
                f"La durée doit être comprise entre 1 et {DUREE_MAX_JOURS} jours.", ephemeral=True
            )
            return

        config_absence = get_config_absence(self.guild_id)
        pole = config_absence.get("poles", {}).get(self.pole_id)
        salon_id = config_absence.get("salon_id")

        if pole is None:
            await interaction.response.send_message("Ce pôle n'existe plus.", ephemeral=True)
            return
        if not salon_id:
            await interaction.response.send_message(
                "Le système d'absence n'est pas encore entièrement configuré (aucun salon défini).", ephemeral=True
            )
            return

        salon = interaction.guild.get_channel(salon_id)
        if salon is None:
            await interaction.response.send_message("Le salon des demandes est introuvable.", ephemeral=True)
            return

        demande_id = uuid.uuid4().hex[:8]
        demande = {
            "id": demande_id,
            "pole_id": self.pole_id,
            "demandeur_id": str(interaction.user.id),
            "raison": self.raison.value.strip(),
            "duree_jours": duree_jours,
            "statut": "en_attente",
            "traite_par": None,
            "date_creation": datetime.now(timezone.utc).isoformat(),
            "date_expiration": None,
        }

        absences = charger_absences()
        absences.setdefault(str(self.guild_id), {})[demande_id] = demande
        sauvegarder_absences(absences)

        embed = construire_embed_demande(interaction.guild, demande, pole)
        mentions_gerants = " ".join(f"<@&{r}>" for r in pole.get("roles_gerants", []))
        vue = VueDecisionAbsence(self.guild_id, demande_id)

        try:
            await salon.send(content=mentions_gerants or None, embed=embed, view=vue)
        except discord.Forbidden:
            await interaction.response.send_message(
                "Permissions insuffisantes pour envoyer la demande dans le salon configuré.", ephemeral=True
            )
            return

        await interaction.response.send_message("✅ Votre demande d'absence a été envoyée aux gérants du pôle.", ephemeral=True)


class SelectPoleDemande(discord.ui.Select):
    def __init__(self, guild_id: int, poles: dict):
        options = [
            discord.SelectOption(label=info.get("nom", "?")[:100], value=pole_id)
            for pole_id, info in list(poles.items())[:25]
        ]
        super().__init__(placeholder="Choisir le pôle concerné...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalNouvelleDemande(self.guild_id, self.values[0]))


class VueChoixPole(discord.ui.View):
    def __init__(self, guild_id: int, poles: dict, auteur_id: int):
        super().__init__(timeout=120)
        self.auteur_id = auteur_id
        self.add_item(SelectPoleDemande(guild_id, poles))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message("Ce menu ne vous est pas destiné.", ephemeral=True)
            return False
        return True


class BoutonDemandeAbsence(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Faire une demande d'absence",
            style=discord.ButtonStyle.primary,
            emoji="📋",
            custom_id="absence_demander",
        )

    async def callback(self, interaction: discord.Interaction):
        config_absence = get_config_absence(interaction.guild.id)
        poles = config_absence.get("poles", {})

        if not poles:
            await interaction.response.send_message(
                "Aucun pôle n'est configuré pour le moment (contactez le staff).", ephemeral=True
            )
            return

        if len(poles) == 1:
            pole_id = next(iter(poles))
            await interaction.response.send_modal(ModalNouvelleDemande(interaction.guild.id, pole_id))
            return

        vue = VueChoixPole(interaction.guild.id, poles, interaction.user.id)
        await interaction.response.send_message("Choisissez le pôle concerné par votre absence :", view=vue, ephemeral=True)


class VuePubliqueAbsence(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(BoutonDemandeAbsence())


# ============================================================
#  PANEL DE GESTION (&absence) — admin
# ============================================================

def construire_embed_accueil_absence(guild: discord.Guild) -> discord.Embed:
    config_absence = get_config_absence(guild.id)
    poles = config_absence.get("poles", {})
    salon = guild.get_channel(config_absence.get("salon_id")) if config_absence.get("salon_id") else None

    embed = discord.Embed(
        title="📋 Panel de gestion des absences",
        description=(
            "Définissez des pôles (Modération, RP, Technique...), chacun avec ses propres gérants et un rôle "
            "attribué automatiquement pendant la durée de l'absence acceptée."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Pôles configurés", value=f"{len(poles)} / {MAX_POLES}", inline=True)
    embed.add_field(name="Salon des demandes", value=salon.mention if salon else "Non configuré", inline=True)
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


class PanelBaseAbsence(discord.ui.View):
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

class SelectPoleExistant(discord.ui.Select):
    def __init__(self, guild_id: int, poles: dict):
        options = [
            discord.SelectOption(label=info.get("nom", "?")[:100], value=pole_id)
            for pole_id, info in list(poles.items())[:25]
        ]
        super().__init__(placeholder="Gérer un pôle existant...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        view: MenuPrincipalAbsence = self.view
        pole_id = self.values[0]
        embed = construire_embed_pole_detail(interaction.guild, self.guild_id, pole_id)
        vue = PanelPoleDetail(self.guild_id, view.auteur_id, pole_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class ModalNouveauPole(discord.ui.Modal, title="Créer un pôle"):
    nom = discord.ui.TextInput(label="Nom du pôle", max_length=80, placeholder="Modération")

    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id

    async def on_submit(self, interaction: discord.Interaction):
        config_absence = get_config_absence(self.guild_id)
        pole_id = uuid.uuid4().hex[:8]
        config_absence.setdefault("poles", {})[pole_id] = {
            "nom": self.nom.value.strip(),
            "roles_gerants": [],
            "role_absence": None,
        }
        sauvegarder_config_absence(self.guild_id, config_absence)

        embed = construire_embed_pole_detail(interaction.guild, self.guild_id, pole_id)
        embed.add_field(
            name="⚠️ À faire",
            value="Configurez les rôles gérants et le rôle d'absence ci-dessous.",
            inline=False,
        )
        vue = PanelPoleDetail(self.guild_id, self.auteur_id, pole_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class PanelSalonDemandes(PanelBaseAbsence):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectSalonAbsence(guild_id))

    @discord.ui.button(label="Envoyer / Mettre à jour le panel public", style=discord.ButtonStyle.success, row=1, emoji="📤")
    async def envoyer(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_absence = get_config_absence(self.guild_id)
        salon_id = config_absence.get("salon_id")

        if not salon_id:
            await interaction.response.send_message("Configurez d'abord un salon ci-dessus.", ephemeral=True)
            return

        salon = interaction.guild.get_channel(salon_id)
        if salon is None:
            await interaction.response.send_message("Le salon configuré est introuvable.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📋 Demande d'absence",
            description="Cliquez ci-dessous pour faire une demande d'absence auprès des gérants du pôle concerné.",
            color=discord.Color.blurple(),
        )
        vue_publique = VuePubliqueAbsence()

        message_final = None
        ancien_id = config_absence.get("message_id")
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

        config_absence["message_id"] = message_final.id
        sauvegarder_config_absence(self.guild_id, config_absence)
        await interaction.response.send_message(f"✅ Panel envoyé/mis à jour dans {salon.mention}.", ephemeral=True)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_absence(interaction.guild)
        vue = MenuPrincipalAbsence(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class SelectSalonAbsence(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(
            placeholder="Salon où seront envoyées les demandes",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        config_absence = get_config_absence(self.guild_id)
        config_absence["salon_id"] = self.values[0].id
        sauvegarder_config_absence(self.guild_id, config_absence)

        embed = construire_embed_accueil_absence(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


class MenuPrincipalAbsence(PanelBaseAbsence):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        poles = get_config_absence(guild_id).get("poles", {})
        if poles:
            self.add_item(SelectPoleExistant(guild_id, poles))

    @discord.ui.button(label="Créer un pôle", style=discord.ButtonStyle.success, row=1, emoji="➕")
    async def creer(self, interaction: discord.Interaction, button: discord.ui.Button):
        poles = get_config_absence(self.guild_id).get("poles", {})
        if len(poles) >= MAX_POLES:
            await interaction.response.send_message(f"Limite de {MAX_POLES} pôles atteinte.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalNouveauPole(self.guild_id, self.auteur_id))

    @discord.ui.button(label="Salon des demandes", style=discord.ButtonStyle.primary, row=1, emoji="📬")
    async def salon(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_absence = get_config_absence(self.guild_id)
        salon_id = config_absence.get("salon_id")
        embed = discord.Embed(
            title="📬 Salon des demandes",
            description="Choisissez le salon où seront postées les demandes d'absence (visibles par les gérants concernés).",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Salon actuel",
            value=f"<#{salon_id}>" if salon_id else "Non configuré",
            inline=False,
        )
        vue = PanelSalonDemandes(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=2)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# --- ECRAN 2 : DETAIL D'UN POLE ---

def construire_embed_pole_detail(guild: discord.Guild, guild_id: int, pole_id: str) -> discord.Embed:
    pole = get_pole(guild_id, pole_id) or {}
    roles_gerants = ", ".join(f"<@&{r}>" for r in pole.get("roles_gerants", []) if guild.get_role(r)) or "Aucun"
    role_absence = guild.get_role(pole.get("role_absence")) if pole.get("role_absence") else None

    embed = discord.Embed(title=f"📋 Pôle : {pole.get('nom', '?')}", color=discord.Color.blurple())
    embed.add_field(name="Gérants (peuvent accepter/refuser)", value=roles_gerants, inline=False)
    embed.add_field(
        name="Rôle attribué pendant l'absence",
        value=role_absence.mention if role_absence else "⚠️ Non configuré",
        inline=False,
    )
    embed.set_footer(text=f"ID interne : {pole_id}")
    return embed


class SelectRolesGerants(discord.ui.RoleSelect):
    def __init__(self, guild_id: int, pole_id: str):
        self.guild_id = guild_id
        self.pole_id = pole_id
        super().__init__(
            placeholder="Rôles gérants pouvant accepter/refuser pour ce pôle",
            min_values=0,
            max_values=10,
        )

    async def callback(self, interaction: discord.Interaction):
        config_absence = get_config_absence(self.guild_id)
        pole = config_absence.get("poles", {}).get(self.pole_id)
        if pole is None:
            await interaction.response.send_message("Ce pôle n'existe plus.", ephemeral=True)
            return

        pole["roles_gerants"] = [r.id for r in self.values]
        sauvegarder_config_absence(self.guild_id, config_absence)

        embed = construire_embed_pole_detail(interaction.guild, self.guild_id, self.pole_id)
        await interaction.response.edit_message(embed=embed, view=self.view)


class SelectRoleAbsence(discord.ui.RoleSelect):
    def __init__(self, guild_id: int, pole_id: str):
        self.guild_id = guild_id
        self.pole_id = pole_id
        super().__init__(
            placeholder="Rôle attribué pendant la durée de l'absence",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        if role.is_default() or role.managed:
            await interaction.response.send_message(
                "Impossible d'utiliser @everyone ou un rôle géré automatiquement.", ephemeral=True
            )
            return

        config_absence = get_config_absence(self.guild_id)
        pole = config_absence.get("poles", {}).get(self.pole_id)
        if pole is None:
            await interaction.response.send_message("Ce pôle n'existe plus.", ephemeral=True)
            return

        pole["role_absence"] = role.id
        sauvegarder_config_absence(self.guild_id, config_absence)

        embed = construire_embed_pole_detail(interaction.guild, self.guild_id, self.pole_id)
        await interaction.response.edit_message(embed=embed, view=self.view)


class ModalRenommerPole(discord.ui.Modal, title="Renommer le pôle"):
    def __init__(self, guild_id: int, auteur_id: int, pole_id: str, nom_actuel: str):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.pole_id = pole_id
        self.nom = discord.ui.TextInput(label="Nom du pôle", max_length=80, default=nom_actuel)
        self.add_item(self.nom)

    async def on_submit(self, interaction: discord.Interaction):
        config_absence = get_config_absence(self.guild_id)
        pole = config_absence.get("poles", {}).get(self.pole_id)
        if pole is None:
            await interaction.response.send_message("Ce pôle n'existe plus.", ephemeral=True)
            return

        pole["nom"] = self.nom.value.strip()
        sauvegarder_config_absence(self.guild_id, config_absence)

        embed = construire_embed_pole_detail(interaction.guild, self.guild_id, self.pole_id)
        vue = PanelPoleDetail(self.guild_id, self.auteur_id, self.pole_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class PanelPoleDetail(PanelBaseAbsence):
    def __init__(self, guild_id: int, auteur_id: int, pole_id: str):
        super().__init__(guild_id, auteur_id)
        self.pole_id = pole_id
        self.add_item(SelectRolesGerants(guild_id, pole_id))
        self.add_item(SelectRoleAbsence(guild_id, pole_id))

    @discord.ui.button(label="Renommer", style=discord.ButtonStyle.primary, row=2, emoji="✏️")
    async def renommer(self, interaction: discord.Interaction, button: discord.ui.Button):
        pole = get_pole(self.guild_id, self.pole_id) or {}
        await interaction.response.send_modal(
            ModalRenommerPole(self.guild_id, self.auteur_id, self.pole_id, pole.get("nom", ""))
        )

    @discord.ui.button(label="Supprimer ce pôle", style=discord.ButtonStyle.danger, row=2, emoji="🗑️")
    async def supprimer(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_absence = get_config_absence(self.guild_id)
        config_absence.get("poles", {}).pop(self.pole_id, None)
        sauvegarder_config_absence(self.guild_id, config_absence)

        embed = construire_embed_accueil_absence(interaction.guild)
        vue = MenuPrincipalAbsence(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=3)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_absence(interaction.guild)
        vue = MenuPrincipalAbsence(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# ============================================================
#  COG
# ============================================================

class Absence(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._vues_enregistrees = False
        self.verifier_expirations.start()

    def cog_unload(self):
        self.verifier_expirations.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._vues_enregistrees:
            return
        self._vues_enregistrees = True

        self.bot.add_view(VuePubliqueAbsence())

        absences = charger_absences()
        for guild_id_str, demandes in absences.items():
            try:
                guild_id = int(guild_id_str)
            except ValueError:
                continue
            for demande_id, demande in demandes.items():
                if demande.get("statut") == "en_attente":
                    self.bot.add_view(VueDecisionAbsence(guild_id, demande_id))

    @tasks.loop(minutes=INTERVALLE_VERIFICATION_MINUTES)
    async def verifier_expirations(self):
        absences = charger_absences()
        modifie = False

        for guild_id_str, demandes in absences.items():
            try:
                guild_id = int(guild_id_str)
            except ValueError:
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue

            config_absence = get_config_absence(guild_id)

            for demande in demandes.values():
                if demande.get("statut") != "acceptee":
                    continue
                expiration = demande.get("date_expiration")
                if not expiration:
                    continue
                if datetime.fromisoformat(expiration) > datetime.now(timezone.utc):
                    continue

                pole = config_absence.get("poles", {}).get(demande["pole_id"])
                role = guild.get_role(pole.get("role_absence")) if pole else None
                membre = guild.get_member(int(demande["demandeur_id"]))

                if role is not None and membre is not None and role in membre.roles:
                    try:
                        await membre.remove_roles(role, reason="[Absence] Fin de la période d'absence")
                    except discord.Forbidden:
                        pass

                demande["statut"] = "terminee"
                modifie = True

        if modifie:
            sauvegarder_absences(absences)

    @verifier_expirations.before_loop
    async def avant_verification(self):
        await self.bot.wait_until_ready()

    @commands.command(
        name="absence",
        aliases=["conge"],
        help="Panel de gestion des absences par pôle (gérants dédiés, rôle temporaire automatique).",
    )
    @commands.guild_only()
    @check_admin()
    async def absence_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil_absence(ctx.guild)
        vue = MenuPrincipalAbsence(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=vue)
        vue.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Absence(bot))
