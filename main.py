import os
import discord
from discord.ext import commands
from discord import app_commands

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=None, intents=intents)

@bot.event
async def on_ready():
    print(f"Bot is online! Logged in as {bot.user} (id: {bot.user.id})")
    await bot.tree.sync()

@bot.tree.command(name="getkey", description="Get the secret key")
async def getkey(interaction: discord.Interaction):
    await interaction.response.send_message("solynotissigma")

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN env var is missing!")
    bot.run(token)
