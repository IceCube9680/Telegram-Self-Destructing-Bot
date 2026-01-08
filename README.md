# Self-Destructing-Bot

# Self-Destructing Media Downloader

A Telegram bot + user-session based tool that **automatically captures and saves self-destructing (TTL) media** using Telethon.

This project uses:
- **Bot Token** → commands, channel upload, management
- **User Sessions** → capture self-destructing photos/videos/documents (TTL)

> ⚠️ Bots alone cannot download self-destructing media.  
> This project works by securely logging in the user account (with consent).

---

## 🚀 Features

- ✅ Capture **self-destructing photos, videos, documents**
- ✅ Instant download before TTL expiry
- ✅ Automatic upload to your Telegram channel
- ✅ Multi-user support
- ✅ Session restore after restart
- ✅ Silent mode (no user notifications)
- ✅ Admin file management tools

---

## 🧠 How It Works

1. Bot runs using **Bot Token**
2. User logs in via `/login` (Telethon session)
3. User receives self-destructing media in private chat
4. Media is **downloaded immediately**
5. File is uploaded to the configured channel

---
## 🛠 Admin Commands

**Command Description**

**Channel Commands:**
`/setchannel` <id> - Set global channel for bot files
`/currentchannel` - Show current channel config
`/testchannel` - Test global channel access

**File Management Commands:**
`/files` - List all files in Media folder only
`/check` - Check for new files in media folder
`/download` <path> - Download specific file
`/delete` <path> - Delete specific file
`/confirm_delete `<path> - Confirm file deletion
`/all` - Download all media files from media folder
`/zip` - Create and send ZIP archive of Media folder

**Log Management Commands:**
`/logs` [lines] [search] - View bot logs (default: 50 lines)
`/clearlogs` - Clear log file (creates backup)
`/download_logs` - Download entire log file
`/loglevel` <level> - Change log level (DEBUG, INFO, WARNING, ERROR)

**System Commands:**
`/ping` - Check bot status and network latency
`/status` - Show download statistics
`/help` - Show this help message

**User Session Commands:**
`/login` - Login with your own Telegram account
`/logout` - Logout from your account
`/mystatus` - Check your login status
`/savetips` - Tips for saving self-destructing media

**User Channel Commands:**
`/mychannel` - Show user's personal channel
`/mychanneltest` - Test user's personal channel
`/setchannel` <id> - Users can set their own channel

---

## 📦 Installation

### 1️⃣ Clone repository
```bash
git clone https://github.com/yourusername/Self-Destructing-Media-Downloader.git
cd Self-Destructing-Media-Downloader
pip install -r requirements.txt
python main.py

---

```
## 🔐 Security Notes

🔒 Session strings are never shared or transmitted
🔒 API credentials are used only during login
🔒 Bot cannot access chats unless the user logs in
🔒 Users can revoke access anytime using /logout
🔒 No passwords or OTPs are logged
🔒 Bot ignores all outgoing messages automatically
🔒 Self-destructing media is downloaded instantly to avoid expiration
⚠️ Using /skip (bot API credentials) increases ban risk

Recommended: Always use your own API_ID & API_HASH

---

## ⚠️ Disclaimer

This project is provided for educational and research purposes only.
The developer is not responsible for misuse
You are responsible for complying with:
Telegram Terms of Service
Local and international laws
Do not use this bot to violate privacy
Do not distribute captured content without consent

❗ Telegram explicitly restricts bots from accessing self-destructing media.
This bot works by user-authorized account sessions, not by bypassing Telegram security.

## 🧠 Important Limitations
Bots cannot directly save TTL media
User login is mandatory for auto-saving
Only private chats are monitored
Group & channel TTL media is ignored
