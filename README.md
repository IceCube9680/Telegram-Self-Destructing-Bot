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
- ✅ Automatic upload to a Telegram channel
- ✅ Multi-user support
- ✅ Session restore after restart
- ✅ Silent mode (no user notifications)
- ✅ Organized folder structure
- ✅ Admin file management tools

---

## 🧠 How It Works

1. Bot runs using **Bot Token**
2. User logs in via `/login` (Telethon session)
3. User receives self-destructing media in private chat
4. Media is **downloaded immediately**
5. File is saved locally and uploaded to the configured channel

---
## 🛠 Admin Commands

**Command Description**

/setchannel <id> - Set upload channel

/currentchannel - Show channel

/testchannel - Test permissions

/files - List saved files

/download <path> - Download file

/delete <path> - Delete file

/zip - ZIP all media

/all - Send all media

/status - Statistics

/ping - Network test

---

## 👤 User Commands

**Command Description**

/login - Login user account

/logout - Logout user

/mystatus - Show login status

/savetips - TTL saving guide

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
