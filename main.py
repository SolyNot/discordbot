import os
import time
import json
import base64
import hashlib
import asyncio
import aiohttp
import random
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button
from datetime import datetime, timezone
import re

OWNER = "SolyNot"
REPO = "discordbot"
FILE = "keys.json"
BRANCH = "main"
TOKEN = os.environ["DISCORD_TOKEN"]
GITHUB = os.environ["GITHUB_TOKEN"]
SECRET = os.environ["KEY_SECRET"]
TASK_STATE_FILE = "tasks_state.json"
KEY_ROTATION_INTERVAL = 6 * 3600
GENERAL_CHANNEL_ID = 1400788529516384349
MEDIA_CHANNEL_ID = 1400788552756760636
TASK_TIMEOUT = 10 * 60  # 10 minutes

def current_key():
    t = int(time.time() // KEY_ROTATION_INTERVAL)
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
            next_time = ((now // KEY_ROTATION_INTERVAL) + 1) * KEY_ROTATION_INTERVAL
            await asyncio.sleep(max(0, next_time - now))

_task_lock = asyncio.Lock()

def load_task_state():
    try:
        with open(TASK_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

async def save_task_state(state):
    async with _task_lock:
        with open(TASK_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

TASKS_POOL = [
    {"type": "general", "text": "Post a meaningful message (≥20 chars) in #general", "channel": GENERAL_CHANNEL_ID},
    {"type": "media", "text": "Post an image or media (attachment or image link) in #media", "channel": MEDIA_CHANNEL_ID},
    {"type": "discussion", "text": "Start a short discussion in #general (post + at least one reply from anyone)", "channel": GENERAL_CHANNEL_ID},
]

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix=None, intents=intents)

@bot.event
async def on_ready():
    print("bot online", bot.user)
    await bot.tree.sync()
    if not getattr(bot, "_updater", False):
        bot._updater = True
        bot.loop.create_task(updater())
    print("slash commands synced and updater started")

IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|gif|webp)(\?.*)?$", re.IGNORECASE)
def is_image_url(url: str) -> bool:
    return bool(IMAGE_EXT_RE.search(url))

async def verify_user_posted(channel: discord.TextChannel, user_id: int, after_ts: int, task_type: str) -> dict:
    after_dt = datetime.fromtimestamp(after_ts, tz=timezone.utc)
    user_messages = []
    async for msg in channel.history(limit=500, after=after_dt, oldest_first=True):
        if msg.author.id == user_id and not msg.author.bot:
            user_messages.append(msg)
            content = (msg.content or "").strip()
            if task_type == "media":
                if msg.attachments:
                    return {"ok": True, "reason": "attachment", "message_id": msg.id}
                for e in msg.embeds:
                    if e.image or e.thumbnail:
                        return {"ok": True, "reason": "embed image", "message_id": msg.id}
                for p in re.split(r"\s+", content):
                    if is_image_url(p):
                        return {"ok": True, "reason": "image link", "message_id": msg.id}
            elif task_type == "general":
                if len(content) >= 20 and len(re.findall(r"[A-Za-z\u00C0-\u017F]", content)) >= 5:
                    return {"ok": True, "reason": "text message", "message_id": msg.id}

    if task_type == "discussion":
        for msg in user_messages:
            replies = [
                r for r in (await channel.history(limit=200).flatten())
                if r.reference and r.reference.message_id == msg.id and r.author.id != user_id
            ]
            if replies:
                return {"ok": True, "reason": "discussion started", "message_id": msg.id}

    return {"ok": False, "reason": "no valid message found", "message_id": None}

class TaskView(View):
    def __init__(self, assigned_user_id: int, task_entry: dict):
        super().__init__(timeout=None)
        self.assigned_user_id = assigned_user_id
        self.task_entry = task_entry

    @discord.ui.button(label="Verify & Claim Key", style=discord.ButtonStyle.primary)
    async def verify_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.assigned_user_id:
            await interaction.response.send_message("This task isn't for you.", ephemeral=True)
            return

        tasks_state = load_task_state()
        uid = str(interaction.user.id)
        entry = tasks_state.get(uid)
        now_ts = int(time.time())

        if not entry:
            await interaction.response.send_message("No active task.", ephemeral=True)
            return

        if now_ts - entry["assigned_at"] > TASK_TIMEOUT and not entry.get("completed"):
            channel_public = interaction.guild.get_channel(entry["channel"])
            if channel_public:
                await channel_public.send(f"⏰ Task timeout for {interaction.user.mention}! Time’s up.")
            del tasks_state[uid]
            await save_task_state(tasks_state)
            await interaction.response.send_message("Task timed out. Please use /getkey again.", ephemeral=True)
            return

        if entry.get("key_given"):
            next_allowed = entry.get("next_allowed_at", 0)
            key = current_key()
            pc_copy = f"```{key}```"
            mobile_copy = f"`{key}`"
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.label = "Completed ✅"
                    child.disabled = True
            if now_ts < next_allowed:
                wait = next_allowed - now_ts
                await interaction.response.send_message(
                    f"You already got the key. Next available in {wait//60} minutes.\n"
                    f"Your key:\nPC copy: {pc_copy}\nMobile copy: {mobile_copy}",
                    view=self,
                    ephemeral=True
                )
                return
            else:
                await interaction.response.send_message(
                    f"You already got this key but here's your copy again:\nPC: {pc_copy}\nMobile: {mobile_copy}",
                    view=self,
                    ephemeral=True
                )
                return

        guild = interaction.guild
        channel_id = entry.get("channel")
        channel = guild.get_channel(channel_id) if guild else None
        if not channel:
            await interaction.response.send_message("Can't access target channel.", ephemeral=True)
            return

        v = await verify_user_posted(channel, interaction.user.id, entry["assigned_at"], entry["type"])
        if not v["ok"]:
            await interaction.response.send_message(f"Verification failed: {v['reason']}.", ephemeral=True)
            return

        entry["completed"] = True
        entry["completed_at"] = int(time.time())
        entry["message_id_evidence"] = v["message_id"]
        entry["key_given"] = True
        entry["next_allowed_at"] = int(time.time()) + KEY_ROTATION_INTERVAL
        tasks_state[uid] = entry
        await save_task_state(tasks_state)

        key = current_key()
        pc_copy = f"```{key}```"
        mobile_copy = f"`{key}`"
        await interaction.response.send_message(
            f"✅ Verification successful!\nYour key:\nPC copy: {pc_copy}\nMobile copy: {mobile_copy}",
            ephemeral=True
        )

        button.label = "Completed ✅"
        button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

@bot.tree.command(name="getkey", description="Assign or claim your key by completing a task.")
async def getkey(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    tasks_state = load_task_state()
    uid = str(interaction.user.id)
    now_ts = int(time.time())
    entry = tasks_state.get(uid)

    if entry:
        if entry.get("key_given"):
            next_allowed = entry.get("next_allowed_at", 0)
            key = current_key()
            pc_copy = f"```{key}```"
            mobile_copy = f"`{key}`"
            view = TaskView(assigned_user_id=interaction.user.id, task_entry=entry)
            for child in view.children:
                if isinstance(child, discord.ui.Button):
                    child.label = "Completed ✅"
                    child.disabled = True
            if now_ts < next_allowed:
                wait = next_allowed - now_ts
                await interaction.followup.send(
                    f"You already got the key. Next available in {wait//60} minutes.\n"
                    f"Your key:\nPC: {pc_copy}\nMobile: {mobile_copy}",
                    view=view,
                    ephemeral=True
                )
                return
            else:
                await interaction.followup.send(
                    f"You already got this key but here’s your copy again:\nPC: {pc_copy}\nMobile: {mobile_copy}",
                    view=view,
                    ephemeral=True
                )
                return

    selected = random.choice(TASKS_POOL)
    task_entry = {
        "assigned": True,
        "type": selected["type"],
        "task_text": selected["text"],
        "channel": selected["channel"],
        "assigned_at": now_ts,
        "completed": False,
        "completed_at": None,
        "key_given": False,
        "message_id_evidence": None,
        "next_allowed_at": 0
    }
    tasks_state[uid] = task_entry
    await save_task_state(tasks_state)

    channel_obj = interaction.guild.get_channel(selected["channel"])
    mention = channel_obj.mention if channel_obj else f"<#{selected['channel']}>"
    content = f"Task for {interaction.user.mention}: **{selected['text']}**\nDo it in {mention}, then run `/getkey` or click Verify."
    view = TaskView(assigned_user_id=interaction.user.id, task_entry=task_entry)
    await interaction.followup.send(content, view=view)

if __name__ == "__main__":
    bot.run(TOKEN)
