import asyncio
import discord
from discord.ext import commands
from commandes._permissions import (
    est_gerant,
    get_anti_raid_status,
    activer_anti_raid,
    desactiver_anti_raid,
    get_role_punition_id,
    check_gerant,
)

# Délai laissé à Discord pour peupler l'audit log avant qu'on aille le lire.
DELAI_AUDIT_LOG = 1.0

# Types de salons qu'on sait recréer automatiquement après une suppression.
CREATEURS_SALON = {
    discord.ChannelType.text: "create_text_channel",
    discord.ChannelType.voice: "create_voice_channel",
    discord.ChannelType.category: "create_category",
    discord.ChannelType.stage_voice: "create_stage_channel",
    discord.ChannelType.forum: "create_forum",
    discord.ChannelType.news: "create_text_channel",
}


class AntiRaid(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # IDs de salons dont l'édition suivante provient du bot lui-même
        # (annulation en cours) : on ignore le on_guild_channel_update
        # correspondant pour éviter une boucle infinie.
        self._salons_en_correction: set[int] = set()

    # --- SANCTIONS DU STAFF FAUTIF ---

    async def punir_staff(self, guild: discord.Guild, moderateur: discord.Member, raison: str):
        if moderateur.id == self.bot.user.id or est_gerant(moderateur):
            return

        role_punition_id = get_role_punition_id(guild.id)
        role_punition = guild.get_role(role_punition_id) if role_punition_id else None
        roles_a_retirer = [r for r in moderateur.roles if not r.is_default()]

        if guild.me.top_role <= moderateur.top_role:
            # Le bot ne pourra de toute façon pas toucher aux rôles de ce membre.
            await self.log_evenement_salon(
                guild,
                "Punition impossible",
                f"{moderateur.mention} a déclenché l'anti-raid ({raison}) mais son rôle le plus "
                f"haut est égal ou supérieur à celui du bot : impossible de le sanctionner "
                f"automatiquement.",
            )
            return

        try:
            if roles_a_retirer:
                await moderateur.remove_roles(*roles_a_retirer, reason=f"[Anti-Raid] {raison}")
            if role_punition:
                await moderateur.add_roles(role_punition, reason="[Anti-Raid] Attribution rôle punition")
            elif role_punition_id:
                await self.log_evenement_salon(
                    guild,
                    "Rôle de punition introuvable",
                    f"Le rôle de punition configuré (ID `{role_punition_id}`) n'existe pas sur ce serveur.",
                )

            from commandes.logs import log_sanction
            await log_sanction(
                guild=guild,
                action="🚨 Anti-Raid : Staff Destitué",
                cible=moderateur,
                moderateur=guild.me,
                raison=f"Tentative de sanction en plein Raid Mode ({raison}). Rôles retirés.",
                couleur=discord.Color.dark_red(),
            )
        except discord.Forbidden:
            await self.log_evenement_salon(
                guild,
                "Punition échouée",
                f"Permissions insuffisantes pour sanctionner {moderateur.mention} ({raison}).",
            )

    async def log_evenement_salon(self, guild: discord.Guild, action: str, details: str):
        embed = discord.Embed(
            title=f"🛡️ Anti-Raid : {action}",
            description=details,
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Système Anti-Raid • {guild.name}")
        try:
            from commandes.logs import envoyer_log
            await envoyer_log(guild, "commandes", embed)
        except Exception:
            pass

    async def _trouver_auteur(
        self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int
    ) -> discord.Member | None:
        """Cherche dans l'audit log le membre responsable d'une action précise."""
        await asyncio.sleep(DELAI_AUDIT_LOG)
        try:
            async for entry in guild.audit_logs(action=action, limit=5):
                if entry.target and entry.target.id == target_id:
                    if isinstance(entry.user, discord.Member):
                        return entry.user
                    return None
        except discord.Forbidden:
            return None
        return None

    # --- COMMANDES DE CONTROLE ---

    @commands.command(name="raidon", help="Active le mode Anti-Raid sur le serveur.")
    @commands.guild_only()
    @check_gerant()
    async def raid_on(self, ctx: commands.Context):
        activer_anti_raid(ctx.guild.id)
        await ctx.send("*Le mode Anti-Raid a été ACTIVÉ*")
        await self.log_evenement_salon(ctx.guild, "Mode Activé", f"Le mode anti-raid a été activé par {ctx.author.mention}.")

    @commands.command(name="raidoff", help="Désactive le mode Anti-Raid sur le serveur.")
    @commands.guild_only()
    @check_gerant()
    async def raid_off(self, ctx: commands.Context):
        desactiver_anti_raid(ctx.guild.id)
        await ctx.send("✅ **Le mode Anti-Raid a été DÉSACTIVÉ.**")
        await self.log_evenement_salon(ctx.guild, "Mode Désactivé", f"Le mode anti-raid a été désactivé par {ctx.author.mention}.")

    # --- EVENEMENTS SALONS ---

    async def _recreer_salon(self, channel: discord.abc.GuildChannel) -> discord.abc.GuildChannel | None:
        methode_nom = CREATEURS_SALON.get(channel.type)
        if methode_nom is None:
            return None

        methode = getattr(channel.guild, methode_nom)
        kwargs = {
            "name": channel.name,
            "overwrites": channel.overwrites,
            "reason": "[Anti-Raid] Restauration",
        }
        if channel.type != discord.ChannelType.category:
            kwargs["category"] = channel.category
        kwargs["position"] = channel.position

        # Attributs supplémentaires selon le type, pour une restauration plus fidèle.
        if isinstance(channel, discord.TextChannel):
            kwargs["topic"] = channel.topic
            kwargs["nsfw"] = channel.nsfw
            kwargs["slowmode_delay"] = channel.slowmode_delay
        elif isinstance(channel, discord.VoiceChannel):
            kwargs["bitrate"] = channel.bitrate
            kwargs["user_limit"] = channel.user_limit

        try:
            return await methode(**kwargs)
        except discord.HTTPException:
            # En cas d'échec (paramètre refusé, etc.), on retente en version minimale.
            try:
                return await methode(
                    name=channel.name,
                    category=channel.category if channel.type != discord.ChannelType.category else None,
                    overwrites=channel.overwrites,
                    reason="[Anti-Raid] Restauration",
                )
            except discord.HTTPException:
                return None

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not get_anti_raid_status(channel.guild.id):
            return

        # Là aussi : recréer le salon ne doit pas attendre le délai de
        # peuplement de l'audit log. On lance les deux en parallèle pour que
        # la restauration soit la plus rapide possible.
        moderateur, nouveau_salon = await asyncio.gather(
            self._trouver_auteur(channel.guild, discord.AuditLogAction.channel_delete, channel.id),
            self._recreer_salon(channel),
        )

        if moderateur is not None:
            await self.punir_staff(channel.guild, moderateur, "Suppression de salon illégitime")

        if nouveau_salon is not None:
            await self.log_evenement_salon(
                channel.guild,
                "Salon Supprimé & Restauré",
                f"Le salon **{channel.name}** a été supprimé"
                + (f" par {moderateur.mention}" if moderateur else "")
                + f". Il a été recréé automatiquement : {nouveau_salon.mention}.",
            )
        else:
            await self.log_evenement_salon(
                channel.guild,
                "Salon Supprimé (restauration impossible)",
                f"Le salon **{channel.name}** a été supprimé"
                + (f" par {moderateur.mention}" if moderateur else "")
                + " mais n'a pas pu être recréé automatiquement (type de salon non pris en charge "
                "ou erreur Discord). Une restauration manuelle est nécessaire.",
            )

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if not get_anti_raid_status(after.guild.id):
            return

        # Cet update provient de notre propre annulation : on l'ignore pour
        # ne pas repartir dans une boucle.
        if after.id in self._salons_en_correction:
            self._salons_en_correction.discard(after.id)
            return

        # On ignore volontairement les changements de "position" : dès qu'un
        # salon est déplacé, créé ou supprimé, Discord renumérote la position
        # de TOUS les autres salons de la catégorie, ce qui déclenche un
        # on_guild_channel_update sur chacun d'eux même si personne n'y a
        # touché. Traiter ça comme une modification illégitime provoque un
        # spam d'embeds sur des salons jamais édités par un humain.
        changements = (
            before.name != after.name
            or before.overwrites != after.overwrites
            or before.category != after.category
        )
        if not changements:
            return

        self._salons_en_correction.add(after.id)

        async def _annuler() -> bool:
            try:
                await after.edit(
                    name=before.name,
                    category=before.category,
                    overwrites=before.overwrites,
                    reason="[Anti-Raid] Annulation des modifications",
                )
                return True
            except discord.Forbidden:
                self._salons_en_correction.discard(after.id)
                return False

        # La correction du salon et la recherche du responsable dans l'audit
        # log n'ont aucune raison de s'enchaîner en série : on les lance en
        # parallèle pour que le salon soit corrigé sans attendre le délai
        # de peuplement de l'audit log.
        moderateur, annulation_ok = await asyncio.gather(
            self._trouver_auteur(after.guild, discord.AuditLogAction.channel_update, after.id),
            _annuler(),
        )

        if moderateur is not None:
            await self.punir_staff(after.guild, moderateur, "Modification de salon illégitime")

        if annulation_ok:
            await self.log_evenement_salon(
                after.guild,
                "Modifications de Salon Annulées",
                f"Des modifications non autorisées sur le salon {after.mention} "
                + (f"par {moderateur.mention} " if moderateur else "")
                + "ont été détectées et annulées.",
            )
        else:
            await self.log_evenement_salon(
                after.guild,
                "Annulation impossible",
                f"Permissions insuffisantes pour annuler les modifications sur {after.mention}.",
            )

    # --- EVENEMENTS SANCTIONS ---

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member):
        # La sanction du staff pour un ban manuel s'applique en permanence,
        # indépendamment du mode anti-raid (qui ne gère que les salons).
        moderateur = await self._trouver_auteur(guild, discord.AuditLogAction.ban, user.id)
        if moderateur is not None:
            await self.punir_staff(guild, moderateur, "Bannissement illégitime")
            # Le déban automatique du membre visé, lui, reste réservé au mode anti-raid
            # (sinon un ban légitime hors raid serait systématiquement annulé).
            if get_anti_raid_status(guild.id):
                try:
                    await guild.unban(user, reason="[Anti-Raid] Annulation du ban")
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # Même logique : la sanction du staff pour un kick manuel s'applique
        # en permanence, indépendamment du mode anti-raid.
        moderateur = await self._trouver_auteur(
            member.guild, discord.AuditLogAction.kick, member.id
        )
        if moderateur is not None:
            await self.punir_staff(member.guild, moderateur, "Kick illégitime")


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaid(bot))