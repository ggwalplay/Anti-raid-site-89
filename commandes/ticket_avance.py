import os
import json
import uuid
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    charger_tickets,
    sauvegarder_tickets,
    check_admin,
    DATA_DIR,
)
from commandes.ticket import (
    get_tickets_config,
    sauvegarder_tickets_config,
    fermer_ticket_definitivement,
)

FEEDBACK_PATH = os.path.join(DATA_DIR, "feedback.json")
HISTORIQUE_PATH = os.path.join(DATA_DIR, "historique_tickets.json")

PRIORITES = ["faible", "normal", "urgent"]
DELAIS_ESCALADE_MINUTES = {"urgent": 15, "normal": 45, "faible": 120}

INTERVALLE_SLA_MINUTES = 5
INTERVALLE_INACTIVITE_MINUTES = 30


# ============================================================
#  STOCKAGE (fichiers séparés, ne touchent pas à data/tickets.json)
# ============================================================

def _charger_json(chemin: str) -> dict:
    if not os.path.isfile(chemin):
        return {}
    with open(chemin, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _sauvegarder_json(chemin: str, data: dict) -> None:
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def charger_feedback() -> dict:
    return _charger_json(FEEDBACK_PATH)


def sauvegarder_feedback(data: dict) -> None:
    _sauvegarder_json(FEEDBACK_PATH, data)


def charger_historique() -> dict:
    return _charger_json(HISTORIQUE_PATH)


def sauvegarder_historique(data: dict) -> None:
    _sauvegarder_json(HISTORIQUE_PATH, data)


# ============================================================
#  CONFIGURATION GLOBALE (config.json, clé "ticket_avance")
# ============================================================

def get_config_avancee(guild_id: int) -> dict:
    config = charger_config()
    return config.get(str(guild_id), {}).get(
        "ticket_avance",
        {"feedback_actif": True, "inactivite_actif": True, "rappel_heures": 24, "fermeture_heures": 72},
    )


def sauvegarder_config_avancee(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["ticket_avance"] = data
    sauvegarder_config(config)


# ============================================================
#  HOOK APPELÉ PAR ticket.py À CHAQUE FERMETURE (avant suppression du salon)
# ============================================================

async def notifier_fermeture_ticket(guild: discord.Guild, entree: dict) -> None:
    _enregistrer_historique(guild.id, entree)

    config_avancee = get_config_avancee(guild.id)
    if config_avancee.get("feedback_actif", True):
        await _envoyer_demande_feedback(guild, entree)


def _enregistrer_historique(guild_id: int, entree: dict) -> None:
    """Garde une trace légère du ticket fermé pour les statistiques, puisque
    tickets.json supprime l'entrée une fois le salon effacé."""
    historique = charger_historique()
    liste = historique.setdefault(str(guild_id), [])
    try:
        duree_traitement = None
        if entree.get("date_ouverture") and entree.get("date_fermeture"):
            debut = datetime.fromisoformat(entree["date_ouverture"])
            fin = datetime.fromisoformat(entree["date_fermeture"])
            duree_traitement = (fin - debut).total_seconds()
    except ValueError:
        duree_traitement = None

    liste.append({
        "type_nom": entree.get("type_nom", "?"),
        "priorite": entree.get("priorite", "normal"),
        "numero": entree.get("numero"),
        "claim_par": entree.get("claim_par"),
        "duree_traitement_secondes": duree_traitement,
        "date_fermeture": entree.get("date_fermeture"),
    })
    # Pas de purge automatique ici : volontairement laissé simple, à nettoyer
    # manuellement si le fichier devient trop gros (voir &ticketstats reset).
    sauvegarder_historique(historique)


# ============================================================
#  FEEDBACK (DM au membre après fermeture)
# ============================================================

class SelectNote(discord.ui.Select):
    def __init__(self, guild_id: int, entree: dict):
        options = [
            discord.SelectOption(label=f"{i} / 5", value=str(i))
            for i in range(1, 6)
        ]
        super().__init__(placeholder="Notez votre expérience", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.entree = entree

    async def callback(self, interaction: discord.Interaction):
        note = int(self.values[0])
        await interaction.response.send_modal(ModalCommentaireFeedback(self.guild_id, self.entree, note))


class VueFeedback(discord.ui.View):
    def __init__(self, guild_id: int, entree: dict):
        super().__init__(timeout=1800)
        self.add_item(SelectNote(guild_id, entree))

    @discord.ui.button(label="Passer", style=discord.ButtonStyle.secondary, row=1)
    async def passer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Pas de souci, merci quand même d'avoir utilisé le support.", view=self)


class ModalCommentaireFeedback(discord.ui.Modal, title="Un commentaire ?"):
    commentaire = discord.ui.TextInput(
        label="Commentaire (optionnel)", style=discord.TextStyle.paragraph, required=False, max_length=500
    )

    def __init__(self, guild_id: int, entree: dict, note: int):
        super().__init__()
        self.guild_id = guild_id
        self.entree = entree
        self.note = note

    async def on_submit(self, interaction: discord.Interaction):
        feedback = charger_feedback()
        liste = feedback.setdefault(str(self.guild_id), [])
        liste.append({
            "id": uuid.uuid4().hex[:8],
            "type_nom": self.entree.get("type_nom", "?"),
            "numero": self.entree.get("numero"),
            "membre_id": self.entree.get("ouvert_par"),
            "note": self.note,
            "commentaire": self.commentaire.value.strip() or None,
            "date": datetime.now(timezone.utc).isoformat(),
        })
        sauvegarder_feedback(feedback)

        await interaction.response.send_message(f"Merci pour votre retour ({self.note}/5) !", ephemeral=True)


async def _envoyer_demande_feedback(guild: discord.Guild, entree: dict) -> None:
    membre_id = entree.get("ouvert_par")
    if not membre_id:
        return

    membre = guild.get_member(int(membre_id))
    if membre is None:
        return

    embed = discord.Embed(
        title="Votre ticket a été fermé",
        description=(
            f"Votre ticket **{entree.get('type_nom', '?')}** (#{entree.get('numero', 0):04d}) "
            f"sur **{guild.name}** vient d'être fermé. Vous pouvez laisser une note si vous le souhaitez."
        ),
        color=discord.Color.blurple(),
    )
    try:
        await membre.send(embed=embed, view=VueFeedback(guild.id, entree))
    except discord.Forbidden:
        pass


# ============================================================
#  FORMULAIRE PRÉ-TICKET ET PRIORITÉ (configuration par type)
# ============================================================

class ModalQuestionsFormulaire(discord.ui.Modal, title="Formulaire pré-ticket"):
    def __init__(self, guild_id: int, type_id: str, questions_actuelles: list[str]):
        super().__init__()
        self.guild_id = guild_id
        self.type_id = type_id
        questions_actuelles = (questions_actuelles + ["", "", ""])[:3]
        self.q1 = discord.ui.TextInput(label="Question 1", required=False, max_length=45, default=questions_actuelles[0])
        self.q2 = discord.ui.TextInput(label="Question 2", required=False, max_length=45, default=questions_actuelles[1])
        self.q3 = discord.ui.TextInput(label="Question 3", required=False, max_length=45, default=questions_actuelles[2])
        self.add_item(self.q1)
        self.add_item(self.q2)
        self.add_item(self.q3)

    async def on_submit(self, interaction: discord.Interaction):
        questions = [q.value.strip() for q in (self.q1, self.q2, self.q3) if q.value.strip()]

        tickets_cfg = get_tickets_config(self.guild_id)
        info = tickets_cfg.get("types", {}).get(self.type_id)
        if info is None:
            await interaction.response.send_message("Ce type de ticket n'existe plus.", ephemeral=True)
            return

        info["questions"] = questions
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        await interaction.response.send_message(
            f"Formulaire mis à jour ({len(questions)} question(s)). "
            "Laissez les 3 champs vides pour désactiver le formulaire sur ce type.",
            ephemeral=True,
        )


class SelectPrioriteDefaut(discord.ui.Select):
    def __init__(self, guild_id: int, type_id: str):
        options = [discord.SelectOption(label=p.capitalize(), value=p) for p in PRIORITES]
        super().__init__(placeholder="Priorité par défaut de ce type", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.type_id = type_id

    async def callback(self, interaction: discord.Interaction):
        tickets_cfg = get_tickets_config(self.guild_id)
        info = tickets_cfg.get("types", {}).get(self.type_id)
        if info is None:
            await interaction.response.send_message("Ce type de ticket n'existe plus.", ephemeral=True)
            return

        info["priorite_defaut"] = self.values[0]
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        await interaction.response.send_message(f"Priorité par défaut réglée sur **{self.values[0]}**.", ephemeral=True)


class SelectRoleEscalade(discord.ui.RoleSelect):
    def __init__(self, guild_id: int, type_id: str):
        super().__init__(placeholder="Rôle à pinger en cas d'escalade (SLA dépassé)", min_values=0, max_values=1)
        self.guild_id = guild_id
        self.type_id = type_id

    async def callback(self, interaction: discord.Interaction):
        tickets_cfg = get_tickets_config(self.guild_id)
        info = tickets_cfg.get("types", {}).get(self.type_id)
        if info is None:
            await interaction.response.send_message("Ce type de ticket n'existe plus.", ephemeral=True)
            return

        info["role_escalade_id"] = self.values[0].id if self.values else None
        sauvegarder_tickets_config(self.guild_id, tickets_cfg)

        await interaction.response.send_message(
            f"Rôle d'escalade : {self.values[0].mention if self.values else 'aucun'}.", ephemeral=True
        )


class PanelTypeAvance(discord.ui.View):
    def __init__(self, guild_id: int, auteur_id: int, type_id: str):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.type_id = type_id
        self.add_item(SelectPrioriteDefaut(guild_id, type_id))
        self.add_item(SelectRoleEscalade(guild_id, type_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message("Seul l'auteur de la commande peut utiliser ce panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Configurer le formulaire", style=discord.ButtonStyle.primary, row=2)
    async def formulaire(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets_cfg = get_tickets_config(self.guild_id)
        info = tickets_cfg.get("types", {}).get(self.type_id, {})
        await interaction.response.send_modal(
            ModalQuestionsFormulaire(self.guild_id, self.type_id, info.get("questions", []))
        )


def construire_embed_type_avance(guild: discord.Guild, guild_id: int, type_id: str) -> discord.Embed:
    tickets_cfg = get_tickets_config(guild_id)
    info = tickets_cfg.get("types", {}).get(type_id, {})

    questions = info.get("questions", [])
    role_escalade = guild.get_role(info.get("role_escalade_id")) if info.get("role_escalade_id") else None

    embed = discord.Embed(title=f"Options avancées — {info.get('nom', '?')}", color=discord.Color.blurple())
    embed.add_field(
        name="Formulaire pré-ticket",
        value="\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1)) if questions else "Aucun (salon créé directement)",
        inline=False,
    )
    embed.add_field(name="Priorité par défaut", value=info.get("priorite_defaut", "normal"), inline=True)
    embed.add_field(name="Rôle d'escalade", value=role_escalade.mention if role_escalade else "Aucun", inline=True)
    return embed


class SelectTypePourAvance(discord.ui.Select):
    def __init__(self, guild_id: int, auteur_id: int, types: dict):
        options = [
            discord.SelectOption(label=(info.get("nom") or "?")[:100], value=type_id)
            for type_id, info in list(types.items())[:25]
        ]
        super().__init__(placeholder="Choisir un type de ticket à configurer...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id
        self.auteur_id = auteur_id

    async def callback(self, interaction: discord.Interaction):
        type_id = self.values[0]
        embed = construire_embed_type_avance(interaction.guild, self.guild_id, type_id)
        vue = PanelTypeAvance(self.guild_id, self.auteur_id, type_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class MenuTicketAvance(discord.ui.View):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        types = get_tickets_config(guild_id).get("types", {})
        if types:
            self.add_item(SelectTypePourAvance(guild_id, auteur_id, types))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message("Seul l'auteur de la commande peut utiliser ce panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Paramètres globaux", style=discord.ButtonStyle.secondary, row=1)
    async def globaux(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_avancee = get_config_avancee(self.guild_id)
        embed = construire_embed_globaux(config_avancee)
        vue = PanelGlobaux(self.guild_id, self.auteur_id)
        await interaction.response.edit_message(embed=embed, view=vue)


# ============================================================
#  PARAMÈTRES GLOBAUX (feedback + inactivité)
# ============================================================

def construire_embed_globaux(config_avancee: dict) -> discord.Embed:
    embed = discord.Embed(title="Paramètres globaux des tickets", color=discord.Color.blurple())
    embed.add_field(name="Feedback après fermeture", value="Activé" if config_avancee.get("feedback_actif", True) else "Désactivé", inline=True)
    embed.add_field(name="Fermeture sur inactivité", value="Activée" if config_avancee.get("inactivite_actif", True) else "Désactivée", inline=True)
    embed.add_field(name="Rappel après", value=f"{config_avancee.get('rappel_heures', 24)} h sans message", inline=True)
    embed.add_field(name="Fermeture après", value=f"{config_avancee.get('fermeture_heures', 72)} h sans message", inline=True)
    return embed


class ModalDelaisInactivite(discord.ui.Modal, title="Délais d'inactivité"):
    rappel = discord.ui.TextInput(label="Heures avant le rappel", max_length=4)
    fermeture = discord.ui.TextInput(label="Heures avant la fermeture auto", max_length=4)

    def __init__(self, guild_id: int, config_avancee: dict):
        super().__init__()
        self.guild_id = guild_id
        self.rappel.default = str(config_avancee.get("rappel_heures", 24))
        self.fermeture.default = str(config_avancee.get("fermeture_heures", 72))

    async def on_submit(self, interaction: discord.Interaction):
        if not self.rappel.value.isdigit() or not self.fermeture.value.isdigit():
            await interaction.response.send_message("Merci d'entrer des nombres entiers d'heures.", ephemeral=True)
            return

        config_avancee = get_config_avancee(self.guild_id)
        config_avancee["rappel_heures"] = int(self.rappel.value)
        config_avancee["fermeture_heures"] = int(self.fermeture.value)
        sauvegarder_config_avancee(self.guild_id, config_avancee)

        embed = construire_embed_globaux(config_avancee)
        await interaction.response.edit_message(embed=embed, view=PanelGlobaux(self.guild_id, interaction.user.id))


class PanelGlobaux(discord.ui.View):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.auteur_id = auteur_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message("Seul l'auteur de la commande peut utiliser ce panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Feedback : activer/désactiver", style=discord.ButtonStyle.primary, row=0)
    async def toggle_feedback(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_avancee = get_config_avancee(self.guild_id)
        config_avancee["feedback_actif"] = not config_avancee.get("feedback_actif", True)
        sauvegarder_config_avancee(self.guild_id, config_avancee)
        embed = construire_embed_globaux(config_avancee)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Inactivité : activer/désactiver", style=discord.ButtonStyle.primary, row=0)
    async def toggle_inactivite(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_avancee = get_config_avancee(self.guild_id)
        config_avancee["inactivite_actif"] = not config_avancee.get("inactivite_actif", True)
        sauvegarder_config_avancee(self.guild_id, config_avancee)
        embed = construire_embed_globaux(config_avancee)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Modifier les délais", style=discord.ButtonStyle.secondary, row=1)
    async def delais(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_avancee = get_config_avancee(self.guild_id)
        await interaction.response.send_modal(ModalDelaisInactivite(self.guild_id, config_avancee))

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        vue = MenuTicketAvance(self.guild_id, self.auteur_id)
        await interaction.response.edit_message(content=None, embed=None, view=vue)


# ============================================================
#  STATISTIQUES
# ============================================================

def construire_embed_stats(guild_id: int) -> discord.Embed:
    historique = charger_historique().get(str(guild_id), [])
    feedback = charger_feedback().get(str(guild_id), [])

    embed = discord.Embed(title="Statistiques des tickets", color=discord.Color.blurple())

    if not historique:
        embed.description = "Aucun ticket fermé n'a encore été enregistré."
        return embed

    durees = [h["duree_traitement_secondes"] for h in historique if h.get("duree_traitement_secondes") is not None]
    duree_moyenne = sum(durees) / len(durees) if durees else None

    embed.add_field(name="Tickets fermés (total)", value=str(len(historique)), inline=True)
    if duree_moyenne is not None:
        embed.add_field(name="Durée moyenne de traitement", value=f"{duree_moyenne / 60:.0f} min", inline=True)

    if feedback:
        notes = [f["note"] for f in feedback]
        embed.add_field(name="Note moyenne", value=f"{sum(notes) / len(notes):.1f} / 5 ({len(notes)} avis)", inline=True)

    par_staff: dict[str, int] = {}
    for h in historique:
        if h.get("claim_par"):
            par_staff[h["claim_par"]] = par_staff.get(h["claim_par"], 0) + 1
    if par_staff:
        classement = sorted(par_staff.items(), key=lambda x: x[1], reverse=True)[:10]
        embed.add_field(
            name="Tickets traités par staff (top 10)",
            value="\n".join(f"<@{staff_id}> : {nb}" for staff_id, nb in classement),
            inline=False,
        )

    return embed


# ============================================================
#  COG
# ============================================================

class TicketAvance(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tache_sla.start()
        self.tache_inactivite.start()

    def cog_unload(self):
        self.tache_sla.cancel()
        self.tache_inactivite.cancel()

    # --- Suivi de l'activité d'un ticket (reset le rappel dès qu'on parle dedans) ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        tickets = charger_tickets()
        tickets_guild = tickets.get(str(message.guild.id), {})
        entree = tickets_guild.get(str(message.channel.id))
        if entree is None or entree.get("ferme"):
            return

        entree["derniere_activite"] = datetime.now(timezone.utc).isoformat()
        entree["rappel_envoye"] = False
        sauvegarder_tickets(tickets)

    # --- Escalade SLA : relance si aucun claim passé le délai de la priorité ---

    @tasks.loop(minutes=INTERVALLE_SLA_MINUTES)
    async def tache_sla(self):
        tickets = charger_tickets()
        maintenant = datetime.now(timezone.utc)

        for guild_id_str, tickets_guild in tickets.items():
            guild = self.bot.get_guild(int(guild_id_str))
            if guild is None:
                continue

            a_sauvegarder = False
            for channel_id_str, entree in tickets_guild.items():
                if entree.get("ferme") or entree.get("claim_par") or entree.get("escalade_envoyee"):
                    continue

                priorite = entree.get("priorite", "normal")
                delai = DELAIS_ESCALADE_MINUTES.get(priorite, 45)
                try:
                    ouverture = datetime.fromisoformat(entree["date_ouverture"])
                except (KeyError, ValueError):
                    continue

                if (maintenant - ouverture).total_seconds() < delai * 60:
                    continue

                salon = guild.get_channel(int(channel_id_str))
                if salon is None:
                    continue

                tickets_cfg = get_tickets_config(guild.id)
                info_type = tickets_cfg.get("types", {}).get(entree.get("type_id", ""), {})
                role_id = info_type.get("role_escalade_id")
                role = guild.get_role(role_id) if role_id else None

                try:
                    await salon.send(
                        (role.mention + " " if role else "")
                        + f"Ce ticket (priorité **{priorite}**) est ouvert depuis plus de {delai} minutes "
                        "sans prise en charge."
                    )
                except discord.HTTPException:
                    pass

                entree["escalade_envoyee"] = True
                a_sauvegarder = True

            if a_sauvegarder:
                sauvegarder_tickets(tickets)

    @tache_sla.before_loop
    async def avant_sla(self):
        await self.bot.wait_until_ready()

    # --- Fermeture automatique sur inactivité (rappel puis fermeture) ---

    @tasks.loop(minutes=INTERVALLE_INACTIVITE_MINUTES)
    async def tache_inactivite(self):
        tickets = charger_tickets()
        maintenant = datetime.now(timezone.utc)

        for guild_id_str in list(tickets.keys()):
            guild = self.bot.get_guild(int(guild_id_str))
            if guild is None:
                continue

            config_avancee = get_config_avancee(guild.id)
            if not config_avancee.get("inactivite_actif", True):
                continue

            rappel_delai = config_avancee.get("rappel_heures", 24) * 3600
            fermeture_delai = config_avancee.get("fermeture_heures", 72) * 3600

            tickets_guild = tickets.get(guild_id_str, {})
            a_sauvegarder = False

            for channel_id_str, entree in list(tickets_guild.items()):
                if entree.get("ferme"):
                    continue

                try:
                    derniere_activite = datetime.fromisoformat(
                        entree.get("derniere_activite") or entree["date_ouverture"]
                    )
                except (KeyError, ValueError):
                    continue

                inactivite_secondes = (maintenant - derniere_activite).total_seconds()
                salon = guild.get_channel(int(channel_id_str))
                if salon is None:
                    continue

                if inactivite_secondes >= fermeture_delai:
                    await fermer_ticket_definitivement(salon, guild, guild.me)
                    continue  # fermer_ticket_definitivement gère déjà tickets.json

                if inactivite_secondes >= rappel_delai and not entree.get("rappel_envoye"):
                    try:
                        await salon.send(
                            "Ce ticket est inactif depuis un moment. "
                            "Il sera fermé automatiquement en l'absence de nouveau message."
                        )
                    except discord.HTTPException:
                        pass
                    entree["rappel_envoye"] = True
                    a_sauvegarder = True

            if a_sauvegarder:
                sauvegarder_tickets(tickets)

    @tache_inactivite.before_loop
    async def avant_inactivite(self):
        await self.bot.wait_until_ready()

    # --- Commandes ---

    @commands.command(
        name="ticketavance",
        aliases=["ticketsavances"],
        help="Configure le formulaire pré-ticket, la priorité/escalade par type, et les paramètres globaux (feedback, inactivité).",
    )
    @commands.guild_only()
    @check_admin()
    async def ticket_avance_panel(self, ctx: commands.Context):
        vue = MenuTicketAvance(ctx.guild.id, ctx.author.id)
        types = get_tickets_config(ctx.guild.id).get("types", {})
        description = (
            "Choisissez un type de ticket pour configurer son formulaire, sa priorité et son rôle d'escalade, "
            "ou ouvrez les paramètres globaux (feedback, inactivité)."
        )
        if not types:
            description = "Aucun type de ticket configuré (voir `&ticket`). Les paramètres globaux restent accessibles."
        embed = discord.Embed(title="Extensions du système de tickets", description=description, color=discord.Color.blurple())
        await ctx.send(embed=embed, view=vue)

    @commands.command(name="ticketstats", help="Affiche les statistiques des tickets (durée de traitement, notes, staff les plus actifs).")
    @commands.guild_only()
    @check_admin()
    async def ticket_stats(self, ctx: commands.Context):
        embed = construire_embed_stats(ctx.guild.id)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketAvance(bot))
