import os
import time
import json
import base64
import hashlib
import asyncio
import random
import re
import urllib.request
import hmac
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.ui import View, Button

# --- Configuration ---
OWNER = "SolyNot"
REPO = "discordbot"
FILE = "keys.json"
BRANCH = "main"

# It is safer to use .get with a default or check existence
TOKEN = os.environ.get("DISCORD_TOKEN")
GITHUB = os.environ.get("GITHUB_TOKEN")
SECRET = os.environ.get("KEY_SECRET")

# IDs should be integers
OWNER_ID = 1082515981814988800
GENERAL_CHANNEL_ID = 1400788529516384349
MEDIA_CHANNEL_ID = 1400788552756760636

TASK_STATE_FILE = "tasks_state.json"
KEY_ROTATION_INTERVAL = 12 * 3600
TASK_TIMEOUT = 150
KEY_BYTES = 32

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')

# --- Helper Functions ---

def current_key():
    if not SECRET:
        logging.error("KEY_SECRET is missing from environment variables.")
        return "ERROR_NO_SECRET"
    
    t = int(time.time() // KEY_ROTATION_INTERVAL)
    msg = str(t).encode()
    hm = hmac.new(SECRET.encode(), msg, digestmod=hashlib.sha512).digest()
    raw = hm[:KEY_BYTES]
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")

def file_content():
    now = int(time.time())
    # REQUEST FULFILLED: "expires_at" has been removed from this dictionary
    obj = {
        "current_key": current_key(),
        "generated_at": now
    }
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)

def get_remote_sync():
    """Fetches the current key file from GitHub."""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"token {GITHUB}"})
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                raw = base64.b64decode(data["content"]).decode()
                return raw, data["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None # File doesn't exist yet
        logging.error(f"GitHub Sync Read Error: {e}")
    except Exception as e:
        logging.error(f"GitHub Sync Error: {e}")
    return None, None

def put_remote_sync(content, sha=None):
    """Updates the key file on GitHub."""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE}"
    payload = {
        "message": "update key",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": BRANCH
    }
    if sha:
        payload["sha"] = sha
    
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "Authorization": f"token {GITHUB}", 
        "Content-Type": "application/json"
    })
    
    try:
        with urllib.request.urlopen(req) as response:
            response.read()
            logging.info("GitHub keys.json updated successfully.")
    except Exception as e:
        logging.error(f"Failed to update GitHub: {e}")

# --- Async I/O Wrappers ---

_task_lock = asyncio.Lock()

