import asyncio
import uuid

import discord
from discord.ext import commands, tasks

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    check_admin,
)

MAX_STATS = 20  # nombre de compteurs max par serveur (raisonnable, pas de limite Discord stricte ici)
INTERVALLE_MAJ_MINUTES = 10  # Discord limite le renommage d'un salon à ~2 fois / 10 min : on respecte cette contrainte
DELAI_ENTRE_EDITS = 2  # secondes entre deux renommages de salons différents, pour lisser la charge sur l'API

TYPES_STATS = {
    "membres": {"label": "Membres totaux", "defaut": "👥 Membres : {count}", "besoin_role": False},
    "humains": {"label": "Humains (hors bots)", "defaut": "🧑 Humains : {count}", "besoin_role": False},
    "bots": {"label": "Bots", "defaut": "🤖 Bots : {count}", "besoin_role": False},
    "role": {"label": "Membres avec un rôle précis", "defaut": "🎭 {role} : {count}", "besoin_role": True},
    "salons_texte": {"label": "Salons textuels", "defaut": "💬 Salons texte : {count}", "besoin_role": False},
    "salons_vocaux": {"label": "Salons vocaux", "defaut": "🔊 Salons vocaux : {count}", "besoin_role": False},
    "salons_total": {"label": "Salons (total)", "defaut": "📁 Salons : {count}", "besoin_role": False},
    "boosts": {"label": "Boosts du serveur", "defaut": "🚀 Boosts : {count}", "besoin_role": False},
}


# ============================================================
#  CONFIGURATION (stockée dans data/config.json, clé "stats_channels")
# ============================================================

def get_stats(guild_id: int) -> dict:
    config = charger_config()
    return config.get(str(guild_id), {}).get("stats_channels", {})


