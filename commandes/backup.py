import io
import json
import gzip
import discord
from datetime import datetime, timezone
from discord.ext import commands

from commandes._permissions import check_owner
from commandes.anti_raid import CREATEURS_SALON

# Limite volontairement prudente : la limite d'upload en MP pour un bot
# (hors boost serveur, qui ne s'applique pas aux MP) tourne autour de 8-10 Mo.
LIMITE_OCTETS = 7 * 1024 * 1024  # 7 Mo


def _permissions_actives(permissions: discord.Permissions) -> list[str]:
    return [nom for nom, valeur in permissions if valeur]


def _construire_backup(guild: discord.Guild, auteur: discord.Member) -> dict:
    serveur = {
        "id": str(guild.id),
        "nom": guild.name,
        "description": guild.description,
        "proprietaire_id": str(guild.owner_id) if guild.owner_id else None,
        "nombre_membres": guild.member_count,
        "niveau_verification": str(guild.verification_level),
        "filtre_contenu_explicite": str(guild.explicit_content_filter),
        "notifications_defaut": str(guild.default_notifications),
        "salon_afk_id": str(guild.afk_channel.id) if guild.afk_channel else None,
        "delai_afk": guild.afk_timeout,
        "niveau_boost": guild.premium_tier,
        "nombre_boosts": guild.premium_subscription_count,
        "locale": str(guild.preferred_locale) if guild.preferred_locale else None,
        "fonctionnalites": list(guild.features),
        "icone_url": str(guild.icon.url) if guild.icon else None,
        "banniere_url": str(guild.banner.url) if guild.banner else None,
    }

    roles = []
    for role in guild.roles:
        roles.append({
            "id": str(role.id),
            "nom": role.name,
            "couleur": str(role.color),
            "position": role.position,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "gere_par_integration": role.managed,
            "permissions_bitfield": role.permissions.value,
            "permissions": _permissions_actives(role.permissions),
        })

    salons = []
    for channel in guild.channels:
        info = {
            "id": str(channel.id),
            "nom": channel.name,
            "type": str(channel.type),
            "position": channel.position,
            "categorie_id": str(channel.category_id) if channel.category_id else None,
        }

        if isinstance(channel, discord.TextChannel):
            info["topic"] = channel.topic
            info["nsfw"] = channel.nsfw
            info["slowmode_delay"] = channel.slowmode_delay
        elif isinstance(channel, discord.VoiceChannel):
            info["bitrate"] = channel.bitrate
            info["user_limit"] = channel.user_limit

        overwrites = []
        for cible, overwrite in channel.overwrites.items():
            allow, deny = overwrite.pair()
            overwrites.append({
                "cible_id": str(cible.id),
                "cible_type": "role" if isinstance(cible, discord.Role) else "membre",
                "cible_nom": getattr(cible, "name", str(cible)),
                "allow_bitfield": allow.value,
                "deny_bitfield": deny.value,
                "allow": _permissions_actives(allow),
                "deny": _permissions_actives(deny),
            })
        info["overwrites"] = overwrites

        salons.append(info)

    emojis = [
        {
            "id": str(e.id),
            "nom": e.name,
            "animated": e.animated,
            "url": str(e.url),
        }
        for e in guild.emojis
    ]

    return {
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "genere_par": f"{auteur} ({auteur.id})",
        "serveur": serveur,
        "roles": roles,
        "salons": salons,
        "emojis": emojis,
    }


