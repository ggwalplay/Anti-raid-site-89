import discord
from datetime import timedelta
from discord.ext import commands

from commandes._permissions import (
    est_staff,
    est_gerant,
    ajouter_avertissement,
    obtenir_avertissements,
    supprimer_avertissement,
    check_staff,
)
from commandes.logs import log_sanction

# --- CONFIGURATION DES TYPES DE SANCTIONS ---

COULEURS_SANCTION = {
    "Avertissement": discord.Color.gold(),
    "Mute": discord.Color.blue(),
    "Kick": discord.Color.orange(),
    "Ban": discord.Color.dark_red(),
}

EMOJIS_SANCTION = {
    "Avertissement": "⚠️",
    "Mute": "🔇",
    "Kick": "👢",
    "Ban": "🔨",
}

DUREES_MUTE = [
    ("10 minutes", 600),
    ("1 heure", 3600),
    ("6 heures", 21600),
    ("1 jour", 86400),
    ("7 jours", 604800),
    ("28 jours", 2419200),  # Maximum autorisé par Discord pour un timeout
]


def _embed_base(titre: str, description: str) -> discord.Embed:
    return discord.Embed(
        title=titre,
        description=description,
        color=discord.Color.blurple(),
    )


def _tronquer(texte: str, longueur_max: int) -> str:
    if len(texte) <= longueur_max:
        return texte
    return texte[: longueur_max - 1].rstrip() + "…"


