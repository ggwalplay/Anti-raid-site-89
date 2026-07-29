import discord
from discord.ext import commands

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    check_admin,
)
from commandes.logs import envoyer_log

MAX_ROLES_RECOMPENSE = 10


# ============================================================
#  CONFIGURATION (stockée dans data/config.json, clé "soutien")
# ============================================================

def get_config_soutien(guild_id: int) -> dict:
    config = charger_config()
    return config.get(str(guild_id), {}).get(
        "soutien", {"texte_statut": "", "roles_recompense": [], "salon_id": None, "message_id": None}
    )


def sauvegarder_config_soutien(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["soutien"] = data
    sauvegarder_config(config)


# ============================================================
#  DETECTION DES CONDITIONS
# ============================================================

def a_le_texte_dans_statut(membre: discord.Member, texte: str) -> bool:
    """Nécessite l'intent Presence (activée dans main.py + portail développeur Discord)."""
    if not texte:
        return False
    for activite in getattr(membre, "activities", ()):
        if isinstance(activite, discord.CustomActivity) and activite.name:
            if texte.lower() in activite.name.lower():
                return True
    return False


def a_le_tag_serveur(utilisateur: discord.abc.User, guild_id: int) -> bool:
    """Le membre a activé le tag de CE serveur sur son profil (débloqué par les boosts)."""
    primary = getattr(utilisateur, "primary_guild", None)
    if primary is None:
        return False
    return primary.identity_enabled is True and primary.id == guild_id


# ============================================================
#  VUE PUBLIQUE
# ============================================================

class SelectRoleRecompense(discord.ui.Select):
    def __init__(self, roles_ids: list[int], guild: discord.Guild):
        options = []
        for role_id in roles_ids[:MAX_ROLES_RECOMPENSE]:
            role = guild.get_role(role_id)
            if role is not None:
                options.append(discord.SelectOption(label=role.name[:100], value=str(role_id)))

        super().__init__(
            placeholder="Choisissez votre rôle de soutien...",
            options=options or [discord.SelectOption(label="Aucun rôle disponible", value="none")],
            min_values=1,
            max_values=1,
            disabled=not options,
        )
        self.roles_ids = roles_ids

    async def callback(self, interaction: discord.Interaction):
        role_choisi_id = int(self.values[0])
        role_choisi = interaction.guild.get_role(role_choisi_id)
        if role_choisi is None:
            await interaction.response.send_message("Ce rôle n'existe plus.", ephemeral=True)
            return

        membre = interaction.user
        roles_a_retirer = [
            interaction.guild.get_role(rid) for rid in self.roles_ids if rid != role_choisi_id
        ]
        roles_a_retirer = [r for r in roles_a_retirer if r is not None and r in membre.roles]

        try:
            if roles_a_retirer:
                await membre.remove_roles(*roles_a_retirer, reason="[Soutien] Changement de rôle de soutien")
            if role_choisi not in membre.roles:
                await membre.add_roles(role_choisi, reason="[Soutien] Rôle de soutien attribué")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Permissions insuffisantes pour attribuer ce rôle (hiérarchie ou droits du bot).", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ Rôle {role_choisi.mention} attribué, merci pour votre soutien !", ephemeral=True
        )

        await envoyer_log(
            interaction.guild,
            "commandes",
            discord.Embed(
                title="💜 Rôle de soutien attribué",
                description=f"{membre.mention} a reçu le rôle {role_choisi.mention} via le panel de soutien.",
                color=discord.Color.purple(),
                timestamp=discord.utils.utcnow(),
            ),
        )


class VueChoixRecompense(discord.ui.View):
    def __init__(self, roles_ids: list[int], guild: discord.Guild, auteur_id: int):
        super().__init__(timeout=120)
        self.auteur_id = auteur_id
        self.add_item(SelectRoleRecompense(roles_ids, guild))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message("Ce menu ne vous est pas destiné.", ephemeral=True)
            return False
        return True