async def _envoyer_en_pieces(destinataire: discord.abc.Messageable, json_texte: str, nom_base: str) -> None:
    """Envoie le JSON en pièce jointe (jamais dans le contenu du message, à cause
    de la limite de 2000 caractères de Discord), avec repli compression/découpage
    si le fichier dépasse la limite de taille d'upload."""
    brut = json_texte.encode("utf-8")

    if len(brut) <= LIMITE_OCTETS:
        fichier = discord.File(io.BytesIO(brut), filename=f"{nom_base}.json")
        await destinataire.send(content="📦 Sauvegarde du serveur (JSON).", file=fichier)
        return

    compresse = gzip.compress(brut)

    if len(compresse) <= LIMITE_OCTETS:
        fichier = discord.File(io.BytesIO(compresse), filename=f"{nom_base}.json.gz")
        await destinataire.send(
            content=(
                "📦 Sauvegarde du serveur (fichier trop volumineux pour du JSON brut, "
                "envoyé compressé en `.gz` — décompressez-le pour le lire)."
            ),
            file=fichier,
        )
        return

    # Cas extrême : même compressé, ça ne passe pas dans une seule pièce jointe.
    parties = [compresse[i : i + LIMITE_OCTETS] for i in range(0, len(compresse), LIMITE_OCTETS)]
    await destinataire.send(
        content=(
            f"📦 Sauvegarde du serveur très volumineuse : envoyée en {len(parties)} parties compressées.\n"
            f"Pour la reconstituer : concaténez les fichiers dans l'ordre puis décompressez le résultat.\n"
            f"Exemple (Linux/Mac) : `cat {nom_base}.part*.bin > {nom_base}.json.gz && gunzip {nom_base}.json.gz`"
        )
    )

    lot: list[discord.File] = []
    for index, partie in enumerate(parties, start=1):
        lot.append(discord.File(io.BytesIO(partie), filename=f"{nom_base}.part{index}.bin"))
        if len(lot) == 10:  # Discord limite à 10 pièces jointes par message
            await destinataire.send(files=lot)
            lot = []
    if lot:
        await destinataire.send(files=lot)


def _tronquer(texte: str, longueur_max: int) -> str:
    if len(texte) <= longueur_max:
        return texte
    return texte[: longueur_max - 1].rstrip() + "…"


# ============================================================
#  &restore — comparaison + recréation des éléments manquants
# ============================================================
# Principe : on ne renvoie/modifie JAMAIS l'intégralité du serveur, seulement
# les rôles et salons présents dans la sauvegarde mais absents de l'état
# actuel (comparaison par nom, les anciens IDs n'existant plus après un raid).

def _diff_roles(guild: discord.Guild, roles_backup: list[dict]) -> list[dict]:
    noms_actuels = {r.name for r in guild.roles}
    manquants = []
    for info in roles_backup:
        if info.get("gere_par_integration") or info.get("nom") == "@everyone":
            continue  # non recréable (rôle géré par Discord/une intégration)
        if info.get("nom") not in noms_actuels:
            manquants.append(info)
    return manquants


def _diff_salons(guild: discord.Guild, salons_backup: list[dict]) -> list[dict]:
    actuels = {(c.name, str(c.type)) for c in guild.channels}
    manquants = [
        info for info in salons_backup
        if (info.get("nom"), info.get("type")) not in actuels
    ]
    # Les catégories doivent être recréées avant les salons qui en dépendent.
    manquants.sort(key=lambda s: 0 if s.get("type") == "category" else 1)
    return manquants


def _nom_categorie(salons_backup: list[dict], categorie_id: str | None) -> str | None:
    if not categorie_id:
        return None
    for salon in salons_backup:
        if salon.get("id") == categorie_id and salon.get("type") == "category":
            return salon.get("nom")
    return None


def _couleur_depuis_hex(texte: str | None) -> discord.Colour:
    if not texte or not texte.startswith("#"):
        return discord.Colour.default()
    try:
        return discord.Colour(int(texte.lstrip("#"), 16))
    except ValueError:
        return discord.Colour.default()


def _type_depuis_texte(texte: str) -> discord.ChannelType | None:
    return getattr(discord.ChannelType, texte, None)


def _reconstruire_overwrites(
    guild: discord.Guild, overwrites_backup: list[dict], roles_par_nom: dict[str, discord.Role]
) -> dict[discord.Role, discord.PermissionOverwrite]:
    """Ne restaure que les overwrites de rôles (celles sur des membres précis sont
    volontairement ignorées : recréer aveuglément des droits individuels est risqué)."""
    resultat = {}
    for ow in overwrites_backup:
        if ow.get("cible_type") != "role":
            continue
        nom_role = ow.get("cible_nom")
        role = guild.default_role if nom_role == "@everyone" else roles_par_nom.get(nom_role)
        if role is None:
            continue
        allow = discord.Permissions(ow.get("allow_bitfield", 0))
        deny = discord.Permissions(ow.get("deny_bitfield", 0))
        resultat[role] = discord.PermissionOverwrite.from_pair(allow, deny)
    return resultat