def sauvegarder_stats(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["stats_channels"] = data
    sauvegarder_config(config)


def get_stat(guild_id: int, stat_id: str) -> dict | None:
    return get_stats(guild_id).get(stat_id)


# ============================================================
#  CALCUL DES VALEURS ET MISE A JOUR DES SALONS
# ============================================================

def calculer_valeur(guild: discord.Guild, info: dict) -> int:
    type_ = info.get("type")
    if type_ == "membres":
        return guild.member_count
    if type_ == "humains":
        return sum(1 for m in guild.members if not m.bot)
    if type_ == "bots":
        return sum(1 for m in guild.members if m.bot)
    if type_ == "role":
        role = guild.get_role(info.get("role_id"))
        return len(role.members) if role is not None else 0
    if type_ == "salons_texte":
        return len(guild.text_channels)
    if type_ == "salons_vocaux":
        return len(guild.voice_channels)
    if type_ == "salons_total":
        return len(guild.channels)
    if type_ == "boosts":
        return guild.premium_subscription_count or 0
    return 0


def construire_nom_salon(guild: discord.Guild, info: dict) -> str:
    valeur = calculer_valeur(guild, info)
    nom = info.get("format") or "{count}"
    nom = nom.replace("{count}", str(valeur))
    if info.get("type") == "role":
        role = guild.get_role(info.get("role_id"))
        nom = nom.replace("{role}", role.name if role else "Rôle supprimé")
    return nom[:100]


async def maj_stats_guild(guild: discord.Guild) -> tuple[int, int]:
    """Met à jour tous les salons stats d'un serveur. Retourne (réussites, échecs)."""
    stats = get_stats(guild.id)
    reussites, echecs = 0, 0
    for info in stats.values():
        salon = guild.get_channel(info.get("salon_id"))
        if salon is None:
            echecs += 1
            continue
        nouveau_nom = construire_nom_salon(guild, info)
        if salon.name == nouveau_nom:
            reussites += 1
            continue
        try:
            await salon.edit(name=nouveau_nom, reason="[Stats] Mise à jour automatique")
            reussites += 1
        except (discord.Forbidden, discord.HTTPException):
            echecs += 1
        await asyncio.sleep(DELAI_ENTRE_EDITS)
    return reussites, echecs


# ============================================================
#  PANEL DE GESTION (&stats) — configuration par les admins
# ============================================================

def construire_embed_accueil_stats(guild: discord.Guild) -> discord.Embed:
    stats = get_stats(guild.id)
    embed = discord.Embed(
        title="📊 Panel de gestion des statistiques",
        description=(
            "Créez des salons vocaux verrouillés qui affichent des statistiques en direct sur votre serveur.\n"
            f"⚠️ Discord limite le renommage d'un salon à ~2 fois par 10 minutes : la mise à jour automatique "
            f"tourne toutes les **{INTERVALLE_MAJ_MINUTES} minutes**."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Statistiques configurées", value=f"{len(stats)} / {MAX_STATS}", inline=True)
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


class PanelBaseStats(discord.ui.View):
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

class SelectStatExistante(discord.ui.Select):
    def __init__(self, guild_id: int, stats: dict):
        options = [
            discord.SelectOption(
                label=TYPES_STATS.get(info.get("type"), {}).get("label", "?")[:100],
                description=(info.get("format") or "")[:100],
                value=stat_id,
            )
            for stat_id, info in list(stats.items())[:25]
        ]
        super().__init__(placeholder="Gérer une statistique existante...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        view: MenuPrincipalStats = self.view
        stat_id = self.values[0]
        embed = construire_embed_stat_detail(interaction.guild, self.guild_id, stat_id)
        vue = PanelStatDetail(self.guild_id, view.auteur_id, stat_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class MenuPrincipalStats(PanelBaseStats):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        stats = get_stats(guild_id)
        if stats:
            self.add_item(SelectStatExistante(guild_id, stats))

    @discord.ui.button(label="Ajouter une statistique", style=discord.ButtonStyle.success, row=1, emoji="➕")
    async def ajouter(self, interaction: discord.Interaction, button: discord.ui.Button):
        stats = get_stats(self.guild_id)
        if len(stats) >= MAX_STATS:
            await interaction.response.send_message(f"Limite de {MAX_STATS} statistiques atteinte.", ephemeral=True)
            return
        embed = discord.Embed(
            title="➕ Nouvelle statistique",
            description="Choisissez le type de statistique à suivre.",
            color=discord.Color.blurple(),
        )
        vue = VueChoixType(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Actualiser maintenant", style=discord.ButtonStyle.primary, row=1, emoji="🔄")
    async def actualiser(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        reussites, echecs = await maj_stats_guild(interaction.guild)
        await interaction.followup.send(
            f"✅ {reussites} salon(s) mis à jour, {echecs} échec(s) (salon supprimé ou permissions manquantes).",
            ephemeral=True,
        )

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=2)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# --- ECRAN 2 : CHOIX DU TYPE (puis rôle si besoin) ---

class SelectTypeStat(discord.ui.Select):
    def __init__(self, guild_id: int):
        options = [
            discord.SelectOption(label=info["label"], value=type_id)
            for type_id, info in TYPES_STATS.items()
        ]
        super().__init__(placeholder="Choisir un type de statistique...", options=options, min_values=1, max_values=1)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        type_id = self.values[0]
        if TYPES_STATS[type_id]["besoin_role"]:
            embed = discord.Embed(
                title="➕ Nouvelle statistique",
                description="Choisissez le rôle à compter.",
                color=discord.Color.blurple(),
            )
            view: VueChoixType = self.view
            vue = VueChoixRole(self.guild_id, view.auteur_id, type_id)
            vue.message = interaction.message
            await interaction.response.edit_message(embed=embed, view=vue)
        else:
            await interaction.response.send_modal(ModalFormatStat(self.guild_id, type_id, None))


class VueChoixType(PanelBaseStats):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectTypeStat(guild_id))

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_stats(interaction.guild)
        vue = MenuPrincipalStats(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class SelectRoleStat(discord.ui.RoleSelect):
    def __init__(self, guild_id: int, type_id: str):
        self.guild_id = guild_id
        self.type_id = type_id
        super().__init__(placeholder="Choisir le rôle à compter", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await interaction.response.send_modal(ModalFormatStat(self.guild_id, self.type_id, role.id))


class VueChoixRole(PanelBaseStats):
    def __init__(self, guild_id: int, auteur_id: int, type_id: str):
        super().__init__(guild_id, auteur_id)
        self.type_id = type_id
        self.add_item(SelectRoleStat(guild_id, type_id))

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="➕ Nouvelle statistique",
            description="Choisissez le type de statistique à suivre.",
            color=discord.Color.blurple(),
        )
        vue = VueChoixType(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class ModalFormatStat(discord.ui.Modal, title="Format d'affichage"):
    def __init__(self, guild_id: int, type_id: str, role_id: int | None):
        super().__init__()
        self.guild_id = guild_id
        self.type_id = type_id
        self.role_id = role_id

        defaut = TYPES_STATS[type_id]["defaut"]
        placeholder_aide = "{count} sera remplacé par le nombre" + (" — {role} par le nom du rôle" if role_id else "")

        self.format = discord.ui.TextInput(
            label="Nom du salon",
            max_length=90,
            default=defaut,
            placeholder=placeholder_aide,
        )
        self.add_item(self.format)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        stats = get_stats(self.guild_id)
        stat_id = uuid.uuid4().hex[:8]
        info = {
            "type": self.type_id,
            "role_id": self.role_id,
            "format": self.format.value.strip() or TYPES_STATS[self.type_id]["defaut"],
            "salon_id": None,
            "categorie_id": None,
        }

        nom_initial = construire_nom_salon(guild, info)

        try:
            salon = await guild.create_voice_channel(
                name=nom_initial,
                overwrites={guild.default_role: discord.PermissionOverwrite(connect=False)},
                reason="[Stats] Création d'un salon de statistique",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Permissions insuffisantes pour créer un salon vocal.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"Erreur Discord lors de la création du salon : {e}", ephemeral=True)
            return

        info["salon_id"] = salon.id
        stats[stat_id] = info
        sauvegarder_stats(self.guild_id, stats)

        embed = construire_embed_stat_detail(guild, self.guild_id, stat_id)
        vue = PanelStatDetail(self.guild_id, interaction.user.id, stat_id)
        await interaction.response.edit_message(embed=embed, view=vue)


# --- ECRAN 3 : DETAIL D'UNE STATISTIQUE ---

def construire_embed_stat_detail(guild: discord.Guild, guild_id: int, stat_id: str) -> discord.Embed:
    info = get_stat(guild_id, stat_id) or {}
    type_label = TYPES_STATS.get(info.get("type"), {}).get("label", "?")
    salon = guild.get_channel(info.get("salon_id")) if info.get("salon_id") else None
    role = guild.get_role(info.get("role_id")) if info.get("role_id") else None
    categorie = guild.get_channel(info.get("categorie_id")) if info.get("categorie_id") else None

    embed = discord.Embed(title=f"📊 {type_label}", color=discord.Color.blurple())
    embed.add_field(name="Salon", value=salon.mention if salon else "⚠️ Salon introuvable (supprimé ?)", inline=True)
    embed.add_field(name="Catégorie", value=categorie.mention if categorie else "Aucune", inline=True)
    if role is not None:
        embed.add_field(name="Rôle compté", value=role.mention, inline=True)
    embed.add_field(name="Format", value=f"`{info.get('format', '')}`", inline=False)
    if guild is not None:
        embed.add_field(name="Aperçu actuel", value=construire_nom_salon(guild, info), inline=False)
    embed.set_footer(text=f"ID interne : {stat_id}")
    return embed


class SelectCategorieStat(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int, stat_id: str):
        self.guild_id = guild_id
        self.stat_id = stat_id
        super().__init__(
            placeholder="Déplacer le salon dans une catégorie",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        stats = get_stats(self.guild_id)
        info = stats.get(self.stat_id)
        if info is None:
            await interaction.response.send_message("Cette statistique n'existe plus.", ephemeral=True)
            return

        categorie = self.values[0]
        info["categorie_id"] = categorie.id
        sauvegarder_stats(self.guild_id, stats)

        salon = interaction.guild.get_channel(info.get("salon_id"))
        if salon is not None:
            try:
                await salon.edit(category=categorie, reason="[Stats] Déplacement de catégorie")
            except (discord.Forbidden, discord.HTTPException):
                pass

        embed = construire_embed_stat_detail(interaction.guild, self.guild_id, self.stat_id)
        await interaction.response.edit_message(embed=embed, view=self.view)


class ModalModifierFormat(discord.ui.Modal, title="Modifier le format d'affichage"):
    def __init__(self, guild_id: int, auteur_id: int, stat_id: str, info: dict):
        super().__init__()
        self.guild_id = guild_id
        self.auteur_id = auteur_id
        self.stat_id = stat_id
        self.format = discord.ui.TextInput(label="Nom du salon", max_length=90, default=info.get("format", ""))
        self.add_item(self.format)

    async def on_submit(self, interaction: discord.Interaction):
        stats = get_stats(self.guild_id)
        info = stats.get(self.stat_id)
        if info is None:
            await interaction.response.send_message("Cette statistique n'existe plus.", ephemeral=True)
            return

        info["format"] = self.format.value.strip() or info.get("format", "")
        sauvegarder_stats(self.guild_id, stats)

        salon = interaction.guild.get_channel(info.get("salon_id"))
        if salon is not None:
            try:
                await salon.edit(name=construire_nom_salon(interaction.guild, info), reason="[Stats] Format modifié")
            except (discord.Forbidden, discord.HTTPException):
                pass

        embed = construire_embed_stat_detail(interaction.guild, self.guild_id, self.stat_id)
        vue = PanelStatDetail(self.guild_id, self.auteur_id, self.stat_id)
        await interaction.response.edit_message(embed=embed, view=vue)


class PanelStatDetail(PanelBaseStats):
    def __init__(self, guild_id: int, auteur_id: int, stat_id: str):
        super().__init__(guild_id, auteur_id)
        self.stat_id = stat_id
        self.add_item(SelectCategorieStat(guild_id, stat_id))

    @discord.ui.button(label="Modifier le format", style=discord.ButtonStyle.primary, row=2, emoji="✏️")
    async def modifier(self, interaction: discord.Interaction, button: discord.ui.Button):
        info = get_stat(self.guild_id, self.stat_id) or {}
        await interaction.response.send_modal(
            ModalModifierFormat(self.guild_id, self.auteur_id, self.stat_id, info)
        )

    @discord.ui.button(label="Actualiser maintenant", style=discord.ButtonStyle.secondary, row=2, emoji="🔄")
    async def actualiser(self, interaction: discord.Interaction, button: discord.ui.Button):
        info = get_stat(self.guild_id, self.stat_id)
        if info is None:
            await interaction.response.send_message("Cette statistique n'existe plus.", ephemeral=True)
            return

        salon = interaction.guild.get_channel(info.get("salon_id"))
        if salon is None:
            await interaction.response.send_message("Le salon associé est introuvable.", ephemeral=True)
            return

        try:
            await salon.edit(name=construire_nom_salon(interaction.guild, info), reason="[Stats] Actualisation manuelle")
        except discord.Forbidden:
            await interaction.response.send_message("Permissions insuffisantes pour renommer ce salon.", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord a refusé la mise à jour (limite de renommage atteinte, réessayez dans quelques minutes).",
                ephemeral=True,
            )
            return

        embed = construire_embed_stat_detail(interaction.guild, self.guild_id, self.stat_id)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Supprimer cette statistique", style=discord.ButtonStyle.danger, row=3, emoji="🗑️")
    async def supprimer(self, interaction: discord.Interaction, button: discord.ui.Button):
        stats = get_stats(self.guild_id)
        info = stats.pop(self.stat_id, None)
        sauvegarder_stats(self.guild_id, stats)

        if info is not None:
            salon = interaction.guild.get_channel(info.get("salon_id"))
            if salon is not None:
                try:
                    await salon.delete(reason="[Stats] Statistique supprimée")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        embed = construire_embed_accueil_stats(interaction.guild)
        vue = MenuPrincipalStats(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=4)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_stats(interaction.guild)
        vue = MenuPrincipalStats(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# ============================================================
#  COG
# ============================================================

class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tache_maj_stats.start()

    def cog_unload(self):
        self.tache_maj_stats.cancel()

    @tasks.loop(minutes=INTERVALLE_MAJ_MINUTES)
    async def tache_maj_stats(self):
        config = charger_config()
        for guild_id_str, data in config.items():
            if not data.get("stats_channels"):
                continue
            try:
                guild_id = int(guild_id_str)
            except ValueError:
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            await maj_stats_guild(guild)

    @tache_maj_stats.before_loop
    async def avant_tache(self):
        await self.bot.wait_until_ready()

    @commands.command(
        name="stats",
        aliases=["statistiques"],
        help="Ouvre le panel de gestion des salons de statistiques (membres, rôles, boosts, salons...).",
    )
    @commands.guild_only()
    @check_admin()
    async def stats_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil_stats(ctx.guild)
        vue = MenuPrincipalStats(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=vue)
        vue.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot))
