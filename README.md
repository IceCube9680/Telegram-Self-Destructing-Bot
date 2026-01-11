Self-Destructing Media Downloader Bot
======================================

A sophisticated Telegram bot that automatically saves self-destructing media from user accounts to designated channels, with offline recovery capabilities and a robust queue system.

Features
========

Core Functionality
------------------
• Self-Destructing Media Detection: Automatically detects and saves photos, videos, documents, audio, and voice messages with TTL (Time-To-Live)
• Dual Channel System:
  - User's Personal Channel: Users can set their own channel for personal media storage
  - Admin's Global Channel: Central backup/archive of all users' media
• Multiple Client Support: Uses different Telegram clients for different operations

Advanced Features
-----------------
• Offline Media Recovery: Queue system that stores missed media when bot is offline
• 48-Hour Media Scan: Check for missed self-destructing media from the last 48 hours
• Database-Backed Queue: SQLite database for reliable media queue management
• Automatic Periodic Checks:
  - Bot startup: Auto-check all users + process queue
  - Every 5 minutes: Process pending media queue
  - Every 1 hour: Auto-check missed media for all users
  - User login: Auto-check that user's missed media

User Management
---------------
• User Session Login: Users can login with their own Telegram accounts
• Personal Channel Configuration: Each user can set their own destination channel
• Session Persistence: User sessions are saved and restored on bot restart
• User Status Monitoring: View login status, channel configuration, and pending media

Admin Tools
-----------
• Comprehensive File Management: List, download, delete, and organize media files
• Log Management: View, download, clear, and adjust log levels
• User Management: View all logged-in users and their status
• Queue Management: Monitor and process pending media items
• Channel Testing: Test channel access and permissions

Setup Instructions
==================

Prerequisites
-------------
• Python 3.7+
• Telegram API credentials (from https://my.telegram.org)
• Bot Token (from @BotFather)
• SQLite (included with Python)

Installation
------------
1. Clone the repository or download the bot files
2. Install required packages:
   pip install telethon aiohttp aiofiles rich
3. Run the bot for the first time - it will prompt for configuration

Configuration Files
-------------------
• settings.json: Main configuration file
• bot_state.json: User sessions and state
• bot_queue.db: SQLite database for media queue
• bot.log: Log file
• Media/: Directory for downloaded files
• user_sessions/: Directory for user session files

Usage
=====

For Users
---------
1. Login to your account: /login
2. Set your personal channel: /setmychannel -1001234567890
3. Test channel access: /mychanneltest
4. Check missed media: /checkmissed
5. View your status: /mystatus

For Admin
---------
1. Set global channel: /setgchannel -1001234567890
2. View all users: /users
3. Check missed media:
   - Specific user: /checkmissed <user_id>
   - All users: /checkmissed all
4. Queue management:
   - Stats: /queue_stats
   - Process: /process_queue
5. File management: /files, /download, /delete
6. Log management: /logs, /clearlogs, /download_logs

Command Reference
=================

User Commands
-------------
/start         - Welcome message and bot overview
/login         - Login with your Telegram account
/logout        - Logout and remove session
/mystatus      - View your login status and configuration
/setmychannel  - Set your personal channel ID
/mychannel     - Show your personal channel settings
/mychanneltest - Test your personal channel access
/checkmissed   - Check for missed self-destructing media (last 48h)
/savetips      - Tips for saving self-destructing media

Admin Commands
--------------
/setgchannel    - Set bot's global channel
/testchannel    - Test global channel access
/currentchannel - Show current channel configuration
/users          - List all logged-in users
/queue_stats    - Show media queue statistics
/process_queue  - Process queued media items
/files          - List all files in Media folder
/download       - Download specific file
/download_zip   - Download folder as ZIP
/delete         - Delete specific file
/all            - Download all media files
/zip            - Create ZIP archive of Media folder
/logs           - View bot logs
/clearlogs      - Clear log file (creates backup)
/download_logs  - Download entire log file
/loglevel       - Change log level
/ping           - Check bot status and network latency
/status         - Show download statistics
/help           - Show complete help message

Technical Details
=================

Architecture
------------
• Main Bot Client: Handles bot commands and admin functions
• User Clients: Separate clients for each logged-in user to monitor their chats
• Media Queue: Database-backed queue for offline media processing
• File Organization: Automatic folder organization by sender

Database Schema
---------------
• media_queue: Pending media items with status tracking
• processed_media: Successfully processed media records
• last_seen: Last checked message IDs for each user/chat

Security Features
-----------------
• User sessions stored separately with their own API credentials
• Optional warning when using bot's API credentials
• Safe entity resolution with multiple fallback methods
• Flood wait error handling

Important Notes
===============

Channel ID Requirements
-----------------------
• Must start with -100 (e.g., -1001234567890)
• Not a user ID: User IDs are positive numbers, channel IDs are negative
• How to get: Add @getidsbot to your channel and send any message

Self-Destructing Media Limitations
----------------------------------
• Bots cannot save self-destructing media sent directly to them
• Users must login with their own accounts to save such media
• Media is saved to both user's personal channel and admin's global channel

API Credentials Warning
-----------------------
• Using bot's API credentials for user accounts is risky
• May lead to account flags or bans
• Users should use their own API credentials when possible

Auto-System Features
====================

1. On Bot Startup:
   • Auto-check missed media for all logged-in users
   • Process any pending media queue
   • Restore all user sessions

2. Periodic Tasks:
   • Every 5 minutes: Process media queue
   • Every 1 hour: Auto-check missed media for all users

3. On User Events:
   • User login: Auto-check that user's missed media
   • New self-destructing media: Immediate download and queue backup

Troubleshooting
===============

Common Issues
-------------
1. "Channel ID is positive" error: You're using a user ID instead of channel ID
2. "Could not find the input entity": Bot doesn't have access to the channel
3. Media not saving: User not logged in or no channel configured
4. Queue not processing: Check /queue_stats and use /process_queue

Log Files
---------
• Check bot.log for detailed error information
• Use /logs command to view recent logs
• Adjust log level with /loglevel DEBUG for troubleshooting

License
=======
This bot is provided for educational and personal use. Users are responsible for complying with Telegram's Terms of Service and applicable laws.

Disclaimer
==========
• Use at your own risk
• The developers are not responsible for any account bans or issues
• Users should respect privacy and copyright laws
• This bot is not affiliated with Telegram

Tip: For best results, ensure all channels have the necessary permissions and the bot has been added as an admin with appropriate rights.
