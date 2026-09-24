# 🤖 Telegram Self-Destructing Media Downloader Bot

A hardened, production-grade Telegram bot + user-session tool that **automatically captures, saves, and archives self-destructing (TTL) media** using Telethon and SQLite with end-to-end Fernet encryption.

---

## 🌟 Key Features

- 🔒 **Self-Destructing Media Capture:** Automatically captures self-destructing photos, videos, voice messages, audio, and documents before TTL expiry.
- 📬 **Dual Channel Architecture:** Direct media delivery to user's personal channel, with optional forwarding to the administrator's backup channel.
- 🛡️ **Centralized Authorization Model:** Immutable owner authority via `ADMIN_ID` combined with dynamic whitelist support via environment variable (`ALLOWED_USERS`) and SQLite database commands (`/allow_user`, `/disallow_user`).
- 🔐 **End-to-End Credential Encryption:** Sensitive credentials (session strings, API hashes) are encrypted at rest using AES-128-CBC + HMAC-SHA256 (Fernet).
- 📦 **Offline Media Recovery:** Automatic 48-hour scan on startup and periodic background worker recovery for media missed during server downtime.
- 🗄️ **Thread-Safe SQLite Queue:** WAL mode with atomic state transitions (`pending` → `processing` → `processed` / `failed`) and automatic startup recovery for stale in-flight items.
- 🛡️ **Path Traversal Protection:** All file operations are strictly verified within the `Media/` boundary.
- 🐳 **Hardened Docker & Non-Root Setup:** Dedicated unprivileged user, permission enforcement, and persistent volume support.

---

## 📋 Architecture & How It Works

1. **Bot Client (`BOT_TOKEN`):** Manages user commands, admin controls, whitelist authorization, and global channel delivery.
2. **User Client (`/login`):** Telethon user session connected with user authorization to monitor incoming private chat TTL media.
3. **Instant Detection & Safe Storage:** TTL metadata is detected from incoming message events or history recovery, downloaded safely to temporary files, and organized into structured folders: `Media/<counter> - <letter> - @<username> - <user_id>/`.
4. **Channel Dispatch:** Uploads file to user's personal channel (via user client) and optionally copies to admin's global archive (via bot client).

---

## 🛠 Command Reference

### 👤 User Commands
| Command | Description |
| :--- | :--- |
| `/start` | Start the bot and view welcome information |
| `/help` | Show command guide based on user role |
| `/login` | Interactive step-by-step account login (API ID, Hash, Phone, OTP, 2FA) |
| `/logout` | Terminate active user session and purge stored credentials |
| `/cancel` | Cancel an in-progress login flow |
| `/mystatus` | View connected account info, uptime, and personal channel status |
| `/setmychannel <id>` | Configure personal channel for media delivery (`-100...`) |
| `/mychannel` | Display configured personal channel |
| `/mychanneltest` | Test posting permissions to your personal channel |
| `/checkmissed` | Scan private chats for missed TTL media from the last 48 hours |
| `/queue_stats` | View count of pending media items in your personal queue |
| `/process_queue` | Process pending items in your personal queue immediately |
| `/savetips` | View troubleshooting tips for self-destructing media capture |

### 👑 Administrator Commands
| Command | Description |
| :--- | :--- |
| `/users` | List all users categorized by Administrator, Authorized, and Inactive |
| `/allow_user <id>` | Authorize a Telegram user ID in the database |
| `/disallow_user <id>` | Deactivate authorization for a user ID (admin cannot be disallowed) |
| `/allowed_users` | Show overview of all authorized users across env and database |
| `/setgchannel <id>` | Set administrator's global backup channel (`-100...`) |
| `/currentchannel` | Show current global channel configuration |
| `/testchannel` | Test posting permissions to the global channel |
| `/globalforward <enable\|disable\|status>` | Toggle forwarding of user media to admin global channel |
| `/files` | List files and storage usage in `Media/` |
| `/check` | List files modified within the last 24 hours |
| `/download <path>` | Securely download a specific file from `Media/` |
| `/download_zip <folder>` | Archive and download a folder within `Media/` as ZIP |
| `/zip` | Create and download complete ZIP archive of `Media/` |
| `/delete <path>` | Request deletion confirmation for a file |
| `/confirm_delete <path>` | Confirm and delete a file from `Media/` |
| `/all` | Download all media files in batches |
| `/queue_stats_all` | Display queue statistics across all users |
| `/process_queue_all` | Process queued items across all users immediately |
| `/checkmissed_all` | Trigger 48-hour missed media scan for all connected users |
| `/checkmissed <id>` | Trigger 48-hour missed media scan for a specific user ID |
| `/status` | View comprehensive disk storage, queue, and session statistics |
| `/ping` | Check response latency |
| `/logs [lines] [search]` | View recent bot log entries |
| `/clearlogs` | Backup and clear current log file |
| `/download_logs` | Download complete `bot.log` file |
| `/loglevel <level>` | Change runtime logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `/help_full` | Download full markdown documentation file |

