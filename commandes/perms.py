import os

import discord
from discord.ext import commands

from commandes._permissions import (
    get_owner_ids,
    parse_ids_env,
    get_roles_gerant_extra,
    set_roles_gerant_extra,
    get_roles_bypass_extra,
    set_roles_bypass_extra,
    get_roles_staff,
    check_owner,
)


def _roles_gerant_fixes() -> list[int]:
    return parse_ids_env(os.getenv("GERANT", ""))


def _roles_bypass_fixes() -> list[int]:
    return parse_ids_env(os.getenv("BYPASS", ""))


def _formater_roles(guild: discord.Guild, ids: list[int]) -> str:
    if not ids:
        return "Aucun"
    return ", ".join(f"<@&{r}>" for r in ids)


# ============================================================
#  PANEL DE GESTION (&perms) — owner uniquement
# ============================================================

def _formater_categorie(guild: discord.Guild, fixes: list[int], extras: list[int]) -> str:
    lignes = [f"**Fixe (.env) :** {_formater_roles(guild, fixes)}"]
    lignes.append(f"**Ajoutés via ce panel :** {_formater_roles(guild, extras)}")
    return "\n".join(lignes)


def construire_embed_accueil_perms(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🔑 Gestion des permissions",
        description=(
            "Vue d'ensemble de la hiérarchie de permissions du bot.\n"
            "⚠️ Les rôles **Gérant** et **Bypass** s'appliquent sur *tous les serveurs* où le bot est présent."
        ),
        color=discord.Color.gold(),
    )

    owners = ", ".join(f"<@{o}>" for o in get_owner_ids()) or "Aucun"
    embed.add_field(
        name="🔴  Owner",
        value=f"{owners}\n*Non modifiable ici — nécessite un accès au `.env` du serveur.*",
        inline=False,
    )
    embed.add_field(
        name="🟠  Gérant",
        value=_formater_categorie(guild, _roles_gerant_fixes(), get_roles_gerant_extra()),
        inline=False,
    )
    embed.add_field(
        name="🟣  Bypass  *(= gérant automatique)*",
        value=_formater_categorie(guild, _roles_bypass_fixes(), get_roles_bypass_extra()),
        inline=False,
    )
    embed.add_field(
        name="🟢  Staff",
        value=f"{_formater_roles(guild, get_roles_staff(guild.id))}\n*Géré via `&setup` → Staff (ce serveur uniquement).*",
        inline=False,
    )

    embed.set_footer(text=f"Serveur affiché : {guild.name}")
    return embed


class PanelBasePerms(discord.ui.View):
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


class MenuPrincipalPerms(PanelBasePerms):
    @discord.ui.button(label="Gérer Gérant", style=discord.ButtonStyle.primary, row=0, emoji="🟠")
    async def gerer_gerant(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_gestion(interaction.guild, "gerant")
        vue = PanelGestionRoles(self.guild_id, self.auteur_id, "gerant")
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Gérer Bypass", style=discord.ButtonStyle.primary, row=0, emoji="🟣")
    async def gerer_bypass(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_gestion(interaction.guild, "bypass")
        vue = PanelGestionRoles(self.guild_id, self.auteur_id, "bypass")
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, row=0, emoji="🔄")
    async def actualiser(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_perms(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=1)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# --- ECRAN DE GESTION D'UNE CATEGORIE (gérant ou bypass) ---

INFOS_CATEGORIE = {
    "gerant": {
        "titre": "🟠 Rôles Gérant supplémentaires",
        "getter": get_roles_gerant_extra,
        "setter": set_roles_gerant_extra,
        "fixes": _roles_gerant_fixes,
    },
    "bypass": {
        "titre": "🟣 Rôles Bypass supplémentaires",
        "getter": get_roles_bypass_extra,
        "setter": set_roles_bypass_extra,
        "fixes": _roles_bypass_fixes,
    },
}


def construire_embed_gestion(guild: discord.Guild, categorie: str) -> discord.Embed:
    infos = INFOS_CATEGORIE[categorie]
    embed = discord.Embed(
        title=infos["titre"],
        description=(
            "Sélectionnez l'ensemble complet des rôles supplémentaires souhaités "
            "(remplace la sélection précédente).\n"
            "⚠️ S'applique sur *tous les serveurs* où le bot est présent.\n\n"
            f"**Base fixe (.env, non modifiable ici) :**\n{_formater_roles(guild, infos['fixes']())}\n\n"
            f"**Actuellement ajoutés via ce panel :**\n{_formater_roles(guild, infos['getter']())}"
        ),
        color=discord.Color.gold(),
    )
    return embed


class SelectRolesSupplementaires(discord.ui.RoleSelect):
    def __init__(self, categorie: str):
        self.categorie = categorie
        super().__init__(
            placeholder="Choisir les rôles supplémentaires (remplace la liste actuelle)",
            min_values=0,
            max_values=10,
        )

    async def callback(self, interaction: discord.Interaction):
        infos = INFOS_CATEGORIE[self.categorie]

        roles_valides = [r for r in self.values if not r.is_default() and not r.managed]
        infos["setter"]([r.id for r in roles_valides])

        embed = construire_embed_gestion(interaction.guild, self.categorie)
        await interaction.response.edit_message(embed=embed, view=self.view)

        if len(roles_valides) != len(self.values):
            await interaction.followup.send(
                "@everyone et les rôles gérés automatiquement (bot/intégration) ont été ignorés.", ephemeral=True
            )


class PanelGestionRoles(PanelBasePerms):
    def __init__(self, guild_id: int, auteur_id: int, categorie: str):
        super().__init__(guild_id, auteur_id)
        self.categorie = categorie
        self.add_item(SelectRolesSupplementaires(categorie))

    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=1)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_perms(interaction.guild)
        vue = MenuPrincipalPerms(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# ============================================================
#  COG
# ============================================================

class Permissions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="perms",
        aliases=["permissions"],
        help="Vue d'ensemble et gestion des rôles Gérant/Bypass supplémentaires (réservé au(x) owner(s)).",
    )
    @commands.guild_only()
    @check_owner()
    async def perms_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil_perms(ctx.guild)
        vue = MenuPrincipalPerms(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=vue)
        vue.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Permissions(bot))
