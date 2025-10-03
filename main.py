import os
import discord
from discord.ext import commands

bot = commands.Bot(command_prefix=None, intents=discord.Intents.default())

@bot.event
async def on_ready():
    print(f"Bot online as {bot.user}")
    await bot.tree.sync()

@bot.tree.command(name="getkey", description="Get your key")
async def getkey(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Hi {interaction.user.name}! Here’s your key:\n```solynotissigma```"
    )

bot.run(os.environ["DISCORD_TOKEN"])
