# -*- coding:utf-8 -*-

import asyncio
import json
import logging
import os
import random
import string
import time
import zipfile
import aiofiles
import aiohttp
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.errors import UserIsBlockedError, PeerIdInvalidError

# Define and create necessary directories
all_media_dir = "Media"
if not os.path.exists(all_media_dir):
    os.makedirs(all_media_dir)

# Configure logging
LOG_FILE = "bot.log"
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# File handler
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

# Setup logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(console_handler)
logger.addHandler(file_handler)

console = Console()
SETTINGS_FILE = "settings.json"
STATE_FILE = "bot_state.json"
SESSIONS_DIR = "user_sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

# Store bot credentials globally for skip function
BOT_API_ID = None
BOT_API_HASH = None
# Store active user clients
ACTIVE_USER_CLIENTS = {}
# Store bot client globally
BOT_CLIENT = None


async def load_config():
    """Load configuration from settings file"""
    if os.path.exists(SETTINGS_FILE):
        async with aiofiles.open(SETTINGS_FILE, mode="r") as file:
            settings = json.loads(await file.read())
        api_id = settings.get("api_id")
        api_hash = settings.get("api_hash")
        admin_id = settings.get("admin_id")
        bot_token = settings.get("bot_token", "")
        session_name = settings.get("session_name", "self_destruct")
        channel_id = settings.get("channel_id")
        
        # Store globally for skip function
        global BOT_API_ID, BOT_API_HASH
        BOT_API_ID = api_id
        BOT_API_HASH = api_hash
        
        return api_id, api_hash, admin_id, bot_token, session_name, channel_id
    
    return await create_new_config()


async def create_new_config():
    """Create new configuration file"""
    console.print("[yellow]No configuration found. Let's create one.[/yellow]")
    
    console.print("\n[cyan]=== Bot Configuration ===[/cyan]")
    console.print("This bot will only use Bot Token for login.")
    console.print("Users can login with their own accounts using /login command.")
    
    api_id = input("Enter your API_ID: ").strip()
    api_hash = input("Enter your API_HASH: ").strip()
    bot_token = input("Enter your Bot Token: ").strip()
    admin_id = input("Enter the Admin ID: ").strip()
    channel_id = input("Enter the Channel ID where files should be saved (e.g., -1001234567890): ").strip()
    session_name = input("Enter session name for bot (default: 'self_destruct'): ").strip()
    
    if not session_name:
        session_name = "self_destruct"
    
    settings = {
        "api_id": api_id,
        "api_hash": api_hash,
        "admin_id": admin_id,
        "bot_token": bot_token,
        "session_name": session_name,
        "channel_id": channel_id
    }
    
    async with aiofiles.open(SETTINGS_FILE, mode="w") as file:
        await file.write(json.dumps(settings, indent=4))
    
    console.print("\n[green]Configuration saved![/green]")
    
    # Store globally for skip function
    global BOT_API_ID, BOT_API_HASH
    BOT_API_ID = api_id
    BOT_API_HASH = api_hash
    
    return api_id, api_hash, admin_id, bot_token, session_name, channel_id


async def update_channel_id(new_channel_id):
    """Update global channel ID in settings file"""
    if os.path.exists(SETTINGS_FILE):
        async with aiofiles.open(SETTINGS_FILE, mode="r") as file:
            settings = json.loads(await file.read())
        
        settings["channel_id"] = new_channel_id
        
        async with aiofiles.open(SETTINGS_FILE, mode="w") as file:
            await file.write(json.dumps(settings, indent=4))
        
        return True
    return False


async def update_user_channel_id(user_id, new_channel_id, state):
    """Update channel ID for a specific user in state"""
    if str(user_id) in state.get("user_sessions", {}):
        state["user_sessions"][str(user_id)]["channel_id"] = new_channel_id
        await save_state(state)
        return True
    return False


async def get_user_channel_id(user_id, state):
    """Get channel ID for a specific user"""
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if user_session:
        return user_session.get("channel_id")
    return None


async def load_state():
    """Load bot state from file"""
    if os.path.exists(STATE_FILE):
        async with aiofiles.open(STATE_FILE, mode="r") as file:
            return json.loads(await file.read())
    return {"letter_counter": 0, "user_folders": {}, "user_sessions": {}, "login_sessions": {}}


async def save_state(state):
    """Save bot state to file"""
    async with aiofiles.open(STATE_FILE, mode="w") as file:
        await file.write(json.dumps(state, indent=4))


def get_user_session_file(user_id):
    """Get session file path for a user"""
    return os.path.join(SESSIONS_DIR, f"user_{user_id}.session")


class RichDownloadProgress:
    """Enhanced progress bar for downloads"""
    def __init__(self, filename, total_size):
        self.filename = filename
        self.total_size = total_size
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console
        )
        self.task = self.progress.add_task(f"[cyan]Downloading {filename}", total=total_size)
        self.progress.start()
    
    def update(self, downloaded):
        self.progress.update(self.task, completed=downloaded)
    
    def close(self):
        self.progress.stop()

async def is_admin(event, admin_id):
    """Check if sender is admin"""
    if admin_id is None:
        return False

    try:
        return event.sender_id == int(admin_id)
    except Exception:
        return False

async def safe_notify(bot_client, user_id, text):
    """
    Safely notify a user.
    If bot cannot DM user, silently ignore.
    """
    try:
        await bot_client.send_message(
            user_id,
            text,
            silent=True
        )

    except (UserIsBlockedError, PeerIdInvalidError):
        # User blocked bot OR never started bot
        return

    except ValueError as e:
        # Entity not found (VERY COMMON)
        if "Could not find the input entity" in str(e):
            return

    except FloodWaitError as e:
        logger.warning(f"FloodWait {e.seconds}s while notifying {user_id}")

    except Exception as e:
        logger.debug(f"safe_notify skipped for {user_id}: {e}")


async def handle_start(event, admin_id):
    """Handle /start command"""
    if event.is_private:
        welcome_message = (
            "🤖 **Welcome to Self-Destructing Media Downloader Bot!**\n\n"
            "This bot can download media files and save them to a channel.\n\n"
            "**Features:**\n"
            "• Download photos, videos, documents\n"
            "• Send files to your personal channel\n"
            "• Progress tracking\n\n"
            "**For Users:**\n"
            "1. Use /login to login with your own account\n"
            "2. Use /setchannel to set your personal channel\n"
            "3. Your self-destructing media will be saved to your channel\n\n"
            "**For Admin:**\n"
            "Use /help to see all available commands.\n\n"
            "Enjoy using the bot!"
        )
        await event.reply(welcome_message, parse_mode='markdown')


async def handle_login(event, admin_id, bot_client, state):
    """Handle /login command for user session login"""
    if not event.is_private:
        await event.reply("❌ Please use this command in private chat.")
        return
    
    user_id = event.sender_id
    user_session_file = get_user_session_file(user_id)
    
    # Check if user already has a session
    if os.path.exists(user_session_file) and str(user_id) in state.get("user_sessions", {}):
        await event.reply(
            "✅ You are already logged in!\n"
            "You can now receive and save self-destructing media from your account.\n\n"
            "Set your personal channel: /setchannel\n"
            "Check your status: /mystatus\n\n"
            "To logout, use /logout command."
        )
        return
    
    # Start login process - Step 1
    message = (
        "**1. Send Your API ID.**\n\n"
        "Click On /skip To Skip This Process\n\n"
        "**NOTE :- If You Skip This Then Your Account Ban Chance Is High.**\n\n"
        "To get API ID and API HASH, visit: https://my.telegram.org"
    )
    
    # Initialize login session
    if "login_sessions" not in state:
        state["login_sessions"] = {}
    
    state["login_sessions"][str(user_id)] = {
        "step": "api_id",
        "api_id": None,
        "api_hash": None,
        "phone": None,
        "phone_code_hash": None,
        "temp_client": None
    }
    
    await save_state(state)
    await event.reply(message, parse_mode='markdown')


async def handle_skip(event, admin_id, state):
    """Handle /skip command during login"""
    if not event.is_private:
        return
    
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    
    if not user_data:
        await event.reply("❌ No login session found. Please start with /login command.")
        return
    
    if user_data.get("step") != "api_id":
        await event.reply("❌ Cannot skip at this step.")
        return
    
    # Skip API ID - use bot's API ID
    global BOT_API_ID, BOT_API_HASH
    if BOT_API_ID is None or BOT_API_HASH is None:
        # Load config if not loaded
        BOT_API_ID, BOT_API_HASH, _, _, _, _ = await load_config()
    
    state["login_sessions"][str(user_id)]["api_id"] = BOT_API_ID
    state["login_sessions"][str(user_id)]["api_hash"] = BOT_API_HASH
    state["login_sessions"][str(user_id)]["step"] = "phone"
    await save_state(state)
    
    await event.reply(
        "⚠️ **Warning:** Using bot's API credentials may increase ban risk!\n\n"
        "**3. Please send your phone number which includes country code**\n"
        "Example: +13124562345, +9171828181889\n\n"
        "Enter /cancel to cancel the process"
    )


async def handle_api_id(event, admin_id, state):
    """Handle API ID input"""
    if not event.is_private:
        return
    
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    
    if not user_data or user_data.get("step") != "api_id":
        return
    
    text = event.text.strip()
    
    # Check if user wants to cancel
    if text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
    
    # Check if user wants to skip
    if text.lower() == "/skip":
        await handle_skip(event, admin_id, state)
        return
    
    # Try to parse as integer
    try:
        api_id = int(text)
        
        # Validate API ID (should be a positive integer)
        if api_id <= 0:
            await event.reply("❌ API ID must be a positive number.\nExample: `1234567`\nEnter /cancel to cancel", parse_mode='markdown')
            return
        
        # Store in state
        state["login_sessions"][str(user_id)]["api_id"] = api_id
        state["login_sessions"][str(user_id)]["step"] = "api_hash"
        await save_state(state)
        
        await event.reply(
            "✅ **API ID saved!**\n\n"
            "**2. Now Send Me Your API HASH**\n\n"
            "Enter /cancel to cancel the process\n"
            "Example: `a1b2c3d4e5f67890abcdef1234567890`"
        )
        
    except ValueError:
        await event.reply("❌ Invalid API_ID. Please send a valid number.\nExample: `1234567`\nEnter /cancel to cancel", parse_mode='markdown')


async def handle_api_hash(event, admin_id, state):
    """Handle API Hash input"""
    if not event.is_private:
        return
    
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    
    if not user_data or user_data.get("step") != "api_hash":
        await event.reply("❌ Please complete previous steps first.")
        return
    
    text = event.text.strip()
    
    # Check if user wants to cancel
    if text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
    
    # The text is the API hash
    api_hash = text
    
    # Update state
    state["login_sessions"][str(user_id)]["api_hash"] = api_hash
    state["login_sessions"][str(user_id)]["step"] = "phone"
    await save_state(state)
    
    await event.reply(
        "**3. Please send your phone number which includes country code**\n"
        "Example: +13124562345, +9171828181889\n\n"
        "Enter /cancel to cancel the process"
    )


async def handle_phone(event, admin_id, state, bot_client):
    """Handle phone number input"""
    if not event.is_private:
        return
    
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    
    if not user_data or user_data.get("step") != "phone":
        await event.reply("❌ Please complete previous steps first.")
        return
    
    text = event.text.strip()
    
    # Check if user wants to cancel
    if text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
    
    phone = text
    
    # Validate phone format
    if not phone.startswith('+'):
        await event.reply("❌ Phone number must start with country code (e.g., +1, +91)\nExample: `+1234567890`", parse_mode='markdown')
        return
    
    try:
        api_id = user_data["api_id"]
        api_hash = user_data["api_hash"]
        
        # Create a unique session for this user
        session = StringSession()
        user_client = TelegramClient(session, api_id, api_hash)
        
        # Store in state
        state["login_sessions"][str(user_id)]["phone"] = phone
        state["login_sessions"][str(user_id)]["step"] = "code"
        await save_state(state)
        
        # Send code request
        await user_client.connect()
        sent_code = await user_client.send_code_request(phone)
        
        # Save session string to state (this is serializable)
        session_string = user_client.session.save()
        state["login_sessions"][str(user_id)]["session_string"] = session_string
        state["login_sessions"][str(user_id)]["phone_code_hash"] = sent_code.phone_code_hash
        await save_state(state)
        
        # Disconnect the client for now
        await user_client.disconnect()
        
        await event.reply(
            "**4. Sending OTP...**\n\n"
            "**5. Please check for an OTP in official telegram account. If you got it, send OTP here after reading the below format.**\n\n"
            "If OTP is 12345, please send it as `1 2 3 4 5`.\n\n"
            "Enter /cancel to cancel The Process"
        )
        
    except Exception as e:
        error_msg = str(e).lower()
        if "phone" in error_msg and "invalid" in error_msg:
            await event.reply("❌ Invalid phone number. Please check and try again.\nExample: `+1234567890`", parse_mode='markdown')
        elif "flood" in error_msg:
            await event.reply("❌ Too many attempts. Please wait before trying again.")
        else:
            await event.reply(f"❌ Error: {str(e)}")
        
        # Clean up
        if str(user_id) in state["login_sessions"]:
            del state["login_sessions"][str(user_id)]
        await save_state(state)


