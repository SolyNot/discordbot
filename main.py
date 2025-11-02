import os
import time
import json
import base64
import hashlib
import asyncio
import random
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button
from datetime import datetime, timezone
import re
import urllib.request

OWNER = "SolyNot"
REPO = "discordbot"
FILE = "keys.json"
BRANCH = "main"
TOKEN = os.environ.get("DISCORD_TOKEN")
GITHUB = os.environ.get("GITHUB_TOKEN")
SECRET = os.environ.get("KEY_SECRET")
OWNER_ID = "1082515981814988800"
TASK_STATE_FILE = "tasks_state.json"
KEY_ROTATION_INTERVAL = 12 * 3600
TASK_TIMEOUT = 150

GENERAL_CHANNEL_ID = 1400788529516384349
MEDIA_CHANNEL_ID = 1400788552756760636

def current_key():
    t = int(time.time() // KEY_ROTATION_INTERVAL)
    return hashlib.sha256(f"{SECRET}{t}".encode()).hexdigest()[:16]

def file_content():
    return json.dumps({"current_key": current_key()}, separators=(",", ":"), sort_keys=True)

def get_remote_sync():
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"token {GITHUB}"})
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                raw = base64.b64decode(data["content"]).decode()
                return raw, data["sha"]
    except Exception as e:
        print(f"Error getting remote file: {e}")
    return None, None

