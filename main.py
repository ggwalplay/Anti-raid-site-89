import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Charger les variables du .env
load_dotenv()

TOKEN = os.getenv("TOKEN")


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="&", intents=intents)


async def charger_commandes():
    """Charge tous les fichiers .py du dossier 'commandes' comme des cogs."""
    dossier = os.path.join(os.path.dirname(__file__), "commandes")

    if not os.path.isdir(dossier):
        print(f"[!] Dossier introuvable : {dossier}")
        return

    for fichier in sorted(os.listdir(dossier)):
        if fichier.endswith(".py") and not fichier.startswith("_"):
            nom_extension = f"commandes.{fichier[:-3]}"
            try:
                await bot.load_extension(nom_extension)
                print(f"[+] Cog chargé : {nom_extension}")
            except Exception as e:
                print(f"[!] Erreur lors du chargement de {nom_extension} : {e}")


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.send(str(error) or "Vous n'avez pas la permission d'utiliser cette commande.")
        return
    if isinstance(error, commands.MissingRequiredArgument) or isinstance(error, commands.BadArgument):
        await ctx.send(f"Utilisation incorrecte. Tapez `&help {ctx.command}` pour voir comment l'utiliser.")
        return

    print(f"[!] Erreur dans la commande {ctx.command} : {error}")


async def main():
    async with bot:
        await charger_commandes()
        await bot.start(TOKEN)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Erreur : TOKEN manquant dans le fichier .env")
    asyncio.run(main())