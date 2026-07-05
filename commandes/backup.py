import io
import json
import gzip
import discord
from datetime import datetime, timezone
from discord.ext import commands

from commandes._permissions import check_owner

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


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))