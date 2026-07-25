import io
import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from commandes._permissions import (
    charger_config,
    sauvegarder_config,
    check_admin,
)
from commandes.logs import envoyer_log

LONGUEUR_CODE = 6
DUREE_VALIDITE_MINUTES = 10
MAX_TENTATIVES = 5

# Alphabet sans caractères ambigus (0/O, 1/I/l) pour rester lisible sur l'image
ALPHABET_CAPTCHA = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

POLICES_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


# ============================================================
#  CONFIGURATION (stockée dans data/config.json, clé "verification")
# ============================================================

def get_config_verif(guild_id: int) -> dict:
    config = charger_config()
    return config.get(str(guild_id), {}).get(
        "verification", {"role_verifie_id": None, "role_a_retirer_id": None, "salon_id": None, "message_id": None}
    )


def sauvegarder_config_verif(guild_id: int, data: dict) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["verification"] = data
    sauvegarder_config(config)


# ============================================================
#  GENERATION DU CAPTCHA
# ============================================================

def generer_code() -> str:
    return "".join(random.choices(ALPHABET_CAPTCHA, k=LONGUEUR_CODE))


def _charger_police(taille: int) -> ImageFont.FreeTypeFont:
    for chemin in POLICES_CANDIDATES:
        try:
            return ImageFont.truetype(chemin, taille)
        except OSError:
            continue
    return ImageFont.load_default()


def generer_image_captcha(code: str) -> io.BytesIO:
    largeur, hauteur = 280, 100
    image = Image.new("RGB", (largeur, hauteur), color=(240, 240, 245))
    dessin = ImageDraw.Draw(image)

    # Bruit : lignes aléatoires en fond
    for _ in range(6):
        x1, y1 = random.randint(0, largeur), random.randint(0, hauteur)
        x2, y2 = random.randint(0, largeur), random.randint(0, hauteur)
        dessin.line((x1, y1, x2, y2), fill=tuple(random.randint(160, 205) for _ in range(3)), width=2)

    # Bruit : points aléatoires
    for _ in range(250):
        x, y = random.randint(0, largeur), random.randint(0, hauteur)
        dessin.point((x, y), fill=tuple(random.randint(160, 210) for _ in range(3)))

    police = _charger_police(42)
    x_pos = 15
    for lettre in code:
        y_pos = random.randint(5, 25)
        couleur = tuple(random.randint(10, 90) for _ in range(3))

        calque = Image.new("RGBA", (55, 65), (255, 255, 255, 0))
        dessin_calque = ImageDraw.Draw(calque)
        dessin_calque.text((5, 5), lettre, font=police, fill=couleur)

        angle = random.randint(-25, 25)
        calque = calque.rotate(angle, expand=True, resample=Image.BICUBIC)
        image.paste(calque, (x_pos, y_pos), calque)
        x_pos += 40

    # Ligne de bruit par-dessus le texte pour gêner l'OCR automatisé
    for _ in range(2):
        x1, y1 = random.randint(0, largeur), random.randint(20, 80)
        x2, y2 = random.randint(0, largeur), random.randint(20, 80)
        dessin.line((x1, y1, x2, y2), fill=tuple(random.randint(80, 140) for _ in range(3)), width=2)

    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    tampon.seek(0)
    return tampon


# ============================================================
#  VUE PUBLIQUE (bouton persistant "Se vérifier")
# ============================================================