class BoutonVerifSoutien(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Vérifier mon soutien",
            style=discord.ButtonStyle.success,
            emoji="💜",
            custom_id="soutien_verifier",
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        config_soutien = get_config_soutien(guild.id)
        texte_requis = config_soutien.get("texte_statut", "")
        roles_ids = config_soutien.get("roles_recompense", [])

        if not roles_ids:
            await interaction.response.send_message(
                "Ce système n'est pas encore configuré (aucun rôle de récompense).", ephemeral=True
            )
            return

        # Le membre depuis le cache du serveur porte les données de présence
        # (activities) si l'intent Presence est active ; interaction.user seul ne les a pas.
        membre_cache = guild.get_member(interaction.user.id) or interaction.user

        eligible_statut = a_le_texte_dans_statut(membre_cache, texte_requis)
        eligible_tag = a_le_tag_serveur(interaction.user, guild.id)

        if not eligible_statut and not eligible_tag:
            description_texte = f"« {texte_requis} »" if texte_requis else "*(non configuré par le staff)*"
            await interaction.response.send_message(
                "❌ Aucune des deux conditions n'est remplie :\n"
                f"• Avoir {description_texte} dans votre statut personnalisé\n"
                "• Avoir activé le tag de ce serveur sur votre profil (débloqué par les boosts)\n\n"
                "Faites l'un des deux puis recliquez sur le bouton.",
                ephemeral=True,
            )
            return

        raisons = []
        if eligible_statut:
            raisons.append("texte de statut détecté")
        if eligible_tag:
            raisons.append("tag du serveur activé")

        vue = VueChoixRecompense(roles_ids, guild, interaction.user.id)
        await interaction.response.send_message(
            f"✅ Condition remplie ({' et '.join(raisons)}) ! Choisissez votre rôle :", view=vue, ephemeral=True
        )


class VuePubliqueSoutien(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(BoutonVerifSoutien())


# ============================================================
#  PANEL DE GESTION (&soutien) — admin
# ============================================================

def construire_embed_accueil_soutien(guild: discord.Guild) -> discord.Embed:
    config_soutien = get_config_soutien(guild.id)
    texte = config_soutien.get("texte_statut")
    roles = ", ".join(
        f"<@&{r}>" for r in config_soutien.get("roles_recompense", []) if guild.get_role(r)
    ) or "Aucun"
    salon = guild.get_channel(config_soutien.get("salon_id")) if config_soutien.get("salon_id") else None

    embed = discord.Embed(
        title="💜 Panel de soutien",
        description=(
            "Récompense les membres qui soutiennent le serveur : soit en mettant un texte précis dans leur "
            "statut personnalisé, soit en activant le **tag du serveur** sur leur profil (débloqué par les boosts). "
            "Une seule des deux conditions suffit.\n\n"
            "⚠️ La détection du texte de statut nécessite l'intent **Presence** activé côté bot "
            "(dans le code *et* sur le portail développeur Discord)."
        ),
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="Texte requis dans le statut",
        value=f"`{texte}`" if texte else "Non configuré",
        inline=False,
    )
    embed.add_field(name="Rôles de récompense (au choix, un seul à la fois)", value=roles, inline=False)
    embed.add_field(name="Salon du panel", value=salon.mention if salon else "Non configuré", inline=False)
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


class PanelBaseSoutien(discord.ui.View):
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


class ModalTexteStatut(discord.ui.Modal, title="Texte requis dans le statut"):
    def __init__(self, guild_id: int, auteur_id: int, texte_actuel: str):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.texte = discord.ui.TextInput(
            label="Texte à détecter (insensible à la casse)",
            max_length=100,
            default=texte_actuel,
            placeholder="Ex : discord.gg/monserveur",
        )
        self.add_item(self.texte)

    async def on_submit(self, interaction: discord.Interaction):
        config_soutien = get_config_soutien(self.guild_id)
        config_soutien["texte_statut"] = self.texte.value.strip()
        sauvegarder_config_soutien(self.guild_id, config_soutien)

        embed = construire_embed_accueil_soutien(interaction.guild)
        vue = PanelSoutien(self.guild_id, self.auteur_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class SelectRolesRecompenseAdmin(discord.ui.RoleSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(
            placeholder="Choisir les rôles de récompense proposés (remplace la liste actuelle)",
            min_values=0,
            max_values=MAX_ROLES_RECOMPENSE,
        )

    async def callback(self, interaction: discord.Interaction):
        roles_valides = [r for r in self.values if not r.is_default() and not r.managed]

        config_soutien = get_config_soutien(self.guild_id)
        config_soutien["roles_recompense"] = [r.id for r in roles_valides]
        sauvegarder_config_soutien(self.guild_id, config_soutien)

        embed = construire_embed_accueil_soutien(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)

        if len(roles_valides) != len(self.values):
            await interaction.followup.send(
                "@everyone et les rôles gérés automatiquement (bot/intégration) ont été ignorés.", ephemeral=True
            )


class SelectSalonSoutien(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(
            placeholder="Salon où sera envoyé le panel public",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        config_soutien = get_config_soutien(self.guild_id)
        config_soutien["salon_id"] = self.values[0].id
        sauvegarder_config_soutien(self.guild_id, config_soutien)

        embed = construire_embed_accueil_soutien(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


class PanelSoutien(PanelBaseSoutien):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectRolesRecompenseAdmin(guild_id))
        self.add_item(SelectSalonSoutien(guild_id))

    @discord.ui.button(label="Modifier le texte requis", style=discord.ButtonStyle.primary, row=2, emoji="✏️")
    async def modifier_texte(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_soutien = get_config_soutien(self.guild_id)
        await interaction.response.send_modal(
            ModalTexteStatut(self.guild_id, self.auteur_id, config_soutien.get("texte_statut", ""))
        )

    @discord.ui.button(label="Envoyer / Mettre à jour le panel", style=discord.ButtonStyle.success, row=2, emoji="📤")
    async def envoyer(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_soutien = get_config_soutien(self.guild_id)

        if not config_soutien.get("salon_id"):
            await interaction.response.send_message("Configurez d'abord un salon.", ephemeral=True)
            return
        if not config_soutien.get("roles_recompense"):
            await interaction.response.send_message(
                "Configurez au moins un rôle de récompense avant d'envoyer le panel.", ephemeral=True
            )
            return

        salon = interaction.guild.get_channel(config_soutien["salon_id"])
        if salon is None:
            await interaction.response.send_message("Le salon configuré est introuvable.", ephemeral=True)
            return

        texte = config_soutien.get("texte_statut")
        embed = discord.Embed(
            title="💜 Soutenez le serveur",
            description=(
                "Vous soutenez ce serveur ? Faites l'une des deux actions suivantes puis cliquez ci-dessous :\n\n"
                + (f"• Mettez **{texte}** dans votre statut personnalisé\n" if texte else "")
                + "• Activez le tag de ce serveur sur votre profil (débloqué par les boosts)\n\n"
                "Vous pourrez ensuite choisir votre rôle de soutien."
            ),
            color=discord.Color.purple(),
        )
        vue_publique = VuePubliqueSoutien()

        message_final = None
        ancien_id = config_soutien.get("message_id")
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

        config_soutien["message_id"] = message_final.id
        sauvegarder_config_soutien(self.guild_id, config_soutien)

        await interaction.response.send_message(f"✅ Panel envoyé/mis à jour dans {salon.mention}.", ephemeral=True)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=3)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# ============================================================
#  COG
# ============================================================

class Soutien(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._vue_enregistree = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._vue_enregistree:
            return
        self._vue_enregistree = True
        self.bot.add_view(VuePubliqueSoutien())

    @commands.command(
        name="soutien",
        aliases=["soutient"],
        help="Panel permettant de récompenser par un rôle les membres qui soutiennent le serveur "
        "(texte de statut ou tag de serveur via les boosts).",
    )
    @commands.guild_only()
    @check_admin()
    async def soutien_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil_soutien(ctx.guild)
        vue = PanelSoutien(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=vue)
        vue.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Soutien(bot))
