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
    set_roles_staff,
    check_owner,
)
from commandes.help import determiner_niveau, NIVEAUX, EMOJI_PAR_DEFAUT, LABEL_PAR_DEFAUT


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
        value=f"{_formater_roles(guild, get_roles_staff(guild.id))}\n*Géré ici ou via `&setup` → Staff (ce serveur uniquement).*",
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


class SelectCategoriePermission(discord.ui.Select):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label="Gérant", value="gerant", emoji="🟠",
                description="Rôles gérant supplémentaires (tous serveurs)",
            ),
            discord.SelectOption(
                label="Bypass", value="bypass", emoji="🟣",
                description="Rôles bypass supplémentaires (tous serveurs)",
            ),
            discord.SelectOption(
                label="Staff", value="staff", emoji="🟢",
                description="Rôles staff de ce serveur",
            ),
        ]
        super().__init__(placeholder="Modifier les rôles d'une catégorie...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        categorie = self.values[0]
        embed = construire_embed_gestion(interaction.guild, categorie)
        vue = PanelGestionRoles(self.guild_id, self.view.auteur_id, categorie)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


class MenuPrincipalPerms(PanelBasePerms):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectCategoriePermission(guild_id))

    @discord.ui.button(label="Commandes ↔ Permissions", style=discord.ButtonStyle.primary, row=1, emoji="🗂️")
    async def voir_commandes(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_commandes_par_permission(interaction.client)
        vue = PanelCommandesPermissions(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.secondary, row=1, emoji="🔄")
    async def actualiser(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_perms(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, row=2)
    async def fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# --- ECRAN "QUELLE PERMISSION FAIT QUELLE COMMANDE" ---
# Réutilise directement la logique de détection de commandes/help.py plutôt
# que de la dupliquer : une seule source de vérité pour les niveaux requis.

def construire_embed_commandes_par_permission(bot: commands.Bot) -> discord.Embed:
    embed = discord.Embed(
        title="🗂️ Commandes par niveau de permission",
        description="Quelle permission est nécessaire pour chaque commande du bot.",
        color=discord.Color.gold(),
    )

    groupes: dict[tuple[str, str], list[commands.Command]] = {}
    for commande in bot.commands:
        niveau = determiner_niveau(commande)
        groupes.setdefault(niveau, []).append(commande)

    ordre = [(emoji, label) for _, emoji, label in NIVEAUX] + [(EMOJI_PAR_DEFAUT, LABEL_PAR_DEFAUT)]
    for emoji, label in ordre:
        commandes_du_niveau = groupes.get((emoji, label))
        if not commandes_du_niveau:
            continue
        noms = ", ".join(f"`&{c.name}`" for c in sorted(commandes_du_niveau, key=lambda c: c.name))
        embed.add_field(name=f"{emoji} {label}", value=noms, inline=False)

    embed.set_footer(text="Inclut les commandes cachées (visibles uniquement par vous en tant qu'owner).")
    return embed


class PanelCommandesPermissions(PanelBasePerms):
    @discord.ui.button(label="Retour", style=discord.ButtonStyle.secondary, row=0)
    async def retour(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = construire_embed_accueil_perms(interaction.guild)
        vue = MenuPrincipalPerms(self.guild_id, self.auteur_id)
        vue.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=vue)


# --- ECRAN DE GESTION D'UNE CATEGORIE (gérant, bypass ou staff) ---

INFOS_CATEGORIE = {
    "gerant": {
        "titre": "🟠 Rôles Gérant supplémentaires",
        "portee": "Tous les serveurs où le bot est présent",
        "getter": lambda guild_id: get_roles_gerant_extra(),
        "setter": lambda guild_id, roles: set_roles_gerant_extra(roles),
        "fixes": lambda guild_id: _roles_gerant_fixes(),
    },
    "bypass": {
        "titre": "🟣 Rôles Bypass supplémentaires",
        "portee": "Tous les serveurs où le bot est présent",
        "getter": lambda guild_id: get_roles_bypass_extra(),
        "setter": lambda guild_id, roles: set_roles_bypass_extra(roles),
        "fixes": lambda guild_id: _roles_bypass_fixes(),
    },
    "staff": {
        "titre": "🟢 Rôles Staff",
        "portee": "Ce serveur uniquement",
        "getter": lambda guild_id: get_roles_staff(guild_id),
        "setter": lambda guild_id, roles: set_roles_staff(guild_id, roles),
        "fixes": lambda guild_id: [],
    },
}


def construire_embed_gestion(guild: discord.Guild, categorie: str) -> discord.Embed:
    infos = INFOS_CATEGORIE[categorie]
    fixes = infos["fixes"](guild.id)

    bloc_fixe = (
        f"**Base fixe (.env, non modifiable ici) :**\n{_formater_roles(guild, fixes)}\n\n"
        if fixes or categorie != "staff"
        else ""
    )

    embed = discord.Embed(
        title=infos["titre"],
        description=(
            "Sélectionnez l'ensemble complet des rôles souhaités (remplace la sélection précédente).\n"
            f"⚠️ Portée : *{infos['portee']}*.\n\n"
            f"{bloc_fixe}"
            f"**Actuellement ajoutés via ce panel :**\n{_formater_roles(guild, infos['getter'](guild.id))}"
        ),
        color=discord.Color.gold(),
    )
    return embed


class SelectRolesSupplementaires(discord.ui.RoleSelect):
    def __init__(self, guild_id: int, categorie: str):
        self.guild_id = guild_id
        self.categorie = categorie
        super().__init__(
            placeholder="Choisir les rôles (remplace la liste actuelle)",
            min_values=0,
            max_values=10,
        )

    async def callback(self, interaction: discord.Interaction):
        infos = INFOS_CATEGORIE[self.categorie]

        roles_valides = [r for r in self.values if not r.is_default() and not r.managed]
        infos["setter"](self.guild_id, [r.id for r in roles_valides])

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
        self.add_item(SelectRolesSupplementaires(guild_id, categorie))

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
        help="Vue d'ensemble des permissions, gestion des rôles Gérant/Bypass/Staff, et mapping commandes ↔ permissions (owner uniquement).",
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
