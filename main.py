import os
import time
import json
import base64
import hashlib
import asyncio
import aiohttp
import discord
from discord.ext import commands

OWNER = "SolyNot"
REPO = "discordbot"
FILE = "keys.json"
BRANCH = "main"
TOKEN = os.environ["DISCORD_TOKEN"]
GITHUB = os.environ["GITHUB_TOKEN"]
SECRET = os.environ["KEY_SECRET"]

def current_key():
    t = int(time.time() // (6 * 3600))
    return hashlib.sha256(f"{SECRET}{t}".encode()).hexdigest()[:16]

def file_content():
    return json.dumps({"current_key": current_key()}, separators=(",", ":"), sort_keys=True)

async def get_remote(session):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}"
    async with session.get(url) as r:
        if r.status == 200:
            data = await r.json()
            raw = base64.b64decode(data["content"]).decode()
            return raw, data["sha"]
        return None, None

async def put_remote(session, content, sha=None):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}"
    payload = {
        "message": "update key",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": BRANCH
    }
    if sha:
        payload["sha"] = sha
    async with session.put(url, json=payload) as r:
        print(r.status, await r.text())

async def update_github(session):
    remote, sha = await get_remote(session)
    local = file_content()
    if remote and remote.strip() == local:
        print("same key, skip")
        return
    await put_remote(session, local, sha)

async def updater():
    headers = {"Authorization": f"token {GITHUB}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            await update_github(session)
            now = time.time()
            next_time = ((now // (6 * 3600)) + 1) * (6 * 3600)
            await asyncio.sleep(max(0, next_time - now))

bot = commands.Bot(command_prefix=None, intents=discord.Intents.default())

@bot.event
async def on_ready():
    print("bot online", bot.user)
    await bot.tree.sync()
    if not getattr(bot, "_updater", False):
        bot._updater = True
        bot.loop.create_task(updater())

@bot.tree.command(name="getkey", description="Get current key")
async def getkey(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hi {interaction.user.name}! Your key:\n```{current_key()}```")

bot.run(TOKEN)
