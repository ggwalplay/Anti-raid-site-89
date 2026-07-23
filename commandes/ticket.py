import asyncio
import io
import re
import uuid
from datetime import datetime, timezone

import discord
from discord.ext import commands

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    charger_tickets,
    sauvegarder_tickets,
    get_logs_channel_id,
    est_admin,
    check_admin,
)
from commandes.logs import envoyer_log

MAX_TYPES = 20  # 5 boutons x 5 lignes max sur une View Discord
DUREE_AVANT_SUPPRESSION = 10  # secondes, laissées pour lire le message de fermeture
LIMITE_TRANSCRIPT = 500  # nombre de messages max inclus dans le transcript


# ============================================================
#  CONFIGURATION (stockée dans data/config.json, clé "tickets")
# ============================================================

def get_tickets_config(guild_id: int) -> dict:
    config = charger_config()
    return config.get(str(guild_id), {}).get(
        "tickets", {"types": {}, "panel_channel_id": None, "panel_message_id": None, "compteur": 0}
    )


def sauvegarder_tickets_config(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["tickets"] = data
    sauvegarder_config(config)


def _nom_salon(nom_type: str, numero: int) -> str:
    """Construit un nom de salon Discord valide (minuscules, tirets) à partir du nom du type."""
    base = re.sub(r"[^a-z0-9\-]", "", nom_type.lower().replace(" ", "-"))
    base = base.strip("-") or "ticket"
    return f"{base}-{numero:04d}"[:100]


def est_staff_du_type(membre: discord.Member, guild_id: int, type_id: str) -> bool:
    """Vrai si le membre est admin, ou possède un des rôles d'accès configurés pour ce type."""
    if est_admin(membre):
        return True
    tickets_cfg = get_tickets_config(guild_id)
    info_type = tickets_cfg.get("types", {}).get(type_id, {})
    roles_membre = {r.id for r in membre.roles}
    return bool(roles_membre.intersection(info_type.get("roles_acces", [])))


# ============================================================
#  CREATION D'UN TICKET (appelée depuis les boutons du panel public)
# ============================================================

async def creer_ticket(interaction: discord.Interaction, guild_id: int, type_id: str) -> None:
    guild = interaction.guild
    tickets_cfg = get_tickets_config(guild_id)
    info = tickets_cfg.get("types", {}).get(type_id)

    if info is None:
        await interaction.response.send_message(
            "Ce type de ticket n'existe plus (configuration modifiée entre-temps).", ephemeral=True
        )
        return

    tickets = charger_tickets()
    tickets_guild = tickets.get(str(guild_id), {})

    # Un seul ticket ouvert par membre et par type à la fois.
    for chan_id, data in tickets_guild.items():
        if (
            not data.get("ferme")
            and data.get("ouvert_par") == str(interaction.user.id)
            and data.get("type_id") == type_id
        ):
            salon_existant = guild.get_channel(int(chan_id))
            if salon_existant is not None:
                await interaction.response.send_message(
                    f"Vous avez déjà un ticket ouvert pour ce type : {salon_existant.mention}", ephemeral=True
                )
                return

    await interaction.response.defer(ephemeral=True)

    categorie = guild.get_channel(info.get("categorie_id")) if info.get("categorie_id") else None
    if not isinstance(categorie, discord.CategoryChannel):
        categorie = None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
        ),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
    }
    for role_id in info.get("roles_acces", []):
        role = guild.get_role(role_id)
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

    compteur = tickets_cfg.get("compteur", 0) + 1
    tickets_cfg["compteur"] = compteur
    sauvegarder_tickets_config(guild_id, tickets_cfg)

    try:
        salon = await guild.create_text_channel(
            name=_nom_salon(info.get("nom", "ticket"), compteur),
            category=categorie,
            overwrites=overwrites,
            reason=f"[Ticket] Ouvert par {interaction.user} ({interaction.user.id})",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "Permissions insuffisantes pour créer le salon de ticket (vérifiez les droits du bot).",
            ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"Erreur Discord lors de la création du salon : {e}", ephemeral=True)
        return

    entree = {
        "type_id": type_id,
        "type_nom": info.get("nom", "Ticket"),
        "ouvert_par": str(interaction.user.id),
        "date_ouverture": datetime.now(timezone.utc).isoformat(),
        "claim_par": None,
        "ferme": False,
        "numero": compteur,
    }
    tickets.setdefault(str(guild_id), {})[str(salon.id)] = entree
    sauvegarder_tickets(tickets)

    mentions_roles = [f"<@&{r}>" for r in info.get("roles_acces", []) if guild.get_role(r)]

    embed = discord.Embed(
        title=f"{info.get('emoji', '🎫')} {info.get('nom', 'Ticket')} — #{compteur:04d}",
        description=info.get("description") or "Un membre du staff va vous répondre dès que possible.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Ouvert par", value=interaction.user.mention, inline=True)
    embed.add_field(name="Équipe notifiée", value=", ".join(mentions_roles) or "Aucune", inline=True)
    embed.set_footer(text=f"ID salon : {salon.id}")

    contenu = " ".join([interaction.user.mention] + mentions_roles)
    await salon.send(content=contenu, embed=embed, view=GestionTicketView())

    await interaction.followup.send(f"✅ Votre ticket a été créé : {salon.mention}", ephemeral=True)

    await envoyer_log(
        guild,
        "tickets",
        discord.Embed(
            title="🎫 Ticket ouvert",
            description=(
                f"{interaction.user.mention} a ouvert un ticket "
                f"**{info.get('nom', 'Ticket')}** (#{compteur:04d}) : {salon.mention}"
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        ),
    )


class BoutonOuvertureTicket(discord.ui.Button):
    def __init__(self, guild_id: int, type_id: str, info: dict):
        super().__init__(
            label=(info.get("nom") or "Ticket")[:80],
            emoji=info.get("emoji") or None,
            style=discord.ButtonStyle.primary,
            custom_id=f"ticket_open|{guild_id}|{type_id}",
        )
        self.guild_id = guild_id
        self.type_id = type_id

    async def callback(self, interaction: discord.Interaction):
        await creer_ticket(interaction, self.guild_id, self.type_id)


class OuvertureTicketView(discord.ui.View):
    """Vue persistante envoyée dans le salon public : un bouton par type de ticket."""

    def __init__(self, guild_id: int, types: dict):
        super().__init__(timeout=None)
        for type_id, info in list(types.items())[:MAX_TYPES]:
            self.add_item(BoutonOuvertureTicket(guild_id, type_id, info))


def construire_vue_ouverture(guild_id: int, types: dict) -> OuvertureTicketView:
    return OuvertureTicketView(guild_id, types)


# ============================================================
#  GESTION D'UN TICKET OUVERT (boutons dans le salon du ticket)
# ============================================================

async def generer_transcript(channel: discord.TextChannel) -> str:
    lignes = [
        f"Transcript du salon #{channel.name} ({channel.id})",
        f"Généré le {datetime.now(timezone.utc).isoformat()}",
        "-" * 60,
    ]
    try:
        async for message in channel.history(limit=LIMITE_TRANSCRIPT, oldest_first=True):
            horodatage = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            auteur = f"{message.author} ({message.author.id})"
            contenu = message.content or "[Pas de contenu texte : embed, composant ou fichier seul]"
            lignes.append(f"[{horodatage}] {auteur} : {contenu}")
            for piece in message.attachments:
                lignes.append(f"    Pièce jointe : {piece.url}")
    except discord.Forbidden:
        lignes.append("(Impossible de lire l'historique du salon : permissions insuffisantes.)")
    return "\n".join(lignes)


class ModalAjoutMembre(discord.ui.Modal, title="Ajouter un membre au ticket"):
    membre_id = discord.ui.TextInput(
        label="ID Discord du membre à ajouter",
        placeholder="123456789012345678",
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        valeur = self.membre_id.value.strip()
        if not valeur.isdigit():
            await interaction.response.send_message("ID invalide : uniquement des chiffres.", ephemeral=True)
            return

        membre = interaction.guild.get_member(int(valeur))
        if membre is None:
            await interaction.response.send_message("Membre introuvable sur ce serveur.", ephemeral=True)
            return

        try:
            await interaction.channel.set_permissions(
                membre, view_channel=True, send_messages=True, read_message_history=True,
                reason=f"[Ticket] Ajouté par {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Permissions insuffisantes pour ajouter ce membre au salon.", ephemeral=True
            )
            return

        await interaction.response.send_message(f"✅ {membre.mention} a été ajouté au ticket par {interaction.user.mention}.")


class ConfirmationFermeture(discord.ui.View):
    """Confirmation avant suppression définitive du salon, avec génération du transcript."""

    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirmer la fermeture", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirmer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        channel = interaction.channel
        guild = interaction.guild

        tickets = charger_tickets()
        entree = tickets.get(str(guild.id), {}).get(str(channel.id))

        transcript_texte = await generer_transcript(channel)

        embed_log = discord.Embed(
            title="🔒 Ticket fermé",
            description=(
                f"Ticket **{entree.get('type_nom', '?')}** (#{entree.get('numero', 0):04d}) "
                f"fermé par {interaction.user.mention}."
                if entree
                else f"Un ticket ({channel.name}) a été fermé par {interaction.user.mention}."
            ),
            color=discord.Color.dark_grey(),
            timestamp=discord.utils.utcnow(),
        )
        if entree:
            embed_log.add_field(name="Ouvert par", value=f"<@{entree.get('ouvert_par')}>", inline=True)
            embed_log.add_field(
                name="Pris en charge par",
                value=f"<@{entree['claim_par']}>" if entree.get("claim_par") else "Personne",
                inline=True,
            )

        fichier = discord.File(io.BytesIO(transcript_texte.encode("utf-8")), filename=f"transcript-{channel.name}.txt")
        channel_logs_id = get_logs_channel_id(guild.id, "tickets")
        salon_logs = guild.get_channel(channel_logs_id) if channel_logs_id else None
        if salon_logs is not None:
            try:
                await salon_logs.send(embed=embed_log, file=fichier)
            except (discord.Forbidden, discord.HTTPException):
                pass

        if entree is not None:
            entree["ferme"] = True
            entree["ferme_par"] = str(interaction.user.id)
            entree["date_fermeture"] = datetime.now(timezone.utc).isoformat()
            sauvegarder_tickets(tickets)

        await channel.send(
            f"🔒 Ticket fermé par {interaction.user.mention}. Suppression du salon dans "
            f"{DUREE_AVANT_SUPPRESSION} secondes..."
        )
        await asyncio.sleep(DUREE_AVANT_SUPPRESSION)

        try:
            await channel.delete(reason=f"[Ticket] Fermé par {interaction.user}")
        except discord.HTTPException:
            pass

        tickets = charger_tickets()
        tickets_guild = tickets.get(str(guild.id), {})
        if str(channel.id) in tickets_guild:
            del tickets_guild[str(channel.id)]
            sauvegarder_tickets(tickets)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def annuler(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Fermeture annulée.", view=self)


class GestionTicketView(discord.ui.View):
    """Vue persistante envoyée dans chaque salon de ticket (custom_id fixes, ré-enregistrée au démarrage)."""

    def __init__(self):
        super().__init__(timeout=None)

    def _entree(self, guild_id: int, channel_id: int) -> dict | None:
        tickets = charger_tickets()
        return tickets.get(str(guild_id), {}).get(str(channel_id))

    def _autorise(self, membre: discord.Member, guild_id: int, entree: dict) -> bool:
        if est_admin(membre):
            return True
        if str(membre.id) == entree.get("ouvert_par"):
            return True
        return est_staff_du_type(membre, guild_id, entree.get("type_id", ""))

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_fermer")
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        entree = self._entree(interaction.guild.id, interaction.channel.id)
        if entree is None:
            await interaction.response.send_message(
                "Ce salon n'est pas (ou plus) reconnu comme un ticket actif.", ephemeral=True
            )
            return
        if not self._autorise(interaction.user, interaction.guild.id, entree):
            await interaction.response.send_message(
                "Vous n'avez pas la permission de fermer ce ticket.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "⚠️ Confirmez-vous la fermeture de ce ticket ? Un transcript sera envoyé en logs et le salon sera supprimé.",
            view=ConfirmationFermeture(),
        )

    @discord.ui.button(label="Réclamer", style=discord.ButtonStyle.success, emoji="🙋", custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        entree = self._entree(interaction.guild.id, interaction.channel.id)
        if entree is None:
            await interaction.response.send_message(
                "Ce salon n'est pas (ou plus) reconnu comme un ticket actif.", ephemeral=True
            )
            return
        if not est_staff_du_type(interaction.user, interaction.guild.id, entree.get("type_id", "")):
            await interaction.response.send_message(
                "Seul un membre du staff assigné à ce type de ticket peut le réclamer.", ephemeral=True
            )
            return

        tickets = charger_tickets()
        tickets[str(interaction.guild.id)][str(interaction.channel.id)]["claim_par"] = str(interaction.user.id)
        sauvegarder_tickets(tickets)

        await interaction.response.send_message(f"🙋 Ticket pris en charge par {interaction.user.mention}.")

    @discord.ui.button(label="Ajouter un membre", style=discord.ButtonStyle.secondary, emoji="➕", custom_id="ticket_add")
    async def ajouter(self, interaction: discord.Interaction, button: discord.ui.Button):
        entree = self._entree(interaction.guild.id, interaction.channel.id)
        if entree is None:
            await interaction.response.send_message(
                "Ce salon n'est pas (ou plus) reconnu comme un ticket actif.", ephemeral=True
            )
            return
        if not self._autorise(interaction.user, interaction.guild.id, entree):
            await interaction.response.send_message(
                "Vous n'avez pas la permission d'ajouter un membre à ce ticket.", ephemeral=True
            )
            return

        await interaction.response.send_modal(ModalAjoutMembre())


# ============================================================
#  PANEL DE GESTION (&ticket) — configuration par les admins
# ============================================================

def construire_embed_accueil_tickets(guild: discord.Guild) -> discord.Embed:
    tickets_cfg = get_tickets_config(guild.id)
    nb_types = len(tickets_cfg.get("types", {}))
    panel_channel = tickets_cfg.get("panel_channel_id")

    embed = discord.Embed(
        title="🎫 Panel de gestion des tickets",
        description=(
            "Configurez les types de tickets, leurs accès, et le panel d'ouverture visible par les membres.\n"
            "Le salon de logs des tickets se configure via `&setup` → *Logs* → *Tickets*."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Types configurés", value=f"{nb_types} / {MAX_TYPES}", inline=True)
    embed.add_field(
        name="Salon du panel", value=f"<#{panel_channel}>" if panel_channel else "Non configuré", inline=True
    )
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


class PanelBaseTicket(discord.ui.View):
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

class MenuPrincipalTicket(PanelBaseTicket):
    @discord.ui.button(label="Types de tickets", style=discord.ButtonStyle.primary, row=0, emoji="🗂️")
    async def bouton_types(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_types(interaction.guild)
        vue = PanelTypes(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Panel d'ouverture", style=discord.ButtonStyle.primary, row=0, emoji="📬")
    async def bouton_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_panel_ouverture(interaction.guild)
        vue = PanelOuverture(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Tickets ouverts", style=discord.ButtonStyle.secondary, row=0, emoji="📂")
    async def bouton_ouverts(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_tickets_ouverts(interaction.guild)
        vue = PanelTicketsOuverts(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=1)
    async def bouton_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# --- ECRAN 2 : LISTE DES TYPES ---

def construire_embed_types(guild: discord.Guild) -> discord.Embed:
    tickets_cfg = get_tickets_config(guild.id)
    types = tickets_cfg.get("types", {})

    embed = discord.Embed(title="🗂️ Types de tickets", color=discord.Color.blurple())
    if not types:
        embed.description = "Aucun type de ticket configuré. Cliquez sur **Créer un type** pour commencer."
        return embed

    for info in types.values():
        categorie = guild.get_channel(info.get("categorie_id")) if info.get("categorie_id") else None
        roles = ", ".join(f"<@&{r}>" for r in info.get("roles_acces", []) if guild.get_role(r)) or "Aucun"
        valeur = (
            f"Catégorie : {categorie.mention if categorie else '⚠️ Non configurée'}\n"
            f"Rôles d'accès : {roles}"
        )
        embed.add_field(name=f"{info.get('emoji', '🎫')} {info.get('nom', '?')}", value=valeur, inline=False)

    embed.set_footer(text=f"{len(types)} / {MAX_TYPES} type(s) configuré(s)")
    return embed


class SelectTypeExistant(discord.ui.Select):
    def __init__(self, guild_id: int, types: dict):
        options = [
            discord.SelectOption(
                label=(info.get("nom") or "?")[:100],
                description=(info.get("description") or "Aucune description")[:100],
                emoji=info.get("emoji") or None,
                value=type_id,
            )
            for type_id, info in list(types.items())[:25]
        ]
        super().__init__(placeholder="Modifier un type existant...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        view: PanelTypes = self.view
        type_id = self.values[0]
        embed = construire_embed_type_detail(interaction.guild, type_id)
        nouvelle_vue = PanelTypeDetail(self.guild_id, view.auteur_id, type_id)
        nouvelle_vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=nouvelle_vue)


class ModalNouveauType(discord.ui.Modal, title="Créer un type de ticket"):
    nom = discord.ui.TextInput(label="Nom du type", max_length=80, placeholder="Signalement")
    emoji = discord.ui.TextInput(label="Emoji (optionnel)", max_length=10, required=False, placeholder="🚩")
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        max_length=300,
        required=False,
        placeholder="Utilisez ce ticket pour signaler un membre qui enfreint le règlement.",
    )

    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id

    async def on_submit(self, interaction: discord.Interaction):
        tickets_cfg = get_tickets_config(self.guild_id)
        type_id = uuid.uuid4().hex[:8]
        tickets_cfg.setdefault("types", {})[type_id] = {
            "nom": self.nom.value.strip(),
            "emoji": self.emoji.value.strip() or "🎫",
            "description": self.description.value.strip(),
            "categorie_id": None,
            "roles_acces": [],
        }
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        embed = construire_embed_type_detail(interaction.guild, type_id)
        embed.add_field(
            name="⚠️ À faire",
            value="Configurez la catégorie et les rôles d'accès ci-dessous avant d'envoyer le panel public.",
            inline=False,
        )
        vue = PanelTypeDetail(self.guild_id, self.auteur_id, type_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class PanelTypes(PanelBaseTicket):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        types = get_tickets_config(guild_id).get("types", {})
        if types:
            self.add_item(SelectTypeExistant(guild_id, types))

    @discord.ui.button(label="Créer un type", style=discord.ButtonStyle.success, row=1, emoji="➕")
    async def creer(self, interaction: discord.Interaction, button: discord.ui.Button):
        types = get_tickets_config(self.guild_id).get("types", {})
        if len(types) >= MAX_TYPES:
            await interaction.response.send_message(f"Limite de {MAX_TYPES} types atteinte.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalNouveauType(self.guild_id, self.auteur_id))

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_tickets(interaction.guild)
        vue = MenuPrincipalTicket(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# --- ECRAN 3 : DETAIL D'UN TYPE (catégorie, rôles, édition, suppression) ---

def construire_embed_type_detail(guild: discord.Guild, type_id: str) -> discord.Embed:
    tickets_cfg = get_tickets_config(guild.id)
    info = tickets_cfg.get("types", {}).get(type_id, {})
    categorie = guild.get_channel(info.get("categorie_id")) if info.get("categorie_id") else None
    roles = ", ".join(f"<@&{r}>" for r in info.get("roles_acces", []) if guild.get_role(r)) or "Aucun rôle configuré"

    embed = discord.Embed(
        title=f"{info.get('emoji', '🎫')} {info.get('nom', '?')}",
        description=info.get("description") or "Aucune description.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Catégorie des salons",
        value=categorie.mention if categorie else "⚠️ Non configurée",
        inline=False,
    )
    embed.add_field(name="Rôles d'accès", value=roles, inline=False)
    embed.set_footer(text=f"ID interne : {type_id}")
    return embed


class SelectCategorieType(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int, type_id: str):
        self.guild_id = guild_id
        self.type_id = type_id
        super().__init__(
            placeholder="Choisir la catégorie où seront créés les salons",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        tickets_cfg = get_tickets_config(self.guild_id)
        info = tickets_cfg.get("types", {}).get(self.type_id)
        if info is None:
            await interaction.response.send_message("Ce type n'existe plus.", ephemeral=True)
            return

        info["categorie_id"] = self.values[0].id
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        embed = construire_embed_type_detail(interaction.guild, self.type_id)
        await interaction.response.edit_message(embed=embed, view=self.view)


class SelectRolesType(discord.ui.RoleSelect):
    def __init__(self, guild_id: int, type_id: str):
        self.guild_id = guild_id
        self.type_id = type_id
        super().__init__(
            placeholder="Choisir les rôles ayant accès à ce type de ticket",
            min_values=0,
            max_values=10,
        )

    async def callback(self, interaction: discord.Interaction):
        tickets_cfg = get_tickets_config(self.guild_id)
        info = tickets_cfg.get("types", {}).get(self.type_id)
        if info is None:
            await interaction.response.send_message("Ce type n'existe plus.", ephemeral=True)
            return

        info["roles_acces"] = [role.id for role in self.values]
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        embed = construire_embed_type_detail(interaction.guild, self.type_id)
        await interaction.response.edit_message(embed=embed, view=self.view)


class ModalModifierType(discord.ui.Modal, title="Modifier le type de ticket"):
    def __init__(self, guild_id: int, auteur_id: int, type_id: str, info: dict):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.type_id = type_id

        self.nom = discord.ui.TextInput(label="Nom du type", max_length=80, default=info.get("nom", ""))
        self.emoji = discord.ui.TextInput(
            label="Emoji (optionnel)", max_length=10, required=False, default=info.get("emoji", "")
        )
        self.description = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            max_length=300,
            required=False,
            default=info.get("description", ""),
        )
        self.add_item(self.nom)
        self.add_item(self.emoji)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        tickets_cfg = get_tickets_config(self.guild_id)
        info = tickets_cfg.get("types", {}).get(self.type_id)
        if info is None:
            await interaction.response.send_message("Ce type n'existe plus.", ephemeral=True)
            return

        info["nom"] = self.nom.value.strip()
        info["emoji"] = self.emoji.value.strip() or "🎫"
        info["description"] = self.description.value.strip()
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        embed = construire_embed_type_detail(interaction.guild, self.type_id)
        vue = PanelTypeDetail(self.guild_id, self.auteur_id, self.type_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class PanelTypeDetail(PanelBaseTicket):
    def __init__(self, guild_id: int, auteur_id: int, type_id: str):
        super().__init__(guild_id, auteur_id)
        self.type_id = type_id
        self.add_item(SelectCategorieType(guild_id, type_id))
        self.add_item(SelectRolesType(guild_id, type_id))

    @discord.ui.button(label="Modifier nom / emoji / description", style=discord.ButtonStyle.primary, row=2, emoji="✏️")
    async def modifier(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets_cfg = get_tickets_config(self.guild_id)
        info = tickets_cfg.get("types", {}).get(self.type_id, {})
        await interaction.response.send_modal(ModalModifierType(self.guild_id, self.auteur_id, self.type_id, info))

    @discord.ui.button(label="Supprimer ce type", style=discord.ButtonStyle.danger, row=2, emoji="🗑️")
    async def supprimer(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets_cfg = get_tickets_config(self.guild_id)
        tickets_cfg.get("types", {}).pop(self.type_id, None)
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        embed = construire_embed_types(interaction.guild)
        vue = PanelTypes(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=3)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_types(interaction.guild)
        vue = PanelTypes(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# --- ECRAN 4 : PANEL D'OUVERTURE PUBLIC ---

def construire_embed_panel_ouverture(guild: discord.Guild) -> discord.Embed:
    tickets_cfg = get_tickets_config(guild.id)
    channel_id = tickets_cfg.get("panel_channel_id")
    nb_types = len(tickets_cfg.get("types", {}))

    embed = discord.Embed(
        title="📬 Panel d'ouverture des tickets",
        description=(
            "Choisissez le salon où sera envoyé le message contenant un bouton par type de ticket. "
            "Les membres cliqueront sur ces boutons pour ouvrir un ticket privé."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Salon actuel", value=f"<#{channel_id}>" if channel_id else "Non configuré", inline=True)
    embed.add_field(name="Types disponibles", value=str(nb_types), inline=True)
    if nb_types == 0:
        embed.add_field(
            name="⚠️ Attention",
            value="Aucun type de ticket configuré : créez-en au moins un avant d'envoyer le panel.",
            inline=False,
        )
    return embed


class SelectSalonPanel(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(
            placeholder="Choisir le salon du panel d'ouverture",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        tickets_cfg = get_tickets_config(self.guild_id)
        tickets_cfg["panel_channel_id"] = self.values[0].id
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        embed = construire_embed_panel_ouverture(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


class PanelOuverture(PanelBaseTicket):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectSalonPanel(guild_id))

    @discord.ui.button(label="Envoyer / Mettre à jour le panel", style=discord.ButtonStyle.success, row=1, emoji="📤")
    async def envoyer(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets_cfg = get_tickets_config(self.guild_id)
        channel_id = tickets_cfg.get("panel_channel_id")
        types = tickets_cfg.get("types", {})

        if not channel_id:
            await interaction.response.send_message("Configurez d'abord un salon ci-dessus.", ephemeral=True)
            return
        if not types:
            await interaction.response.send_message(
                "Créez au moins un type de ticket avant d'envoyer le panel.", ephemeral=True
            )
            return

        salon = interaction.guild.get_channel(channel_id)
        if salon is None:
            await interaction.response.send_message("Le salon configuré est introuvable.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎫 Support — Ouvrir un ticket",
            description="Cliquez sur le bouton correspondant à votre demande pour ouvrir un ticket privé avec le staff.",
            color=discord.Color.blurple(),
        )
        for info in types.values():
            embed.add_field(
                name=f"{info.get('emoji', '🎫')} {info.get('nom', '?')}",
                value=info.get("description") or "—",
                inline=False,
            )

        vue_ouverture = construire_vue_ouverture(self.guild_id, types)

        message_final = None
        ancien_id = tickets_cfg.get("panel_message_id")
        if ancien_id:
            try:
                ancien_message = await salon.fetch_message(ancien_id)
                await ancien_message.edit(embed=embed, view=vue_ouverture)
                message_final = ancien_message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message_final = None

        if message_final is None:
            try:
                message_final = await salon.send(embed=embed, view=vue_ouverture)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Permissions insuffisantes pour envoyer un message dans ce salon.", ephemeral=True
                )
                return

        tickets_cfg["panel_message_id"] = message_final.id
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        await interaction.response.send_message(f"✅ Panel envoyé/mis à jour dans {salon.mention}.", ephemeral=True)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_tickets(interaction.guild)
        vue = MenuPrincipalTicket(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# --- ECRAN 5 : TICKETS ACTUELLEMENT OUVERTS ---

def construire_embed_tickets_ouverts(guild: discord.Guild) -> discord.Embed:
    tickets = charger_tickets()
    tickets_guild = {
        cid: data for cid, data in tickets.get(str(guild.id), {}).items() if not data.get("ferme")
    }

    embed = discord.Embed(title="📂 Tickets actuellement ouverts", color=discord.Color.blurple())
    if not tickets_guild:
        embed.description = "Aucun ticket ouvert actuellement."
        return embed

    for chan_id, data in list(tickets_guild.items())[:25]:
        salon = guild.get_channel(int(chan_id))
        valeur = (
            f"Type : {data.get('type_nom', '?')}\n"
            f"Ouvert par : <@{data.get('ouvert_par')}>\n"
            f"Pris en charge : {'<@' + data['claim_par'] + '>' if data.get('claim_par') else 'Non'}"
        )
        embed.add_field(
            name=salon.mention if salon else f"Salon supprimé ({chan_id})", value=valeur, inline=False
        )

    embed.set_footer(text=f"{len(tickets_guild)} ticket(s) ouvert(s)")
    return embed


class PanelTicketsOuverts(PanelBaseTicket):
    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.primary, row=0, emoji="🔄")
    async def actualiser(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_tickets_ouverts(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=0)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_tickets(interaction.guild)
        vue = MenuPrincipalTicket(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# ============================================================
#  COG
# ============================================================

class Ticket(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._vues_enregistrees = False

    @commands.Cog.listener()
    async def on_ready(self):
        # Ré-enregistre les vues persistantes (boutons) à chaque (re)connexion,
        # une seule fois par démarrage : sans ça, les boutons cesseraient de
        # répondre après un redémarrage du bot.
        if self._vues_enregistrees:
            return
        self._vues_enregistrees = True

        self.bot.add_view(GestionTicketView())

        config = charger_config()
        for guild_id_str, data in config.items():
            tickets_cfg = data.get("tickets")
            if not tickets_cfg or not tickets_cfg.get("types"):
                continue
            try:
                guild_id = int(guild_id_str)
            except ValueError:
                continue
            self.bot.add_view(construire_vue_ouverture(guild_id, tickets_cfg["types"]))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        # Si un salon de ticket est supprimé manuellement (hors bouton Fermer),
        # on nettoie quand même l'entrée pour ne pas garder un ticket fantôme.
        tickets = charger_tickets()
        tickets_guild = tickets.get(str(channel.guild.id), {})
        if str(channel.id) in tickets_guild:
            del tickets_guild[str(channel.id)]
            sauvegarder_tickets(tickets)

    @commands.command(
        name="ticket",
        aliases=["tickets"],
        help="Ouvre le panel de gestion complet du système de tickets (types, accès, panel d'ouverture).",
    )
    @commands.guild_only()
    @check_admin()
    async def ticket_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil_tickets(ctx.guild)
        vue = MenuPrincipalTicket(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=vue)
        vue.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Ticket(bot))
