import os, time, hashlib, json, base64, requests, discord
from discord.ext import commands

OWNER = "SolyNot"
REPO = "discordbot"
FILE = "keys.json"
BRANCH = "main"
TOKEN = os.environ["DISCORD_TOKEN"]
GITHUB = os.environ["GITHUB_TOKEN"]
SECRET = os.environ["KEY_SECRET"]

def key():
    t = int(time.time() // (6*3600))
    return hashlib.sha256(f"{SECRET}{t}".encode()).hexdigest()[:16]

def update_github():
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}"
    headers = {"Authorization": f"token {GITHUB}"}
    content = base64.b64encode(json.dumps({"current_key": key()}).encode()).decode()

    r = requests.get(url, headers=headers)
    data = {"message":"update key","content":content,"branch":BRANCH}
    if r.status_code == 200:
        data["sha"] = r.json()["sha"]

    resp = requests.put(url, headers=headers, json=data)
    print(resp.status_code, resp.text)

bot = commands.Bot(command_prefix=None, intents=discord.Intents.default())

@bot.event
async def on_ready():
    print(f"Bot online as {bot.user}")
    await bot.tree.sync()
    update_github()

@bot.tree.command(name="getkey", description="Get current key")
async def getkey(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hi {interaction.user.name}! Your key:\n```{key()}```")

bot.run(TOKEN)
