import os, time, hashlib, json, requests, base64, discord
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
    content = base64.b64encode(json.dumps({"current_key": key()}).encode()).decode()
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}?ref={BRANCH}"
    headers = {"Authorization": f"token {GITHUB}"}
    r = requests.get(url, headers=headers)
    data = {"message":"update key","content":content,"branch":BRANCH}
    if r.status_code == 200:
        data["sha"] = r.json()["sha"]
    requests.put(f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}", headers=headers, json=data)

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