async def handle_code(event, admin_id, state):
    """Handle verification code input"""
    if not event.is_private:
        return
    
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    
    if not user_data or user_data.get("step") != "code":
        await event.reply("❌ Please complete previous steps first.")
        return
    
    # Check if user wants to cancel
    if event.text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
    
    # Extract code (remove spaces if user sent with spaces)
    code = event.text.strip().replace(' ', '')
    
    if not code.isdigit():
        await event.reply("❌ Invalid OTP format. Please send only numbers.\nExample: `1 2 3 4 5` or `12345`", parse_mode='markdown')
        return
    
    if len(code) < 4 or len(code) > 6:
        await event.reply("❌ OTP should be 4-6 digits. Please check and try again.")
        return
    
    try:
        phone = user_data["phone"]
        phone_code_hash = user_data["phone_code_hash"]
        session_string = user_data.get("session_string")
        
        if not session_string:
            await event.reply("❌ Session data missing. Please restart login with /login")
            return
        
        # Recreate client from session string
        session = StringSession(session_string)
        user_client = TelegramClient(
            session,
            user_data["api_id"],
            user_data["api_hash"]
        )
        
        await user_client.connect()
        
        # Try to sign in
        try:
            await user_client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=phone_code_hash
            )
            
            # Login successful
            await complete_user_login(event, user_id, user_client, state)
            
        except Exception as e:
            error_msg = str(e).lower()
            # Check if 2FA is required
            if "two-steps" in error_msg or "2fa" in error_msg or "password" in error_msg:
                # Save updated session string
                new_session_string = user_client.session.save()
                state["login_sessions"][str(user_id)]["session_string"] = new_session_string
                state["login_sessions"][str(user_id)]["step"] = "2fa"
                await save_state(state)
                
                await event.reply(
                    "**6. Your account has enabled two-step verification. Please provide the password.**\n\n"
                    "Enter /cancel to cancel The Process"
                )
                await user_client.disconnect()
            else:
                # Invalid code
                if "code" in error_msg and "invalid" in error_msg:
                    await event.reply("❌ Invalid OTP. Please check and try again.\nIf OTP is 12345, send as `1 2 3 4 5`", parse_mode='markdown')
                elif "code" in error_msg and "expired" in error_msg:
                    await event.reply("❌ OTP expired. Please restart login process with /login")
                    # Clean up
                    if str(user_id) in state["login_sessions"]:
                        del state["login_sessions"][str(user_id)]
                    await save_state(state)
                else:
                    await event.reply(f"❌ Error: {str(e)}")
                
                await user_client.disconnect()
        
    except Exception as e:
        await event.reply(f"❌ Error: {str(e)}")
        if 'user_client' in locals():
            try:
                await user_client.disconnect()
            except:
                pass


async def handle_2fa(event, admin_id, state):
    """Handle 2FA password input"""
    if not event.is_private:
        return
    
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    
    if not user_data or user_data.get("step") != "2fa":
        await event.reply("❌ Please complete previous steps first.")
        return
    
    # Check if user wants to cancel
    if event.text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
    
    password = event.text.strip()
    
    if not password:
        await event.reply("❌ Please provide your 2FA password.\nEnter /cancel to cancel", parse_mode='markdown')
        return
    
    try:
        session_string = user_data.get("session_string")
        
        if not session_string:
            await event.reply("❌ Session data missing. Please restart login with /login")
            return
        
        # Recreate client from session string
        session = StringSession(session_string)
        user_client = TelegramClient(
            session,
            user_data["api_id"],
            user_data["api_hash"]
        )
        
        await user_client.connect()
        
        # Complete sign in with 2FA
        await user_client.sign_in(password=password)
        
        # Login successful
        await complete_user_login(event, user_id, user_client, state)
        
    except Exception as e:
        error_msg = str(e).lower()
        if "password" in error_msg and "invalid" in error_msg:
            await event.reply("❌ Invalid 2FA password. Please try again.")
        else:
            await event.reply(f"❌ Error: {str(e)}")
        
        if 'user_client' in locals():
            try:
                await user_client.disconnect()
            except:
                pass


async def setup_user_client_handlers(user_client, user_id, bot_client, state):
    """Setup event handlers for user client to catch self-destructing media"""
    
    console.print(f"[yellow]Setting up handlers for user {user_id}[/yellow]")
    
    @user_client.on(events.NewMessage(incoming=True))
    async def user_media_handler(event):
        try:
            # 🚫 Ignore outgoing messages
            if event.out:
                return

            # 🚫 Ignore non-private chats
            if not event.is_private:
                return

            # 🚫 Ignore messages without media
            if not event.media:
                return

            msg = event.message

            # ✅ Check for TTL (self-destructing media)
            ttl = None
            if hasattr(msg, 'media') and msg.media:
                if hasattr(msg.media, 'ttl_seconds'):
                    ttl = msg.media.ttl_seconds
                elif hasattr(msg.media, 'photo') and hasattr(msg.media.photo, 'ttl_seconds'):
                    ttl = msg.media.photo.ttl_seconds
                elif hasattr(msg.media, 'document') and hasattr(msg.media.document, 'ttl_seconds'):
                    ttl = msg.media.document.ttl_seconds
                elif hasattr(msg.media, 'video') and hasattr(msg.media.video, 'ttl_seconds'):
                    ttl = msg.media.video.ttl_seconds
                elif hasattr(msg.media, 'video_note') and hasattr(msg.media.video_note, 'ttl_seconds'):
                    ttl = msg.media.video_note.ttl_seconds
                elif hasattr(msg.media, 'voice') and hasattr(msg.media.voice, 'ttl_seconds'):
                    ttl = msg.media.voice.ttl_seconds
                elif hasattr(msg.media, 'audio') and hasattr(msg.media.audio, 'ttl_seconds'):
                    ttl = msg.media.audio.ttl_seconds

            # ❌ Ignore normal media (no TTL) - SILENTLY
            if not ttl:
                return  # ✅ No console print, just return

            # ✅ Only log self-destructing media
            console.print(f"[green]⚠️ Self-destructing media detected for user {user_id} (TTL: {ttl}s)[/green]")
            
            # ✅ Download immediately
            await user_downloader(
                event,
                user_client,
                bot_client,
                all_media_dir,
                state
            )

            console.print(f"[magenta]✅ Media saved for user {user_id}[/magenta]")
            
            # ✅ Log the successful save
            logger.info(f"User {user_id} saved self-destructing media (TTL: {ttl}s)")

        except Exception as e:
            # Only log actual errors
            console.print(f"[red]❌ Error in user media handler for {user_id}: {e}[/red]")
            logger.error(f"User media handler error for {user_id}: {e}")


async def complete_user_login(event, user_id, user_client, state):
    """Complete user login process"""
    try:
        # Get user info
        me = await user_client.get_me()
        
        # Save session to file
        session_string = user_client.session.save()
        user_session_file = get_user_session_file(user_id)
        
        async with aiofiles.open(user_session_file, mode="w") as f:
            await f.write(session_string)
        
        # Update state
        if "user_sessions" not in state:
            state["user_sessions"] = {}
        
        state["user_sessions"][str(user_id)] = {
            "api_id": user_client.api_id,
            "api_hash": user_client.api_hash,
            "username": me.username,
            "phone": me.phone,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "session_file": user_session_file,
            "login_time": time.time(),
            "channel_id": None  # Initialize with no channel
        }
        
        # Clean up login session
        if "login_sessions" in state and str(user_id) in state["login_sessions"]:
            del state["login_sessions"][str(user_id)]
        
        await save_state(state)
        
        # Get bot client
        global BOT_CLIENT
        if BOT_CLIENT:
            # Setup handlers for user client
            await setup_user_client_handlers(user_client, user_id, BOT_CLIENT, state)
        
        # Store user client in global dictionary
        global ACTIVE_USER_CLIENTS
        ACTIVE_USER_CLIENTS[str(user_id)] = user_client
        
        # Start the user client in background
        await user_client.start()
        
        # Send welcome message
        welcome_msg = (
            f"✅ **Login Successful!**\n\n"
            f"👤 **Account Details:**\n"
            f"• Name: {me.first_name} {me.last_name if me.last_name else ''}\n"
            f"• Username: @{me.username if me.username else 'Not set'}\n"
            f"• Phone: {me.phone}\n"
            f"• User ID: {me.id}\n\n"
            f"📱 **Now you can:**\n"
            f"• Set your personal channel: /setchannel\n"
            f"• Self-destructing media will be automatically saved to your channel\n"
            f"• Use /mystatus to check your session\n"
            f"• Use /logout to logout\n\n"
        )
        
        await event.reply(welcome_msg, parse_mode='markdown')
        
        console.print(f"[green]✓ User {user_id} (@{me.username}) logged in successfully[/green]")
        
    except Exception as e:
        await event.reply(f"❌ Error completing login: {str(e)}")
        console.print(f"[red]Error completing login for user {user_id}: {e}[/red]")
        
        # Clean up on error
        if str(user_id) in state.get("login_sessions", {}):
            try:
                await user_client.disconnect()
            except:
                pass
            del state["login_sessions"][str(user_id)]
            await save_state(state)

