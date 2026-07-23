import os
import json
import uuid
from datetime import datetime, timezone
import discord
from discord.ext import commands

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.json"
)
DATA_DIR = os.path.dirname(DATA_PATH)
WARNS_PATH = os.path.join(DATA_DIR, "warns.json")
WHITELIST_PATH = os.path.join(DATA_DIR, "whitelist.json")
BLACKLIST_PATH = os.path.join(DATA_DIR, "blacklist.json")
TICKETS_PATH = os.path.join(DATA_DIR, "tickets.json")


def charger_config() -> dict:
    if not os.path.isfile(DATA_PATH):
        return {}
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            # Fichier corrompu ou vide : on repart sur une config vierge
            # plutôt que de faire planter tout le bot au démarrage.
            return {}


def sauvegarder_config(data: dict) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def parse_ids_env(brut: str) -> list[int]:
    """Parse une valeur du .env du type '1234','5678' ou 1234,5678."""
    ids = []
    for part in brut.split(","):
        part = part.strip().strip('"').strip("'")
        if part.isdigit():
            ids.append(int(part))
    return ids


def get_roles_gerant() -> list[int]:
    return parse_ids_env(os.getenv("GERANT", ""))


def get_roles_bypass() -> list[int]:
    return parse_ids_env(os.getenv("BYPASS", ""))


def get_owner_ids() -> list[int]:
    return parse_ids_env(os.getenv("OWNER", ""))


def est_owner(user_id: int) -> bool:
    """Réservé au(x) propriétaire(s) du bot défini(s) dans le .env (OWNER=...)."""
    return user_id in get_owner_ids()


def get_roles_staff(guild_id: int) -> list[int]:
    config = charger_config()
    return config.get(str(guild_id), {}).get("staff_roles", [])


def get_logs_channel_id(guild_id: int, categorie: str) -> int | None:
    """categorie doit être 'commandes' ou 'sanctions'."""
    config = charger_config()
    return config.get(str(guild_id), {}).get(f"logs_{categorie}_channel")


def est_bypass(membre: discord.Member) -> bool:
    roles_membre = {r.id for r in membre.roles}
    return bool(roles_membre.intersection(get_roles_bypass()))


def est_gerant(membre: discord.Member) -> bool:
    if est_bypass(membre):
        return True
    roles_membre = {r.id for r in membre.roles}
    return bool(roles_membre.intersection(get_roles_gerant()))


def est_staff(membre: discord.Member) -> bool:
    """Un gérant ou un bypass est automatiquement staff."""
    if est_gerant(membre):
        return True
    roles_membre = {r.id for r in membre.roles}
    return bool(roles_membre.intersection(get_roles_staff(membre.guild.id)))


# --- SYSTEME DE STATUT POUR L'ANTI-RAID ---

def get_anti_raid_status(guild_id: int) -> bool:
    """Renvoie True si l'anti-raid est activé pour ce serveur, sinon False."""
    config = charger_config()
    return config.get(str(guild_id), {}).get("anti_raid_actif", False)


def activer_anti_raid(guild_id: int) -> None:
    """Active l'anti-raid pour ce serveur."""
    config = charger_config()
    config.setdefault(str(guild_id), {})["anti_raid_actif"] = True
    sauvegarder_config(config)


def desactiver_anti_raid(guild_id: int) -> None:
    """Désactive l'anti-raid pour ce serveur."""
    config = charger_config()
    config.setdefault(str(guild_id), {})["anti_raid_actif"] = False
    sauvegarder_config(config)


def get_role_punition_id(guild_id: int) -> int | None:
    """Rôle donné au staff sanctionné par l'anti-raid.

    Priorité à la config par serveur (configurable via &setup plus tard si besoin),
    sinon repli sur la variable d'environnement MEMBRE.
    """
    config = charger_config()
    role_id = config.get(str(guild_id), {}).get("role_punition")
    if role_id is not None:
        return role_id

    brut = os.getenv("MEMBRE")
    if brut is None:
        return None
    try:
        return int(brut)
    except ValueError:
        return None


def set_role_punition(guild_id: int, role_id: int | None) -> None:
    config = charger_config()
    config.setdefault(str(guild_id), {})["role_punition"] = role_id
    sauvegarder_config(config)


# --- SYSTEME D'AVERTISSEMENTS (WARNS) ---
# Stocké dans data/warns.json, séparé de config.json pour ne pas alourdir
# le fichier de configuration avec un historique qui grossit dans le temps.

