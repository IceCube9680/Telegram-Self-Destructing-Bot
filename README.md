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

## 📦 Installation

### 1️⃣ Clone repository
```bash
git clone https://github.com/yourusername/Self-Destructing-Media-Downloader.git
cd Self-Destructing-Media-Downloader
pip install -r requirements.txt
python main.py```