class VuePublicVerif(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Se vérifier", style=discord.ButtonStyle.success, emoji="🔐", custom_id="verif_lancer")
    async def se_verifier(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "Verification" = interaction.client.get_cog("Verification")
        if cog is None:
            await interaction.response.send_message("Système de vérification indisponible pour le moment.", ephemeral=True)
            return
        await cog.lancer_captcha(interaction)


class VueValidationCaptcha(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=DUREE_VALIDITE_MINUTES * 60)
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce captcha ne vous est pas destiné.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Valider mon code", style=discord.ButtonStyle.primary, emoji="✅")
    async def valider(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModalCodeCaptcha(self.guild_id, self.user_id))

    @discord.ui.button(label="Nouveau code", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def nouveau_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "Verification" = interaction.client.get_cog("Verification")
        if cog is None:
            await interaction.response.send_message("Système de vérification indisponible pour le moment.", ephemeral=True)
            return
        await cog.lancer_captcha(interaction)


class ModalCodeCaptcha(discord.ui.Modal, title="Vérification"):
    code = discord.ui.TextInput(label="Code affiché sur l'image", max_length=10, placeholder="Ex : K7F3ZQ")

    def __init__(self, guild_id: int, user_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        cog: "Verification" = interaction.client.get_cog("Verification")
        if cog is None:
            await interaction.response.send_message("Système de vérification indisponible pour le moment.", ephemeral=True)
            return
        await cog.verifier_code(interaction, self.guild_id, self.user_id, self.code.value)


# ============================================================
#  PANEL DE GESTION (&verif) — configuration par les admins
# ============================================================

def construire_embed_accueil_verif(guild: discord.Guild) -> discord.Embed:
    config_verif = get_config_verif(guild.id)
    role_verifie = guild.get_role(config_verif["role_verifie_id"]) if config_verif.get("role_verifie_id") else None
    role_a_retirer = guild.get_role(config_verif["role_a_retirer_id"]) if config_verif.get("role_a_retirer_id") else None
    salon = guild.get_channel(config_verif["salon_id"]) if config_verif.get("salon_id") else None

    embed = discord.Embed(
        title="🔐 Panel de vérification (captcha)",
        description=(
            "Les nouveaux membres devront recopier un code affiché sur une image pour obtenir l'accès au serveur.\n"
            "Le salon de logs se configure via `&setup` → *Logs* → *Vérification*."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Rôle attribué", value=role_verifie.mention if role_verifie else "⚠️ Non configuré", inline=True)
    embed.add_field(name="Rôle retiré (optionnel)", value=role_a_retirer.mention if role_a_retirer else "Aucun", inline=True)
    embed.add_field(name="Salon du panel", value=salon.mention if salon else "Non configuré", inline=True)
    embed.set_footer(text=f"Serveur : {guild.name}")
    return embed


class PanelBaseVerif(discord.ui.View):
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


class SelectRoleVerifie(discord.ui.RoleSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(placeholder="Rôle à attribuer une fois vérifié", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        if role.is_default() or role.managed:
            await interaction.response.send_message(
                "Impossible d'utiliser @everyone ou un rôle géré automatiquement.", ephemeral=True
            )
            return

        config_verif = get_config_verif(self.guild_id)
        config_verif["role_verifie_id"] = role.id
        sauvegarder_config_verif(self.guild_id, config_verif)

        embed = construire_embed_accueil_verif(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


class SelectRoleARetirer(discord.ui.RoleSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(
            placeholder="Rôle à retirer une fois vérifié (optionnel, ex: Non-vérifié)",
            min_values=0,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        config_verif = get_config_verif(self.guild_id)

        if not self.values:
            config_verif["role_a_retirer_id"] = None
        else:
            role = self.values[0]
            if role.is_default() or role.managed:
                await interaction.response.send_message(
                    "Impossible d'utiliser @everyone ou un rôle géré automatiquement.", ephemeral=True
                )
                return
            config_verif["role_a_retirer_id"] = role.id

        sauvegarder_config_verif(self.guild_id, config_verif)

        embed = construire_embed_accueil_verif(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


class SelectSalonVerif(discord.ui.ChannelSelect):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(
            placeholder="Salon où sera envoyé le panel public",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        config_verif = get_config_verif(self.guild_id)
        config_verif["salon_id"] = self.values[0].id
        sauvegarder_config_verif(self.guild_id, config_verif)

        embed = construire_embed_accueil_verif(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)


class PanelVerifConfig(PanelBaseVerif):
    def __init__(self, guild_id: int, auteur_id: int):
        super().__init__(guild_id, auteur_id)
        self.add_item(SelectRoleVerifie(guild_id))
        self.add_item(SelectRoleARetirer(guild_id))
        self.add_item(SelectSalonVerif(guild_id))

    @discord.ui.button(label="Envoyer / Mettre à jour le panel", style=discord.ButtonStyle.success, row=3, emoji="📤")
    async def envoyer(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_verif = get_config_verif(self.guild_id)

        if not config_verif.get("role_verifie_id"):
            await interaction.response.send_message("Configurez d'abord un rôle à attribuer.", ephemeral=True)
            return
        if not config_verif.get("salon_id"):
            await interaction.response.send_message("Configurez d'abord un salon.", ephemeral=True)
            return

        salon = interaction.guild.get_channel(config_verif["salon_id"])
        if salon is None:
            await interaction.response.send_message("Le salon configuré est introuvable.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🔐 Vérification requise",
            description=(
                "Cliquez sur le bouton ci-dessous pour recevoir un code à recopier et débloquer l'accès au serveur."
            ),
            color=discord.Color.blurple(),
        )
        vue_publique = VuePublicVerif()

        message_final = None
        ancien_id = config_verif.get("message_id")
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

        config_verif["message_id"] = message_final.id
        sauvegarder_config_verif(self.guild_id, config_verif)

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

class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.captchas: dict[tuple[int, int], dict] = {}
        self._vue_enregistree = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._vue_enregistree:
            return
        self._vue_enregistree = True
        self.bot.add_view(VuePublicVerif())

    async def lancer_captcha(self, interaction: discord.Interaction) -> None:
        config_verif = get_config_verif(interaction.guild.id)
        if not config_verif.get("role_verifie_id"):
            await interaction.response.send_message(
                "La vérification n'est pas encore configurée sur ce serveur (contactez le staff).", ephemeral=True
            )
            return

        role = interaction.guild.get_role(config_verif["role_verifie_id"])
        if role is not None and role in interaction.user.roles:
            await interaction.response.send_message("Vous êtes déjà vérifié(e) !", ephemeral=True)
            return

        code = generer_code()
        cle = (interaction.guild.id, interaction.user.id)
        self.captchas[cle] = {
            "code": code,
            "tentatives": 0,
            "expire": datetime.now(timezone.utc) + timedelta(minutes=DUREE_VALIDITE_MINUTES),
        }

        image = generer_image_captcha(code)
        fichier = discord.File(image, filename="captcha.png")

        embed = discord.Embed(
            title="🔐 Vérification",
            description=(
                f"Recopiez le code affiché ci-dessous (non sensible à la casse), valable "
                f"{DUREE_VALIDITE_MINUTES} minutes."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_image(url="attachment://captcha.png")

        vue = VueValidationCaptcha(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(embed=embed, file=fichier, view=vue, ephemeral=True)

    async def verifier_code(self, interaction: discord.Interaction, guild_id: int, user_id: int, saisie: str) -> None:
        cle = (guild_id, user_id)
        entree = self.captchas.get(cle)

        if entree is None:
            await interaction.response.send_message(
                "Votre code a expiré ou n'existe plus. Cliquez à nouveau sur **Se vérifier**.", ephemeral=True
            )
            return

        if datetime.now(timezone.utc) > entree["expire"]:
            del self.captchas[cle]
            await interaction.response.send_message(
                "Votre code a expiré. Cliquez à nouveau sur **Se vérifier**.", ephemeral=True
            )
            return

        if saisie.strip().upper() != entree["code"]:
            entree["tentatives"] += 1
            if entree["tentatives"] >= MAX_TENTATIVES:
                del self.captchas[cle]
                await interaction.response.send_message(
                    "❌ Trop de tentatives incorrectes. Cliquez à nouveau sur **Se vérifier** pour recommencer.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                f"❌ Code incorrect ({entree['tentatives']}/{MAX_TENTATIVES} tentatives). Réessayez.", ephemeral=True
            )
            return

        # Code correct
        del self.captchas[cle]
        config_verif = get_config_verif(guild_id)
        guild = interaction.guild
        membre = interaction.user
        erreurs = []

        role_verifie = guild.get_role(config_verif.get("role_verifie_id"))
        if role_verifie is not None:
            try:
                await membre.add_roles(role_verifie, reason="[Vérification] Captcha réussi")
            except discord.Forbidden:
                erreurs.append("attribution du rôle vérifié")

        role_a_retirer_id = config_verif.get("role_a_retirer_id")
        if role_a_retirer_id:
            role_a_retirer = guild.get_role(role_a_retirer_id)
            if role_a_retirer is not None and role_a_retirer in membre.roles:
                try:
                    await membre.remove_roles(role_a_retirer, reason="[Vérification] Captcha réussi")
                except discord.Forbidden:
                    erreurs.append("retrait du rôle non-vérifié")

        if erreurs:
            await interaction.response.send_message(
                "✅ Captcha validé, mais erreur de permissions pour : "
                + ", ".join(erreurs)
                + ". Contactez le staff pour finaliser.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("✅ Vérification réussie, bienvenue !", ephemeral=True)

        await envoyer_log(
            guild,
            "verification",
            discord.Embed(
                title="🔐 Membre vérifié",
                description=f"{membre.mention} a réussi la vérification captcha.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            ),
        )

    @commands.command(
        name="verif",
        aliases=["verification"],
        help="Ouvre le panel de configuration du système de vérification par captcha.",
    )
    @commands.guild_only()
    @check_admin()
    async def verif_panel(self, ctx: commands.Context):
        embed = construire_embed_accueil_verif(ctx.guild)
        vue = PanelVerifConfig(ctx.guild.id, ctx.author.id)
        message = await ctx.send(embed=embed, view=vue)
        vue.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