def _load_json_sync():
    if not os.path.exists(TASK_STATE_FILE):
        return {}
    try:
        with open(TASK_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_json_sync(state):
    with open(TASK_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

async def load_task_state():
    """Asynchronously load task state."""
    return await bot.loop.run_in_executor(None, _load_json_sync)

async def save_task_state(state):
    """Asynchronously save task state."""
    async with _task_lock:
        await bot.loop.run_in_executor(None, _save_json_sync, state)

def update_github_sync():
    remote, sha = get_remote_sync()
    local = file_content()
    
    # Only update if content changed
    if remote and remote.strip() == local:
        return
    put_remote_sync(local, sha)

# --- Background Tasks ---

async def key_rotator():
    """Checks if key needs rotation and syncs to GitHub."""
    while True:
        try:
            await bot.loop.run_in_executor(None, update_github_sync)
        except Exception as e:
            logging.error(f"Key rotator error: {e}")

        now = time.time()
        # Calculate time until next rotation
        next_rotation = ((now // KEY_ROTATION_INTERVAL) + 1) * KEY_ROTATION_INTERVAL
        sleep_duration = max(10, next_rotation - now) # Sleep at least 10s
        await asyncio.sleep(sleep_duration)

async def check_timeouts():
    """Checks for timed out tasks every 30 seconds."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            tasks_state = await load_task_state()
            now_ts = int(time.time())
            general_channel = bot.get_channel(GENERAL_CHANNEL_ID)
            modified = False
            
            for uid, entry in list(tasks_state.items()):
                # Skip if completed, already timed out, or key already given
                if entry.get("completed") or entry.get("timed_out") or entry.get("key_given"):
                    continue

                if (now_ts - entry["assigned_at"] > TASK_TIMEOUT):
                    entry["timed_out"] = True
                    modified = True
                    
                    # Notify user in General
                    user = bot.get_user(int(uid))
                    if general_channel and user:
                        try:
                            await general_channel.send(f"{user.mention}, your task has timed out. Use `/getkey` again.")
                        except discord.HTTPException:
                            pass # Cant send message
                    
                    # Disable buttons on the interaction message
                    try:
                        interaction_channel = bot.get_channel(entry.get("interaction_channel_id"))
                        if interaction_channel:
                            msg_id = entry.get("message_id")
                            if msg_id:
                                task_message = await interaction_channel.fetch_message(msg_id)
                                view = View()
                                button = Button(label="❌ Timeout", style=discord.ButtonStyle.danger, disabled=True)
                                view.add_item(button)
                                await task_message.edit(view=view)
                    except (discord.NotFound, discord.Forbidden):
                        pass # Message deleted or no perm
                    except Exception as e:
                        logging.error(f"Error handling timeout UI: {e}")

            if modified:
                await save_task_state(tasks_state)

        except Exception as e:
            logging.error(f"Error in check_timeouts: {e}")
            
        await asyncio.sleep(30)

# --- Bot Setup ---

intents = discord.Intents.default()
intents.guilds = True
intents.members = True # Privileged intent
intents.messages = True
intents.message_content = True # Privileged intent

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

TASKS_POOL = [
    {"type": "general", "text": "Post a meaningful message (≥20 characters) in #general", "channel": GENERAL_CHANNEL_ID},
    {"type": "media", "text": "Post an image or media (attachment or image link) in #media", "channel": MEDIA_CHANNEL_ID},
    {"type": "general_reply", "text": "Reply to a message older than 1 hour in #general", "channel": GENERAL_CHANNEL_ID},
    {"type": "general_question", "text": "Ask a question in the #general channel", "channel": GENERAL_CHANNEL_ID},
    {"type": "media_multiple", "text": "Post a message with at least 2 images in #media", "channel": MEDIA_CHANNEL_ID},
    {"type": "media_reply", "text": "Reply to a message in the #media channel", "channel": MEDIA_CHANNEL_ID},
]

IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|gif|webp)(\?.*)?$", re.IGNORECASE)

def is_image_url(url: str) -> bool:
    return bool(IMAGE_EXT_RE.search(url))

async def verify_user_posted(channel: discord.TextChannel, user_id: int, after_ts: int, task_type: str) -> dict:
    after_dt = datetime.fromtimestamp(after_ts, tz=timezone.utc)
    
    # We use history to find the user's message.
    try:
        async for msg in channel.history(limit=100, after=after_dt, oldest_first=True):
            if msg.author.id != user_id or msg.author.bot:
                continue

            content = (msg.content or "").strip()
            
            if task_type == "media":
                has_attachment = bool(msg.attachments)
                has_link = any(is_image_url(p) for p in re.split(r'\s+', content))
                has_embed_img = any(e.image for e in msg.embeds)
                if has_attachment or has_link or has_embed_img:
                    return {"ok": True, "reason": "media found", "message_id": msg.id}
            
            elif task_type == "general":
                if len(content) >= 20:
                    return {"ok": True, "reason": "text message", "message_id": msg.id}
            
            elif task_type == "general_reply":
                if msg.reference and isinstance(msg.reference.resolved, discord.Message):
                    ref_msg = msg.reference.resolved
                    # Check if referenced message is older than 1 hour
                    time_diff = (msg.created_at - ref_msg.created_at).total_seconds()
                    if time_diff > 3600:
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
                    
    except Exception as e:
        logging.error(f"Error during verification: {e}")
        return {"ok": False, "reason": "error reading history", "message_id": None}
        
    return {"ok": False, "reason": "no valid message found", "message_id": None}

# --- UI Views ---

class TaskView(View):
    def __init__(self, assigned_user_id: int):
        super().__init__(timeout=None)
        self.assigned_user_id = assigned_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.assigned_user_id:
            await interaction.response.send_message("This task isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Verify & Claim Key", style=discord.ButtonStyle.primary)
    async def verify_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        tasks_state = await load_task_state()
        uid = str(interaction.user.id)
        entry = tasks_state.get(uid)

        if not entry:
            await interaction.followup.send("No active task found.", ephemeral=True)
            return
        
        if entry.get("timed_out"):
            await interaction.followup.send("Task timed out. Please use `/getkey` again.", ephemeral=True)
            return

        # If key already given, just show it again
        if entry.get("key_given"):
            key = current_key()
            await interaction.followup.send(f"You already have your key:\nPC: ```{key}```\nMobile: `{key}`", ephemeral=True)
            return

        channel = interaction.guild.get_channel(entry["channel"])
        if not channel:
            await interaction.followup.send("Bot cannot access the required channel to verify.", ephemeral=True)
            return

        # Verification Logic
        v = await verify_user_posted(channel, interaction.user.id, entry["assigned_at"], entry["type"])
        
        if not v["ok"]:
            await interaction.followup.send(f"Verification failed: {v['reason']}. Please try again.", ephemeral=True)
            return

        # Success - Update State
        entry.update({
            "completed": True,
            "completed_at": int(time.time()),
            "message_id_evidence": v["message_id"],
            "key_given": True,
        })
        tasks_state[uid] = entry
        await save_task_state(tasks_state)

        # Give Key
        key = current_key()
        await interaction.followup.send(f"Verification successful!\n\n**Your Key:**\nPC: ```{key}```\nMobile: `{key}`", ephemeral=True)

        # Update Button State
        button.label = "Completed"
        button.disabled = True
        button.style = discord.ButtonStyle.success
        try:
            await interaction.message.edit(view=self)
        except:
            pass

    @discord.ui.button(label="Cancel Task", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        tasks_state = await load_task_state()
        uid = str(interaction.user.id)
        
        if uid in tasks_state:
            del tasks_state[uid]
            await save_task_state(tasks_state)
            
        for child in self.children:
            child.disabled = True
        button.label = "Cancelled"
        
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Task cancelled.", ephemeral=True)

class KeyRevealView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Show Key", style=discord.ButtonStyle.secondary)
    async def show_key_button(self, interaction: discord.Interaction, button: Button):
        key = current_key()
        await interaction.response.send_message(f"Your key: ```{key}```", ephemeral=True)

# --- Slash Commands ---

@bot.tree.command(name="getkey", description="Get a task to complete for a key.")
async def getkey(interaction: discord.Interaction):
    tasks_state = await load_task_state()
    uid = str(interaction.user.id)
    entry = tasks_state.get(uid)

    # User has an active, non-timed-out task
    if entry and not entry.get("completed") and not entry.get("timed_out"):
        channel_mention = f"<#{entry['channel']}>"
        text = f"You have an unfinished task.\n**{entry['task_text']}**\nComplete it in {channel_mention} and click Verify below."
        # We can't easily summon the old View object if persistent views aren't setup with custom IDs, 
        # so we send a fresh message or tell them to check the old one. 
        # For simplicity, we create a new view here attached to the new message.
        view = TaskView(assigned_user_id=interaction.user.id)
        await interaction.response.send_message(text, view=view, ephemeral=True)
        return

    # User already has a key (valid session)
    if entry and entry.get("key_given") and not entry.get("timed_out"):
        key = current_key()
        view = KeyRevealView()
        await interaction.response.send_message(f"You already obtained the key.\nPC: ```{key}```\nMobile: `{key}`", view=view, ephemeral=True)
        return

    # Assign New Task
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
    content = f"**Task for {interaction.user.mention}:**\n{selected_task['text']}\n\n1. Go to {channel_mention}\n2. Complete the task.\n3. Come back here and click **Verify & Claim Key**."
    
    view = TaskView(assigned_user_id=interaction.user.id)
    await interaction.response.send_message(content, view=view, ephemeral=False)
    
    # Save message ID for timeout edits
    response_message = await interaction.original_response()
    task_entry["message_id"] = response_message.id
    task_entry["interaction_channel_id"] = interaction.channel_id
    
    tasks_state[uid] = task_entry
    await save_task_state(tasks_state)

@bot.tree.command(name="instantkey", description="Owner-only: instantly get current key.")
async def instantkey(interaction: discord.Interaction):
    is_owner = str(interaction.user.id) == str(OWNER_ID) or interaction.user.name == OWNER
    
    if not is_owner:
        await interaction.response.send_message("Only the owner can use this.", ephemeral=True)
        return

    key = current_key()
    await interaction.response.send_message(f"```{key}```", ephemeral=True)
    
    try:
        # Force sync
        await bot.loop.run_in_executor(None, update_github_sync)
        general = bot.get_channel(GENERAL_CHANNEL_ID)
        if general:
            await general.send(f"Key updated by {interaction.user.mention}.")
    except Exception as e:
        logging.error(f"Instant key error: {e}")

@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.tree.sync()
    
    if not getattr(bot, "_updater_started", False):
        bot.loop.create_task(key_rotator())
        bot.loop.create_task(check_timeouts())
        bot._updater_started = True

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        logging.critical("DISCORD_TOKEN missing from environment variables.")