async def _creer_role(guild: discord.Guild, info: dict) -> tuple[discord.Role | None, str | None]:
    try:
        role = await guild.create_role(
            name=info["nom"][:100],
            colour=_couleur_depuis_hex(info.get("couleur")),
            permissions=discord.Permissions(info.get("permissions_bitfield", 0)),
            hoist=info.get("hoist", False),
            mentionable=info.get("mentionable", False),
            reason="[Restore] Rôle recréé depuis la sauvegarde",
        )
        return role, None
    except discord.HTTPException as e:
        return None, str(e)


async def _creer_salon(
    guild: discord.Guild,
    info: dict,
    categories_par_nom: dict[str, discord.CategoryChannel],
    overwrites: dict[discord.Role, discord.PermissionOverwrite],
    categorie_nom: str | None,
) -> tuple[discord.abc.GuildChannel | None, str | None]:
    type_ = _type_depuis_texte(info.get("type", ""))
    if type_ is None:
        return None, "type de salon inconnu"

    methode_nom = CREATEURS_SALON.get(type_)
    if methode_nom is None:
        return None, "type de salon non restaurable automatiquement"
    methode = getattr(guild, methode_nom)

    kwargs = {
        "name": info["nom"][:100],
        "overwrites": overwrites,
        "reason": "[Restore] Salon recréé depuis la sauvegarde",
    }
    if type_ != discord.ChannelType.category:
        kwargs["category"] = categories_par_nom.get(categorie_nom) if categorie_nom else None

    if type_ in (discord.ChannelType.text, discord.ChannelType.news):
        kwargs["topic"] = info.get("topic")
        kwargs["nsfw"] = info.get("nsfw", False)
        kwargs["slowmode_delay"] = info.get("slowmode_delay", 0)
    elif type_ == discord.ChannelType.voice:
        kwargs["bitrate"] = info.get("bitrate")
        kwargs["user_limit"] = info.get("user_limit")

    try:
        return await methode(**kwargs), None
    except discord.HTTPException as e:
        return None, str(e)


async def _appliquer_restauration(
    guild: discord.Guild, donnees: dict
) -> tuple[list[discord.Role], list[tuple[str, str]], list[discord.abc.GuildChannel], list[tuple[str, str]]]:
    roles_backup = donnees.get("roles", [])
    salons_backup = donnees.get("salons", [])

    roles_crees: list[discord.Role] = []
    roles_echecs: list[tuple[str, str]] = []
    for info in _diff_roles(guild, roles_backup):
        role, erreur = await _creer_role(guild, info)
        (roles_crees if role else roles_echecs).append(role if role else (info["nom"], erreur))

    roles_par_nom = {r.name: r for r in guild.roles}
    categories_par_nom = {c.name: c for c in guild.categories}

    salons_crees: list[discord.abc.GuildChannel] = []
    salons_echecs: list[tuple[str, str]] = []
    for info in _diff_salons(guild, salons_backup):
        categorie_nom = _nom_categorie(salons_backup, info.get("categorie_id"))
        overwrites = _reconstruire_overwrites(guild, info.get("overwrites", []), roles_par_nom)
        salon, erreur = await _creer_salon(guild, info, categories_par_nom, overwrites, categorie_nom)
        if salon is not None:
            salons_crees.append(salon)
            if info.get("type") == "category":
                categories_par_nom[salon.name] = salon
        else:
            salons_echecs.append((info["nom"], erreur))

    return roles_crees, roles_echecs, salons_crees, salons_echecs