async def send_to_user_channel(user_client, file_path, username, channel_id):
    """Send file to user's personal channel using USER'S client"""
    try:
        if not os.path.exists(file_path):
            logger.error("File does not exist")
            return False

        # Normalize channel_id
        try:
            channel_id = int(channel_id)
        except Exception:
            logger.error(f"Invalid channel_id: {channel_id}")
            return False

        # ✅ NEW: Check if channel_id is negative (should be for channels)
        if channel_id >= 0:
            console.print(f"[red]ERROR: Channel ID is positive ({channel_id}). This is likely a USER ID, not a CHANNEL ID![/red]")
            return False

        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        filename = os.path.basename(file_path)

        caption = (
            f"📥 Downloaded from: @{username}\n"
            f"📁 File: {filename}\n"
            f"📊 Size: {file_size_mb:.2f} MB\n"
            f"🕒 Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        console.print(f"[yellow]Sending to user's channel {channel_id} using USER client[/yellow]")

        # Try to send file using user's client
        try:
            await user_client.send_file(
                channel_id,
                file=file_path,
                caption=caption
            )
            console.print(f"[green]✓ File sent to user's channel {channel_id} using USER client[/green]")
            return True
        except Exception as e:
            console.print(f"[red]Failed to send to user's channel: {e}[/red]")
            
            # Check if it's a common error
            error_str = str(e).lower()
            
            # If it's a "PeerUser" error, it means channel_id is actually a user ID
            if "peeruser" in error_str or "user_id" in error_str:
                console.print(f"[red]CRITICAL: Channel ID {channel_id} is actually a USER ID, not a channel![/red]")
                console.print(f"[red]User needs to set a proper channel ID starting with -100[/red]")
                return False
            
            # Try alternative method
            try:
                entity = await user_client.get_entity(channel_id)
                await user_client.send_file(
                    entity,
                    file=file_path,
                    caption=caption
                )
                console.print(f"[green]✓ File sent via entity using USER client[/green]")
                return True
            except Exception as e2:
                console.print(f"[red]Entity send failed using USER client: {e2}[/red]")
                return False

    except Exception as e:
        console.print(f"[red]send_to_user_channel fatal error: {e}[/red]")
        return False

async def send_to_admin_channel(bot_client, file_path, username, channel_id):
    """Send file to admin's channel using BOT client"""
    try:
        if not os.path.exists(file_path):
            logger.error("File does not exist")
            return False

        # Normalize channel_id
        try:
            channel_id = int(channel_id)
        except Exception:
            logger.error(f"Invalid channel_id: {channel_id}")
            return False

        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        filename = os.path.basename(file_path)

        caption = (
            f"📥 Downloaded from: @{username}\n"
            f"📁 File: {filename}\n"
            f"📊 Size: {file_size_mb:.2f} MB\n"
            f"🕒 Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        console.print(f"[yellow]Sending to admin's channel {channel_id} using BOT client[/yellow]")

        # Try to send file using bot's client
        try:
            await bot_client.send_file(
                channel_id,
                file=file_path,
                caption=caption
            )
            console.print(f"[green]✓ File sent to admin's channel {channel_id} using BOT client[/green]")
            return True
        except Exception as e:
            console.print(f"[red]Failed to send to admin's channel: {e}[/red]")
            
            # Try alternative method
            try:
                entity = await bot_client.get_entity(channel_id)
                await bot_client.send_file(
                    entity,
                    file=file_path,
                    caption=caption
                )
                console.print(f"[green]✓ File sent via entity using BOT client[/green]")
                return True
            except Exception as e2:
                console.print(f"[red]Entity send failed using BOT client: {e2}[/red]")
                return False

    except Exception as e:
        console.print(f"[red]send_to_admin_channel fatal error: {e}[/red]")
        return False
        
async def user_downloader(event, user_client, bot_client, all_media_dir, state):
    """Download media from user's account and send to BOTH channels"""
    try:
        # ✅ FIX: Pehle receiver (logged-in user) ka ID nikalo
        try:
            receiver_entity = await user_client.get_me()
            receiver_id = receiver_entity.id
            receiver_username = receiver_entity.username if receiver_entity.username else "NoUsername"
        except:
            receiver_id = "Unknown"
            receiver_username = "Unknown"
        
        console.print(f"[cyan]Receiver (logged-in user): {receiver_username} (ID: {receiver_id})[/cyan]")
        
        # ✅ Ab sender (jo media bhej raha hai) ka info nikalo
        try:
            sender = await event.get_sender()
            sender_username = sender.username if sender.username else "NoUsername"
            sender_id = sender.id if sender.id else "Unknown"
        except:
            sender_username = "Unknown"
            sender_id = "Unknown"

        console.print(f"[cyan]Downloading from @{sender_username} (Sender ID: {sender_id}) to @{receiver_username} (Receiver ID: {receiver_id})[/cyan]")

        # Find existing folder or create new one
        # ✅ SENDER ke info se folder banaye (organization ke liye)
        user_folder_key = f"{sender_username}_{sender_id}"

        if user_folder_key in state["user_folders"]:
            user_folder_name = state["user_folders"][user_folder_key]
        else:
            counter = state["letter_counter"]
            letter = string.ascii_uppercase[counter % 26]
            user_folder_name = f"{counter:02d} - {letter} - @{sender_username} - {sender_id}"
            state["user_folders"][user_folder_key] = user_folder_name
            state["letter_counter"] += 1
            await save_state(state)

        user_folder_path = os.path.join(all_media_dir, user_folder_name)
        os.makedirs(user_folder_path, exist_ok=True)

        # Generate unique filename
        timestamp = int(time.time())
        random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

        # Determine file type and extension
        if event.photo:
            file_ext = ".jpg"
            media_type = "photo"
        elif event.video:
            file_ext = ".mp4"
            media_type = "video"
        elif event.document:
            if hasattr(event.document, 'attributes') and event.document.attributes:
                for attr in event.document.attributes:
                    if hasattr(attr, 'file_name') and attr.file_name:
                        file_ext = os.path.splitext(attr.file_name)[1]
                        break
                else:
                    file_ext = ".bin"
            else:
                file_ext = ".bin"
            media_type = "document"
        elif event.audio:
            file_ext = ".mp3"
            media_type = "audio"
        elif event.voice:
            file_ext = ".ogg"
            media_type = "voice"
        elif event.video_note:
            file_ext = ".mp4"
            media_type = "video_note"
        else:
            file_ext = ".bin"
            media_type = "unknown"

        filename = f"{timestamp}_{random_str}{file_ext}"
        file_path = os.path.join(user_folder_path, filename)

        # Download with progress
        file_size = event.file.size if event.file else 0
        console.print(f"[cyan]File size: {file_size} bytes[/cyan]")

        progress = RichDownloadProgress(filename, file_size) if file_size > 0 else None

        await event.download_media(
            file=file_path,
            progress_callback=lambda c, t: progress.update(c) if progress else None
        )

        if progress:
            progress.close()

        # Verify save
        if not os.path.exists(file_path):
            console.print("[red]ERROR: File was not saved[/red]")
            return

        actual_size = os.path.getsize(file_path)
        file_size_mb = actual_size / (1024 * 1024) if actual_size > 0 else 0

        console.print(
            f"[green]✓ Downloaded {media_type} ({file_size_mb:.2f} MB) from @{sender_username} → {filename}[/green]"
        )
        logger.info(
            f"Downloaded {media_type} ({file_size_mb:.2f} MB) from @{sender_username} to @{receiver_username} → {filename}"
        )

        # ✅ IMPORTANT FIX: Ab RECEIVER ka ID use karo user session dhoondne ke liye
        # Send to BOTH channels if configured
        # 1. First check if RECEIVER (logged-in user) has personal channel
        user_session = state.get("user_sessions", {}).get(str(receiver_id))  # ✅ CHANGE HERE
        
        user_channel_id = None
        if user_session:
            # Use RECEIVER's personal channel if set
            user_channel_id = user_session.get("channel_id")
            console.print(f"[cyan]RECEIVER's personal channel ID: {user_channel_id}[/cyan]")
        else:
            console.print(f"[yellow]No user session found for receiver ID: {receiver_id}[/yellow]")
            # Debug: Print all user sessions
            console.print(f"[yellow]Available user sessions: {list(state.get('user_sessions', {}).keys())}[/yellow]")
        
        # 2. Get admin's global channel
        admin_channel_id = getattr(bot_client, "channel_id", None)
        console.print(f"[cyan]Admin global channel ID: {admin_channel_id}[/cyan]")
        
        # Track sending status
        sent_to_user_channel = False
        sent_to_admin_channel = False
        
        # Send to RECEIVER's personal channel FIRST (using RECEIVER'S client)
        if user_channel_id:
            try:
                success = await send_to_user_channel(user_client, file_path, sender_username, user_channel_id)
                if success:
                    console.print(f"[green]✓ File sent to RECEIVER's personal channel {user_channel_id}[/green]")
                    sent_to_user_channel = True
                else:
                    console.print(f"[red]Failed to send to RECEIVER's personal channel {user_channel_id}[/red]")
                    logger.warning(f"File saved but failed to send to RECEIVER's channel: {filename}")
            except Exception as e:
                console.print(f"[red]RECEIVER channel upload error: {e}[/red]")
                logger.error(f"RECEIVER channel upload error: {e}")
        
        # Send to admin's global channel SECOND (using BOT client)
        if admin_channel_id:
            # Check if admin channel is different from RECEIVER's channel
            if admin_channel_id != user_channel_id:
                try:
                    success = await send_to_admin_channel(bot_client, file_path, sender_username, admin_channel_id)
                    if success:
                        console.print(f"[green]✓ File sent to admin's global channel {admin_channel_id}[/green]")
                        sent_to_admin_channel = True
                    else:
                        console.print(f"[red]Failed to send to admin's global channel {admin_channel_id}[/red]")
                        logger.warning(f"File saved but failed to send to admin channel: {filename}")
                except Exception as e:
                    console.print(f"[red]Admin channel upload error: {e}[/red]")
                    logger.error(f"Admin channel upload error: {e}")
            else:
                console.print("[yellow]Admin channel and RECEIVER channel are same, skipping duplicate send[/yellow]")
                sent_to_admin_channel = True  # Already sent via RECEIVER channel
        
        # Send summary
        if sent_to_user_channel or sent_to_admin_channel:
            channels_sent = []
            if sent_to_user_channel:
                channels_sent.append("RECEIVER's personal channel")
                        
            console.print(f"[green]✓ File sent to: {', '.join(channels_sent)}[/green]")
            
            # ✅ Also notify the receiver about the save
            try:
                global BOT_CLIENT
                if BOT_CLIENT:
                    channel_names = []
                    if sent_to_user_channel:
                        channel_names.append("your personal channel")
                    
            except Exception as e:
                console.print(f"[yellow]Could not notify user: {e}[/yellow]")
        else:
            console.print("[yellow]No channel configured, file saved locally only[/yellow]")

    except Exception as e:
        console.print(f"[red]Error in user_downloader: {e}[/red]")
        logger.error(f"User downloader error: {e}")

async def handle_cancel(event, admin_id, state):
    """Handle /cancel command during login"""
    if not event.is_private:
        return
    
    user_id = event.sender_id
    
    # Check if user is in login process
    if str(user_id) not in state.get("login_sessions", {}):
        await event.reply("❌ No active login session to cancel.")
        return
    
    # Remove login session
    del state["login_sessions"][str(user_id)]
    await save_state(state)
    
    await event.reply("❌ Login process cancelled.")


async def handle_logout(event, admin_id, state):
    """Handle /logout command"""
    if not event.is_private:
        await event.reply("❌ Please use this command in private chat.")
        return
    
    user_id = event.sender_id
    
    # Check if user has a session
    if str(user_id) not in state.get("user_sessions", {}):
        await event.reply("❌ You are not logged in.")
        return
    
    # Remove session file
    user_session_file = get_user_session_file(user_id)
    if os.path.exists(user_session_file):
        os.remove(user_session_file)
    
    # Remove from state
    if "user_sessions" in state and str(user_id) in state["user_sessions"]:
        del state["user_sessions"][str(user_id)]
    
    # Disconnect and remove active user client
    global ACTIVE_USER_CLIENTS
    if str(user_id) in ACTIVE_USER_CLIENTS:
        try:
            await ACTIVE_USER_CLIENTS[str(user_id)].disconnect()
        except:
            pass
        del ACTIVE_USER_CLIENTS[str(user_id)]
    
    await save_state(state)
    
    await event.reply("✅ Successfully logged out. Your session has been removed.")


async def handle_mystatus(event, admin_id, state):
    """Handle /mystatus command to show user session status"""
    if not event.is_private:
        await event.reply("❌ Please use this command in private chat.")
        return
    
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    
    if not user_session:
        await event.reply(
            "❌ **You are not logged in.**\n\n"
            "To login with your own account:\n"
            "1. Use /login command\n"
            "2. Follow the steps to enter:\n"
            "   - API_ID\n"
            "   - API_HASH\n"
            "   - Phone number\n"
            "   - Verification code\n"
            "   - 2FA password (if enabled)"
        )
        return
    
    # Calculate login duration
    login_time = user_session.get("login_time", time.time())
    duration = time.time() - login_time
    hours = int(duration // 3600)
    minutes = int((duration % 3600) // 60)
    
    # Check if user has personal channel
    channel_id = user_session.get("channel_id")
    channel_status = "✅ Set" if channel_id else "❌ Not set"
    
    if channel_id:
        try:
            entity = await event.client.get_entity(channel_id)
            channel_name = getattr(entity, 'title', 'Unknown')
            channel_info = f"• Channel: {channel_name}\n• ID: {channel_id}"
        except:
            channel_info = f"• Channel ID: {channel_id} (Unable to access)"
    else:
        channel_info = "• Use /setchannel to set your personal channel"
    
    await event.reply(
        f"✅ **Logged In**\n\n"
        f"👤 **Account Details:**\n"
        f"• Name: {user_session.get('first_name', 'Unknown')} {user_session.get('last_name', '')}\n"
        f"• Username: @{user_session.get('username', 'Not set')}\n"
        f"• Phone: {user_session.get('phone', 'Unknown')}\n"
        f"• API_ID: {user_session.get('api_id')}\n"
        f"• Logged in for: {hours}h {minutes}m\n\n"
        f"📱 **Settings:**\n"
        f"• Personal Channel: {channel_status}\n"
        f"{channel_info}\n\n"
        f"📥 **Status:**\n"
        f"• Self-destructing media monitoring: ✅ Active\n"
        f"• Files saved to your channel: {'✅' if channel_id else '❌'}\n\n"
        f"⚠️ **Security:**\n"
        f"• Use /logout when done\n"
        f"• Session stored securely"
    )

async def handle_mychannel(event, admin_id, state):
    """Show user's personal channel configuration"""
    if not event.is_private:
        await event.reply("❌ Please use this command in private chat.")
        return
    
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    
    if not user_session:
        await event.reply("❌ You are not logged in. Use /login first.")
        return
    
    channel_id = user_session.get("channel_id")
    
    if channel_id:
        # ✅ NEW: Check if channel_id is valid format
        try:
            channel_id_int = int(channel_id)
            
            if channel_id_int >= 0:
                await event.reply(
                    "⚠️ **INVALID CHANNEL ID FORMAT!**\n\n"
                    "Your current channel ID is a **USER ID** (positive number).\n"
                    "**Channel IDs must start with `-100`** (negative number).\n\n"
                    f"**Current (Wrong):** `{channel_id}`\n"
                    f"**Should be like:** `-1001234567890`\n\n"
                    "**To fix this:**\n"
                    "1. Get your correct channel ID:\n"
                    "   - Add @getidsbot to your channel\n"
                    "   - Send any message\n"
                    "   - Copy the ID (starts with -100)\n"
                    "2. Use `/setchannel -1001234567890` to update"
                )
                return
            
            try:
                entity = await event.client.get_entity(channel_id_int)
                await event.reply(
                    f"📢 **Your Personal Channel Configuration**\n"
                    f"• Channel: {getattr(entity, 'title', 'Unknown')}\n"
                    f"• ID: `{channel_id}`\n"
                    f"• Username: @{getattr(entity, 'username', 'None')}\n"
                    f"• Your self-destructing media will be sent to this channel.\n"
                )
            except Exception as e:
                await event.reply(
                    f"⚠️ Your channel ID is set to `{channel_id}`, but I can't access it.\n"
                    f"Error: {str(e)}\n"
                    f"Make sure the bot is added as admin to this channel.\n"
                    f"Use /setchannel to update your channel."
                )
        except ValueError:
            await event.reply(
                f"⚠️ **Invalid Channel ID Format!**\n"
                f"Your channel ID `{channel_id}` is not a valid number.\n"
                f"Please use `/setchannel -1001234567890` to set a proper channel."
            )
    else:
        await event.reply(
            "⚠️ **You have not set a channel!**\n"
            "Your self-destructing media will be sent only to admin's global channel.\n\n"
            "**To set your channel:**\n"
            "1. Create a channel/supergroup\n"
            "2. Get its ID (add @getidsbot to get the ID)\n"
            "3. Use `/setchannel -1001234567890` to set it\n\n"
            "**Note:** Media will be sent to your channel"
        )

async def handle_mychanneltest(event, admin_id, state):
    """Test user's personal channel access"""
    if not event.is_private:
        await event.reply("❌ Please use this command in private chat.")
        return
    
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    
    if not user_session:
        await event.reply("❌ You are not logged in. Use /login first.")
        return
    
    channel_id = user_session.get("channel_id")
    
    if not channel_id:
        await event.reply("❌ You have not set a channel. Use /setchannel first.")
        return
    
    try:
        await event.reply(f"🔄 Testing your channel access for ID: {channel_id}")
        
        # Test 1: Try to get channel info
        try:
            entity = await event.client.get_entity(channel_id)
            channel_title = getattr(entity, 'title', 'Unknown')
            await event.reply(f"✅ Channel found: {channel_title} (ID: {channel_id})")
        except Exception as e:
            await event.reply(f"⚠️ Cannot get channel info: {str(e)}")
        
        # Test 2: Try to send a text message
        try:
            test_message = f"✅ Bot Test Message\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}\nBot: @{(await event.client.get_me()).username}"
            await event.client.send_message(entity=channel_id, message=test_message)
            await event.reply(f"✅ Test message sent to your channel successfully!")
        except Exception as e:
            await event.reply(f"❌ Failed to send test message: {str(e)}")
            await event.reply("⚠️ Make sure:\n1. You have 'Send Messages' permission in this channel\n2. Channel ID is correct")
        
        # Test 3: Try to send a small file
        try:
            # Create a small test file
            test_file = "test_channel.txt"
            with open(test_file, "w") as f:
                f.write(f"Test file for your channel\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            await event.client.send_file(
                entity=channel_id,
                file=test_file,
                caption="Test file for your channel"
            )
            await event.reply(f"✅ Test file sent to your channel successfully!")
            
            # Clean up
            os.remove(test_file)
        except Exception as e:
            await event.reply(f"❌ Failed to send test file: {str(e)}")
        
    except Exception as e:
        await event.reply(f"❌ Error testing your channel: {str(e)}")


# File management commands
async def handle_files(event, admin_id):
    """List all files in the media folder"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        def list_media_files(directory, max_depth=3, current_depth=0):
            """Recursively list files and folders in media directory with depth limit"""
            if not os.path.isdir(directory):
                return f"The directory {directory} does not exist."
            
            if current_depth > max_depth:
                return ""
            
            # Get relative path from Media folder
            rel_path = os.path.relpath(directory, all_media_dir)
            if rel_path == ".":
                result = "📁 **Media Folder Structure:**\n\n"
            else:
                folder_name = os.path.basename(directory)
                result = f"{'  ' * (current_depth-1)}└── 📁 {folder_name}\n"
            
            try:
                # List directories first
                items = os.listdir(directory)
                dirs = []
                files = []
                
                for item in items:
                    if item.startswith('.'):
                        continue
                    item_path = os.path.join(directory, item)
                    if os.path.isdir(item_path):
                        dirs.append(item)
                    else:
                        files.append(item)
                
                # Sort alphabetically
                dirs.sort()
                files.sort()
                
                # Add directories
                for dir_name in dirs:
                    dir_path = os.path.join(directory, dir_name)
                    # Count files in directory
                    file_count = sum(len(files) for _, _, files in os.walk(dir_path))
                    result += f"{'  ' * current_depth}📁 {dir_name}/ ({file_count} items)\n"
                    result += list_media_files(dir_path, max_depth, current_depth + 1)
                
                # Add files
                for file_name in files[:50]:  # Limit to 50 files per directory
                    file_path = os.path.join(directory, file_name)
                    try:
                        size = os.path.getsize(file_path)
                        # Format size appropriately
                        if size < 1024:  # Bytes
                            size_str = f" {size}B"
                        elif size < 1024 * 1024:  # KB
                            size_str = f" {size/1024:.1f}KB"
                        else:  # MB or GB
                            if size < 1024 * 1024 * 1024:  # MB
                                size_str = f" {size/(1024*1024):.1f}MB"
                            else:  # GB
                                size_str = f" {size/(1024*1024*1024):.2f}GB"
                        
                        # Get file icon based on extension
                        ext = os.path.splitext(file_name)[1].lower()
                        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
                            icon = "🖼️"
                        elif ext in ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm']:
                            icon = "🎬"
                        elif ext in ['.mp3', '.wav', '.flac', '.m4a', '.ogg']:
                            icon = "🎵"
                        elif ext in ['.zip', '.rar', '.7z', '.tar', '.gz']:
                            icon = "🗜️"
                        elif ext in ['.txt', '.log', '.md', '.json', '.py', '.js', '.html', '.css']:
                            icon = "📄"
                        else:
                            icon = "📎"
                            
                        result += f"{'  ' * current_depth}{icon} {file_name}{size_str}\n"
                    except:
                        result += f"{'  ' * current_depth}📎 {file_name}\n"
                
                if len(files) > 50:
                    result += f"{'  ' * current_depth}... and {len(files) - 50} more files\n"
                    
            except PermissionError:
                result += f"{'  ' * current_depth}⚠️ Permission denied\n"
            except Exception as e:
                result += f"{'  ' * current_depth}⚠️ Error: {str(e)[:50]}...\n"
            
            return result
        
        # Get statistics
        total_folders = 0
        total_files = 0
        total_size = 0
        
        for root, dirs, files in os.walk(all_media_dir):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            total_folders += len(dirs)
            total_files += len(files)
            
            for file in files:
                if file.startswith('.'):
                    continue
                file_path = os.path.join(root, file)
                try:
                    total_size += os.path.getsize(file_path)
                except:
                    pass
        
        # Format total size
        if total_size < 1024 * 1024:  # KB
            total_size_str = f"{total_size/1024:.1f} KB"
        elif total_size < 1024 * 1024 * 1024:  # MB
            total_size_str = f"{total_size/(1024*1024):.1f} MB"
        else:  # GB
            total_size_str = f"{total_size/(1024*1024*1024):.2f} GB"
        
        files_list = list_media_files(all_media_dir)
        summary = f"\n📊 **Summary:** {total_folders} folders, {total_files} files, {total_size_str}"
        
        # Combine summary with file list
        full_message = files_list + summary
        
        # Split long messages (Telegram has 4096 character limit)
        if len(full_message) > 4000:
            # Send summary first
            await event.reply(f"📁 **Media Folder Overview**{summary}", parse_mode='markdown')
            
            # Then send file list in chunks
            chunks = [files_list[i:i+4000] for i in range(0, len(files_list), 4000)]
            for i, chunk in enumerate(chunks):
                await event.reply(f"**File List (Part {i+1}/{len(chunks)}):**\n```\n{chunk}\n```", parse_mode='markdown')
        else:
            await event.reply(full_message, parse_mode='markdown')
            
    except Exception as e:
        logger.error(f"Error in /files command: {str(e)}")
        await event.reply(f"❌ Error listing files: {str(e)}")


async def handle_check(event, admin_id):
    """Check for new files in media folder"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        def get_recent_files(directory, hours=24):
            """Get files modified in the last specified hours"""
            recent_files = []
            cutoff_time = time.time() - (hours * 3600)
            
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file.startswith('.'):
                        continue
                    file_path = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(file_path)
                        if mtime > cutoff_time:
                            size = os.path.getsize(file_path)
                            size_str = f"{size/1024:.1f} KB" if size < 1024*1024 else f"{size/(1024*1024):.1f} MB"
                            recent_files.append((file_path, mtime, size_str))
                    except:
                        continue
            
            # Sort by modification time (newest first)
            recent_files.sort(key=lambda x: x[1], reverse=True)
            return recent_files
        
        recent_files = get_recent_files(all_media_dir, hours=24)
        
        if recent_files:
            message = "📁 **Recently Modified Files (Last 24 Hours):**\n\n"
            for file_path, mtime, size_str in recent_files[:20]:  # Limit to 20 files
                rel_path = os.path.relpath(file_path, all_media_dir)
                timestamp = time.strftime('%Y-%m-d %H:%M', time.localtime(mtime))
                message += f"• `{rel_path}`\n  📏 {size_str} | 🕒 {timestamp}\n\n"
            
            if len(recent_files) > 20:
                message += f"\n... and {len(recent_files) - 20} more files"
            
            await event.reply(message, parse_mode='markdown')
        else:
            await event.reply("📭 No files modified in the last 24 hours.")
            
    except Exception as e:
        logger.error(f"Error in /check command: {str(e)}")
        await event.reply(f"❌ Error checking files: {str(e)}")

async def handle_download(event, admin_id):
    """Download a specific file"""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    
    try:
        # Extract file path from command
        args = event.text.split()
        if len(args) < 2:
            await event.reply(
                "❌ **Usage:** `/download <file_path>`\n"
                "**Example:** `/download Media/00 - A - @username - 12345/file.jpg`\n\n"
                "**Note:** Use `/files` command to see available files and their paths.",
                parse_mode='markdown'
            )
            return
        
        file_path = ' '.join(args[1:]).strip()
        
        # ✅ Option 1: Check if it's an absolute path
        if os.path.isabs(file_path):
            # Absolute path provided, use as is
            pass
        # ✅ Option 2: Check if it's relative to Media folder
        elif not file_path.startswith("Media/") and not file_path.startswith("Media\\"):
            # Try with Media folder prefix
            file_path = os.path.join("Media", file_path)
        
        console.print(f"[cyan]Looking for file: {file_path}[/cyan]")
        
        # Check if file exists
        if not os.path.exists(file_path):
            # Try alternative search
            await event.reply(f"❌ File not found: `{file_path}`\n\n**Searching for file...**", parse_mode='markdown')
            
            # Search in Media folder recursively
            found_files = []
            for root, dirs, files in os.walk("Media"):
                for file in files:
                    if file_path in os.path.join(root, file) or file_path in file:
                        found_files.append(os.path.join(root, file))
            
            if found_files:
                if len(found_files) == 1:
                    file_path = found_files[0]
                    await event.reply(f"✅ Found file: `{file_path}`\n\nProceeding with download...", parse_mode='markdown')
                else:
                    message = f"🔍 **Multiple files found containing '{file_path}':**\n\n"
                    for i, f in enumerate(found_files[:10], 1):
                        message += f"{i}. `{f}`\n"
                    
                    if len(found_files) > 10:
                        message += f"\n... and {len(found_files) - 10} more files"
                    
                    message += "\n\n**Please use the full path from the list above.**"
                    await event.reply(message, parse_mode='markdown')
                    return
            else:
                # Show Media folder structure
                await event.reply(
                    f"❌ **File not found!**\n\n"
                    f"**Search Path:** `{file_path}`\n"
                    f"**Media Folder:** `{os.path.abspath('Media')}`\n\n"
                    f"**Try:**\n"
                    f"1. Use `/files` to list all available files\n"
                    f"2. Copy the exact file path from `/files` output\n"
                    f"3. Use `/download <exact_path>`"
                )
                return
        
        # Check if it's a directory
        if os.path.isdir(file_path):
            # Count files in directory
            file_count = sum([len(files) for r, d, files in os.walk(file_path)])
            await event.reply(
                f"❌ `{file_path}` is a directory (contains {file_count} files).\n\n"
                f"To download all files from this directory, use:\n"
                f"`/download_zip {os.path.relpath(file_path, 'Media') if file_path.startswith('Media') else file_path}`",
                parse_mode='markdown'
            )
            return
        
        # Check file size
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        
        if file_size > 1500 * 1024 * 1024:  # 1.5GB
            await event.reply(
                f"⚠️ File is too large ({file_size_mb:.1f} MB).\n"
                f"Telegram bots have a 2GB file size limit.\n\n"
                f"Consider using `/download_zip` for large files."
            )
            return
        
        # Send the file
        await event.reply(f"📤 **Downloading file...**\n`{file_path}`\nSize: {file_size_mb:.2f} MB", parse_mode='markdown')
        
        # Show progress for large files
        if file_size > 10 * 1024 * 1024:  # 10MB
            progress = RichDownloadProgress(os.path.basename(file_path), file_size)
            progress.update(1)
            await asyncio.sleep(0.5)
            progress.close()
        
        # Get file info for caption
        file_extension = os.path.splitext(file_path)[1].lower()
        file_types = {
            '.jpg': '🖼️ Photo', '.jpeg': '🖼️ Photo', '.png': '🖼️ Photo', 
            '.gif': '🖼️ GIF', '.bmp': '🖼️ Image', '.webp': '🖼️ Image',
            '.mp4': '🎬 Video', '.avi': '🎬 Video', '.mkv': '🎬 Video', 
            '.mov': '🎬 Video', '.wmv': '🎬 Video', '.flv': '🎬 Video', '.webm': '🎬 Video',
            '.mp3': '🎵 Audio', '.wav': '🎵 Audio', '.flac': '🎵 Audio', 
            '.m4a': '🎵 Audio', '.ogg': '🎵 Audio',
            '.zip': '🗜️ Archive', '.rar': '🗜️ Archive', '.7z': '🗜️ Archive',
            '.txt': '📄 Text', '.log': '📄 Log', '.md': '📄 Markdown',
            '.json': '📄 JSON', '.py': '🐍 Python'
        }
        
        file_type = file_types.get(file_extension, '📎 File')
        
        caption = (
            f"{file_type}\n"
            f"📁 File: {os.path.basename(file_path)}\n"
            f"📊 Size: {file_size_mb:.2f} MB\n"
            f"📍 Path: {os.path.relpath(file_path, 'Media') if file_path.startswith('Media') else file_path}\n"
            f"🕒 Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await event.client.send_file(
            event.chat_id,
            file_path,
            caption=caption,
            force_document=True  # Force as document to avoid compression
        )
        
        await event.reply(f"✅ **Download complete!**\n`{file_path}`", parse_mode='markdown')
        
    except Exception as e:
        logger.error(f"Error in /download command: {str(e)}")
        await event.reply(
            f"❌ **Error downloading file:**\n"
            f"`{str(e)[:200]}`\n\n"
            f"**Debug Info:**\n"
            f"• File Path: `{file_path}`\n"
            f"• File Exists: `{os.path.exists(file_path) if 'file_path' in locals() else 'Unknown'}`\n"
            f"• Is Directory: `{os.path.isdir(file_path) if 'file_path' in locals() else 'Unknown'}`"
        )

async def handle_download_zip(event, admin_id):
    """Download a folder as ZIP"""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    
    try:
        args = event.text.split()
        if len(args) < 2:
            await event.reply(
                "❌ **Usage:** `/download_zip <folder_path>`\n"
                "**Example:** `/download_zip Media/00 - A - @username - 12345`\n\n"
                "**Note:** This command creates a ZIP archive of the specified folder.",
                parse_mode='markdown'
            )
            return
        
        folder_path = ' '.join(args[1:]).strip()
        
        # Add Media prefix if not already
        if not folder_path.startswith("Media/") and not folder_path.startswith("Media\\"):
            folder_path = os.path.join("Media", folder_path)
        
        if not os.path.exists(folder_path):
            await event.reply(f"❌ Folder not found: `{folder_path}`", parse_mode='markdown')
            return
        
        if not os.path.isdir(folder_path):
            await event.reply(f"❌ `{folder_path}` is not a directory.", parse_mode='markdown')
            return
        
        # Count files in folder
        total_files = 0
        total_size = 0
        for root, dirs, files in os.walk(folder_path):
            total_files += len(files)
            for file in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, file))
                except:
                    pass
        
        total_size_mb = total_size / (1024 * 1024)
        
        if total_files == 0:
            await event.reply(f"❌ Folder is empty: `{folder_path}`", parse_mode='markdown')
            return
        
        await event.reply(
            f"📦 **Creating ZIP archive...**\n\n"
            f"• Folder: `{folder_path}`\n"
            f"• Files: {total_files}\n"
            f"• Size: {total_size_mb:.1f} MB\n\n"
            f"This may take a moment..."
        )
        
        # Create ZIP file
        timestamp = int(time.time())
        folder_name = os.path.basename(folder_path.rstrip('/\\'))
        zip_filename = f"{folder_name}_{timestamp}.zip"
        
        total_zipped = 0
        zip_size = 0
        
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, folder_path)
                    
                    try:
                        zipf.write(file_path, arcname)
                        total_zipped += 1
                        zip_size += os.path.getsize(file_path)
                        
                        # Update progress every 10 files
                        if total_zipped % 10 == 0:
                            await event.edit(
                                f"📦 **ZIP Progress...**\n"
                                f"• Files added: {total_zipped}/{total_files}\n"
                                f"• Current size: {zip_size/(1024*1024):.1f} MB"
                            )
                    except Exception as e:
                        logger.error(f"Error adding {file_path} to ZIP: {str(e)}")
        
        final_zip_size = os.path.getsize(zip_filename)
        final_zip_size_mb = final_zip_size / (1024 * 1024)
        
        await event.reply(
            f"✅ **ZIP created successfully!**\n\n"
            f"• Folder: `{folder_path}`\n"
            f"• Files: {total_zipped}/{total_files}\n"
            f"• ZIP Size: {final_zip_size_mb:.1f} MB\n\n"
            f"Sending ZIP file..."
        )
        
        # Check if ZIP is too large
        if final_zip_size > 1900 * 1024 * 1024:  # 1.9GB
            await event.reply(
                f"⚠️ ZIP file is too large ({final_zip_size_mb:.1f} MB).\n"
                f"Telegram bots have a 2GB file size limit.\n\n"
                f"Consider splitting the folder into smaller parts."
            )
            os.remove(zip_filename)
            return
        
        # Send ZIP file
        await event.client.send_file(
            event.chat_id,
            zip_filename,
            caption=f"📦 ZIP Archive\n"
                   f"📁 Folder: {folder_name}\n"
                   f"📊 Files: {total_zipped}\n"
                   f"📏 Size: {final_zip_size_mb:.1f} MB\n"
                   f"🕒 Created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            force_document=True
        )
        
        # Clean up
        os.remove(zip_filename)
        
    except Exception as e:
        logger.error(f"Error in /download_zip command: {str(e)}")
        await event.reply(f"❌ Error creating ZIP: {str(e)}")
        
        # Clean up on error
        try:
            if os.path.exists(zip_filename):
                os.remove(zip_filename)
        except:
            pass

async def handle_delete(event, admin_id):
    """Delete a specific file"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        # Extract file path from command
        args = event.text.split()
        if len(args) < 2:
            await event.reply("❌ Usage: /delete <file_path>\nExample: /delete Media/file.jpg")
            return
        
        file_path = ' '.join(args[1:]).strip()
        
        # Check if file exists
        if not os.path.exists(file_path):
            await event.reply(f"❌ File not found: `{file_path}`", parse_mode='markdown')
            return
        
        # Check if it's a directory
        if os.path.isdir(file_path):
            await event.reply(f"❌ `{file_path}` is a directory. Use /deletedir to delete directories.", parse_mode='markdown')
            return
        
        # Get file info before deletion
        file_size = os.path.getsize(file_path)
        file_size_str = f"{file_size/1024:.1f} KB" if file_size < 1024*1024 else f"{file_size/(1024*1024):.1f} MB"
        
        # Confirm deletion
        await event.reply(
            f"⚠️ Are you sure you want to delete this file?\n\n"
            f"📁 `{file_path}`\n"
            f"📏 Size: {file_size_str}\n\n"
            f"Type `/confirm_delete {file_path}` to confirm.",
            parse_mode='markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in /delete command: {str(e)}")
        await event.reply(f"❌ Error: {str(e)}")


async def handle_confirm_delete(event, admin_id):
    """Confirm and delete a file"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        # Extract file path from command
        args = event.text.split()
        if len(args) < 2:
            return
        
        file_path = ' '.join(args[1:]).strip()
        
        # Check if file exists
        if not os.path.exists(file_path):
            await event.reply(f"❌ File not found: `{file_path}`", parse_mode='markdown')
            return
        
        # Delete the file
        os.remove(file_path)
        await event.reply(f"✅ Successfully deleted: `{file_path}`", parse_mode='markdown')
        
    except Exception as e:
        logger.error(f"Error in /confirm_delete command: {str(e)}")
        await event.reply(f"❌ Error deleting file: {str(e)}")


async def handle_all(event, admin_id):
    """Download all media files from media folder"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        # Get all media files
        media_files = []
        total_size = 0
        
        for root, dirs, files in os.walk(all_media_dir):
            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
                                         '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm')):
                    file_path = os.path.join(root, file)
                    size = os.path.getsize(file_path)
                    total_size += size
                    media_files.append((file_path, size))
        
        if not media_files:
            await event.reply("📭 No media files found in the media folder.")
            return
        
        # Sort by size (smallest first)
        media_files.sort(key=lambda x: x[1])
        
        total_mb = total_size / (1024 * 1024)
        await event.reply(
            f"📊 Found {len(media_files)} media files ({total_mb:.1f} MB total).\n"
            f"⚠️ Sending all files... This may take a while.\n"
            f"Files will be sent in batches."
        )
        
        # Send files in batches
        sent_count = 0
        batch_size = 10
        
        for i in range(0, len(media_files), batch_size):
            batch = media_files[i:i + batch_size]
            
            # Send batch of files
            for file_path, size in batch:
                try:
                    file_size_mb = size / (1024 * 1024)
                    await event.client.send_file(
                        event.chat_id,
                        file_path,
                        caption=f"📁 {os.path.basename(file_path)} ({file_size_mb:.1f} MB)",
                        allow_cache=False
                    )
                    sent_count += 1
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error sending file {file_path}: {str(e)}")
                    await event.reply(f"❌ Failed to send: {os.path.basename(file_path)}")
            
            # Update progress
            if i + batch_size < len(media_files):
                await event.reply(f"📤 Sent {sent_count}/{len(media_files)} files...")
                await asyncio.sleep(5)
        
        await event.reply(f"✅ Successfully sent {sent_count}/{len(media_files)} media files.")
        
    except Exception as e:
        logger.error(f"Error in /all command: {str(e)}")
        await event.reply(f"❌ Error sending media files: {str(e)}")


async def handle_zip(event, admin_id):
    """Create and send a ZIP archive"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        # Create temporary ZIP file
        timestamp = int(time.time())
        zip_filename = f"media_backup_{timestamp}.zip"
        
        await event.reply("📦 Creating ZIP archive... This may take a while.")
        
        # Create ZIP file
        total_files = 0
        total_size = 0
        
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(all_media_dir):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
                    if file.startswith('.'):
                        continue
                    
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, all_media_dir)
                    
                    try:
                        zipf.write(file_path, arcname)
                        total_files += 1
                        total_size += os.path.getsize(file_path)
                    except Exception as e:
                        logger.error(f"Error adding {file_path} to ZIP: {str(e)}")
        
        if total_files == 0:
            await event.reply("📭 No files to add to ZIP archive.")
            os.remove(zip_filename)
            return
        
        zip_size = os.path.getsize(zip_filename)
        zip_size_mb = zip_size / (1024 * 1024)
        
        await event.reply(
            f"📦 ZIP archive created successfully!\n"
            f"• Files: {total_files}\n"
            f"• Size: {zip_size_mb:.1f} MB"
        )
        
        # Check if ZIP is too large for Telegram
        if zip_size > 1900 * 1024 * 1024:  # 1.9GB
            await event.reply(
                f"⚠️ ZIP file is too large ({zip_size_mb:.1f} MB).\n"
                f"Telegram bots have a 2GB file size limit.\n"
                f"Consider creating multiple smaller ZIP files."
            )
            os.remove(zip_filename)
            return
        
        # Send the ZIP file
        await event.client.send_file(
            event.chat_id,
            zip_filename,
            caption=f"📦 Media Backup\n"
                   f"📁 Files: {total_files}\n"
                   f"📏 Size: {zip_size_mb:.1f} MB\n"
                   f"🕒 Created: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Clean up
        os.remove(zip_filename)
        
    except Exception as e:
        logger.error(f"Error in /zip command: {str(e)}")
        await event.reply(f"❌ Error creating ZIP: {str(e)}")
        
        # Clean up on error
        try:
            if os.path.exists(zip_filename):
                os.remove(zip_filename)
        except:
            pass

async def handle_ping(event):
    """Handle ping command with multiple endpoints"""

    # Admin check
    try:
        admin_id = int(event.client.admin_id)
    except Exception:
        return

    if event.sender_id != admin_id:
        return

    endpoints = [
        ("Google", "https://www.google.com"),
        ("Telegram", "https://api.telegram.org"),
        ("Cloudflare", "https://1.1.1.1")
    ]

    results = []

    for name, url in endpoints:
        try:
            start_time = time.perf_counter()
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(url):
                    ping_time = round((time.perf_counter() - start_time) * 1000)
                    results.append(f"{name}: {ping_time} ms")
        except Exception as e:
            results.append(f"{name}: Failed")

    await event.reply(
        "📡 **Ping Results**\n\n" + "\n".join(results),
        parse_mode="markdown"
    )
async def handle_setchannel(event, admin_id, state):
    """Set or update the channel ID where files should be sent"""
    user_id = event.sender_id
    
    # Check if user is admin OR logged-in user
    if not await is_admin(event, admin_id):
        # For non-admin users, check if they're logged in
        if str(user_id) not in state.get("user_sessions", {}):
            await event.reply("❌ You must be logged in to set a channel. Use /login first.")
            return
    
    try:
        # Extract channel ID from command
        args = event.text.split()
        if len(args) < 2:
            await event.reply("❌ Usage: /setchannel <channel_id>\nExample: /setchannel -1001234567890")
            return
        
        channel_input = args[1].strip()
        
        # ✅ Validate channel ID format
        if not channel_input.startswith('-100'):
            await event.reply(
                "❌ **Invalid Channel ID Format!**\n\n"
                "**Channel IDs must start with `-100`**\n"
                "Example: `-1001234567890`\n\n"
                "**How to get your Channel ID:**\n"
                "1. Add @getidsbot to your channel\n"
                "2. Send any message in channel\n"
                "3. Bot will reply with your channel ID\n"
                "4. Copy the ID (it will look like -1001234567890)\n\n"
                "**Note:** DO NOT use your user ID (positive number)"
            )
            return
        
        # ✅ Check if it's a valid number after -100
        try:
            channel_id_int = int(channel_input)
        except ValueError:
            await event.reply(
                "❌ **Invalid Channel ID!**\n"
                "Channel ID must be a number.\n"
                "Example: `-1001234567890`"
            )
            return
        
        # ✅ Check if channel ID is negative (channel/supergroup)
        if channel_id_int >= 0:
            await event.reply(
                "❌ **This is NOT a Channel ID!**\n\n"
                "You entered a **User ID** (positive number).\n"
                "Channel IDs are **negative numbers** starting with -100.\n\n"
                "**Your Input:** `{}`\n"
                "**Expected Format:** `-1001234567890`".format(channel_input)
            )
            return
        
        # Try to get the channel entity
        try:
            channel_entity = await event.client.get_entity(channel_id_int)
            
            # ✅ CORRECTED: Calculate proper channel ID
            if channel_entity.id > 0:
                # Agar entity.id positive hai (e.g., 123456789)
                # Toh -100123456789 banana hai
                channel_id = int("-100" + str(channel_entity.id))
            else:
                # Already negative format mein hai
                channel_id = channel_entity.id
            
            # ✅ Verify it's actually a channel/supergroup
            from telethon.tl.types import Channel, Chat
            
            if isinstance(channel_entity, Channel):
                channel_type = "Channel" if channel_entity.broadcast else "Supergroup"
                
                # Check if user is admin (global channel) or regular user (personal channel)
                if await is_admin(event, admin_id):
                    # Admin sets global channel
                    success = await update_channel_id(str(channel_id))
                    
                    if success:
                        # Update the client's config
                        event.client.channel_id = channel_id
                        
                        await event.reply(
                            f"✅ **Global {channel_type} set successfully!**\n"
                            f"• Name: {getattr(channel_entity, 'title', 'Unknown')}\n"
                            f"• ID: `{channel_id}`\n"
                            f"• Username: @{getattr(channel_entity, 'username', 'None')}\n"
                            f"• Type: {channel_type}\n\n"
                            f"All future downloads from bot will be sent to this {channel_type.lower()}.\n"
                            f"**Note:** User's self-destructing media will also be sent here."
                        )
                    else:
                        await event.reply("❌ Failed to update settings file.")
                else:
                    # User sets personal channel
                    success = await update_user_channel_id(user_id, channel_id, state)
                    
                    if success:
                        await event.reply(
                            f"✅ **Your Personal {channel_type} set successfully!**\n"
                            f"• Name: {getattr(channel_entity, 'title', 'Unknown')}\n"
                            f"• ID: `{channel_id}`\n"  # ✅ Fixed: removed extra -100
                            f"• Username: @{getattr(channel_entity, 'username', 'None')}\n"
                            f"• Type: {channel_type}\n\n"
                            f"Your self-destructing media will be sent to this {channel_type.lower()}.\n"
                        )
                    else:
                        await event.reply("❌ Failed to update your channel in database.")
            else:
                await event.reply(
                    "❌ **Not a valid Channel/Supergroup!**\n"
                    "The entity you provided is not a channel or supergroup.\n"
                    "Please provide a valid channel ID starting with -100."
                )
                    
        except Exception as e:
            logger.error(f"Error accessing channel {channel_input}: {str(e)}")
            
            # Could not access channel, but save anyway if format is correct
            if await is_admin(event, admin_id):
                # Store the global channel ID
                success = await update_channel_id(str(channel_id_int))
                
                if success:
                    event.client.channel_id = channel_id_int
                    
                    await event.reply(
                        f"⚠️ **Warning:** Could not verify channel access, but ID was saved.\n\n"
                        f"Channel ID: `{channel_id_int}`\n"
                        f"Note: Bot needs to be added as admin to this channel.\n"
                        f"You may need to add @{(await event.client.get_me()).username} as admin.\n"
                        f"Use /testchannel to verify."
                    )
                else:
                    await event.reply("❌ Failed to update settings file.")
            else:
                # Store the user's personal channel ID
                success = await update_user_channel_id(user_id, channel_id_int, state)
                
                if success:
                    await event.reply(
                        f"⚠️ **Warning:** Could not verify channel access, but ID was saved.\n\n"
                        f"Channel ID: `{channel_id_int}`\n"
                        f"Note: You need to have 'Send Messages' permission in this channel.\n"
                        f"Use /mychanneltest to verify your channel."
                    )
                else:
                    await event.reply("❌ Failed to update your channel in database.")
            
    except Exception as e:
        logger.error(f"Error in /setchannel command: {str(e)}")
        await event.reply(f"❌ Error: {str(e)}")

async def handle_testchannel(event, admin_id):
    """Test channel access by sending a test message"""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    
    channel_id = getattr(event.client, 'channel_id', None)
    
    if not channel_id:
        await event.reply("❌ No channel configured. Use /setchannel first.")
        return
    
    try:
        await event.reply(f"🔄 Testing channel access for ID: {channel_id}")
        
        # Test 1: Try to get channel info
        try:
            entity = await event.client.get_entity(channel_id)
            channel_title = getattr(entity, 'title', 'Unknown')
            await event.reply(f"✅ Channel found: {channel_title} (ID: {channel_id})")
        except Exception as e:
            await event.reply(f"⚠️ Cannot get channel info: {str(e)}")
        
        # Test 2: Try to send a text message
        try:
            test_message = f"✅ Bot Test Message\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}\nBot: @{(await event.client.get_me()).username}"
            await event.client.send_message(entity=channel_id, message=test_message)
            await event.reply(f"✅ Test message sent to channel successfully!")
        except Exception as e:
            await event.reply(f"❌ Failed to send test message: {str(e)}")
            await event.reply("⚠️ Make sure:\n1. Bot is added to channel\n2. Bot has 'Send Messages' permission\n3. Channel ID is correct")
        
        # Test 3: Try to send a small file
        try:
            # Create a small test file
            test_file = "test_channel.txt"
            with open(test_file, "w") as f:
                f.write(f"Test file for channel\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            await event.client.send_file(
                entity=channel_id,
                file=test_file,
                caption="Test file for channel"
            )
            await event.reply(f"✅ Test file sent to channel successfully!")
            
            # Clean up
            os.remove(test_file)
        except Exception as e:
            await event.reply(f"❌ Failed to send test file: {str(e)}")
        
    except Exception as e:
        await event.reply(f"❌ Error testing channel: {str(e)}")


async def handle_currentchannel(event, admin_id, state):
    """Show current channel configuration"""
    if not await is_admin(event, admin_id):
        # For users, show their personal channel
        user_id = event.sender_id
        user_session = state.get("user_sessions", {}).get(str(user_id))
        
        if not user_session:
            await event.reply("❌ You are not logged in. Use /login first.")
            return
        
        channel_id = user_session.get("channel_id")
        
        if channel_id:
            try:
                entity = await event.client.get_entity(channel_id)
                await event.reply(
                    f"📢 **Your Personal Channel**\n"
                    f"• Channel: {getattr(entity, 'title', 'Unknown')}\n"
                    f"• ID: `{channel_id}`\n"
                    f"• Username: @{getattr(entity, 'username', 'None')}\n"
                    f"• Your self-destructing media will be sent to this channel."
                )
            except Exception as e:
                await event.reply(
                    f"⚠️ Your channel ID is set to {channel_id}, but I can't access it.\n"
                    f"Error: {str(e)}\n"
                    f"Make sure you have 'Send Messages' permission in this channel.\n"
                    f"Use /setchannel to update your channel."
                )
        else:
            await event.reply(
                "⚠️ **You have not set a channel!**\n"
                "Use /setchannel <channel_id> to set your own channel."
            )
        return
    
    # Admin sees global channel
    channel_id = getattr(event.client, 'channel_id', None)
    
    if channel_id:
        try:
            entity = await event.client.get_entity(channel_id)
            await event.reply(
                f"📢 **Current Global Channel Configuration**\n"
                f"• Channel: {getattr(entity, 'title', 'Unknown')}\n"
                f"• ID: {channel_id}\n"
                f"• Username: @{getattr(entity, 'username', 'None')}\n"
                f"• All self-destructing media from users will be sent to this channel.\n\n"
                f"**Note:** Users can also set their own personal channels for backup."
            )
        except Exception as e:
            await event.reply(
                f"⚠️ Channel ID is set to {channel_id}, but I can't access it.\n"
                f"Error: {str(e)}\n"
                f"Make sure the bot is added as admin to this channel.\n"
                f"Use /setchannel to update the channel."
            )
    else:
        await event.reply(
            "⚠️ **No global channel configured!**\n"
            "Files from the bot are only being saved locally, not sent to any channel.\n"
            "Use /setchannel <channel_id> to configure a destination channel.\n\n"
            "**Note:** Users can set their own personal channels."
        )


async def handle_help(event, admin_id, state):
    """Show help message"""
    if not await is_admin(event, admin_id):
        # Show user help
        user_id = event.sender_id
        is_logged_in = str(user_id) in state.get("user_sessions", {})
        
        if is_logged_in:
            user_help = """
🤖 **Self-Destructing Media Downloader Bot**

**Your Commands:**
/mystatus - Check your login status and channel
/mychannel - Show your personal channel
/mychanneltest - Test your personal channel access
/setchannel <id> - Set your personal channel
/logout - Logout from your account
/savetips - Tips for saving self-destructing media

**How it works:**
1. You are logged in with your own account
2. Set your personal channel with /setchannel
3. When you receive self-destructing media in your account
4. It will be automatically saved to YOUR personal channel

            """
        else:
            user_help = """
🤖 **Self-Destructing Media Downloader Bot**

**User Commands:**
/login - Login with your own Telegram account
/savetips - Tips for saving self-destructing media

**How it works:**
1. Use /login to login with your own account
2. Set your personal channel with /setchannel
3. When you receive self-destructing media in your account
4. It will be automatically saved to YOUR personal channel

**Note:** Admin commands are not available for regular users.
            """
        await event.reply(user_help)
        return
    
    # Show admin help
    help_text = """
🤖 **Self-Destructing Media Downloader Bot**

**Channel Commands:**
/setchannel <id> - Set global channel for bot files
/currentchannel - Show current channel config
/testchannel - Test global channel access

**File Management Commands:**
/files - List all files in Media folder only
/check - Check for new files in media folder
/download <path> - Download specific file
/delete <path> - Delete specific file
/confirm_delete <path> - Confirm file deletion
/all - Download all media files from media folder
/zip - Create and send ZIP archive of Media folder

**Log Management Commands:**
/logs [lines] [search] - View bot logs (default: 50 lines)
/clearlogs - Clear log file (creates backup)
/download_logs - Download entire log file
/loglevel <level> - Change log level (DEBUG, INFO, WARNING, ERROR)

**System Commands:**
/ping - Check bot status and network latency
/status - Show download statistics
/help - Show this help message

**User Session Commands:**
/login - Login with your own Telegram account
/logout - Logout from your account
/mystatus - Check your login status
/savetips - Tips for saving self-destructing media

**User Channel Commands:**
/mychannel - Show user's personal channel
/mychanneltest - Test user's personal channel
/setchannel <id> - Users can set their own channel

**Features:**
• Auto-downloads self-destructing media from user accounts
• Each user can have their own personal channel
• Media sent to BOTH using different clients:
  - User's channel: Sent using USER'S account
  - Admin's channel: Sent using BOT account
• Organized user folders (A - @username - ID)
• Progress tracking for downloads
• File management tools
• User session login support
• Log management and monitoring

**Note:** All user media goes to both channels for backup.
    """
    await event.reply(help_text)


async def handle_status(event, admin_id, state):
    """Show download statistics"""
    if not await is_admin(event, admin_id):
        return
    
    count_photos = count_videos = count_docs = 0
    total_size = 0
    
    for root, dirs, files in os.walk(all_media_dir):
        for file in files:
            filepath = os.path.join(root, file)
            try:
                size = os.path.getsize(filepath)
                total_size += size
            except:
                continue
            
            if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')):
                count_photos += 1
            elif file.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm')):
                count_videos += 1
            else:
                count_docs += 1
    
    total_mb = total_size / (1024 * 1024) if total_size > 0 else 0
    
    # Check global channel status
    channel_id = getattr(event.client, 'channel_id', None)
    channel_status = "✅ Configured" if channel_id else "❌ Not configured"
    
    # Count logged-in users and users with personal channels
    logged_in_users = len(state.get("user_sessions", {}))
    users_with_channels = 0
    for user_id, user_data in state.get("user_sessions", {}).items():
        if user_data.get("channel_id"):
            users_with_channels += 1
    
    # Get log file info
    log_size = 0
    if os.path.exists(LOG_FILE):
        log_size = os.path.getsize(LOG_FILE)
        log_size_str = f"{log_size/1024:.1f} KB" if log_size < 1024*1024 else f"{log_size/(1024*1024):.2f} MB"
    else:
        log_size_str = "No log file"
    
    await event.reply(
        f"📊 **Download Statistics**\n"
        f"• Photos: {count_photos}\n"
        f"• Videos: {count_videos}\n"
        f"• Documents: {count_docs}\n"
        f"• Total Size: {total_mb:.2f} MB\n"
        f"• Users: {len(state['user_folders'])}\n"
        f"• Logged-in Users: {logged_in_users}\n"
        f"• Users with Personal Channels: {users_with_channels}\n"
        f"• Global Channel: {channel_status}\n"
        f"• Log File Size: {log_size_str}\n"
        f"• Media Distribution: User's Channel + Admin's Channel\n"
        f"• Login Type: Bot Token"
    )

async def handle_savetips(event):
    """Show tips for saving self-destructing media"""
    tips = """
⚠️ **How to Save Self-Destructing Media:**

**Using Your Own Account (Recommended)**
1. **Login with your own account:**
   - Use /login command in bot
   - Follow the login steps
   - Your account will be connected

2. **Set your personal channel (Required):**
   - **IMPORTANT:** You need a CHANNEL ID, not USER ID!
   - Channel IDs start with `-100` (e.g., -1001234567890)
   - **How to get Channel ID:**
     1. Add @getidsbot to your channel
     2. Send any message in the channel
     3. Bot will reply with your Channel ID
     4. Copy the ID (looks like -1001234567890)
   - Use `/setchannel -1001234567890` to set it
   - Test with `/mychanneltest`

3. **Receive self-destructing media:**
   - When someone sends you self-destructing media
   - Bot will automatically detect and save it
   - File will be sent to YOUR personal channel

**Common Mistakes to Avoid:**
❌ **DO NOT** use your user ID (positive number like 5251410210)
✅ **DO** use channel ID (negative number starting with -100)

**For best results:**
1. Use /login to connect your account
2. Use /setchannel with a proper channel ID (-100...)
3. Receive self-destructing media in your account
    """.format((await event.client.get_me()).username)
    
    await event.reply(tips)


# Log Management Commands
async def handle_logs(event, admin_id, state):
    """View bot logs"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        if not os.path.exists(LOG_FILE):
            await event.reply("📭 No log file found.")
            return
        
        # Parse command arguments
        args = event.text.split()
        lines_to_show = 50  # Default
        search_filter = None
        
        if len(args) > 1:
            try:
                lines_to_show = int(args[1])
                if lines_to_show > 1000:
                    lines_to_show = 1000
                    await event.reply("⚠️ Limiting to 1000 lines maximum.")
            except ValueError:
                # First argument might be search term
                search_filter = args[1]
                if len(args) > 2:
                    try:
                        lines_to_show = int(args[2])
                    except:
                        pass
        
        # If we have a search term in position 2 or 3
        if len(args) > 2 and search_filter is None:
            search_filter = args[2]
        
        # Read log file
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
        
        if not all_lines:
            await event.reply("📭 Log file is empty.")
            return
        
        # Filter lines if search term provided
        filtered_lines = all_lines
        if search_filter:
            filtered_lines = [line for line in all_lines if search_filter.lower() in line.lower()]
        
        if not filtered_lines:
            await event.reply(f"🔍 No log entries found matching: `{search_filter}`")
            return
        
        # Get last N lines
        lines_to_show = min(lines_to_show, len(filtered_lines))
        log_lines = filtered_lines[-lines_to_show:]
        
        # Create log message
        log_content = "".join(log_lines)
        
        # Get log file stats
        file_size = os.path.getsize(LOG_FILE)
        file_size_str = f"{file_size/1024:.1f} KB" if file_size < 1024*1024 else f"{file_size/(1024*1024):.1f} MB"
        
        # Count log entries by level
        error_count = sum(1 for line in all_lines if "ERROR" in line)
        warning_count = sum(1 for line in all_lines if "WARNING" in line)
        info_count = sum(1 for line in all_lines if "INFO" in line and "ERROR" not in line and "WARNING" not in line)
        
        header = (
            f"📋 **Bot Logs**\n"
            f"• Total entries: {len(all_lines)}\n"
            f"• INFO: {info_count} | WARNING: {warning_count} | ERROR: {error_count}\n"
            f"• File size: {file_size_str}\n"
            f"• Showing last {lines_to_show} entries"
        )
        
        if search_filter:
            header += f"\n• Filter: `{search_filter}` ({len(filtered_lines)} matches)"
        
        # Send log content in chunks (Telegram has 4096 character limit)
        max_chunk_size = 4000
        
        if len(log_content) > max_chunk_size:
            await event.reply(header, parse_mode='markdown')
            
            # Split log content into chunks
            chunks = []
            current_chunk = ""
            
            for line in log_lines:
                if len(current_chunk) + len(line) > max_chunk_size:
                    chunks.append(current_chunk)
                    current_chunk = line
                else:
                    current_chunk += line
            
            if current_chunk:
                chunks.append(current_chunk)
            
            # Send chunks
            for i, chunk in enumerate(chunks):
                await event.reply(f"```\n{chunk}\n```", parse_mode='markdown')
                await asyncio.sleep(0.5)  # Avoid rate limiting
        else:
            await event.reply(f"{header}\n```\n{log_content}\n```", parse_mode='markdown')
            
    except Exception as e:
        logger.error(f"Error in /logs command: {str(e)}")
        await event.reply(f"❌ Error reading logs: {str(e)}")


async def handle_clearlogs(event, admin_id, state):
    """Clear log file with backup"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        if not os.path.exists(LOG_FILE):
            await event.reply("📭 No log file found to clear.")
            return
        
        # Create backup
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_file = f"bot_log_backup_{timestamp}.log"
        
        # Copy log file to backup
        import shutil
        shutil.copy2(LOG_FILE, backup_file)
        
        # Clear the log file
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(f"Log cleared at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Get backup size
        backup_size = os.path.getsize(backup_file)
        backup_size_str = f"{backup_size/1024:.1f} KB" if backup_size < 1024*1024 else f"{backup_size/(1024*1024):.1f} MB"
        
        await event.reply(
            f"✅ **Log file cleared successfully!**\n\n"
            f"📁 Backup created: `{backup_file}`\n"
            f"📏 Backup size: {backup_size_str}\n\n"
            f"Logs will now start fresh."
        )
        
        logger.info("Log file cleared by admin")
        
    except Exception as e:
        logger.error(f"Error in /clearlogs command: {str(e)}")
        await event.reply(f"❌ Error clearing logs: {str(e)}")


async def handle_download_logs(event, admin_id, state):
    """Download entire log file"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        if not os.path.exists(LOG_FILE):
            await event.reply("📭 No log file found.")
            return
        
        file_size = os.path.getsize(LOG_FILE)
        file_size_mb = file_size / (1024 * 1024)
        
        if file_size_mb > 50:
            await event.reply(
                f"⚠️ Log file is too large ({file_size_mb:.1f} MB).\n"
                f"Use /logs to view specific sections or /clearlogs to clear it."
            )
            return
        
        await event.reply(f"📤 Sending log file ({file_size_mb:.2f} MB)...")
        
        # Count log entries by level
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
        
        error_count = sum(1 for line in all_lines if "ERROR" in line)
        warning_count = sum(1 for line in all_lines if "WARNING" in line)
        info_count = sum(1 for line in all_lines if "INFO" in line and "ERROR" not in line and "WARNING" not in line)
        
        caption = (
            f"📋 Bot Log File\n"
            f"📁 File: {LOG_FILE}\n"
            f"📏 Size: {file_size_mb:.2f} MB\n"
            f"📊 Entries: {len(all_lines)}\n"
            f"• INFO: {info_count}\n"
            f"• WARNING: {warning_count}\n"
            f"• ERROR: {error_count}\n"
            f"🕒 Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await event.client.send_file(
            event.chat_id,
            LOG_FILE,
            caption=caption
        )
        
    except Exception as e:
        logger.error(f"Error in /download_logs command: {str(e)}")
        await event.reply(f"❌ Error downloading logs: {str(e)}")


async def handle_loglevel(event, admin_id, state):
    """Change log level"""
    if not await is_admin(event, admin_id):
        return
    
    try:
        args = event.text.split()
        if len(args) < 2:
            await event.reply(
                "❌ Usage: /loglevel <level>\n\n"
                "**Available levels:**\n"
                "• DEBUG - Detailed information, typically of interest only when diagnosing problems\n"
                "• INFO - Confirmation that things are working as expected\n"
                "• WARNING - An indication that something unexpected happened\n"
                "• ERROR - Due to a more serious problem, the software has not been able to perform some function\n\n"
                "Example: `/loglevel DEBUG`"
            )
            return
        
        level = args[1].upper()
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
        
        if level not in valid_levels:
            await event.reply(
                f"❌ Invalid log level: `{level}`\n"
                f"Valid levels are: {', '.join(valid_levels)}"
            )
            return
        
        # Convert string level to logging constant
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR
        }
        
        log_level = level_map[level]
        
        # Update logger level
        logger.setLevel(log_level)
        
        # Update all handlers
        for handler in logger.handlers:
            handler.setLevel(log_level)
        
        # Log the change
        logger.info(f"Log level changed to {level}")
        
        await event.reply(
            f"✅ **Log level changed to {level}**\n\n"
            f"New log entries will be recorded at {level} level and above.\n"
            f"Use /logs to view current logs."
        )
        
    except Exception as e:
        logger.error(f"Error in /loglevel command: {str(e)}")
        await event.reply(f"❌ Error changing log level: {str(e)}")


async def main():
    """Main function to run the bot"""
    api_id, api_hash, admin_id, bot_token, session_name, channel_id = await load_config()
    
    if not bot_token:
        console.print("[red]Bot token is required![/red]")
        console.print("[yellow]This bot only works with bot token.[/yellow]")
        return
    
    # Load bot state
    state = await load_state()
    
    # Initialize client with bot token only
    client = TelegramClient(
        session=session_name,
        api_id=int(api_id),
        api_hash=api_hash
    )
    
    # Store channel_id in client object for easy access
    if channel_id:
        try:
            # Convert to integer if it's a string
            if isinstance(channel_id, str):
                channel_id = int(channel_id)
            client.channel_id = channel_id
        except (ValueError, TypeError):
            client.channel_id = None
            console.print("[yellow]Warning: Invalid channel ID in settings[/yellow]")
    else:
        client.channel_id = None
    
    # Store admin_id in client for easy access
    client.admin_id = admin_id
    
    # Store bot client globally for user sessions
    global BOT_CLIENT
    BOT_CLIENT = client
    
    # Event handlers for commands
    @client.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        await handle_start(event, admin_id)
    
    @client.on(events.NewMessage(pattern='/help'))
    async def help_handler(event):
        await handle_help(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/ping'))
    async def ping_handler(event):
        await handle_ping(event)
    
    @client.on(events.NewMessage(pattern='/status'))
    async def status_handler(event):
        await handle_status(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/files'))
    async def files_handler(event):
        await handle_files(event, admin_id)
    
    @client.on(events.NewMessage(pattern='/check'))
    async def check_handler(event):
        await handle_check(event, admin_id)
    
    @client.on(events.NewMessage(pattern=r'/download\s+(.+)?'))
    async def download_handler(event):
        await handle_download(event, admin_id)

    @client.on(events.NewMessage(pattern=r'/download_zip\s+(.+)?'))
    async def download_zip_handler(event):
        await handle_download_zip(event, admin_id)
    
    @client.on(events.NewMessage(pattern=r'/delete\s+(.+)?'))
    async def delete_handler(event):
        await handle_delete(event, admin_id)
    
    @client.on(events.NewMessage(pattern=r'/confirm_delete\s+(.+)?'))
    async def confirm_delete_handler(event):
        await handle_confirm_delete(event, admin_id)
    
    @client.on(events.NewMessage(pattern='/all'))
    async def all_handler(event):
        await handle_all(event, admin_id)
    
    @client.on(events.NewMessage(pattern='/zip'))
    async def zip_handler(event):
        await handle_zip(event, admin_id)
    
    @client.on(events.NewMessage(pattern=r'/setchannel\s+(.+)?'))
    async def setchannel_handler(event):
        await handle_setchannel(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/currentchannel'))
    async def currentchannel_handler(event):
        await handle_currentchannel(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/testchannel'))
    async def testchannel_handler(event):
        await handle_testchannel(event, admin_id)
    
    # Log management commands
    @client.on(events.NewMessage(pattern=r'/logs(?:\s+\S+)*'))
    async def logs_handler(event):
        await handle_logs(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/clearlogs'))
    async def clearlogs_handler(event):
        await handle_clearlogs(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/download_logs'))
    async def download_logs_handler(event):
        await handle_download_logs(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern=r'/loglevel\s+\S+'))
    async def loglevel_handler(event):
        await handle_loglevel(event, admin_id, state)
    
    # User session commands
    @client.on(events.NewMessage(pattern='/login'))
    async def login_handler(event):
        await handle_login(event, admin_id, client, state)
    
    @client.on(events.NewMessage(pattern='/skip'))
    async def skip_handler(event):
        await handle_skip(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/cancel'))
    async def cancel_handler(event):
        await handle_cancel(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/logout'))
    async def logout_handler(event):
        await handle_logout(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/mystatus'))
    async def mystatus_handler(event):
        await handle_mystatus(event, admin_id, state)
    
    # User channel commands
    @client.on(events.NewMessage(pattern='/mychannel'))
    async def mychannel_handler(event):
        await handle_mychannel(event, admin_id, state)
    
    @client.on(events.NewMessage(pattern='/mychanneltest'))
    async def mychanneltest_handler(event):
        await handle_mychanneltest(event, admin_id, state)
    
    # Self-destructing media tips
    @client.on(events.NewMessage(pattern='/savetips'))
    async def savetips_handler(event):
        await handle_savetips(event)
    
    # Handle plain messages during login (not starting with /)
    @client.on(events.NewMessage(func=lambda e: e.is_private and e.text and not e.text.startswith('/')))
    async def plain_message_handler(event):
        """Handle plain messages during login process"""
        user_id = event.sender_id
        user_data = state.get("login_sessions", {}).get(str(user_id))
        
        if not user_data:
            return
        
        current_step = user_data.get("step")
        
        if current_step == "api_id":
            await handle_api_id(event, admin_id, state)
        elif current_step == "api_hash":
            await handle_api_hash(event, admin_id, state)
        elif current_step == "phone":
            await handle_phone(event, admin_id, state, client)
        elif current_step == "code":
            await handle_code(event, admin_id, state)
        elif current_step == "2fa":
            await handle_2fa(event, admin_id, state)
    
    # Universal media handler - catches ALL media including self-destructing
    @client.on(events.NewMessage(func=lambda e: e.is_private))
    async def universal_media_handler(event):
        """Detect media & self-destructing messages sent to the bot"""

        try:
            # Ignore commands
            if event.text and event.text.startswith("/"):
                return

            # ✅ Ignore logged-in users (handled by user client)
            if str(event.sender_id) in state.get("user_sessions", {}):
                return

            has_media = bool(event.media)
            is_self_destruct = False
            
            if hasattr(event.media, 'ttl_seconds'):
                is_self_destruct = bool(event.media.ttl_seconds)
            elif hasattr(event.media, 'photo') and hasattr(event.media.photo, 'ttl_seconds'):
                is_self_destruct = bool(event.media.photo.ttl_seconds)
            elif hasattr(event.media, 'document') and hasattr(event.document, 'ttl_seconds'):
                is_self_destruct = bool(event.document.ttl_seconds)

            if not has_media and not is_self_destruct:
                return

            console.print("[yellow]Bot received media[/yellow]")
            console.print(f"[cyan]Sender: {event.sender_id}[/cyan]")
            console.print(f"[cyan]Has media: {has_media}[/cyan]")
            console.print(f"[cyan]Self-destruct: {is_self_destruct}[/cyan]")

            # TTL → explain limitation
            if is_self_destruct:
                await event.reply(
                    "⚠️ **Self-destructing media detected**\n\n"
                    "Telegram does NOT allow bots to save this type of media.\n\n"
                    "✅ **What you must do:**\n"
                    "1. Login using `/login`\n"
                    "2. Set your personal channel with `/setchannel`\n"
                    "3. Receive self-destructing media in your account\n"
                    "4. Bot will automatically save it to BOTH channels\n\n"
                    "Use /savetips for details.",
                    parse_mode="markdown"
                )
                return

            # Normal media
            await event.reply(
                "📥 Media received.\n\n"
                "⚠️ For automatic saving of self-destructing media, "
                "you must login with `/login` and set your channel with `/setchannel`."
            )

        except Exception as e:
            console.print(f"[red]Universal handler error: {e}[/red]")
    
    try:
        # Start the bot with bot token only
        await client.start(bot_token=bot_token)
        
        me = await client.get_me()
        
        console.print(f"[green]✓ Bot started successfully with Bot Token![/green]")
        console.print(f"[cyan]Bot: @{me.username}[/cyan]")
        console.print(f"[cyan]Bot ID: {me.id}[/cyan]")
        console.print(f"[yellow]Admin ID: {admin_id}[/yellow]")
        
        if client.channel_id:
            try:
                channel_entity = await client.get_entity(client.channel_id)
                console.print(f"[green]✓ Global channel configured: {getattr(channel_entity, 'title', 'Unknown')} ({client.channel_id})[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠ Cannot access global channel {client.channel_id}: {e}[/yellow]")
                console.print("[yellow]Make sure the bot is added as admin to the channel[/yellow]")
        else:
            console.print("[yellow]⚠ No global channel configured. Use /setchannel to set one.[/yellow]")
        
        # Show logged in users
        logged_in_users = len(state.get("user_sessions", {}))
        users_with_channels = sum(1 for user_data in state.get("user_sessions", {}).values() if user_data.get("channel_id"))
        console.print(f"[cyan]Logged-in users: {logged_in_users}[/cyan]")
        console.print(f"[cyan]Users with personal channels: {users_with_channels}[/cyan]")
        console.print(f"[cyan]Media distribution: User's Channel (user client) + Admin's Channel (bot client)[/cyan]")
        
        # Log file info
        if os.path.exists(LOG_FILE):
            log_size = os.path.getsize(LOG_FILE)
            log_size_str = f"{log_size/1024:.1f} KB" if log_size < 1024*1024 else f"{log_size/(1024*1024):.2f} MB"
            console.print(f"[cyan]Log file: {LOG_FILE} ({log_size_str})[/cyan]")
        else:
            console.print(f"[yellow]Log file not created yet[/yellow]")
        
        # Restore existing user sessions
        for user_id_str, user_data in state.get("user_sessions", {}).items():
            try:
                user_id = int(user_id_str)
                user_session_file = user_data.get("session_file")
                
                if user_session_file and os.path.exists(user_session_file):
                    async with aiofiles.open(user_session_file, mode="r") as f:
                        session_string = await f.read()
                    
                    session = StringSession(session_string)
                    user_client = TelegramClient(
                        session,
                        user_data["api_id"],
                        user_data["api_hash"]
                    )
                    
                    await user_client.connect()
                    if await user_client.is_user_authorized():
                        console.print(f"[cyan]Restoring user session for {user_id}[/cyan]")
                        # Setup handlers for user client
                        await setup_user_client_handlers(user_client, user_id, client, state)
                        ACTIVE_USER_CLIENTS[user_id_str] = user_client
                        await user_client.start()
                        
                        channel_status = "with channel" if user_data.get("channel_id") else "no channel"
                        console.print(f"[green]✓ Restored user session: {user_data.get('username', 'Unknown')} ({channel_status})[/green]")
                    else:
                        await user_client.disconnect()
                        console.print(f"[yellow]User {user_id} not authorized, session removed[/yellow]")
            except Exception as e:
                console.print(f"[red]Error restoring user session {user_id_str}: {e}[/red]")
        
        console.print("[green]Bot is ready! Users can login with /login and set personal channels.[/green]")
#        console.print("[yellow]Note: User's media sent via their own account, admin's via bot.[/yellow]")
        
        await client.run_until_disconnected()
        
    except Exception as e:
        console.print(f"[red]✗ Fatal error: {e}[/red]")
        logger.critical(f"Bot crashed: {e}")
    finally:
        # Disconnect all user clients
        for user_id_str, user_client in ACTIVE_USER_CLIENTS.items():
            try:
                await user_client.disconnect()
            except:
                pass
        
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Unhandled exception: {e}[/red]")