def put_remote_sync(content, sha=None):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}"
    payload = {
        "message": "update key",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": BRANCH
    }
    if sha:
        payload["sha"] = sha
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="PUT", headers={"Authorization": f"token {GITHUB}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            print(response.status, response.read().decode())
    except Exception as e:
        print(f"Error putting remote file: {e}")

async def updater():
    while True:
        await bot.loop.run_in_executor(None, update_github_sync)
        now = time.time()
        next_time = ((now // KEY_ROTATION_INTERVAL) + 1) * KEY_ROTATION_INTERVAL
        await asyncio.sleep(max(0, next_time - now))

def update_github_sync():
    remote, sha = get_remote_sync()
    local = file_content()
    if remote and remote.strip() == local:
        print("same key, skip")
        return
    put_remote_sync(local, sha)

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
    {"type": "general", "text": "Post a meaningful message (≥20 characters) in #general", "channel": GENERAL_CHANNEL_ID},
    {"type": "media", "text": "Post an image or media (attachment or image link) in #media", "channel": MEDIA_CHANNEL_ID},
    {"type": "general_reply", "text": f"Reply to a message older than 1 hour in #general", "channel": GENERAL_CHANNEL_ID},
    {"type": "general_question", "text": "Ask a question in the #general channel", "channel": GENERAL_CHANNEL_ID},
    {"type": "media_multiple", "text": "Post a message with at least 2 images in #media", "channel": MEDIA_CHANNEL_ID},
    {"type": "media_reply", "text": "Reply to a message in the #media channel", "channel": MEDIA_CHANNEL_ID},
]

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

async def check_timeouts():
    await bot.wait_until_ready()
    while not bot.is_closed():
        tasks_state = load_task_state()
        now_ts = int(time.time())
        general_channel = bot.get_channel(GENERAL_CHANNEL_ID)
        modified = False
        for uid, entry in list(tasks_state.items()):
            if not entry.get("completed") and not entry.get("timed_out") and (now_ts - entry["assigned_at"] > TASK_TIMEOUT):
                entry["timed_out"] = True
                modified = True
                user = bot.get_user(int(uid))
                if general_channel and user:
                    await general_channel.send(f"{user.mention}, your task has timed out. Use `/getkey` again.")
                try:
                    interaction_channel = bot.get_channel(entry["interaction_channel_id"])
                    if interaction_channel:
                        task_message = await interaction_channel.fetch_message(entry["message_id"])
                        view = View()
                        button = Button(label="❌ Timeout", style=discord.ButtonStyle.danger, disabled=True)
                        view.add_item(button)
                        await task_message.edit(view=view)
                except discord.NotFound:
                    print(f"Task message for user {uid} not found.")
                except Exception as e:
                    print(f"Error updating timeout message: {e}")
        if modified:
            await save_task_state(tasks_state)
        await asyncio.sleep(30)

@bot.event
async def on_ready():
    print("bot online", bot.user)
    await bot.tree.sync()
    if not getattr(bot, "_updater_started", False):
        bot.loop.create_task(updater())
        bot.loop.create_task(check_timeouts())
        bot._updater_started = True
    print("slash commands synced and background tasks started")

IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|gif|webp)(\?.*)?$", re.IGNORECASE)
def is_image_url(url: str) -> bool:
    return bool(IMAGE_EXT_RE.search(url))

async def verify_user_posted(channel: discord.TextChannel, user_id: int, after_ts: int, task_type: str) -> dict:
    after_dt = datetime.fromtimestamp(after_ts, tz=timezone.utc)
    async for msg in channel.history(limit=500, after=after_dt, oldest_first=True):
        if msg.author.id == user_id and not msg.author.bot:
            content = (msg.content or "").strip()
            if task_type == "media":
                if msg.attachments or any(is_image_url(p) for p in re.split(r'\s+', content)) or any(e.image for e in msg.embeds):
                    return {"ok": True, "reason": "media found", "message_id": msg.id}
            elif task_type == "general":
                if len(content) >= 20:
                    return {"ok": True, "reason": "text message", "message_id": msg.id}
            elif task_type == "general_reply":
                if msg.reference and isinstance(msg.reference.resolved, discord.Message):
                    if (msg.created_at - msg.reference.resolved.created_at).total_seconds() > 3600:
                        return {"ok": True, "reason": "valid reply", "message_id": msg.id}
            elif task_type == "general_question":
                if content.endswith('?'):
                    return {"ok": True, "reason": "question asked", "message_id": msg.id}
            elif task_type == "media_multiple":
                image_links = [p for p in re.split(r'\s+', content) if is_image_url(p)]
                if len(msg.attachments) + len(image_links) >= 2:
                    return {"ok": True, "reason": "multiple images found", "message_id": msg.id}
            elif task_type == "media_reply":
                if msg.reference:
                    return {"ok": True, "reason": "reply in media", "message_id": msg.id}
    return {"ok": False, "reason": "no valid message found", "message_id": None}

class TaskView(View):
    def __init__(self, assigned_user_id: int):
        super().__init__(timeout=None)
        self.assigned_user_id = assigned_user_id

    @discord.ui.button(label="Verify & Claim Key", style=discord.ButtonStyle.primary)
    async def verify_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.assigned_user_id:
            await interaction.response.send_message("This task isn't for you.", ephemeral=True)
            return
        tasks_state = load_task_state()
        uid = str(interaction.user.id)
        entry = tasks_state.get(uid)
        if not entry:
            await interaction.response.send_message("No active task.", ephemeral=True)
            return
        if entry.get("timed_out"):
            await interaction.response.send_message("Task timed out. Please use /getkey again.", ephemeral=True)
            return
        if entry.get("key_given"):
            key = current_key()
            pc_copy, mobile_copy = f"```{key}```", f"`{key}`"
            await interaction.response.send_message(f"You already have your key: PC: {pc_copy}, Mobile: {mobile_copy}", ephemeral=True)
            return
        channel = interaction.guild.get_channel(entry["channel"])
        if not channel:
            await interaction.response.send_message("Cannot access the required channel.", ephemeral=True)
            return
        v = await verify_user_posted(channel, interaction.user.id, entry["assigned_at"], entry["type"])
        if not v["ok"]:
            await interaction.response.send_message(f"Verification failed: {v['reason']}.", ephemeral=True)
            return
        entry.update({
            "completed": True,
            "completed_at": int(time.time()),
            "message_id_evidence": v["message_id"],
            "key_given": True,
        })
        tasks_state[uid] = entry
        await save_task_state(tasks_state)
        key = current_key()
        pc_copy, mobile_copy = f"```{key}```", f"`{key}`"
        await interaction.response.send_message(f"✅ Verification successful!\nYour key:\nPC copy: {pc_copy}\nMobile copy: {mobile_copy}", ephemeral=True)
        button.label = "Completed ✅"
        button.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Cancel Task", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.assigned_user_id:
            await interaction.response.send_message("This task isn't for you.", ephemeral=True)
            return
        tasks_state = load_task_state()
        uid = str(interaction.user.id)
        entry = tasks_state.get(uid)
        if not entry:
            await interaction.response.send_message("No active task to cancel.", ephemeral=True)
            return
        tasks_state.pop(uid, None)
        await save_task_state(tasks_state)
        for child in self.children:
            try:
                child.disabled = True
            except Exception:
                pass
        button.label = "Cancelled"
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Your task has been cancelled.", ephemeral=True)

class KeyRevealView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Show Key", style=discord.ButtonStyle.secondary)
    async def show_key_button(self, interaction: discord.Interaction, button: Button):
        key = current_key()
        await interaction.response.send_message(f"🔑 Your key: ```{key}```", ephemeral=True)

@bot.tree.command(name="getkey", description="Get a task to complete for a key.")
async def getkey(interaction: discord.Interaction):
    tasks_state = load_task_state()
    uid = str(interaction.user.id)
    entry = tasks_state.get(uid)
    if entry and not entry.get("completed") and not entry.get("timed_out"):
        channel_mention = f"<#{entry['channel']}>"
        content = f"You have an unfinished task!\n**{entry['task_text']}**\nPlease complete it in {channel_mention} and click 'Verify' on the original message."
        await interaction.response.send_message(content, ephemeral=True)
        return
    if entry and entry.get("key_given") and not entry.get("timed_out"):
        key = current_key()
        pc_copy, mobile_copy = f"```{key}```", f"`{key}`"
        view = KeyRevealView()
        await interaction.response.send_message(f"You already got the key. Here it is again:\nPC: {pc_copy}\nMobile: {mobile_copy}", view=view, ephemeral=True)
        return
    selected_task = random.choice(TASKS_POOL)
    task_entry = {
        "type": selected_task["type"],
        "task_text": selected_task["text"],
        "channel": selected_task["channel"],
        "assigned_at": int(time.time()),
        "completed": False,
        "timed_out": False,
        "key_given": False,
    }
    channel_mention = f"<#{selected_task['channel']}>"
    content = f"Task for {interaction.user.mention}: **{selected_task['text']}**\nComplete it in {channel_mention}, then click Verify."
    view = TaskView(assigned_user_id=interaction.user.id)
    await interaction.response.send_message(content, view=view, ephemeral=False)
    response_message = await interaction.original_response()
    task_entry["message_id"] = response_message.id
    task_entry["interaction_channel_id"] = interaction.channel_id
    tasks_state[uid] = task_entry
    await save_task_state(tasks_state)

@bot.tree.command(name="instantkey", description="Owner-only: instantly get current key (no task).")
async def instantkey(interaction: discord.Interaction):
    allowed = False
    try:
        if str(interaction.user.id) == str(OWNER_ID):
            allowed = True
    except Exception:
        allowed = False
    if not allowed:
        if interaction.user.name == OWNER:
            allowed = True
    if not allowed:
        await interaction.response.send_message("❌ Only the owner can use `/instantkey`.", ephemeral=True)
        return
    key = current_key()
    await interaction.response.send_message(f"🔐 Instant key:\n```{key}```", ephemeral=True)
    try:
        general = bot.get_channel(GENERAL_CHANNEL_ID)
        if general:
            await general.send(f"{interaction.user.mention} generated the current key instantly (owner command).")
    except Exception as e:
        print("Could not send audit log to general:", e)
    try:
        bot.loop.create_task(asyncio.to_thread(update_github_sync))
    except Exception as e:
        print("Failed to schedule github sync:", e)

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("DISCORD_TOKEN environment variable not set.")