class PanelBase(discord.ui.View):
    """Vue de base : seul l'auteur de la commande &sanctions peut interagir."""

    def __init__(self, guild_id: int, auteur_id: int, cible_id: int | None = None, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.cible_id = cible_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message(
                "Seul le créateur de ce panel peut l'utiliser.", ephemeral=True
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

    async def annuler(self, interaction: discord.Interaction) -> None:
        embed = _embed_base("Sanction annulée", "Le panel a été fermé sans appliquer de sanction.")
        embed.color = discord.Color.light_grey()
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


# --- ETAPE 1 : CHOIX DU MEMBRE ---

class SelectMembreSanction(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Choisir le membre à sanctionner", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view: VueChoixMembre = self.view
        cible = self.values[0]

        if cible.id == interaction.user.id:
            await interaction.response.send_message("Vous ne pouvez pas vous sanctionner vous-même.", ephemeral=True)
            return
        if cible.id == interaction.client.user.id:
            await interaction.response.send_message("Vous ne pouvez pas sanctionner le bot.", ephemeral=True)
            return
        if not isinstance(cible, discord.Member):
            await interaction.response.send_message("Ce membre ne semble plus être sur le serveur.", ephemeral=True)
            return
        if est_gerant(cible) and not est_gerant(interaction.user):
            await interaction.response.send_message(
                "Vous ne pouvez pas sanctionner un gérant.", ephemeral=True
            )
            return

        nouvelle_vue = VueChoixType(view.guild_id, view.auteur_id, cible.id)
        embed = _embed_base(
            "Sanction — Choix du type",
            f"Membre sélectionné : {cible.mention}\nChoisissez le type de sanction à appliquer.",
        )
        nouvelle_vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=nouvelle_vue)


class VueChoixMembre(PanelBase):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectMembreSanction())

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.danger, row=1)
    async def bouton_annuler(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.annuler(interaction)


# --- ETAPE 2 : CHOIX DU TYPE DE SANCTION ---

class SelectTypeSanction(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=nom, emoji=EMOJIS_SANCTION[nom])
            for nom in ("Avertissement", "Mute", "Kick", "Ban")
        ]
        super().__init__(placeholder="Choisir le type de sanction", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view: VueChoixType = self.view
        type_sanction = self.values[0]

        if type_sanction == "Mute":
            nouvelle_vue = VueChoixDuree(view.guild_id, view.auteur_id, view.cible_id)
            embed = _embed_base(
                "Sanction — Durée du mute",
                f"Membre : <@{view.cible_id}>\nChoisissez la durée du mute (timeout).",
            )
            nouvelle_vue.message = interaction.message
            await interaction.response.edit_message(embed=embed, view=nouvelle_vue)
            return

        modal = ModalSanction(view.guild_id, view.auteur_id, view.cible_id, type_sanction, interaction.message)
        await interaction.response.send_modal(modal)


class VueChoixType(PanelBase):
    def __init__(self, guild_id: int, auteur_id: int, cible_id: int):
        super().__init__(guild_id, auteur_id, cible_id)
        self.add_item(SelectTypeSanction())

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.danger, row=1)
    async def bouton_annuler(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.annuler(interaction)


# --- ETAPE 2 BIS : CHOIX DE LA DUREE (UNIQUEMENT POUR LE MUTE) ---

class SelectDureeMute(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=str(secondes))
            for label, secondes in DUREES_MUTE
        ]
        super().__init__(placeholder="Choisir la durée", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view: VueChoixDuree = self.view
        secondes = int(self.values[0])

        modal = ModalSanction(
            view.guild_id, view.auteur_id, view.cible_id, "Mute", interaction.message, duree_secondes=secondes
        )
        await interaction.response.send_modal(modal)


class VueChoixDuree(PanelBase):
    def __init__(self, guild_id: int, auteur_id: int, cible_id: int):
        super().__init__(guild_id, auteur_id, cible_id)
        self.add_item(SelectDureeMute())

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.danger, row=1)
    async def bouton_annuler(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.annuler(interaction)


# --- ETAPE 3 : RAISON + PREUVE (MODAL) ---

class ModalSanction(discord.ui.Modal):
    raison = discord.ui.TextInput(
        label="Raison de la sanction",
        style=discord.TextStyle.short,
        max_length=300,
        required=True,
    )
    preuve = discord.ui.TextInput(
        label="Preuve (lien, capture, explication...)",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(
        self,
        guild_id: int,
        auteur_id: int,
        cible_id: int,
        type_sanction: str,
        message_panel: discord.Message,
        duree_secondes: int | None = None,
    ):
        super().__init__(title=f"Sanction : {type_sanction}")
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.cible_id = cible_id
        self.type_sanction = type_sanction
        self.message_panel = message_panel
        self.duree_secondes = duree_secondes

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message(
                "Seul le créateur de ce panel peut valider la sanction.", ephemeral=True
            )
            return

        guild = interaction.guild
        moderateur = interaction.user
        raison = self.raison.value.strip()
        preuve = self.preuve.value.strip()

        cible = guild.get_member(self.cible_id)
        if cible is None:
            try:
                cible = await guild.fetch_member(self.cible_id)
            except discord.NotFound:
                cible = None

        if cible is None:
            embed = _embed_base("Erreur", "Ce membre a quitté le serveur, la sanction n'a pas pu être appliquée.")
            embed.color = discord.Color.red()
            await interaction.response.edit_message(embed=embed, view=None)
            return

        erreur = self._verifier_hierarchie(guild, moderateur, cible)
        if erreur is None:
            erreur = await self._appliquer_sanction(cible, raison)

        if self.type_sanction == "Avertissement" and erreur is None:
            ajouter_avertissement(guild.id, cible.id, moderateur.id, raison, preuve)

        couleur = COULEURS_SANCTION.get(self.type_sanction, discord.Color.red())
        titre_log = f"{EMOJIS_SANCTION.get(self.type_sanction, '')} {self.type_sanction}".strip()
        if self.duree_secondes:
            titre_log += f" ({self._formater_duree(self.duree_secondes)})"

        await log_sanction(
            guild=guild,
            action=titre_log,
            cible=cible,
            moderateur=moderateur,
            raison=raison if erreur is None else f"{raison}\n\n⚠️ Action Discord non appliquée : {erreur}",
            couleur=couleur,
            preuve=preuve,
        )

        if erreur is None:
            embed = _embed_base(
                "✅ Sanction appliquée",
                f"**{self.type_sanction}** appliqué à {cible.mention}.\nLa sanction a été envoyée dans le salon de logs.",
            )
            embed.color = couleur
        else:
            embed = _embed_base(
                "⚠️ Sanction partiellement appliquée",
                f"La sanction **{self.type_sanction}** a été journalisée mais l'action Discord a échoué :\n{erreur}",
            )
            embed.color = discord.Color.red()

        await interaction.response.edit_message(embed=embed, view=None)

    @staticmethod
    def _formater_duree(secondes: int) -> str:
        for label, valeur in DUREES_MUTE:
            if valeur == secondes:
                return label
        return f"{secondes}s"

    def _verifier_hierarchie(
        self, guild: discord.Guild, moderateur: discord.Member, cible: discord.Member
    ) -> str | None:
        if self.type_sanction == "Avertissement":
            return None
        if guild.me.top_role <= cible.top_role:
            return "le rôle du bot est trop bas pour agir sur ce membre."
        if moderateur.top_role <= cible.top_role and not est_gerant(moderateur):
            return "vous ne pouvez pas sanctionner un membre ayant un rôle égal ou supérieur au vôtre."
        return None

    async def _appliquer_sanction(self, cible: discord.Member, raison: str) -> str | None:
        try:
            if self.type_sanction == "Kick":
                await cible.kick(reason=raison)
            elif self.type_sanction == "Ban":
                await cible.ban(reason=raison, delete_message_seconds=0)
            elif self.type_sanction == "Mute" and self.duree_secondes:
                await cible.timeout(timedelta(seconds=self.duree_secondes), reason=raison)
            # "Avertissement" ne déclenche aucune action Discord.
            return None
        except discord.Forbidden:
            return "permissions insuffisantes pour le bot."
        except discord.HTTPException as e:
            return f"erreur Discord ({e})."


def _embed_warnlist(cible_affichage: str, avertissements: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title=f"⚠️ Avertissements — {cible_affichage}",
        color=discord.Color.gold(),
    )

    if not avertissements:
        embed.description = "Aucun avertissement enregistré pour ce membre."
        return embed

    # Un embed ne peut contenir que 25 champs max : on affiche les plus récents.
    affiches = avertissements[-25:]
    for index, warn in enumerate(affiches, start=1):
        date_affichee = warn.get("date", "")[:19].replace("T", " ")
        valeur = (
            f"Staff : <@{warn.get('staff_id')}>\n"
            f"Raison : {_tronquer(warn.get('raison') or 'Aucune raison', 200)}"
        )
        if warn.get("preuve"):
            valeur += f"\nPreuve : {_tronquer(warn['preuve'], 200)}"
        embed.add_field(name=f"#{index} — {date_affichee} UTC", value=valeur, inline=False)

    if len(avertissements) > 25:
        embed.set_footer(text=f"{len(avertissements)} avertissements au total (25 plus récents affichés).")
    else:
        embed.set_footer(text=f"{len(avertissements)} avertissement(s) au total.")
    return embed


class SelectSuppressionWarn(discord.ui.Select):
    def __init__(self, avertissements: list[dict]):
        options = []
        for index, warn in enumerate(avertissements, start=1):
            date_affichee = warn.get("date", "")[:10]
            raison = warn.get("raison") or "Aucune raison"
            options.append(
                discord.SelectOption(
                    label=f"#{index} — {date_affichee}",
                    description=_tronquer(raison, 100),
                    value=warn["id"],
                )
            )
        super().__init__(
            placeholder="Supprimer un avertissement (réservé aux gérants)",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: VueWarnlist = self.view

        if not est_gerant(interaction.user):
            await interaction.response.send_message(
                "Seul un gérant peut supprimer un avertissement.", ephemeral=True
            )
            return

        warn_id = self.values[0]
        supprime = supprimer_avertissement(view.guild_id, view.cible_id, warn_id)
        if not supprime:
            await interaction.response.send_message(
                "Cet avertissement n'existe plus (déjà supprimé ?).", ephemeral=True
            )
            return

        avertissements = obtenir_avertissements(view.guild_id, view.cible_id)
        embed = _embed_warnlist(view.cible_affichage, avertissements)
        nouvelle_vue = VueWarnlist(view.guild_id, view.auteur_id, view.cible_id, view.cible_affichage, avertissements)
        nouvelle_vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=nouvelle_vue)


class VueWarnlist(PanelBase):
    def __init__(
        self,
        guild_id: int,
        auteur_id: int,
        cible_id: int,
        cible_affichage: str,
        avertissements: list[dict],
    ):
        super().__init__(guild_id, auteur_id, cible_id)
        self.cible_affichage = cible_affichage
        if avertissements:
            self.add_item(SelectSuppressionWarn(avertissements[:25]))

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.secondary, row=1)
    async def bouton_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class Sanction(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="sanctions", help="Ouvre le panel interactif pour sanctionner un membre (warn/mute/kick/ban).")
    @commands.guild_only()
    @check_staff()
    async def sanctions(self, ctx: commands.Context):
        embed = _embed_base("Sanction — Choix du membre", "Sélectionnez le membre à sanctionner.")
        view = VueChoixMembre(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    @commands.command(name="warnlist", help="Affiche l'historique des avertissements d'un membre.")
    @commands.guild_only()
    @check_staff()
    async def warnlist(self, ctx: commands.Context, *, cible: str = None):
        if cible is None:
            await ctx.send("Utilisation : `&warnlist @membre` ou `&warnlist <id_discord>`")
            return

        utilisateur = await self._resoudre_utilisateur(ctx, cible)
        if utilisateur is None:
            await ctx.send("Membre introuvable. Utilisez une mention ou un ID Discord valide.")
            return

        avertissements = obtenir_avertissements(ctx.guild.id, utilisateur.id)
        cible_affichage = str(utilisateur)
        embed = _embed_warnlist(cible_affichage, avertissements)
        view = VueWarnlist(ctx.guild.id, ctx.author.id, utilisateur.id, cible_affichage, avertissements)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    @staticmethod
    async def _resoudre_utilisateur(ctx: commands.Context, texte: str):
        """Accepte une mention (<@id> / <@!id>) ou un ID brut."""
        texte = texte.strip().lstrip("<@!").rstrip(">")
        if not texte.isdigit():
            return None

        user_id = int(texte)
        membre = ctx.guild.get_member(user_id)
        if membre is not None:
            return membre

        try:
            return await ctx.bot.fetch_user(user_id)
        except discord.NotFound:
            return None


async def setup(bot: commands.Bot):
    await bot.add_cog(Sanction(bot))