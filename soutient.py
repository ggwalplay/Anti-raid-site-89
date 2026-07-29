import discord
from discord.ext import commands

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    check_admin,
)
from commandes.logs import envoyer_log

CONFIG_PAR_DEFAUT = {
    "texte_statut": None,
    "role_id": None,
    "salon_id": None,
    "message_id": None,
}


# ============================================================
#  CONFIGURATION (stockée dans data/config.json, clé "soutien")
# ============================================================

def get_config_soutien(guild_id: int) -> dict:
    config = charger_config()
    return {**CONFIG_PAR_DEFAUT, **config.get(str(guild_id), {}).get("soutien", {})}


def sauvegarder_config_soutien(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["soutien"] = data
    sauvegarder_config(config)


# ============================================================
#  VERIFICATION DES DEUX CONDITIONS
# ============================================================

def statut_contient_texte(membre: discord.Member, texte_recherche: str) -> bool:
    """Vérifie si le statut personnalisé actuel du membre contient le texte demandé.

    Nécessite l'intent Presence (activé côté code ET dans le portail développeur
    Discord) pour que les activités des membres soient visibles par le bot.
    """
    if not texte_recherche:
        return False

    for activite in membre.activities:
        if isinstance(activite, discord.CustomActivity) and activite.name:
            if texte_recherche.lower() in activite.name.lower():
                return True
    return False


def possede_tag_serveur(membre: discord.Member, guild_id: int) -> bool:
    """Vérifie si le membre a le tag de ce serveur activé sur son profil.

    Fonctionnalité "Server Tag" (liée aux boosts du serveur, niveau 3), exposée
    par discord.py via Member.primary_guild.
    """
    primary_guild = getattr(membre, "primary_guild", None)
    if primary_guild is None:
        return False
    return bool(primary_guild.identity_enabled) and primary_guild.id == guild_id


def construire_embed_accueil_soutien(guild: discord.Guild) -> discord.Embed:
    config_soutien = get_config_soutien(guild.id)
    role = guild.get_role(config_soutien["role_id"]) if config_soutien.get("role_id") else None
    salon = guild.get_channel(config_soutien["salon_id"]) if config_soutien.get("salon_id") else None

    embed = discord.Embed(
        title="💜 Panel de soutien",
        description=(
            "Récompensez les membres qui soutiennent le serveur en mettant un texte précis dans leur "
            "statut personnalisé **ou** en activant le tag du serveur sur leur profil (boost).\n"
            "Une seule des deux conditions suffit pour recevoir le rôle."
        ),
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="Texte à mettre dans le statut",
        value=f"`{config_soutien['texte_statut']}`" if config_soutien.get("texte_statut") else "⚠️ Non configuré",
        inline=False,
    )
    embed.add_field(
        name="Alternative : tag du serveur",
        value="Toujours activée (aucune configuration nécessaire)",
        inline=False,
    )
    embed.add_field(name="Rôle attribué", value=role.mention if role else "⚠️ Non configuré", inline=True)
    embed.add_field(name="Salon du panel", value=salon.mention if salon else "Non configuré", inline=True)
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


# ============================================================
#  VUE PUBLIQUE (bouton persistant "Vérifier mon soutien")
# ============================================================

class VuePublicSoutien(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Vérifier mon soutien",
        style=discord.ButtonStyle.success,
        emoji="💜",
        custom_id="soutien_verifier",
    )
    async def verifier(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "Soutien" = interaction.client.get_cog("Soutien")
        if cog is None:
            await interaction.response.send_message("Système de soutien indisponible pour le moment.", ephemeral=True)
            return
        await cog.verifier_soutien(interaction)


# ============================================================
#  PANEL DE GESTION (&soutien) — configuration par les admins
# ============================================================

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


class ModalTexteStatut(discord.ui.Modal, title="Texte à mettre dans le statut"):
    def __init__(self, guild_id: int, auteur_id: int, texte_actuel: str | None):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.texte = discord.ui.TextInput(
            label="Texte recherché dans le statut personnalisé",
            placeholder="Ex : .gg/monserveur",
            max_length=100,
            default=texte_actuel or "",
        )
        self.add_item(self.texte)

    async def on_submit(self, interaction: discord.Interaction):
        config_soutien = get_config_soutien(self.guild_id)
        config_soutien["texte_statut"] = self.texte.value.strip()
        sauvegarder_config_soutien(self.guild_id, config_soutien)

        embed = construire_embed_accueil_soutien(interaction.guild)
        vue = PanelSoutienConfig(self.guild_id, self.auteur_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class SelectRoleSoutien(discord.ui.RoleSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(placeholder="Rôle à attribuer en cas de soutien détecté", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        if role.is_default() or role.managed:
            await interaction.response.send_message(
                "Impossible d'utiliser @everyone ou un rôle géré automatiquement.", ephemeral=True
            )
            return

        config_soutien = get_config_soutien(self.guild_id)
        config_soutien["role_id"] = role.id
        sauvegarder_config_soutien(self.guild_id, config_soutien)

        embed = construire_embed_accueil_soutien(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


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


class PanelSoutienConfig(PanelBaseSoutien):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectRoleSoutien(guild_id))
        self.add_item(SelectSalonSoutien(guild_id))

    @discord.ui.button(label="Définir le texte du statut", style=discord.ButtonStyle.primary, row=2, emoji="✏️")
    async def definir_texte(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_soutien = get_config_soutien(self.guild_id)
        await interaction.response.send_modal(
            ModalTexteStatut(self.guild_id, self.auteur_id, config_soutien.get("texte_statut"))
        )

    @discord.ui.button(label="Envoyer / Mettre à jour le panel", style=discord.ButtonStyle.success, row=3, emoji="📤")
    async def envoyer(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_soutien = get_config_soutien(self.guild_id)

        if not config_soutien.get("role_id"):
            await interaction.response.send_message("Configurez d'abord un rôle à attribuer.", ephemeral=True)
            return
        if not config_soutien.get("texte_statut"):
            await interaction.response.send_message("Configurez d'abord le texte du statut.", ephemeral=True)
            return
        if not config_soutien.get("salon_id"):
            await interaction.response.send_message("Configurez d'abord un salon.", ephemeral=True)
            return

        salon = interaction.guild.get_channel(config_soutien["salon_id"])
        if salon is None:
            await interaction.response.send_message("Le salon configuré est introuvable.", ephemeral=True)
            return

        embed = discord.Embed(
            title="💜 Soutenez le serveur",
            description=(
                f"Mettez **{config_soutien['texte_statut']}** dans votre statut personnalisé, **ou** activez le "
                "tag de ce serveur sur votre profil (fonctionnalité liée aux boosts), puis cliquez ci-dessous "
                "pour recevoir votre rôle."
            ),
            color=discord.Color.purple(),
        )
        vue_publique = VuePublicSoutien()

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

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=4)
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
        self.bot.add_view(VuePublicSoutien())

    async def verifier_soutien(self, interaction: discord.Interaction) -> None:
        config_soutien = get_config_soutien(interaction.guild.id)
        role_id = config_soutien.get("role_id")
        texte_statut = config_soutien.get("texte_statut")

        if not role_id or not texte_statut:
            await interaction.response.send_message(
                "Le système de soutien n'est pas encore configuré sur ce serveur (contactez le staff).",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                "Le rôle configuré est introuvable (contactez le staff).", ephemeral=True
            )
            return

        membre = interaction.user
        if role in membre.roles:
            await interaction.response.send_message("Vous avez déjà le rôle de soutien !", ephemeral=True)
            return

        statut_ok = statut_contient_texte(membre, texte_statut)
        tag_ok = possede_tag_serveur(membre, interaction.guild.id)

        if not statut_ok and not tag_ok:
            await interaction.response.send_message(
                f"❌ Condition non remplie. Mettez **{texte_statut}** dans votre statut personnalisé, ou activez "
                "le tag de ce serveur sur votre profil, puis réessayez.\n"
                "-# Si vous venez de le faire, patientez quelques secondes le temps que Discord synchronise votre profil.",
                ephemeral=True,
            )
            return

        try:
            await membre.add_roles(role, reason="[Soutien] Condition de soutien remplie")
        except discord.Forbidden:
            await interaction.response.send_message(
                "✅ Condition remplie, mais le bot n'a pas la permission d'attribuer ce rôle. Contactez le staff.",
                ephemeral=True,
            )
            return

        moyen = "le texte de votre statut" if statut_ok else "le tag du serveur sur votre profil"
        await interaction.response.send_message(f"✅ Merci pour votre soutien ! Rôle {role.mention} attribué (via {moyen}).", ephemeral=True)

        await envoyer_log(
            interaction.guild,
            "roles",
            discord.Embed(
                title="💜 Rôle de soutien attribué",
                description=f"{membre.mention} a reçu {role.mention} (via {moyen}).",
                color=discord.Color.purple(),
                timestamp=discord.utils.utcnow(),
            ),
        )

    @commands.command(
        name="soutien",
        aliases=["soutient", "support"],
        help="Ouvre le panel de configuration du système de soutien (statut ou tag du serveur -> rôle).",
    )
    @commands.guild_only()
    @check_admin()
    async def soutien_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil_soutien(ctx.guild)
        vue = PanelSoutienConfig(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=vue)
        vue.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Soutien(bot))
