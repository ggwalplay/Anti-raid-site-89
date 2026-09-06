import re

import aiohttp
import discord
from discord.ext import commands

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    check_admin,
)

REGEX_LIEN_ROBLOX = re.compile(r"roblox\.com/(?:users/)?(\d+)")


# ============================================================
#  CONFIGURATION (stockée dans data/config.json, clé "roblox")
# ============================================================

def get_config_roblox(guild_id: int) -> dict:
    config = charger_config()
    return config.get(str(guild_id), {}).get(
        "roblox", {"salon_id": None, "panel_message_id": None, "entries_message_ids": [], "comptes": {}}
    )


def sauvegarder_config_roblox(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["roblox"] = data
    sauvegarder_config(config)


# ============================================================
#  APPEL A L'API ROBLOX (récupération pseudo + image du skin)
# ============================================================

async def recuperer_profil_roblox(entree: str) -> dict | None:
    """À partir d'un lien de profil, d'un ID ou d'un pseudo Roblox, renvoie
    {"roblox_id", "pseudo", "avatar_url", "lien"} ou None si introuvable."""
    entree = entree.strip()
    roblox_id: int | None = None

    if entree.isdigit():
        roblox_id = int(entree)
    else:
        correspondance = REGEX_LIEN_ROBLOX.search(entree)
        if correspondance:
            roblox_id = int(correspondance.group(1))

    async with aiohttp.ClientSession() as session:
        if roblox_id is None:
            # Traité comme un pseudo : on résout l'ID via l'API Roblox
            try:
                async with session.post(
                    "https://users.roblox.com/v1/usernames/users",
                    json={"usernames": [entree], "excludeBannedUsers": False},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    resultats = data.get("data") or []
                    if not resultats:
                        return None
                    roblox_id = resultats[0]["id"]
                    pseudo = resultats[0]["name"]
            except (aiohttp.ClientError, TimeoutError):
                return None
        else:
            # On a un ID : on récupère le pseudo associé
            try:
                async with session.get(
                    f"https://users.roblox.com/v1/users/{roblox_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    pseudo = data.get("name")
                    if not pseudo:
                        return None
            except (aiohttp.ClientError, TimeoutError):
                return None

        # Image du skin (avatar complet, pas juste le visage)
        avatar_url = None
        try:
            async with session.get(
                "https://thumbnails.roblox.com/v1/users/avatar",
                params={"userIds": roblox_id, "size": "420x420", "format": "Png", "isCircular": "false"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    resultats = data.get("data") or []
                    if resultats:
                        avatar_url = resultats[0].get("imageUrl")
        except (aiohttp.ClientError, TimeoutError):
            pass

    return {
        "roblox_id": roblox_id,
        "pseudo": pseudo,
        "avatar_url": avatar_url,
        "lien": f"https://www.roblox.com/users/{roblox_id}/profile",
    }


# ============================================================
#  CONSTRUCTION DE LA LISTE (bannière + sections par grade + cartes)
# ============================================================

LIGNE_FINE = "─" * 32


def _couleur_role(role: discord.Role) -> discord.Color:
    return role.color if role.color != discord.Color.default() else discord.Color.from_rgb(47, 49, 54)


def construire_embed_entete(guild: discord.Guild, nb_membres: int) -> discord.Embed:
    embed = discord.Embed(
        title="TROMBINOSCOPE  ·  ROBLOX",
        description=(
            f"{LIGNE_FINE}\n"
            f"**{nb_membres}** compte(s) enregistré(s) sur **{guild.name}**\n"
            f"Classement par grade, du plus élevé au plus bas."
        ),
        color=discord.Color.from_rgb(47, 49, 54),
        timestamp=discord.utils.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Mise à jour automatique")
    return embed


def construire_embed_section(role: discord.Role, nb_membres: int) -> discord.Embed:
    embed = discord.Embed(
        description=f"### {role.name.upper()}\n{LIGNE_FINE}\n{nb_membres} membre(s)",
        color=_couleur_role(role),
    )
    return embed


def construire_embed_compte(membre: discord.Member, compte: dict, rang: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"{rang:02d}   ·   {compte.get('pseudo', '?')}",
        url=compte.get("lien"),
        description=f"{LIGNE_FINE}\n[Voir le profil Roblox]({compte.get('lien')})",
        color=_couleur_role(membre.top_role),
    )
    embed.set_author(name=membre.display_name, icon_url=membre.display_avatar.url)
    embed.add_field(name="DISCORD", value=membre.mention, inline=True)
    embed.add_field(name="GRADE", value=membre.top_role.mention, inline=True)
    if compte.get("avatar_url"):
        embed.set_image(url=compte["avatar_url"])
    embed.set_footer(text=f"ID Roblox — {compte.get('roblox_id', '?')}")
    return embed


async def actualiser_liste(guild: discord.Guild) -> None:
    """Supprime l'ancien affichage et reposte tout : bannière, puis une section
    par grade (couleur du rôle) contenant les cartes triées, et le panel en bas."""
    config_roblox = get_config_roblox(guild.id)
    salon = guild.get_channel(config_roblox.get("salon_id")) if config_roblox.get("salon_id") else None
    if salon is None:
        return

    # Supprimer les anciens messages (bannière + sections + cartes + panel)
    anciens_ids = list(config_roblox.get("entries_message_ids", []))
    if config_roblox.get("panel_message_id"):
        anciens_ids.append(config_roblox["panel_message_id"])
    for message_id in anciens_ids:
        try:
            message = await salon.fetch_message(message_id)
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    # Nettoyer les comptes dont le membre a quitté le serveur
    comptes = config_roblox.get("comptes", {})
    entrees_valides = []
    for discord_id, compte in list(comptes.items()):
        membre = guild.get_member(int(discord_id))
        if membre is None:
            comptes.pop(discord_id, None)
            continue
        entrees_valides.append((membre, compte))

    # Tri par position de rôle la plus haute, du plus haut au plus bas
    entrees_valides.sort(key=lambda paire: paire[0].top_role.position, reverse=True)

    nouveaux_ids = []

    banniere = await salon.send(embed=construire_embed_entete(guild, len(entrees_valides)))
    nouveaux_ids.append(banniere.id)

    role_section_courante = None
    rang = 0
    for membre, compte in entrees_valides:
        rang += 1
        if membre.top_role != role_section_courante:
            role_section_courante = membre.top_role
            nb_dans_section = sum(1 for m, _ in entrees_valides if m.top_role == role_section_courante)
            message_section = await salon.send(embed=construire_embed_section(role_section_courante, nb_dans_section))
            nouveaux_ids.append(message_section.id)

        message = await salon.send(embed=construire_embed_compte(membre, compte, rang))
        nouveaux_ids.append(message.id)

    panel_message = await salon.send(
        embed=discord.Embed(
            title="ENREGISTRER UN COMPTE",
            description=(
                f"{LIGNE_FINE}\n"
                "Utilise le bouton ci-dessous pour ajouter ou mettre à jour "
                "ton compte Roblox dans le classement ci-dessus.\n\n"
                "Ton grade est déterminé automatiquement par ton rôle Discord le plus élevé."
            ),
            color=discord.Color.from_rgb(47, 49, 54),
        ).set_thumbnail(url=guild.icon.url if guild.icon else None),
        view=VuePubliqueRoblox(),
    )

    config_roblox["comptes"] = comptes
    config_roblox["entries_message_ids"] = nouveaux_ids
    config_roblox["panel_message_id"] = panel_message.id
    sauvegarder_config_roblox(guild.id, config_roblox)


# ============================================================
#  FLUX PUBLIC : LE MEMBRE ENREGISTRE SON COMPTE (ephemeral)
# ============================================================

class ModalRoblox(discord.ui.Modal, title="Enregistrer mon compte Roblox"):
    entree = discord.ui.TextInput(
        label="Lien de ton profil, ID ou pseudo Roblox",
        placeholder="https://www.roblox.com/users/123456789/profile",
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        profil = await recuperer_profil_roblox(self.entree.value)
        if profil is None:
            await interaction.followup.send(
                "Compte Roblox introuvable. Vérifie le lien, l'ID ou le pseudo et réessaie.",
                ephemeral=True,
            )
            return

        config_roblox = get_config_roblox(interaction.guild.id)
        if config_roblox.get("salon_id") is None:
            await interaction.followup.send(
                "La liste n'est pas configurée sur ce serveur (contacte un admin).", ephemeral=True
            )
            return

        config_roblox.setdefault("comptes", {})[str(interaction.user.id)] = {
            "roblox_id": profil["roblox_id"],
            "pseudo": profil["pseudo"],
            "avatar_url": profil["avatar_url"],
            "lien": profil["lien"],
        }
        sauvegarder_config_roblox(interaction.guild.id, config_roblox)

        await actualiser_liste(interaction.guild)

        await interaction.followup.send(
            f"Ton compte **{profil['pseudo']}** a été ajouté/mis à jour dans le classement.", ephemeral=True
        )


class VuePubliqueRoblox(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Enregistrer mon compte",
        style=discord.ButtonStyle.secondary,
        custom_id="roblox:inscription",
    )
    async def inscription(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModalRoblox())


# ============================================================
#  COG
# ============================================================

class Roblox(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._vue_enregistree = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._vue_enregistree:
            return
        self._vue_enregistree = True
        self.bot.add_view(VuePubliqueRoblox())

    @commands.command(
        name="robloxpanel",
        help="Installe (ou réinstalle) la liste des comptes Roblox dans le salon actuel.",
    )
    @commands.guild_only()
    @check_admin()
    async def robloxpanel(self, ctx: commands.Context):
        config_roblox = get_config_roblox(ctx.guild.id)
        config_roblox["salon_id"] = ctx.channel.id
        sauvegarder_config_roblox(ctx.guild.id, config_roblox)

        await actualiser_liste(ctx.guild)
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Roblox(bot))
