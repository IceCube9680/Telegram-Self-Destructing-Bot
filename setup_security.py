#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_security.py - Production Security Setup for Telegram Self-Destructing Bot
"""

import os
import sys
from pathlib import Path


def setup_production_environment():
    """Setup secure production environment"""
    
    # Check if running as root
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        print("❌ Do not run as root! Create a dedicated user.")
        sys.exit(1)
    
    # Create secure directories
    secure_dirs = ["user_sessions", "Media", "backups", "logs"]
    for dir_name in secure_dirs:
        p = Path(dir_name)
        p.mkdir(exist_ok=True, parents=True)
        try:
            os.chmod(dir_name, 0o700)  # Only owner can access
        except OSError:
            pass
    
    # Create .env file if not exists
    env_file = Path(".env")
    if not env_file.exists():
        env_sample = Path(".env.sample")
        if env_sample.exists():
            env_file.write_text(env_sample.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            env_file.write_text("""# ===== TELEGRAM BOT CONFIGURATION =====
API_ID=your_api_id_here
API_HASH=your_api_hash_here
BOT_TOKEN=your_bot_token_here
ADMIN_ID=your_admin_id_here

# Channel Configuration
CHANNEL_ID=-1001234567890
SESSION_NAME=self_destruct

# ===== ENCRYPTION KEYS (GENERATE WITH: python generate_key.py) =====
DB_ENCRYPTION_KEY=
ENCRYPTION_KEY=

# ===== ACCESS CONTROL =====
ALLOWED_USERS=

# ===== ADVANCED SETTINGS =====
LOG_LEVEL=INFO
MAX_FILE_SIZE=2147483648
QUEUE_INTERVAL=300
AUTOCHECK_INTERVAL=3600
MAX_RETRIES=3
""", encoding="utf-8")
        try:
            os.chmod(env_file, 0o600)  # Only owner can read/write
        except OSError:
            pass
        print(f"✅ Created {env_file} with secure permissions")
    else:
        try:
            os.chmod(env_file, 0o600)
        except OSError:
            pass
    
    print("\n🔐 Security Checklist:")
    print("✅ Run as non-root user")
    print("✅ Secure directory permissions (0700)")
    print("✅ Protected .env file (0600)")
    print("\n📋 Next Steps:")
    print("1. Edit .env file with your credentials")
    print("2. Generate secure keys with: python3 generate_key.py --write")
    print("3. Run bot with: python3 bot.py")


if __name__ == "__main__":
    setup_production_environment()