---

## ⚙️ Environment Variables

Create a `.env` file in the root directory (or run `python3 setup_security.py`):

```env
# ===== TELEGRAM BOT CONFIGURATION =====
# Get from https://my.telegram.org
API_ID=1234567
API_HASH=abcdef1234567890abcdef1234567890

# Get from @BotFather
BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ

# Administrator Telegram Numeric User ID (Always Authorized)
ADMIN_ID=123456789

# Channel Configuration (Must start with -100)
CHANNEL_ID=-1001234567890
SESSION_NAME=self_destruct

# ===== ACCESS CONTROL =====
# Optional comma-separated list of authorized User IDs
ALLOWED_USERS=987654321,555555555

# ===== ENCRYPTION KEYS =====
# Generate using: python3 generate_key.py --write
ENCRYPTION_KEY=32_byte_base64_fernet_key_here
DB_ENCRYPTION_KEY=64_char_hex_random_string_here

# ===== ADVANCED SETTINGS =====
LOG_LEVEL=INFO
MAX_FILE_SIZE=2147483648
QUEUE_INTERVAL=300
AUTOCHECK_INTERVAL=3600
MAX_RETRIES=3
```

---

## 🚀 Quick Setup & Installation

### 1. Clone Repository & Setup Virtual Environment
```bash
git clone https://github.com/IceCube9680/Telegram-Self-Destructing-Bot.git
cd Telegram-Self-Destructing-Bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Generate Encryption Keys & Security Directories
```bash
python3 setup_security.py
python3 generate_key.py --write
```

### 3. Edit `.env`
Fill in `API_ID`, `API_HASH`, `BOT_TOKEN`, and `ADMIN_ID`.

### 4. Run the Bot
```bash
python3 bot.py
```

---

## 🐳 Docker Deployment

Run with Docker Compose or standalone Docker container with non-root security:

```bash
docker build -t self-destructing-bot .
docker run -d \
  --name telegram-bot \
  --restart unless-stopped \
  --env-file .env \
  -v $(pwd)/Media:/app/Media \
  -v $(pwd)/user_sessions:/app/user_sessions \
  -v $(pwd)/backups:/app/backups \
  -v $(pwd)/logs:/app/logs \
  self-destructing-bot
```

---

## 🧪 Running Automated Tests

Run the full automated unit test suite:

```bash
python3 -m unittest discover -s . -p "test_*.py"
```

---

## 🔐 Security & Privacy Notes

- 🔒 **Zero Token Exposure:** Bot tokens, API hashes, session strings, and 2FA passwords are never printed in logs or sent over unencrypted channels.
- 🔒 **Data Encryption:** Session strings and API hashes in `bot_queue.db` are encrypted using Fernet (AES-128 in CBC mode with HMAC-SHA256).
- 🔒 **Path Traversal Defenses:** File download and delete commands sanitize filenames and strictly verify that paths stay within `Media/`.
- 🔒 **Owner Immutability:** The administrator defined by `ADMIN_ID` can never be locked out or deactivated through database commands.

---

## ⚠️ Disclaimer

This project is for educational and authorized archiving purposes only. Ensure compliance with Telegram Terms of Service and applicable privacy regulations.