class ConfirmationRestore(discord.ui.View):
    """Aperçu déjà validé par l'owner : ce bouton déclenche la création effective."""

    def __init__(self, guild: discord.Guild, auteur_id: int, donnees: dict):
        super().__init__(timeout=120)
        self.guild = guild
        self.auteur_id = auteur_id
        self.donnees = donnees

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.auteur_id:
            await interaction.response.send_message(
                "Seul l'auteur de la commande peut confirmer cette restauration.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Confirmer la restauration", style=discord.ButtonStyle.danger, emoji="♻️")
    async def confirmer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="⏳ Restauration en cours...", embed=None, view=self)

        roles_crees, roles_echecs, salons_crees, salons_echecs = await _appliquer_restauration(
            self.guild, self.donnees
        )

        embed = discord.Embed(
            title="♻️ Restauration terminée",
            color=discord.Color.orange() if (roles_echecs or salons_echecs) else discord.Color.green(),
        )
        embed.add_field(name="Rôles créés", value=str(len(roles_crees)), inline=True)
        embed.add_field(name="Salons créés", value=str(len(salons_crees)), inline=True)
        if roles_echecs:
            embed.add_field(
                name=f"Rôles en échec ({len(roles_echecs)})",
                value=_tronquer("\n".join(f"• {n} : {e}" for n, e in roles_echecs), 1000),
                inline=False,
            )
        if salons_echecs:
            embed.add_field(
                name=f"Salons en échec ({len(salons_echecs)})",
                value=_tronquer("\n".join(f"• {n} : {e}" for n, e in salons_echecs), 1000),
                inline=False,
            )

        await interaction.edit_original_response(content=None, embed=embed, view=None)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def annuler(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Restauration annulée.", embed=None, view=self)
        self.stop()


class Backup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="backup", help="Génère une sauvegarde JSON du serveur et l'envoie en MP.")
    @commands.guild_only()
    @check_owner()
    async def backup(self, ctx: commands.Context):
        message_statut = await ctx.send("⏳ Génération de la sauvegarde en cours...")

        donnees = _construire_backup(ctx.guild, ctx.author)
        json_texte = json.dumps(donnees, indent=2, ensure_ascii=False)
        nom_base = f"backup_{ctx.guild.id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        try:
            await _envoyer_en_pieces(ctx.author, json_texte, nom_base)
        except discord.Forbidden:
            await message_statut.edit(
                content="❌ Impossible de vous envoyer la sauvegarde en MP (vérifiez vos paramètres de "
                "confidentialité, notamment 'Autoriser les messages privés des membres du serveur')."
            )
            return

        await message_statut.edit(content="✅ Sauvegarde envoyée en message privé.")

    @commands.command(
        name="restore",
        help="Compare une sauvegarde JSON (&backup) à l'état actuel du serveur et propose de recréer "
        "les rôles/salons manquants (aperçu avant confirmation, rien n'est modifié sans validation).",
    )
    @commands.guild_only()
    @check_owner()
    async def restore(self, ctx: commands.Context):
        if not ctx.message.attachments:
            await ctx.send(
                "Joignez le fichier de sauvegarde (`.json` ou `.json.gz` généré par `&backup`) à cette commande."
            )
            return

        piece = ctx.message.attachments[0]
        brut = await piece.read()

        if piece.filename.endswith(".gz"):
            try:
                brut = gzip.decompress(brut)
            except OSError:
                await ctx.send("❌ Fichier `.gz` invalide ou corrompu.")
                return

        try:
            donnees = json.loads(brut.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            await ctx.send("❌ Fichier JSON invalide ou corrompu.")
            return

        roles_backup = donnees.get("roles", [])
        salons_backup = donnees.get("salons", [])
        roles_manquants = _diff_roles(ctx.guild, roles_backup)
        salons_manquants = _diff_salons(ctx.guild, salons_backup)

        if not roles_manquants and not salons_manquants:
            await ctx.send(
                "✅ Rien à restaurer : le serveur correspond déjà à la sauvegarde "
                "(aucun rôle ou salon manquant)."
            )
            return

        embed = discord.Embed(
            title="♻️ Aperçu de la restauration",
            description=(
                "Éléments présents dans la sauvegarde mais absents du serveur actuel. "
                "**Rien n'est encore modifié** — confirmez ci-dessous pour appliquer."
            ),
            color=discord.Color.orange(),
        )
        if roles_manquants:
            embed.add_field(
                name=f"Rôles manquants ({len(roles_manquants)})",
                value=_tronquer("\n".join(f"• {r['nom']}" for r in roles_manquants), 1000),
                inline=False,
            )
        if salons_manquants:
            embed.add_field(
                name=f"Salons manquants ({len(salons_manquants)})",
                value=_tronquer(
                    "\n".join(f"• {s['nom']} ({s['type']})" for s in salons_manquants), 1000
                ),
                inline=False,
            )
        embed.set_footer(
            text="Les overwrites sur des membres précis, les emojis et la position exacte des rôles "
            "ne sont pas restaurés automatiquement."
        )

        vue = ConfirmationRestore(ctx.guild, ctx.author.id, donnees)
        await ctx.send(embed=embed, view=vue)


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))
