# 🚀 Self-Destructing Media Downloader Bot

A powerful **Telegram automation bot** built with **Telethon** that captures, stores, and recovers **self-destructing (TTL) media** from user accounts — even when the server is offline.

This project is designed for **reliability, persistence, and recovery**, using a **SQLite-backed queue system**, per-user sessions, and automatic offline replay.

---

## ✨ Features

### 🔥 Core Functionality
- Detects **self-destructing (TTL) media**
- Supports:
  - Photos
  - Videos
  - Documents
  - Audio & Voice notes
  - Video notes
- Instant download before expiration
- Dual-channel delivery:
  - User’s personal channel
  - Admin/global archive channel

---

### 📴 Offline Media Recovery (Major Feature)
- Media is **never lost** if the server goes offline
- Missed media is stored in a **persistent SQLite queue**
- Automatically processed when the bot comes back online
- Manual recovery command:
→ Scans **last 48 hours** of private chats

---

### 🧠 Queue & Database System
- SQLite (WAL mode) for concurrency safety
- Tracks:
- pending
- processing
- processed
- failed
- expired
- Retry logic with capped attempts
- Deduplication using `(user_id, chat_id, message_id)`

---

### 👤 User Login System
- Secure login using **Telegram user accounts**
- Supports:
- OTP login
- 2FA (password)
- Session persistence using `StringSession`
- Optional `/skip` login (⚠️ high risk, not recommended)

---

### 📂 Smart File Organization
Media/
└── 00 - A - @username - user_id/
└── 1700000000_ab12cd.jpg

- Organized per sender
- Collision-safe filenames
- Persistent folder mapping

---

### 📊 Rich Console Interface
- Live progress bars
- Speed, size & ETA
- Console + file logging (`bot.log`)

---

## 🤖 Bot Commands

### 👥 User Commands
| Command | Description |
|------|------------|
| `/start` | Welcome message |
| `/login` | Login with Telegram account |
| `/setchannel` | Set personal archive channel |
| `/checkmissed` | Recover last 48h missed media |
| `/mystatus` | Show session info |
| `/logout` | Logout and remove session |
| `/cancel` | Cancel login process |

---

## ⚙️ Installation
1️⃣ Clone Repository
git clone https://github.com/yourusername/self-destruct-media-bot.git
cd self-destruct-media-bot

2️⃣ Install Dependencies
pip install -r requirements.txt

3️⃣ Run the Bot
python bot.py


---

## 🧾 Configuration (settings.json)
{
  "api_id": 123456,
  "api_hash": "your_api_hash",
  "bot_token": "your_bot_token",
  "admin_id": 123456789,
  "session_name": "self_destruct",
  "channel_id": -1001234567890
}

⚠️ Channel ID must start with -100 and the bot must be admin

## 🗄 Database Tables

media_queue – Pending & retryable media

processed_media – Completed history

last_seen – Per-chat tracking

All data persists across restarts.

## ⚠️ Security Warning

Avoid /skip unless absolutely necessary

Reusing API credentials can trigger bans

Always prefer personal API ID & HASH

## 📌 Use Cases

Archiving disappearing media

Offline-resilient backups

Moderation & compliance logging

Personal Telegram automation

## 📜 Disclaimer

This project is for educational and personal use only.
You are responsible for complying with Telegram Terms of Service and applicable laws.

## 🧑‍💻 Author

Built for long-running servers, offline recovery, and reliability.