def charger_warns() -> dict:
    if not os.path.isfile(WARNS_PATH):
        return {}
    with open(WARNS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def sauvegarder_warns(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WARNS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def ajouter_avertissement(
    guild_id: int,
    membre_id: int,
    staff_id: int,
    raison: str,
    preuve: str | None = None,
) -> dict:
    """Enregistre un avertissement et renvoie l'entrée créée (contient un 'id' unique)."""
    warns = charger_warns()
    guild_key = str(guild_id)
    membre_key = str(membre_id)
    warns.setdefault(guild_key, {}).setdefault(membre_key, [])

    entree = {
        "id": uuid.uuid4().hex[:8],
        "sanctionne_id": str(membre_id),
        "staff_id": str(staff_id),
        "raison": raison,
        "preuve": preuve,
        "date": datetime.now(timezone.utc).isoformat(),
    }
    warns[guild_key][membre_key].append(entree)
    sauvegarder_warns(warns)
    return entree


def obtenir_avertissements(guild_id: int, membre_id: int) -> list[dict]:
    warns = charger_warns()
    return warns.get(str(guild_id), {}).get(str(membre_id), [])


def supprimer_avertissement(guild_id: int, membre_id: int, warn_id: str) -> bool:
    """Supprime un avertissement précis par son id. Renvoie True si trouvé et supprimé."""
    warns = charger_warns()
    guild_key = str(guild_id)
    membre_key = str(membre_id)
    liste = warns.get(guild_key, {}).get(membre_key)
    if not liste:
        return False

    nouvelle_liste = [w for w in liste if w.get("id") != warn_id]
    if len(nouvelle_liste) == len(liste):
        return False

    warns[guild_key][membre_key] = nouvelle_liste
    sauvegarder_warns(warns)
    return True


# --- WHITELIST ANTI-BAN ---
# Un membre whitelist qui se fait bannir est automatiquement débanni (voir commandes/wl.py).

def charger_whitelist() -> dict:
    if not os.path.isfile(WHITELIST_PATH):
        return {}
    with open(WHITELIST_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def sauvegarder_whitelist(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WHITELIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def est_whitelist(guild_id: int, membre_id: int) -> bool:
    whitelist = charger_whitelist()
    return membre_id in whitelist.get(str(guild_id), [])


def ajouter_whitelist(guild_id: int, membre_id: int) -> None:
    whitelist = charger_whitelist()
    guild_key = str(guild_id)
    whitelist.setdefault(guild_key, [])
    if membre_id not in whitelist[guild_key]:
        whitelist[guild_key].append(membre_id)
    sauvegarder_whitelist(whitelist)


def retirer_whitelist(guild_id: int, membre_id: int) -> None:
    whitelist = charger_whitelist()
    guild_key = str(guild_id)
    if guild_key not in whitelist:
        return
    whitelist[guild_key] = [i for i in whitelist[guild_key] if i != membre_id]
    sauvegarder_whitelist(whitelist)


# --- BLACKLIST (BAN AUTOMATIQUE) ---
# Un membre blacklist est banni immédiatement (&bl) et re-banni automatiquement
# s'il tente de rejoindre le serveur tant qu'il est sur la liste.

def charger_blacklist() -> dict:
    if not os.path.isfile(BLACKLIST_PATH):
        return {}
    with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def sauvegarder_blacklist(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(BLACKLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def est_blacklist(guild_id: int, membre_id: int) -> bool:
    blacklist = charger_blacklist()
    return str(membre_id) in blacklist.get(str(guild_id), {})


def obtenir_entree_blacklist(guild_id: int, membre_id: int) -> dict | None:
    blacklist = charger_blacklist()
    return blacklist.get(str(guild_id), {}).get(str(membre_id))


def ajouter_blacklist(guild_id: int, membre_id: int, staff_id: int, raison: str) -> dict:
    """Ajoute un membre à la blacklist et renvoie l'entrée créée."""
    blacklist = charger_blacklist()
    guild_key = str(guild_id)
    blacklist.setdefault(guild_key, {})

    entree = {
        "staff_id": str(staff_id),
        "raison": raison,
        "date": datetime.now(timezone.utc).isoformat(),
    }
    blacklist[guild_key][str(membre_id)] = entree
    sauvegarder_blacklist(blacklist)
    return entree


def retirer_blacklist(guild_id: int, membre_id: int) -> bool:
    """Retire un membre de la blacklist. Renvoie True si trouvé et supprimé."""
    blacklist = charger_blacklist()
    guild_key = str(guild_id)
    membre_key = str(membre_id)
    if membre_key not in blacklist.get(guild_key, {}):
        return False

    del blacklist[guild_key][membre_key]
    sauvegarder_blacklist(blacklist)
    return True


# --- CHECKS DE PERMISSIONS ---
# A utiliser en décorateur sur les commandes (@check_gerant(), etc.).
# Comme ce sont de vrais "checks" discord.py, la commande &help peut les
# tester automatiquement (via command.can_run) pour n'afficher que les
# commandes que l'utilisateur a le droit d'utiliser.

def check_gerant():
    async def predicate(ctx: commands.Context) -> bool:
        if not est_gerant(ctx.author):
            raise commands.CheckFailure(
                "Vous n'avez pas la permission d'utiliser cette commande (rôle gérant requis)."
            )
        return True
    return commands.check(predicate)


def check_staff():
    async def predicate(ctx: commands.Context) -> bool:
        if not est_staff(ctx.author):
            raise commands.CheckFailure(
                "Vous n'avez pas la permission d'utiliser cette commande (rôle staff requis)."
            )
        return True
    return commands.check(predicate)


def check_owner():
    async def predicate(ctx: commands.Context) -> bool:
        if not est_owner(ctx.author.id):
            raise commands.CheckFailure(
                "Cette commande est réservée au(x) propriétaire(s) du bot."
            )
        return True
    return commands.check(predicate)


def est_admin(membre: discord.Member) -> bool:
    """Membre possédant la permission Discord native 'Administrateur'.

    Un gérant/bypass (défini dans le .env) est aussi considéré admin
    automatiquement, même sans la permission Discord, pour rester cohérent
    avec le reste de la hiérarchie du bot.
    """
    if est_gerant(membre):
        return True
    return membre.guild_permissions.administrator


def check_admin():
    async def predicate(ctx: commands.Context) -> bool:
        if not est_admin(ctx.author):
            raise commands.CheckFailure(
                "Vous n'avez pas la permission d'utiliser cette commande "
                "(permission **Administrateur** requise)."
            )
        return True
    return commands.check(predicate)


# --- STOCKAGE DES TICKETS OUVERTS ---
# Séparé de config.json (qui contient la configuration des types/panel) :
# ce fichier ne contient que l'état des tickets actuellement/anciennement
# ouverts, indexé par salon.

def charger_tickets() -> dict:
    if not os.path.isfile(TICKETS_PATH):
        return {}
    with open(TICKETS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def sauvegarder_tickets(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TICKETS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
