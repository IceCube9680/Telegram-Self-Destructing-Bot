# -*- coding:utf-8 -*-
"""
Telegram Self-Destructing Media Downloader Bot
Production-Grade Hardened Implementation
"""

import asyncio
import base64
import hashlib
import json
import logging
import logging.handlers
import os
import random
import re
import secrets
import shutil
import string
import sys
import time
import zipfile
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Set, Any, List, Dict, Union, Tuple, cast

import aiofiles
import aiohttp
import threading
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn
)
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    UserIsBlockedError,
    PeerIdInvalidError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    UserNotMutualContactError,
    AuthKeyUnregisteredError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError
)

# Load environment variables
load_dotenv()

# Base directories and files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "Media")
SESSIONS_DIR = os.path.join(BASE_DIR, "user_sessions")
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
STATE_FILE = os.path.join(BASE_DIR, "bot_state.json")
DB_FILE = os.path.join(BASE_DIR, "bot_queue.db")
LOG_FILE = os.path.join(BASE_DIR, "bot.log")

# Create required directories with safe permissions
for d in [MEDIA_DIR, SESSIONS_DIR, BACKUPS_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass

# Setup logging with log rotation
log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)

formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
console_handler.setLevel(log_level)

file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)
file_handler.setLevel(log_level)

logger = logging.getLogger("SelfDestructBot")
logger.setLevel(log_level)
# Remove any existing handlers to avoid duplicates
logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)

console = Console()

# Global runtime state
ACTIVE_USER_CLIENTS: Dict[str, TelegramClient] = {}
BOT_CLIENT: Optional[TelegramClient] = None
MEDIA_QUEUE = None
BOT_CONFIG: Optional[Dict[str, Any]] = None
CONFIGURED_ADMIN_ID: Optional[int] = None
FERNET_CIPHER: Optional[Fernet] = None

# Backward-compatibility alias
all_media_dir = MEDIA_DIR


# =====================================================================
# PATH SANITIZATION & SECURITY HELPERS
# =====================================================================

def sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal and illegal characters."""
    if not filename:
        return "unnamed_file.bin"
    # Strip null bytes and directory components
    cleaned = filename.replace('\x00', '')
    cleaned = os.path.basename(cleaned.replace('\\', '/'))
    cleaned = re.sub(r'[\r\n\t]', '', cleaned)
    cleaned = re.sub(r'[^a-zA-Z0-9._\- ]', '_', cleaned)
    # Prevent leading/trailing dots
    cleaned = cleaned.strip('. ')
    return cleaned if cleaned else "unnamed_file.bin"


def sanitize_folder_name(name: str) -> str:
    """Sanitize folder component to prevent path traversal."""
    if not name:
        return "unknown"
    cleaned = name.replace('\x00', '')
    cleaned = os.path.basename(cleaned.replace('\\', '/'))
    cleaned = re.sub(r'[^a-zA-Z0-9._\- @]', '_', cleaned)
    cleaned = cleaned.strip('. ')
    return cleaned if cleaned else "unknown"


def is_safe_media_path(target_path: str, base_dir: str = MEDIA_DIR) -> bool:
    """Ensure target_path resolves strictly within base_dir."""
    try:
        real_base = os.path.realpath(base_dir)
        real_target = os.path.realpath(target_path)
        return real_target.startswith(real_base) and real_target != real_base
    except Exception:
        return False


def resolve_safe_media_path(user_input_path: str, base_dir: str = MEDIA_DIR) -> Optional[str]:
    """Safely resolve user input path into a verified path within MEDIA_DIR."""
    if not user_input_path:
        return None
    raw = user_input_path.strip().strip('"\'')
    if '\x00' in raw:
        return None
    
    # Normalize path
    if os.path.isabs(raw):
        candidate = os.path.normpath(raw)
    else:
        # Check if user passed "Media/..." or relative path
        if raw.startswith("Media/") or raw.startswith("Media\\"):
            candidate = os.path.normpath(os.path.join(BASE_DIR, raw))
        else:
            candidate = os.path.normpath(os.path.join(base_dir, raw))
            
    if is_safe_media_path(candidate, base_dir):
        return candidate
    return None


# =====================================================================
# CENTRALIZED AUTHORIZATION HELPERS
# =====================================================================

def parse_admin_id(raw_admin_id: Any) -> Optional[int]:
    """Parse and validate an ADMIN_ID as a positive numeric Telegram user ID."""
    if raw_admin_id is None:
        return None
    raw_str = str(raw_admin_id).strip().strip('"\'')
    if not raw_str:
        return None
    try:
        val = int(raw_str)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def parse_allowed_users(raw_allowed_users: Any) -> Set[int]:
    """Safely parse comma-separated ALLOWED_USERS into a set of numeric IDs."""
    if not raw_allowed_users:
        return set()
    raw_str = str(raw_allowed_users).strip().strip('"\'')
    if not raw_str:
        return set()
    result = set()
    for item in raw_str.split(','):
        clean_item = item.strip().strip('"\'')
        if clean_item:
            try:
                val = int(clean_item)
                if val > 0:
                    result.add(val)
            except (ValueError, TypeError):
                logger.warning(f"Invalid user ID in ALLOWED_USERS: {clean_item}")
    return result


def extract_user_id(user_id_or_event: Any) -> Optional[int]:
    """Extract numeric Telegram user ID from an event, int, or str."""
    if user_id_or_event is None:
        return None
    if isinstance(user_id_or_event, int):
        return user_id_or_event if user_id_or_event > 0 else None
    if hasattr(user_id_or_event, 'sender_id'):
        sender_id = user_id_or_event.sender_id
        if sender_id is not None:
            try:
                val = int(sender_id)
                return val if val > 0 else None
            except (ValueError, TypeError):
                return None
    try:
        raw_str = str(user_id_or_event).strip().strip('"\'')
        val = int(raw_str)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def is_admin_id(user_id_or_event: Any, admin_id: Optional[Any] = None) -> bool:
    """Synchronous check if user is the configured administrator."""
    target_admin = parse_admin_id(admin_id) if admin_id is not None else CONFIGURED_ADMIN_ID
    if target_admin is None:
        target_admin = parse_admin_id(os.getenv("ADMIN_ID"))
    if target_admin is None:
        return False
    user_id = extract_user_id(user_id_or_event)
    if user_id is None:
        return False
    return user_id == target_admin


async def is_admin(user_id_or_event: Any, admin_id: Optional[Any] = None) -> bool:
    """Check if sender or user_id is the configured administrator."""
    return is_admin_id(user_id_or_event, admin_id)


async def is_allowed_user(user_id_or_event: Any) -> bool:
    """Check if user exists in ALLOWED_USERS environment variable or is active in SQLite database."""
    user_id = extract_user_id(user_id_or_event)
    if user_id is None:
        return False
    
    # 1. Check ALLOWED_USERS environment variable
    env_users = parse_allowed_users(os.getenv("ALLOWED_USERS", ""))
    if user_id in env_users:
        return True
    
    # 2. Check SQLite database allowed_users table where is_active = 1
    if MEDIA_QUEUE:
        try:
            conn = MEDIA_QUEUE._get_connection()
            try:
                c = conn.cursor()
                c.execute(
                    "SELECT user_id FROM allowed_users WHERE user_id = ? AND is_active = 1",
                    (user_id,)
                )
                row = c.fetchone()
                return row is not None
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Error checking allowed_users database: {e}")
            return False
            
    return False


async def is_authorized(user_id_or_event: Any, admin_id: Optional[Any] = None) -> bool:
    """Authoritative centralized authorization check.
    Hierarchy:
    1. Admin (ADMIN_ID) -> ALWAYS authorized (regardless of database record or state)
    2. Explicitly Allowed User (ALLOWED_USERS env or active in database) -> Authorized
    3. Everything else -> Denied
    """
    user_id = extract_user_id(user_id_or_event)
    if user_id is None:
        logger.warning(f"Authorization denied for invalid/null user_id={user_id_or_event}")
        return False
    
    # Step 1: Admin check (FIRST and authoritative - bypasses any DB restriction)
    if is_admin_id(user_id, admin_id):
        return True
    
    # Step 2: Allowed user check
    if await is_allowed_user(user_id):
        return True
    
    # Step 3: Deny
    logger.warning(f"Authorization denied for user_id={user_id}")
    return False


# =====================================================================
# APPLICATION & DATABASE ENCRYPTION
# =====================================================================

def init_encryption(key_env: Optional[str] = None) -> Fernet:
    """Initialize Fernet encryption with deterministic key derivation and safety checks."""
    global FERNET_CIPHER
    
    raw_key = key_env or os.getenv("ENCRYPTION_KEY")
    if raw_key:
        raw_key = raw_key.strip().strip('"\'')
        # Check if already a valid 32-byte urlsafe base64 key (44 chars)
        try:
            decoded = base64.urlsafe_b64decode(raw_key.encode('utf-8'))
            if len(decoded) == 32:
                FERNET_CIPHER = Fernet(raw_key.encode('utf-8'))
                return FERNET_CIPHER
        except Exception:
            pass
        
        # Deterministically derive 32-byte key using SHA-256
        key_hash = hashlib.sha256(raw_key.encode('utf-8')).digest()
        derived_key = base64.urlsafe_b64encode(key_hash)
        FERNET_CIPHER = Fernet(derived_key)
        return FERNET_CIPHER
    
    # Check if existing session files or encrypted database rows exist
    existing_sessions = [f for f in os.listdir(SESSIONS_DIR) if f.endswith('.session')] if os.path.exists(SESSIONS_DIR) else []
    
    if existing_sessions:
        logger.critical(
            "CRITICAL: ENCRYPTION_KEY is not set, but existing user sessions were found! "
            "Generating a new key would make existing sessions unrecoverable. "
            "Please configure ENCRYPTION_KEY in your .env file or generate one using 'python3 generate_key.py --write'."
        )
        raise RuntimeError("ENCRYPTION_KEY missing while encrypted sessions exist. Aborting to prevent data loss.")
    
    # Fresh setup without key - generate temporary fallback key
    logger.warning("⚠️ ENCRYPTION_KEY not set in environment. Generating a temporary key for this session.")
    logger.warning("⚠️ Please run 'python3 generate_key.py --write' to persist a key before production use.")
    temp_key = Fernet.generate_key()
    FERNET_CIPHER = Fernet(temp_key)
    return FERNET_CIPHER


# Initialize cipher on module load
FERNET_CIPHER = init_encryption()


def encrypt_data(data: str) -> str:
    """Encrypt sensitive data using Fernet."""
    if not data:
        return ""
    if FERNET_CIPHER is None:
        init_encryption()
    encrypted = FERNET_CIPHER.encrypt(data.encode('utf-8'))
    return encrypted.decode('utf-8')


def decrypt_data(encrypted_data: str) -> str:
    """Decrypt sensitive data with safe error handling."""
    if not encrypted_data:
        return ""
    if FERNET_CIPHER is None:
        init_encryption()
    try:
        decrypted = FERNET_CIPHER.decrypt(encrypted_data.encode('utf-8'))
        return decrypted.decode('utf-8')
    except InvalidToken:
        logger.error("Failed to decrypt data: Invalid encryption key or corrupted ciphertext.")
        return ""
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return ""


# =====================================================================
# DATABASE CLASS WITH WAL & THREAD SAFETY
# =====================================================================

class MediaQueue:
    """Database-based queue for pending media with encryption support and thread safety"""
    
    def __init__(self):
        self._lock = threading.RLock()
        self.init_database()
        self.reset_stale_processing()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-safe database connection with WAL mode."""
        with self._lock:
            conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            
            # Optional SQLCipher DB key if supported by SQLite build
            encryption_key = os.getenv("DB_ENCRYPTION_KEY")
            if encryption_key:
                safe_key = encryption_key.strip().replace("'", "''")
                try:
                    conn.execute(f"PRAGMA key='{safe_key}'")
                except sqlite3.OperationalError:
                    pass
            
            return conn
    
    def init_database(self):
        """Initialize SQLite database schema and indexes."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            
            # 1. media_queue table
            c.execute('''
                CREATE TABLE IF NOT EXISTS media_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    session_string TEXT NOT NULL,
                    api_id INTEGER NOT NULL,
                    api_hash TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    sender_username TEXT,
                    sender_id INTEGER,
                    received_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    retry_count INTEGER DEFAULT 0,
                    last_attempt TIMESTAMP,
                    status TEXT DEFAULT 'pending',
                    ttl_seconds INTEGER,
                    download_time TIMESTAMP,
                    UNIQUE(user_id, message_id, chat_id)
                )
            ''')
            
            # 2. processed_media table
            c.execute('''
                CREATE TABLE IF NOT EXISTS processed_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    queue_id INTEGER,
                    user_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    sender_username TEXT,
                    processed_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    channel_sent BOOLEAN DEFAULT 0,
                    file_path TEXT
                )
            ''')
            
            # 3. last_seen table
            c.execute('''
                CREATE TABLE IF NOT EXISTS last_seen (
                    user_id INTEGER,
                    chat_id INTEGER NOT NULL,
                    last_message_id INTEGER DEFAULT 0,
                    last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, chat_id)
                )
            ''')
            
            # 4. allowed_users table for access control
            c.execute('''
                CREATE TABLE IF NOT EXISTS allowed_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    added_by INTEGER,
                    added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')
            
            # Indexes
            c.execute('CREATE INDEX IF NOT EXISTS idx_media_queue_user_status ON media_queue(user_id, status)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_media_queue_status ON media_queue(status)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_last_seen_user_chat ON last_seen(user_id, chat_id)')
            
            conn.commit()
            console.print("[green]✓ Database initialization complete[/green]")
            
        except Exception as e:
            console.print(f"[red]Database initialization error: {e}[/red]")
            logger.error(f"Database initialization error: {e}")
        finally:
            if conn:
                conn.close()
    
    def reset_stale_processing(self):
        """Reset items stuck in 'processing' state back to 'pending' on startup."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute("UPDATE media_queue SET status = 'pending' WHERE status = 'processing'")
            reset_count = c.rowcount
            conn.commit()
            if reset_count > 0:
                logger.info(f"Reset {reset_count} stale in-flight queue items back to 'pending'.")
        except Exception as e:
            logger.error(f"Error resetting stale processing items: {e}")
        finally:
            if conn:
                conn.close()
    
    async def add_to_queue(self, user_id: int, session_string: str, api_id: int, api_hash: str,
                           message_id: int, chat_id: int, media_type: str, sender_username: str = None,
                           sender_id: int = None, ttl_seconds: int = None) -> Optional[int]:
        """Add media to queue for processing with encrypted sensitive data."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            
            # Check if already exists
            c.execute('''
                SELECT id FROM media_queue 
                WHERE user_id = ? AND message_id = ? AND chat_id = ?
            ''', (user_id, message_id, chat_id))
            
            if c.fetchone():
                return None
            
            # Encrypt sensitive credentials before storing in DB
            encrypted_session = encrypt_data(session_string)
            encrypted_api_hash = encrypt_data(api_hash)
            
            # Insert new record
            c.execute('''
                INSERT INTO media_queue 
                (user_id, session_string, api_id, api_hash, message_id, chat_id, 
                 media_type, sender_username, sender_id, status, ttl_seconds, download_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, CURRENT_TIMESTAMP)
            ''', (user_id, encrypted_session, api_id, encrypted_api_hash, message_id, chat_id, 
                  media_type, sender_username, sender_id, ttl_seconds))
            
            queue_id = c.lastrowid
            conn.commit()
            
            logger.info(f"Media added to queue: ID {queue_id} for user {user_id}")
            return queue_id
            
        except Exception as e:
            logger.error(f"Error adding to queue: {e}")
            return None
        finally:
            if conn:
                conn.close()
    
    async def get_pending_media(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get pending media items across all users with decrypted credentials."""
        conn = None
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            c.execute('''
                SELECT * FROM media_queue 
                WHERE status = 'pending' 
                ORDER BY download_time ASC 
                LIMIT ?
            ''', (limit,))
            
            rows = c.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item.get('session_string'):
                    item['session_string'] = decrypt_data(item['session_string'])
                if item.get('api_hash'):
                    item['api_hash'] = decrypt_data(item['api_hash'])
                result.append(item)
            
            return result
        except Exception as e:
            logger.error(f"Error getting pending media: {e}")
            return []
        finally:
            if conn:
                conn.close()
    
    async def get_pending_media_for_user(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """Get pending media items for a specific user with decrypted credentials."""
        conn = None
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            c.execute('''
                SELECT * FROM media_queue 
                WHERE status = 'pending' AND user_id = ?
                ORDER BY download_time ASC 
                LIMIT ?
            ''', (user_id, limit))
            
            rows = c.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item.get('session_string'):
                    item['session_string'] = decrypt_data(item['session_string'])
                if item.get('api_hash'):
                    item['api_hash'] = decrypt_data(item['api_hash'])
                result.append(item)
            
            return result
        except Exception as e:
            logger.error(f"Error getting pending media for user {user_id}: {e}")
            return []
        finally:
            if conn:
                conn.close()
    
    async def update_status(self, queue_id: int, status: str, retry_count: Optional[int] = None) -> bool:
        """Update media queue status."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            
            if retry_count is not None:
                c.execute('''
                    UPDATE media_queue 
                    SET status = ?, retry_count = ?, last_attempt = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, retry_count, queue_id))
            else:
                c.execute('''
                    UPDATE media_queue 
                    SET status = ?, last_attempt = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, queue_id))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating queue status: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    async def mark_as_processed(self, queue_id: int, user_id: int, message_id: int, chat_id: int,
                                media_type: str, sender_username: str = None, channel_sent: bool = False,
                                file_path: str = None) -> bool:
        """Mark media as processed in both processed_media and media_queue."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            
            c.execute('''
                INSERT INTO processed_media 
                (queue_id, user_id, message_id, chat_id, media_type, 
                 sender_username, channel_sent, file_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (queue_id, user_id, message_id, chat_id, media_type, 
                  sender_username, channel_sent, file_path))
            
            c.execute("UPDATE media_queue SET status = 'processed' WHERE id = ?", (queue_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error marking as processed: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    async def update_last_seen(self, user_id: int, chat_id: int, last_message_id: int) -> bool:
        """Update last seen message ID for a user in a specific chat."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO last_seen 
                (user_id, chat_id, last_message_id, last_check)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, chat_id, last_message_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating last seen: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    async def get_last_seen(self, user_id: int, chat_id: int) -> int:
        """Get last seen message ID for a user in a specific chat."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute('SELECT last_message_id FROM last_seen WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
            result = c.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Error getting last seen: {e}")
            return 0
        finally:
            if conn:
                conn.close()
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics summary."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            stats = {}
            
            c.execute('SELECT status, COUNT(*) FROM media_queue GROUP BY status')
            for status, count in c.fetchall():
                stats[f'{status}_count'] = count
            
            c.execute('SELECT COUNT(*) FROM processed_media')
            stats['total_processed'] = c.fetchone()[0]
            
            c.execute('SELECT user_id, COUNT(*) FROM media_queue WHERE status="pending" GROUP BY user_id')
            stats['pending_by_user'] = {str(uid): count for uid, count in c.fetchall()}
            
            return stats
        except Exception as e:
            logger.error(f"Error getting queue stats: {e}")
            return {}
        finally:
            if conn:
                conn.close()
    
    # Access Control DB operations
    async def is_user_allowed(self, user_id: int) -> bool:
        """Check if user is allowed to use the bot (delegates to centralized is_authorized)."""
        return await is_authorized(user_id)
    
    async def add_allowed_user(self, user_id: int, username: str, added_by: int) -> bool:
        """Add or reactivate user in allowed list."""
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute('''
                INSERT INTO allowed_users 
                (user_id, username, added_by, added_time, is_active)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    added_by = excluded.added_by,
                    added_time = CURRENT_TIMESTAMP,
                    is_active = 1
            ''', (user_id, username, added_by))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding allowed user: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    async def remove_allowed_user(self, user_id: int) -> bool:
        """Remove/deactivate user from allowed list (Admin cannot be deactivated)."""
        if is_admin_id(user_id):
            logger.warning(f"Attempted to deactivate admin user_id={user_id} in database - blocked.")
            return False
            
        conn = None
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute('UPDATE allowed_users SET is_active = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error removing allowed user: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    async def get_allowed_users(self) -> List[Dict[str, Any]]:
        """Get list of all active allowed users from database."""
        conn = None
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('''
                SELECT user_id, username, added_by, added_time 
                FROM allowed_users 
                WHERE is_active = 1
                ORDER BY added_time DESC
            ''')
            return [dict(row) for row in c.fetchall()]
        except Exception as e:
            logger.error(f"Error getting allowed users: {e}")
            return []
        finally:
            if conn:
                conn.close()

    async def get_all_users_db(self) -> List[Dict[str, Any]]:
        """Get list of all users from database (both active and inactive)."""
        conn = None
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute('''
                SELECT user_id, username, added_by, added_time, is_active 
                FROM allowed_users 
                ORDER BY added_time DESC
            ''')
            return [dict(row) for row in c.fetchall()]
        except Exception as e:
            logger.error(f"Error getting all users from database: {e}")
            return []
        finally:
            if conn:
                conn.close()


# =====================================================================
# CONFIGURATION & STATE MANAGEMENT
# =====================================================================

async def load_config() -> Tuple[Optional[int], Optional[str], Optional[int], Optional[str], str, Optional[int]]:
    """Load configuration strictly from environment variables, with settings.json fallback."""
    global BOT_CONFIG, CONFIGURED_ADMIN_ID
    
    api_id_raw = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    admin_id_raw = os.getenv("ADMIN_ID")
    bot_token = os.getenv("BOT_TOKEN", "")
    session_name = os.getenv("SESSION_NAME", "self_destruct")
    channel_id_raw = os.getenv("CHANNEL_ID")
    
    # Strip whitespace/quotes
    if api_hash:
        api_hash = api_hash.strip().strip('"\'')
    if bot_token:
        bot_token = bot_token.strip().strip('"\'')
    if session_name:
        session_name = session_name.strip().strip('"\'')
    
    # Parse integers
    api_id = None
    if api_id_raw:
        try:
            api_id = int(str(api_id_raw).strip().strip('"\''))
        except (ValueError, TypeError):
            logger.error("Invalid API_ID in environment: must be an integer.")
            
    admin_id = parse_admin_id(admin_id_raw)
    
    channel_id = None
    if channel_id_raw:
        try:
            channel_id = int(str(channel_id_raw).strip().strip('"\''))
        except (ValueError, TypeError):
            logger.warning("Invalid CHANNEL_ID in environment.")
            
    # Check settings.json fallback if missing
    if (not api_id or not api_hash or not admin_id or not bot_token) and os.path.exists(SETTINGS_FILE):
        try:
            async with aiofiles.open(SETTINGS_FILE, mode="r") as f:
                settings = json.loads(await f.read())
            api_id = api_id or (int(settings["api_id"]) if settings.get("api_id") else None)
            api_hash = api_hash or settings.get("api_hash")
            admin_id = admin_id or parse_admin_id(settings.get("admin_id"))
            bot_token = bot_token or settings.get("bot_token", "")
            session_name = session_name or settings.get("session_name", "self_destruct")
            if channel_id is None and settings.get("channel_id"):
                try:
                    channel_id = int(settings["channel_id"])
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to read settings.json fallback: {e}")
            
    BOT_CONFIG = {
        "api_id": api_id,
        "api_hash": api_hash,
        "bot_token": bot_token,
        "admin_id": admin_id,
        "channel_id": channel_id,
        "session_name": session_name
    }
    CONFIGURED_ADMIN_ID = admin_id
    
    return api_id, api_hash, admin_id, bot_token, session_name, channel_id


async def update_channel_id(new_channel_id: str) -> bool:
    """Update global channel ID in settings.json."""
    try:
        settings = {}
        if os.path.exists(SETTINGS_FILE):
            async with aiofiles.open(SETTINGS_FILE, mode="r") as file:
                settings = json.loads(await file.read())
        settings["channel_id"] = str(new_channel_id)
        async with aiofiles.open(SETTINGS_FILE, mode="w") as file:
            await file.write(json.dumps(settings, indent=4))
        return True
    except Exception as e:
        logger.error(f"Error updating channel ID in settings: {e}")
        return False


async def update_user_channel_id(user_id: int, new_channel_id: int, state: dict) -> bool:
    """Update personal channel ID for a specific user."""
    if str(user_id) in state.get("user_sessions", {}):
        state["user_sessions"][str(user_id)]["channel_id"] = new_channel_id
        await save_state(state)
        return True
    return False


async def get_user_channel_id(user_id: int, state: dict) -> Optional[int]:
    """Get channel ID for a specific user."""
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if user_session:
        return user_session.get("channel_id")
    return None


async def load_state() -> dict:
    """Load bot state from bot_state.json safely."""
    if os.path.exists(STATE_FILE):
        try:
            async with aiofiles.open(STATE_FILE, mode="r") as file:
                state = json.loads(await file.read())
        except Exception as e:
            logger.error(f"Error reading state file {STATE_FILE}: {e}")
            state = {}
    else:
        state = {}
        
    state.setdefault("letter_counter", 0)
    state.setdefault("user_folders", {})
    state.setdefault("user_sessions", {})
    state.setdefault("login_sessions", {})
    state.setdefault("global_forwarding_enabled", True)
    return state


async def save_state(state: dict):
    """Save bot state to bot_state.json atomically."""
    temp_file = f"{STATE_FILE}.tmp"
    try:
        async with aiofiles.open(temp_file, mode="w") as file:
            await file.write(json.dumps(state, indent=4))
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        logger.error(f"Error saving state: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass


def get_user_session_file(user_id: int) -> str:
    """Get secure session file path for a user."""
    return os.path.join(SESSIONS_DIR, f"user_{int(user_id)}.session")


class RichDownloadProgress:
    """Enhanced progress bar for terminal download tracking."""
    def __init__(self, filename: str, total_size: int):
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
    
    def update(self, downloaded: int):
        self.progress.update(self.task, completed=downloaded)
    
    def close(self):
        self.progress.stop()


async def safe_notify(bot_client: TelegramClient, user_id: int, text: str):
    """Safely notify a user via bot DM without throwing on blocks or missing peers."""
    if not bot_client:
        return
    try:
        await bot_client.send_message(user_id, text, silent=True)
    except (UserIsBlockedError, PeerIdInvalidError):
        pass
    except ValueError as e:
        if "Could not find the input entity" in str(e):
            pass
    except FloodWaitError as e:
        logger.warning(f"FloodWait {e.seconds}s while notifying user {user_id}")
    except Exception as e:
        logger.debug(f"safe_notify skipped for {user_id}: {e}")


def extract_ttl(message: Any) -> Optional[int]:
    """Extract self-destructing TTL in seconds from all potential Telethon message metadata."""
    if not message or not hasattr(message, 'media') or not message.media:
        return None
    
    media = message.media
    # 1. Direct ttl_seconds on media
    if hasattr(media, 'ttl_seconds') and media.ttl_seconds:
        return int(media.ttl_seconds)
    
    # 2. Photo ttl_seconds
    if hasattr(media, 'photo') and hasattr(media.photo, 'ttl_seconds') and media.photo.ttl_seconds:
        return int(media.photo.ttl_seconds)
        
    # 3. Document ttl_seconds
    if hasattr(media, 'document') and hasattr(media.document, 'ttl_seconds') and media.document.ttl_seconds:
        return int(media.document.ttl_seconds)
        
    # 4. Video / Voice / Audio attributes
    for attr_name in ['video', 'document', 'voice', 'audio', 'video_note']:
        sub = getattr(media, attr_name, None)
        if sub and hasattr(sub, 'ttl_seconds') and sub.ttl_seconds:
            return int(sub.ttl_seconds)
            
    # 5. Document attributes list inspection
    if hasattr(media, 'document') and hasattr(media.document, 'attributes') and media.document.attributes:
        for attr in media.document.attributes:
            if hasattr(attr, 'ttl_seconds') and attr.ttl_seconds:
                return int(attr.ttl_seconds)
                
    return None


def get_media_type_str(event_or_message: Any) -> Tuple[str, str]:
    """Determine media extension and media type name from message."""
    msg = getattr(event_or_message, 'message', event_or_message)
    if not msg or not hasattr(msg, 'media') or not msg.media:
        return ".bin", "unknown"
        
    if getattr(msg, 'photo', None):
        return ".jpg", "photo"
    elif getattr(msg, 'video', None):
        return ".mp4", "video"
    elif getattr(msg, 'video_note', None):
        return ".mp4", "video_note"
    elif getattr(msg, 'voice', None):
        return ".ogg", "voice"
    elif getattr(msg, 'audio', None):
        return ".mp3", "audio"
    elif getattr(msg, 'document', None):
        ext = ".bin"
        if hasattr(msg.document, 'attributes') and msg.document.attributes:
            for attr in msg.document.attributes:
                if hasattr(attr, 'file_name') and attr.file_name:
                    ext = os.path.splitext(attr.file_name)[1] or ".bin"
                    break
        return ext, "document"
    return ".bin", "unknown"


# =====================================================================
# CHANNEL & FILE DELIVERY HELPERS
# =====================================================================

async def send_to_user_channel(user_client: TelegramClient, file_path: str, username: str, channel_id: int) -> bool:
    """Send file to user's personal channel using USER'S client."""
    try:
        if not os.path.exists(file_path):
            logger.error(f"Cannot upload to user channel: File not found {file_path}")
            return False

        try:
            channel_id = int(channel_id)
        except Exception:
            logger.error(f"Invalid channel_id: {channel_id}")
            return False

        if channel_id >= 0:
            console.print(f"[red]ERROR: Channel ID {channel_id} is a positive user ID, not a channel ID![/red]")
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

        console.print(f"[yellow]Sending to user channel {channel_id} using USER client[/yellow]")

        try:
            await user_client.send_file(channel_id, file=file_path, caption=caption)
            console.print(f"[green]✓ File sent to user channel {channel_id}[/green]")
            return True
        except Exception as e:
            logger.warning(f"Direct send to channel {channel_id} failed: {e}. Resolving entity...")
            entity = await user_client.get_entity(channel_id)
            await user_client.send_file(entity, file=file_path, caption=caption)
            console.print(f"[green]✓ File sent via entity to user channel {channel_id}[/green]")
            return True

    except Exception as e:
        console.print(f"[red]send_to_user_channel failed: {e}[/red]")
        logger.error(f"send_to_user_channel error for channel {channel_id}: {e}")
        return False


async def send_to_admin_channel(bot_client: TelegramClient, file_path: str, username: str, channel_id: int) -> bool:
    """Send file to admin's channel using BOT client."""
    try:
        if not os.path.exists(file_path):
            logger.error(f"Cannot upload to admin channel: File not found {file_path}")
            return False

        try:
            channel_id = int(channel_id)
        except Exception:
            logger.error(f"Invalid admin channel_id: {channel_id}")
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

        console.print(f"[yellow]Sending to admin channel {channel_id} using BOT client[/yellow]")

        try:
            await bot_client.send_file(channel_id, file=file_path, caption=caption)
            console.print(f"[green]✓ File sent to admin channel {channel_id}[/green]")
            return True
        except Exception as e:
            logger.warning(f"Direct send to admin channel {channel_id} failed: {e}. Resolving entity...")
            entity = await bot_client.get_entity(channel_id)
            await bot_client.send_file(entity, file=file_path, caption=caption)
            console.print(f"[green]✓ File sent via entity to admin channel {channel_id}[/green]")
            return True

    except Exception as e:
        console.print(f"[red]send_to_admin_channel failed: {e}[/red]")
        logger.error(f"send_to_admin_channel error for channel {channel_id}: {e}")
        return False


async def organize_and_save_file(file_path: str, sender_username: str, user_id: int, state: dict, media_type: str) -> str:
    """Organize and move temporary file into sanitized user subfolder."""
    try:
        clean_sender = sanitize_folder_name(sender_username)
        user_folder_key = f"{clean_sender}_{user_id}"
        
        if user_folder_key in state["user_folders"]:
            user_folder_name = state["user_folders"][user_folder_key]
        else:
            counter = state["letter_counter"]
            letter = string.ascii_uppercase[counter % 26]
            user_folder_name = f"{counter:02d} - {letter} - @{clean_sender} - {user_id}"
            state["user_folders"][user_folder_key] = user_folder_name
            state["letter_counter"] += 1
            await save_state(state)
        
        user_folder_path = os.path.join(MEDIA_DIR, user_folder_name)
        os.makedirs(user_folder_path, exist_ok=True)
        
        timestamp = int(time.time())
        random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        
        # Clean extension
        ext = media_type if media_type.startswith('.') else f".{media_type}"
        clean_ext = sanitize_filename(ext)
        final_filename = f"{timestamp}_{random_str}{clean_ext}"
        final_path = os.path.join(user_folder_path, final_filename)
        
        # Safe move
        shutil.move(file_path, final_path)
        console.print(f"[green]✓ File organized: {final_path}[/green]")
        return final_path
        
    except Exception as e:
        logger.error(f"Error organizing file: {e}")
        return file_path


# =====================================================================
# MEDIA DOWNLOADER & QUEUE PROCESSOR
# =====================================================================

async def user_downloader(event: Any, user_client: TelegramClient, bot_client: TelegramClient,
                          media_base_dir: str, state: dict):
    """Download self-destructing media from user account and distribute to channels."""
    temp_path = None
    try:
        try:
            receiver_entity = await user_client.get_me()
            receiver_id = receiver_entity.id
            receiver_username = receiver_entity.username or "NoUsername"
        except Exception:
            receiver_id = "Unknown"
            receiver_username = "Unknown"
        
        try:
            sender = await event.get_sender()
            sender_username = sender.username if sender and getattr(sender, 'username', None) else "NoUsername"
            sender_id = sender.id if sender and getattr(sender, 'id', None) else "Unknown"
        except Exception:
            sender_username = "Unknown"
            sender_id = "Unknown"

        console.print(f"[cyan]Downloading TTL media from @{sender_username} (Sender ID: {sender_id}) for @{receiver_username} (ID: {receiver_id})[/cyan]")

        file_ext, media_type_name = get_media_type_str(event)
        
        # Generate safe temporary file
        timestamp = int(time.time())
        random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        temp_filename = f"temp_{timestamp}_{random_str}{file_ext}"
        temp_path = os.path.join(MEDIA_DIR, temp_filename)

        file_size = event.file.size if getattr(event, 'file', None) else 0
        progress = RichDownloadProgress(temp_filename, file_size) if file_size > 0 else None

        # Download with retry
        for attempt in range(3):
            try:
                await event.download_media(
                    file=temp_path,
                    progress_callback=lambda c, t: progress.update(c) if progress else None
                )
                break
            except FloodWaitError as fwe:
                logger.warning(f"FloodWait during download: {fwe.seconds}s. Waiting...")
                await asyncio.sleep(fwe.seconds + 1)
            except Exception as dl_err:
                if attempt == 2:
                    raise dl_err
                await asyncio.sleep(1)

        if progress:
            progress.close()

        if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
            console.print("[red]ERROR: Downloaded file is missing or empty.[/red]")
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            return

        # Organize into structured user folder
        final_path = await organize_and_save_file(temp_path, sender_username, receiver_id, state, file_ext)

        # Dispatch to channels
        global_forwarding_enabled = state.get("global_forwarding_enabled", True)
        user_session = state.get("user_sessions", {}).get(str(receiver_id))
        user_channel_id = user_session.get("channel_id") if user_session else None
        admin_channel_id = getattr(bot_client, "channel_id", None)

        sent_to_user_channel = False
        sent_to_admin_channel = False

        if user_channel_id:
            try:
                if await send_to_user_channel(user_client, final_path, sender_username, user_channel_id):
                    sent_to_user_channel = True
            except Exception as e:
                logger.error(f"User channel upload failed: {e}")

        if admin_channel_id and global_forwarding_enabled:
            if admin_channel_id != user_channel_id:
                try:
                    if await send_to_admin_channel(bot_client, final_path, sender_username, admin_channel_id):
                        sent_to_admin_channel = True
                except Exception as e:
                    logger.error(f"Admin channel upload failed: {e}")
            else:
                sent_to_admin_channel = True

        logger.info(f"Media saved successfully for user {receiver_id} → {os.path.basename(final_path)}")

    except Exception as e:
        console.print(f"[red]Error in user_downloader: {e}[/red]")
        logger.error(f"user_downloader error: {e}")
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


async def user_downloader_queue(user_client: TelegramClient, file_path: str, sender_username: str,
                                user_id: int, state: dict, media_type: str, ttl: Optional[int]) -> bool:
    """Process downloaded media from queue and distribute to channels."""
    try:
        global_forwarding_enabled = state.get("global_forwarding_enabled", True)
        user_session = state.get("user_sessions", {}).get(str(user_id))
        user_channel_id = user_session.get("channel_id") if user_session else None

        if user_channel_id:
            try:
                await send_to_user_channel(user_client, file_path, sender_username, user_channel_id)
            except Exception as e:
                logger.error(f"User channel upload failed in queue: {e}")

        global BOT_CLIENT
        if BOT_CLIENT and getattr(BOT_CLIENT, 'channel_id', None) and global_forwarding_enabled:
            admin_channel = BOT_CLIENT.channel_id
            if admin_channel != user_channel_id:
                try:
                    await send_to_admin_channel(BOT_CLIENT, file_path, sender_username, admin_channel)
                except Exception as e:
                    logger.error(f"Admin channel upload failed in queue: {e}")

        # Organize into final folder
        await organize_and_save_file(file_path, sender_username, user_id, state, media_type)
        return True
    except Exception as e:
        logger.error(f"Error in user_downloader_queue: {e}")
        return False


async def process_queue_items(user_id: Optional[int] = None, limit: int = 10, state: Optional[dict] = None) -> Tuple[int, int]:
    """Unified queue processor handling both per-user and global queue processing."""
    if not MEDIA_QUEUE:
        return 0, 0
    
    if state is None:
        state = await load_state()
        
    if user_id is not None:
        pending_items = await MEDIA_QUEUE.get_pending_media_for_user(user_id, limit=limit)
    else:
        pending_items = await MEDIA_QUEUE.get_pending_media(limit=limit)
        
    if not pending_items:
        return 0, 0
        
    max_retries = int(os.getenv("MAX_RETRIES", "3"))
    processed_count = 0
    failed_count = 0
    
    for item in pending_items:
        item_id = item['id']
        curr_retry = item.get('retry_count', 0)
        
        if curr_retry >= max_retries:
            logger.warning(f"Queue item {item_id} exceeded max retries ({max_retries}). Marking failed.")
            await MEDIA_QUEUE.update_status(item_id, 'failed')
            failed_count += 1
            continue
            
        await MEDIA_QUEUE.update_status(item_id, 'processing', curr_retry + 1)
        
        user_client = None
        temp_file_path = None
        try:
            session_str = item.get('session_string')
            if not session_str:
                logger.error(f"Queue item {item_id} missing session string.")
                await MEDIA_QUEUE.update_status(item_id, 'failed')
                failed_count += 1
                continue
                
            session = StringSession(session_str)
            user_client = TelegramClient(session, item['api_id'], item['api_hash'])
            await user_client.connect()
            
            if not await user_client.is_user_authorized():
                logger.error(f"User {item['user_id']} is no longer authorized. Failing queue item {item_id}.")
                await MEDIA_QUEUE.update_status(item_id, 'failed')
                failed_count += 1
                await user_client.disconnect()
                continue
                
            # Resolve chat entity
            entity = None
            try:
                entity = await user_client.get_entity(item['chat_id'])
            except Exception:
                # Try from dialogs cache
                try:
                    dialogs = await user_client.get_dialogs(limit=20)
                    for d in dialogs:
                        if d.entity.id == item['chat_id']:
                            entity = d.entity
                            break
                except Exception:
                    pass
                    
            if not entity:
                logger.error(f"Could not resolve entity for chat {item['chat_id']}.")
                await MEDIA_QUEUE.update_status(item_id, 'pending')
                failed_count += 1
                await user_client.disconnect()
                continue
                
            message = await user_client.get_messages(entity, ids=item['message_id'])
            if not message or not message.media:
                logger.warning(f"Message {item['message_id']} in chat {item['chat_id']} not found or has no media.")
                await MEDIA_QUEUE.update_status(item_id, 'failed')
                failed_count += 1
                await user_client.disconnect()
                continue
                
            ttl = extract_ttl(message) or item.get('ttl_seconds')
            
            # Temporary download
            timestamp = int(time.time())
            random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
            file_ext = item.get('media_type', 'bin')
            if not file_ext.startswith('.'):
                file_ext = f".{file_ext}"
            temp_filename = f"temp_q_{timestamp}_{random_str}{file_ext}"
            temp_file_path = os.path.join(MEDIA_DIR, temp_filename)
            
            # Download media
            await message.download_media(file=temp_file_path)
            
            if os.path.exists(temp_file_path) and os.path.getsize(temp_file_path) > 0:
                success = await user_downloader_queue(
                    user_client=user_client,
                    file_path=temp_file_path,
                    sender_username=item.get('sender_username') or "Unknown",
                    user_id=item['user_id'],
                    state=state,
                    media_type=file_ext,
                    ttl=ttl
                )
                
                if success:
                    await MEDIA_QUEUE.mark_as_processed(
                        queue_id=item_id,
                        user_id=item['user_id'],
                        message_id=item['message_id'],
                        chat_id=item['chat_id'],
                        media_type=file_ext,
                        sender_username=item.get('sender_username'),
                        channel_sent=True,
                        file_path=temp_file_path
                    )
                    await MEDIA_QUEUE.update_last_seen(item['user_id'], item['chat_id'], item['message_id'])
                    processed_count += 1
                else:
                    await MEDIA_QUEUE.update_status(item_id, 'pending')
                    failed_count += 1
            else:
                logger.error(f"Downloaded queue file {temp_file_path} is empty or missing.")
                await MEDIA_QUEUE.update_status(item_id, 'pending')
                failed_count += 1
                
            await user_client.disconnect()
            await asyncio.sleep(1)
            
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait in queue processing: {fwe.seconds}s.")
            await MEDIA_QUEUE.update_status(item_id, 'pending')
            failed_count += 1
            if user_client:
                try:
                    await user_client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(fwe.seconds + 1)
        except Exception as e:
            logger.error(f"Error processing queue item {item_id}: {e}")
            await MEDIA_QUEUE.update_status(item_id, 'failed' if curr_retry + 1 >= max_retries else 'pending')
            failed_count += 1
            if user_client:
                try:
                    await user_client.disconnect()
                except Exception:
                    pass
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass
                    
    return processed_count, failed_count


async def process_queued_media():
    """Process pending queue items across all users."""
    logger.info("Running process_queued_media()...")
    processed, failed = await process_queue_items(user_id=None, limit=10)
    logger.info(f"process_queued_media() completed: {processed} processed, {failed} failed.")


async def process_user_queued_media(user_id: int, state: dict):
    """Process pending queue items for a specific user."""
    logger.info(f"Running process_user_queued_media() for user {user_id}...")
    processed, failed = await process_queue_items(user_id=user_id, limit=10, state=state)
    logger.info(f"User {user_id} queue processing completed: {processed} processed, {failed} failed.")


# =====================================================================
# MISSED MEDIA SCANNER
# =====================================================================

async def check_missed_media(user_id: int, user_client: TelegramClient, state: dict) -> int:
    """Scan private chats for missed self-destructing media in the last 48 hours."""
    logger.info(f"Scanning missed media for user {user_id} (last 48 hours)...")
    total_found = 0
    total_queued = 0
    
    try:
        dialogs = await user_client.get_dialogs(limit=50)
        cutoff_time = datetime.now() - timedelta(hours=48)
        
        for dialog in dialogs:
            if dialog.is_user and getattr(dialog.entity, 'bot', False) is False:
                chat_id = dialog.entity.id
                last_seen_id = await MEDIA_QUEUE.get_last_seen(user_id, chat_id)
                
                try:
                    messages = []
                    async for message in user_client.iter_messages(
                        dialog.entity,
                        limit=200,
                        offset_date=cutoff_time,
                        reverse=True
                    ):
                        messages.append(message)
                        
                    for message in messages:
                        if message.id <= last_seen_id:
                            continue
                            
                        ttl = extract_ttl(message)
                        if ttl and ttl > 0:
                            total_found += 1
                            session_string = user_client.session.save()
                            
                            sender = None
                            try:
                                sender = await message.get_sender()
                            except Exception:
                                pass
                                
                            sender_username = sender.username if sender and getattr(sender, 'username', None) else "Unknown"
                            sender_id = sender.id if sender and getattr(sender, 'id', None) else None
                            
                            file_ext, _ = get_media_type_str(message)
                            
                            queue_id = await MEDIA_QUEUE.add_to_queue(
                                user_id=user_id,
                                session_string=session_string,
                                api_id=user_client.api_id,
                                api_hash=user_client.api_hash,
                                message_id=message.id,
                                chat_id=chat_id,
                                media_type=file_ext,
                                sender_username=sender_username,
                                sender_id=sender_id,
                                ttl_seconds=ttl
                            )
                            if queue_id:
                                total_queued += 1
                                
                    if messages:
                        max_id = max(m.id for m in messages)
                        if max_id > last_seen_id:
                            await MEDIA_QUEUE.update_last_seen(user_id, chat_id, max_id)
                            
                except Exception as chat_err:
                    logger.warning(f"Error checking chat {chat_id} for user {user_id}: {chat_err}")
                    
        logger.info(f"Missed media scan for user {user_id}: Found {total_found}, Queued {total_queued}")
        return total_found
    except Exception as e:
        logger.error(f"check_missed_media error for user {user_id}: {e}")
        return 0


# =====================================================================
# EVENT HANDLERS & USER/ADMIN COMMANDS
# =====================================================================

async def setup_user_client_handlers(user_client: TelegramClient, user_id: int, bot_client: TelegramClient, state: dict):
    """Setup event handlers for user client to catch incoming self-destructing media."""
    
    @user_client.on(events.NewMessage(incoming=True))
    async def user_media_handler(event):
        try:
            if event.out or not event.is_private or not event.media:
                return
                
            ttl = extract_ttl(event.message)
            if not ttl or ttl <= 0:
                return  # Silently ignore normal non-TTL media
                
            console.print(f"[green]⚠️ Self-destructing media detected for user {user_id} (TTL: {ttl}s)[/green]")
            
            # Download immediately
            await user_downloader(event, user_client, bot_client, MEDIA_DIR, state)
            
            # Update last seen
            await MEDIA_QUEUE.update_last_seen(user_id, event.chat_id, event.id)
            
        except Exception as e:
            logger.error(f"User media handler error for {user_id}: {e}")


async def complete_user_login(event: Any, user_id: int, user_client: TelegramClient, state: dict):
    """Complete user authentication, persist session, setup handlers, and run missed media check."""
    try:
        me = await user_client.get_me()
        session_string = user_client.session.save()
        user_session_file = get_user_session_file(user_id)
        
        # Save session file with 0600 permissions
        async with aiofiles.open(user_session_file, mode="w") as f:
            await f.write(session_string)
        try:
            os.chmod(user_session_file, 0o600)
        except OSError:
            pass
            
        state.setdefault("user_sessions", {})
        state["user_sessions"][str(user_id)] = {
            "api_id": user_client.api_id,
            "api_hash": user_client.api_hash,
            "username": me.username,
            "phone": me.phone,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "session_file": user_session_file,
            "login_time": time.time(),
            "channel_id": state.get("user_sessions", {}).get(str(user_id), {}).get("channel_id")
        }
        
        if str(user_id) in state.get("login_sessions", {}):
            del state["login_sessions"][str(user_id)]
            
        await save_state(state)
        
        global BOT_CLIENT, ACTIVE_USER_CLIENTS
        if BOT_CLIENT:
            await setup_user_client_handlers(user_client, user_id, BOT_CLIENT, state)
            
        ACTIVE_USER_CLIENTS[str(user_id)] = user_client
        await cast(Any, user_client.start())
        
        # Run background check for missed media
        missed_count = await check_missed_media(user_id, user_client, state)
        
        welcome_msg = (
            f"✅ **Login Successful!**\n\n"
            f"👤 **Account Details:**\n"
            f"• Name: {me.first_name} {me.last_name or ''}\n"
            f"• Username: @{me.username or 'Not set'}\n"
            f"• User ID: `{me.id}`\n\n"
        )
        if missed_count > 0:
            welcome_msg += f"📥 **Found {missed_count} missed self-destructing media from the last 48 hours!** (Queued)\n\n"
            
        welcome_msg += (
            f"📱 **Next Steps:**\n"
            f"• Set personal channel: `/setmychannel -100xxxxxxxxxx`\n"
            f"• Test channel access: `/mychanneltest`\n"
            f"• Check session status: `/mystatus`\n"
            f"• Check missed media: `/checkmissed`\n"
            f"• Logout anytime: `/logout`\n\n"
            f"🛡️ **Offline Media Recovery:** ACTIVE"
        )
        
        await event.reply(welcome_msg, parse_mode='markdown')
        logger.info(f"User {user_id} (@{me.username}) successfully logged in.")
        
    except Exception as e:
        logger.error(f"Error completing login for user {user_id}: {e}")
        await event.reply(f"❌ Error completing login: {e}")
        if str(user_id) in state.get("login_sessions", {}):
            del state["login_sessions"][str(user_id)]
            await save_state(state)


# --- USER COMMANDS ---

async def handle_start(event: Any, admin_id: Optional[int]):
    """Handle /start command."""
    if not event.is_private:
        return
    welcome_message = (
        "🤖 **Welcome to Self-Destructing Media Downloader Bot!**\n\n"
        "This bot captures self-destructing (TTL) media using authorized user sessions.\n\n"
        "**Features:**\n"
        "• Instant capture of self-destructing photos, videos, voice, audio, and documents\n"
        "• Automatic delivery to your personal Telegram channel\n"
        "• Dual-channel backup with optional global forwarding\n"
        "• 48-hour offline recovery queue system\n\n"
        "**Getting Started:**\n"
        "1. Login: `/login`\n"
        "2. Set personal channel: `/setmychannel <channel_id>`\n"
        "3. Test channel: `/mychanneltest`\n"
        "4. Recover missed media: `/checkmissed`\n"
        "5. See all commands: `/help`"
    )
    await event.reply(welcome_message, parse_mode='markdown')


async def handle_login(event: Any, admin_id: Optional[int], bot_client: TelegramClient, state: dict):
    """Handle /login command."""
    if not event.is_private:
        await event.reply("❌ Please use this command in private chat.")
        return
        
    user_id = event.sender_id
    if not await is_authorized(user_id, admin_id):
        admin_contact = "the administrator"
        try:
            target_admin = parse_admin_id(admin_id) or CONFIGURED_ADMIN_ID
            if target_admin and bot_client:
                admin_ent = await bot_client.get_entity(target_admin)
                admin_contact = f"@{admin_ent.username}" if getattr(admin_ent, 'username', None) else "the administrator"
        except Exception:
            pass
        await event.reply(
            "❌ **Access Denied**\n\n"
            "You are not authorized to use this bot.\n"
            f"Please contact {admin_contact} for access."
        )
        return
        
    user_session_file = get_user_session_file(user_id)
    if os.path.exists(user_session_file):
        try:
            async with aiofiles.open(user_session_file, mode="r") as f:
                session_string = await f.read()
            if session_string:
                session = StringSession(session_string)
                user_client = TelegramClient(
                    session,
                    BOT_CONFIG["api_id"] if BOT_CONFIG else int(os.getenv("API_ID", "0")),
                    BOT_CONFIG["api_hash"] if BOT_CONFIG else os.getenv("API_HASH", "")
                )
                await user_client.connect()
                if await user_client.is_user_authorized():
                    await setup_user_client_handlers(user_client, user_id, bot_client, state)
                    ACTIVE_USER_CLIENTS[str(user_id)] = user_client
                    await cast(Any, user_client.start())
                    await event.reply(
                        "✅ **You are already logged in!**\n\n"
                        "• Set channel: `/setmychannel <id>`\n"
                        "• Check status: `/mystatus`\n"
                        "• Recover media: `/checkmissed`\n"
                        "• Logout: `/logout`"
                    )
                    return
                else:
                    await user_client.disconnect()
                    os.remove(user_session_file)
        except Exception as e:
            logger.warning(f"Session verification failed for {user_id}: {e}")
            try:
                if os.path.exists(user_session_file):
                    os.remove(user_session_file)
            except OSError:
                pass
                
    state.setdefault("login_sessions", {})
    state["login_sessions"][str(user_id)] = {
        "step": "api_id",
        "api_id": None,
        "api_hash": None,
        "phone": None,
        "phone_code_hash": None,
        "session_string": None
    }
    await save_state(state)
    
    await event.reply(
        "**1. Send Your Telegram API ID:**\n\n"
        "Obtain credentials from https://my.telegram.org\n"
        "Enter `/cancel` anytime to abort.",
        parse_mode='markdown'
    )


async def handle_api_id(event: Any, admin_id: Optional[int], state: dict):
    """Handle API ID input in login flow."""
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    if not user_data or user_data.get("step") != "api_id":
        return
        
    text = event.text.strip()
    if text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
        
    try:
        api_id = int(text)
        if api_id <= 0:
            raise ValueError()
        state["login_sessions"][str(user_id)]["api_id"] = api_id
        state["login_sessions"][str(user_id)]["step"] = "api_hash"
        await save_state(state)
        await event.reply(
            "✅ **API ID saved!**\n\n"
            "**2. Now send your Telegram API HASH:**\n"
            "Enter `/cancel` to abort.",
            parse_mode='markdown'
        )
    except ValueError:
        await event.reply("❌ Invalid API ID. Must be a positive number.\nExample: `1234567`\nEnter `/cancel` to abort.")


async def handle_api_hash(event: Any, admin_id: Optional[int], state: dict):
    """Handle API Hash input in login flow."""
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    if not user_data or user_data.get("step") != "api_hash":
        return
        
    text = event.text.strip()
    if text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
        
    state["login_sessions"][str(user_id)]["api_hash"] = text
    state["login_sessions"][str(user_id)]["step"] = "phone"
    await save_state(state)
    await event.reply(
        "**3. Please send your phone number with country code:**\n"
        "Example: `+13124562345` or `+919876543210`\n\n"
        "Enter `/cancel` to abort.",
        parse_mode='markdown'
    )


async def handle_phone(event: Any, admin_id: Optional[int], state: dict, bot_client: TelegramClient):
    """Handle phone number input and request verification code."""
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    if not user_data or user_data.get("step") != "phone":
        return
        
    text = event.text.strip()
    if text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
        
    phone = text
    if not phone.startswith('+'):
        await event.reply("❌ Phone number must start with country code (e.g., `+1`, `+91`).\nEnter `/cancel` to abort.")
        return
        
    user_client = None
    try:
        api_id = user_data["api_id"]
        api_hash = user_data["api_hash"]
        
        session = StringSession()
        user_client = TelegramClient(session, api_id, api_hash)
        await user_client.connect()
        
        sent_code = await user_client.send_code_request(phone)
        
        state["login_sessions"][str(user_id)]["phone"] = phone
        state["login_sessions"][str(user_id)]["step"] = "code"
        state["login_sessions"][str(user_id)]["session_string"] = user_client.session.save()
        state["login_sessions"][str(user_id)]["phone_code_hash"] = sent_code.phone_code_hash
        await save_state(state)
        
        await user_client.disconnect()
        await event.reply(
            "**4. OTP Verification Code Sent!**\n\n"
            "Please check your official Telegram account for the login code.\n"
            "Format: Send code with spaces, e.g. `1 2 3 4 5` or `12345`.\n\n"
            "Enter `/cancel` to abort.",
            parse_mode='markdown'
        )
    except FloodWaitError as fwe:
        await event.reply(f"❌ Telegram rate limit: Too many attempts. Please wait {fwe.seconds} seconds.")
        if user_client:
            try:
                await user_client.disconnect()
            except Exception:
                pass
    except Exception as e:
        await event.reply(f"❌ Error sending code: {e}")
        if str(user_id) in state.get("login_sessions", {}):
            del state["login_sessions"][str(user_id)]
            await save_state(state)
        if user_client:
            try:
                await user_client.disconnect()
            except Exception:
                pass


async def handle_code(event: Any, admin_id: Optional[int], state: dict):
    """Handle verification OTP input."""
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    if not user_data or user_data.get("step") != "code":
        return
        
    text = event.text.strip()
    if text.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
        
    code = text.replace(' ', '')
    if not code.isdigit() or not (4 <= len(code) <= 6):
        await event.reply("❌ Invalid OTP format. Please send digits only (4 to 6 numbers).\nExample: `1 2 3 4 5`.")
        return
        
    user_client = None
    try:
        session = StringSession(user_data["session_string"])
        user_client = TelegramClient(session, user_data["api_id"], user_data["api_hash"])
        await user_client.connect()
        
        try:
            await user_client.sign_in(
                phone=user_data["phone"],
                code=code,
                phone_code_hash=user_data["phone_code_hash"]
            )
            await complete_user_login(event, user_id, user_client, state)
        except SessionPasswordNeededError:
            state["login_sessions"][str(user_id)]["session_string"] = user_client.session.save()
            state["login_sessions"][str(user_id)]["step"] = "2fa"
            await save_state(state)
            await user_client.disconnect()
            await event.reply(
                "🔐 **Two-Step Verification (2FA) is enabled on this account.**\n\n"
                "Please enter your 2FA password:\n"
                "Enter `/cancel` to abort.",
                parse_mode='markdown'
            )
        except PhoneCodeInvalidError:
            await event.reply("❌ Invalid OTP code. Please check and try again.")
            await user_client.disconnect()
        except PhoneCodeExpiredError:
            await event.reply("❌ OTP code expired. Please restart with `/login`.")
            if str(user_id) in state.get("login_sessions", {}):
                del state["login_sessions"][str(user_id)]
                await save_state(state)
            await user_client.disconnect()
            
    except Exception as e:
        logger.error(f"Error during sign_in for user {user_id}: {e}")
        await event.reply(f"❌ Error signing in: {e}")
        if user_client:
            try:
                await user_client.disconnect()
            except Exception:
                pass


async def handle_2fa(event: Any, admin_id: Optional[int], state: dict):
    """Handle 2FA password entry."""
    user_id = event.sender_id
    user_data = state.get("login_sessions", {}).get(str(user_id))
    if not user_data or user_data.get("step") != "2fa":
        return
        
    password = event.text.strip()
    if password.lower() == "/cancel":
        await handle_cancel(event, admin_id, state)
        return
        
    user_client = None
    try:
        session = StringSession(user_data["session_string"])
        user_client = TelegramClient(session, user_data["api_id"], user_data["api_hash"])
        await user_client.connect()
        
        await user_client.sign_in(password=password)
        await complete_user_login(event, user_id, user_client, state)
    except PasswordHashInvalidError:
        await event.reply("❌ Incorrect 2FA password. Please try again or `/cancel` to abort.")
        if user_client:
            try:
                await user_client.disconnect()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"2FA login error for user {user_id}: {e}")
        await event.reply(f"❌ 2FA error: {e}")
        if user_client:
            try:
                await user_client.disconnect()
            except Exception:
                pass


async def handle_cancel(event: Any, admin_id: Optional[int], state: dict):
    """Handle /cancel command to abort login state."""
    if not event.is_private:
        return
    user_id = event.sender_id
    if str(user_id) in state.get("login_sessions", {}):
        del state["login_sessions"][str(user_id)]
        await save_state(state)
        await event.reply("✅ Active login process has been cancelled.")
    else:
        await event.reply("ℹ️ No active login process to cancel.")


async def handle_logout(event: Any, admin_id: Optional[int], state: dict):
    """Handle /logout command."""
    if not event.is_private:
        await event.reply("❌ Please use this command in private chat.")
        return
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if not user_session:
        await event.reply("❌ You are not logged in.")
        return
        
    user_session_file = get_user_session_file(user_id)
    if os.path.exists(user_session_file):
        try:
            os.remove(user_session_file)
        except OSError:
            pass
            
    if str(user_id) in state.get("user_sessions", {}):
        del state["user_sessions"][str(user_id)]
        await save_state(state)
        
    global ACTIVE_USER_CLIENTS
    if str(user_id) in ACTIVE_USER_CLIENTS:
        try:
            await ACTIVE_USER_CLIENTS[str(user_id)].disconnect()
        except Exception:
            pass
        del ACTIVE_USER_CLIENTS[str(user_id)]
        
    await event.reply("✅ Successfully logged out. Your session has been removed.")


async def handle_mystatus(event: Any, admin_id: Optional[int], state: dict):
    """Handle /mystatus command."""
    if not event.is_private:
        return
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if not user_session:
        await event.reply("❌ **You are not logged in.** Use `/login` to connect your account.")
        return
        
    login_time = user_session.get("login_time", time.time())
    duration = max(0, time.time() - login_time)
    hours = int(duration // 3600)
    minutes = int((duration % 3600) // 60)
    
    channel_id = user_session.get("channel_id")
    channel_info = f"• Channel ID: `{channel_id}`" if channel_id else "• Channel: ❌ Not set (use `/setmychannel`)"
    
    stats = await MEDIA_QUEUE.get_queue_stats() if MEDIA_QUEUE else {}
    pending_count = stats.get('pending_by_user', {}).get(str(user_id), 0)
    
    await event.reply(
        f"✅ **Account Status: CONNECTED**\n\n"
        f"👤 **Details:**\n"
        f"• Name: {user_session.get('first_name', 'Unknown')} {user_session.get('last_name', '')}\n"
        f"• Username: @{user_session.get('username', 'Not set')}\n"
        f"• User ID: `{user_id}`\n"
        f"• Connected for: {hours}h {minutes}m\n\n"
        f"📢 **Channel Configuration:**\n"
        f"{channel_info}\n\n"
        f"📥 **Queue & Recovery:**\n"
        f"• Pending media in queue: {pending_count}\n"
        f"• 48h offline recovery: ✅ Active"
    )


async def handle_setmychannel(event: Any, admin_id: Optional[int], state: dict):
    """Handle /setmychannel <channel_id>."""
    if not event.is_private:
        return
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if not user_session:
        await event.reply("❌ You are not logged in. Use `/login` first.")
        return
        
    args = event.text.split()
    if len(args) < 2:
        await event.reply(
            "❌ **Usage:** `/setmychannel <channel_id>`\n"
            "**Example:** `/setmychannel -1001234567890`\n\n"
            "Channel IDs must start with `-100`."
        )
        return
        
    raw_id = args[1].strip()
    if not raw_id.startswith('-100'):
        await event.reply("❌ Invalid format! Channel IDs must be negative numbers starting with `-100`.\nExample: `-1001234567890`")
        return
        
    try:
        channel_id = int(raw_id)
    except ValueError:
        await event.reply("❌ Channel ID must be a valid number.")
        return
        
    success = await update_user_channel_id(user_id, channel_id, state)
    if success:
        await event.reply(
            f"✅ **Personal Channel configured successfully!**\n\n"
            f"• Channel ID: `{channel_id}`\n"
            f"• Test access with: `/mychanneltest`"
        )
    else:
        await event.reply("❌ Failed to update channel configuration.")


async def handle_mychannel(event: Any, admin_id: Optional[int], state: dict):
    """Handle /mychannel command."""
    if not event.is_private:
        return
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if not user_session:
        await event.reply("❌ You are not logged in. Use `/login` first.")
        return
        
    channel_id = user_session.get("channel_id")
    if channel_id:
        await event.reply(
            f"📢 **Your Personal Channel:**\n\n"
            f"• Channel ID: `{channel_id}`\n"
            f"• Test with: `/mychanneltest`\n"
            f"• Change with: `/setmychannel <id>`"
        )
    else:
        await event.reply("ℹ️ You have not configured a personal channel yet. Use `/setmychannel -100xxxxxxxxxx`.")


async def handle_mychanneltest(event: Any, admin_id: Optional[int], state: dict):
    """Test user's personal channel access using their connected user client."""
    if not event.is_private:
        return
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if not user_session:
        await event.reply("❌ You are not logged in. Use `/login` first.")
        return
        
    channel_id = user_session.get("channel_id")
    if not channel_id:
        await event.reply("❌ No personal channel configured. Use `/setmychannel` first.")
        return
        
    await event.reply(f"🔄 Testing personal channel access (`{channel_id}`)...")
    
    global ACTIVE_USER_CLIENTS
    user_client = ACTIVE_USER_CLIENTS.get(str(user_id))
    
    if not user_client or not user_client.is_connected():
        await event.reply("❌ User client is not currently connected. Try `/login` again.")
        return
        
    try:
        # Test posting using USER's client (which is what handles personal media delivery)
        test_msg = f"✅ Personal Channel Test Successful!\nTime: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        await user_client.send_message(channel_id, test_msg)
        await event.reply(f"✅ **Channel Test Passed!**\nTest message was successfully posted to `{channel_id}` via your account.")
    except ChannelPrivateError:
        await event.reply("❌ Failed: The channel is private and your user account does not have access.")
    except ChatAdminRequiredError:
        await event.reply("❌ Failed: Your user account lacks permission to post messages in this channel.")
    except ValueError as e:
        await event.reply(f"❌ Failed: Could not resolve channel ID `{channel_id}` ({e}). Ensure the ID starts with `-100`.")
    except Exception as e:
        await event.reply(f"❌ Channel test failed: {e}")


async def handle_checkmissed(event: Any, admin_id: Optional[int], state: dict):
    """Handle /checkmissed for regular user."""
    if not event.is_private:
        return
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if not user_session:
        await event.reply("❌ You are not logged in. Use `/login` first.")
        return
        
    await event.reply("🔄 Scanning private chats for missed self-destructing media (last 48 hours)...")
    
    global ACTIVE_USER_CLIENTS
    user_client = ACTIVE_USER_CLIENTS.get(str(user_id))
    if not user_client:
        await event.reply("❌ User client is not active. Please `/login`.")
        return
        
    found = await check_missed_media(user_id, user_client, state)
    if found > 0:
        await event.reply(
            f"✅ **Found {found} missed self-destructing media items!**\n"
            "They have been queued for processing.\n"
            "Use `/queue_stats` to see queue status or `/process_queue` to process immediately."
        )
    else:
        await event.reply("📭 No missed self-destructing media found in the last 48 hours.")


async def handle_queue_stats(event: Any, admin_id: Optional[int], state: dict):
    """Show personal queue statistics."""
    if not event.is_private:
        return
    user_id = event.sender_id
    stats = await MEDIA_QUEUE.get_queue_stats() if MEDIA_QUEUE else {}
    pending = stats.get('pending_by_user', {}).get(str(user_id), 0)
    
    await event.reply(
        f"📊 **Your Media Queue:**\n\n"
        f"• Pending Items: {pending}\n"
        f"• Total Processed (All Users): {stats.get('total_processed', 0)}\n\n"
        f"Use `/process_queue` to process your queue immediately."
    )


async def handle_process_queue(event: Any, admin_id: Optional[int], state: dict):
    """Process personal queue items."""
    if not event.is_private:
        return
    user_id = event.sender_id
    user_session = state.get("user_sessions", {}).get(str(user_id))
    if not user_session:
        await event.reply("❌ You are not logged in. Use `/login` first.")
        return
        
    await event.reply("🔄 Processing your pending media queue...")
    processed, failed = await process_queue_items(user_id=user_id, limit=10, state=state)
    await event.reply(f"✅ **Queue processing completed:** {processed} processed, {failed} failed.")


async def handle_savetips(event: Any):
    """Display tips for saving self-destructing media."""
    tips = (
        "💡 **Self-Destructing Media Tips & Guidelines:**\n\n"
        "1. **Telegram Limitation:** Regular Telegram bots cannot directly access TTL media. "
        "This tool uses an authorized user session (`/login`) to receive and capture media.\n"
        "2. **Personal Channel:** Configure your personal channel with `/setmychannel -100xxxxxxxxxx`. "
        "Ensure channel IDs start with `-100`.\n"
        "3. **Offline Recovery:** If the bot was offline when someone sent TTL media, use `/checkmissed` to scan up to 48 hours back.\n"
        "4. **Security:** Your API credentials and session strings are encrypted using Fernet (AES-128-CBC) and never shared."
    )
    await event.reply(tips)


# --- ADMIN COMMANDS ---

async def handle_setgchannel(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Set global bot channel."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
        
    args = event.text.split()
    if len(args) < 2:
        await event.reply("❌ Usage: `/setgchannel <channel_id>`\nExample: `/setgchannel -1001234567890`")
        return
        
    raw_id = args[1].strip()
    if not raw_id.startswith('-100'):
        await event.reply("❌ Channel ID must start with `-100`.")
        return
        
    try:
        channel_id = int(raw_id)
    except ValueError:
        await event.reply("❌ Invalid integer channel ID.")
        return
        
    await update_channel_id(str(channel_id))
    if hasattr(event.client, 'channel_id'):
        event.client.channel_id = channel_id
    if BOT_CLIENT:
        BOT_CLIENT.channel_id = channel_id
        
    await event.reply(f"✅ **Global Channel set to:** `{channel_id}`")


async def handle_currentchannel(event: Any, admin_id: Optional[int], state: dict):
    """Admin/User: Display global channel configuration."""
    if not await is_admin(event, admin_id):
        await handle_mychannel(event, admin_id, state)
        return
        
    ch_id = getattr(event.client, 'channel_id', None) or (BOT_CONFIG.get('channel_id') if BOT_CONFIG else None)
    if ch_id:
        await event.reply(f"📢 **Bot Global Channel:** `{ch_id}`")
    else:
        await event.reply("ℹ️ No global channel configured. Use `/setgchannel <channel_id>`.")


async def handle_testchannel(event: Any, admin_id: Optional[int]):
    """Admin: Test global channel posting access."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
        
    ch_id = getattr(event.client, 'channel_id', None)
    if not ch_id:
        await event.reply("❌ No global channel configured. Use `/setgchannel`.")
        return
        
    try:
        test_msg = f"✅ Global Channel Test Successful!\nTime: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        await event.client.send_message(ch_id, test_msg)
        await event.reply(f"✅ Test message successfully sent to global channel `{ch_id}`.")
    except Exception as e:
        await event.reply(f"❌ Global channel test failed: {e}")


async def handle_globalforward(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Toggle forwarding to global channel."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
        
    args = event.text.split()
    if len(args) == 1 or args[1].lower() == "status":
        curr = state.get("global_forwarding_enabled", True)
        await event.reply(f"📢 **Global Forwarding Status:** {'✅ ENABLED' if curr else '❌ DISABLED'}\n\nToggle with `/globalforward enable` or `/globalforward disable`.")
        return
        
    action = args[1].lower()
    if action == "enable":
        state["global_forwarding_enabled"] = True
        await save_state(state)
        await event.reply("✅ **Global Forwarding ENABLED.** Media will be copied to admin's global channel.")
    elif action == "disable":
        state["global_forwarding_enabled"] = False
        await save_state(state)
        await event.reply("❌ **Global Forwarding DISABLED.** Media will go only to personal user channels.")
    else:
        await event.reply("❌ Usage: `/globalforward <enable|disable|status>`")


# --- ADMIN FILE MANAGEMENT (HARDENED AGAINST PATH TRAVERSAL) ---

async def handle_files(event: Any, admin_id: Optional[int]):
    """Admin: List files within the Media folder safely."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
        
    total_files = 0
    total_size = 0
    file_list = []
    
    for root, dirs, files in os.walk(MEDIA_DIR):
        for f in files:
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
                total_size += sz
                total_files += 1
                rel = os.path.relpath(p, MEDIA_DIR)
                if len(file_list) < 30:
                    file_list.append(f"• `{rel}` ({sz / (1024*1024):.2f} MB)")
            except Exception:
                pass
                
    size_mb = total_size / (1024 * 1024)
    msg = f"📁 **Media Directory Summary:**\n• Total Files: {total_files}\n• Total Size: {size_mb:.2f} MB\n\n"
    if file_list:
        msg += "**Recent Files:**\n" + "\n".join(file_list)
        if total_files > 30:
            msg += f"\n\n_...and {total_files - 30} more files._"
    else:
        msg += "_No files found in Media directory._"
        
    await event.reply(msg, parse_mode='markdown')


async def handle_check(event: Any, admin_id: Optional[int]):
    """Admin: Check for files modified in the last 24 hours."""
    if not await is_admin(event, admin_id):
        return
    cutoff = time.time() - (24 * 3600)
    recent = []
    for root, dirs, files in os.walk(MEDIA_DIR):
        for f in files:
            p = os.path.join(root, f)
            try:
                mtime = os.path.getmtime(p)
                if mtime > cutoff:
                    sz = os.path.getsize(p)
                    recent.append((os.path.relpath(p, MEDIA_DIR), sz, mtime))
            except Exception:
                pass
                
    recent.sort(key=lambda x: x[2], reverse=True)
    if not recent:
        await event.reply("📭 No media files modified in the last 24 hours.")
        return
        
    lines = [f"• `{r[0]}` ({r[1]/(1024*1024):.2f} MB)" for r in recent[:25]]
    await event.reply(f"📁 **Files in Last 24 Hours ({len(recent)} total):**\n\n" + "\n".join(lines), parse_mode='markdown')


async def handle_download(event: Any, admin_id: Optional[int]):
    """Admin: Download a specific file strictly within Media folder."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
        
    args = event.text.split(maxsplit=1)
    if len(args) < 2:
        await event.reply("❌ Usage: `/download <relative_path_in_media>`")
        return
        
    target = resolve_safe_media_path(args[1], MEDIA_DIR)
    if not target or not os.path.exists(target) or os.path.isdir(target):
        await event.reply("❌ Invalid path or file not found within Media directory.")
        return
        
    sz = os.path.getsize(target)
    if sz > 1900 * 1024 * 1024:
        await event.reply("⚠️ File exceeds 1.9 GB Telegram upload limit.")
        return
        
    await event.reply(f"📤 Uploading `{os.path.basename(target)}` ({sz / (1024*1024):.2f} MB)...")
    try:
        await event.client.send_file(event.chat_id, target, force_document=True)
    except Exception as e:
        await event.reply(f"❌ Upload error: {e}")


async def handle_download_zip(event: Any, admin_id: Optional[int]):
    """Admin: Download a folder within Media as a ZIP archive."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
        
    args = event.text.split(maxsplit=1)
    if len(args) < 2:
        await event.reply("❌ Usage: `/download_zip <relative_folder_in_media>`")
        return
        
    target = resolve_safe_media_path(args[1], MEDIA_DIR)
    if not target or not os.path.exists(target) or not os.path.isdir(target):
        await event.reply("❌ Invalid folder path or folder not found within Media directory.")
        return
        
    zip_path = os.path.join(BACKUPS_DIR, f"folder_{int(time.time())}.zip")
    await event.reply(f"📦 Creating ZIP archive for `{os.path.basename(target)}`...")
    
    def make_zip():
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(target):
                for file in files:
                    fp = os.path.join(root, file)
                    arc = os.path.relpath(fp, target)
                    zipf.write(fp, arc)
                    
    await asyncio.to_thread(make_zip)
    
    try:
        if os.path.exists(zip_path) and os.path.getsize(zip_path) <= 1900 * 1024 * 1024:
            await event.client.send_file(event.chat_id, zip_path, force_document=True)
        else:
            await event.reply("⚠️ Archive exceeds Telegram file size limit.")
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)


async def handle_delete(event: Any, admin_id: Optional[int]):
    """Admin: Request deletion confirmation for a file strictly within Media folder."""
    if not await is_admin(event, admin_id):
        return
    args = event.text.split(maxsplit=1)
    if len(args) < 2:
        await event.reply("❌ Usage: `/delete <relative_file_path>`")
        return
    target = resolve_safe_media_path(args[1], MEDIA_DIR)
    if not target or not os.path.exists(target) or os.path.isdir(target):
        await event.reply("❌ Invalid path or file not found within Media directory.")
        return
    rel = os.path.relpath(target, MEDIA_DIR)
    await event.reply(f"⚠️ Confirm deletion of `{rel}`?\nRun: `/confirm_delete {rel}`")


async def handle_confirm_delete(event: Any, admin_id: Optional[int]):
    """Admin: Confirm deletion of a file strictly within Media folder."""
    if not await is_admin(event, admin_id):
        return
    args = event.text.split(maxsplit=1)
    if len(args) < 2:
        return
    target = resolve_safe_media_path(args[1], MEDIA_DIR)
    if not target or not os.path.exists(target) or os.path.isdir(target):
        await event.reply("❌ Invalid path or file not found.")
        return
    os.remove(target)
    await event.reply(f"✅ File deleted: `{os.path.basename(target)}`")


async def handle_all(event: Any, admin_id: Optional[int]):
    """Admin: Send all media files in batches."""
    if not await is_admin(event, admin_id):
        return
    media_files = []
    for root, dirs, files in os.walk(MEDIA_DIR):
        for f in files:
            media_files.append(os.path.join(root, f))
    if not media_files:
        await event.reply("📭 No media files found.")
        return
    await event.reply(f"📤 Sending {len(media_files)} files in batches...")
    for fp in media_files[:50]:
        try:
            await event.client.send_file(event.chat_id, fp)
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error sending file {fp}: {e}")


async def handle_zip(event: Any, admin_id: Optional[int]):
    """Admin: Create full ZIP archive of Media directory."""
    if not await is_admin(event, admin_id):
        return
    zip_path = os.path.join(BACKUPS_DIR, f"full_media_{int(time.time())}.zip")
    await event.reply("📦 Creating full Media backup ZIP...")
    
    def make_full_zip():
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(MEDIA_DIR):
                for f in files:
                    fp = os.path.join(root, f)
                    arc = os.path.relpath(fp, MEDIA_DIR)
                    zipf.write(fp, arc)
                    
    await asyncio.to_thread(make_full_zip)
    try:
        if os.path.exists(zip_path) and os.path.getsize(zip_path) <= 1900 * 1024 * 1024:
            await event.client.send_file(event.chat_id, zip_path, force_document=True)
        else:
            await event.reply("⚠️ Full archive exceeds Telegram file size limit.")
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)


# --- ADMIN LOGS & SYSTEM COMMANDS ---

async def handle_logs(event: Any, admin_id: Optional[int], state: dict):
    """Admin: View recent logs with secret masking."""
    if not await is_admin(event, admin_id):
        return
    args = event.text.split()
    lines_count = 50
    search_query = None
    if len(args) > 1:
        if args[1].isdigit():
            lines_count = min(int(args[1]), 500)
        else:
            search_query = args[1]
    if len(args) > 2 and args[2].isdigit():
        lines_count = min(int(args[2]), 500)
        
    if not os.path.exists(LOG_FILE):
        await event.reply("📭 No log file found.")
        return
        
    with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        all_lines = f.readlines()
        
    if search_query:
        filtered = [l for l in all_lines if search_query.lower() in l.lower()]
    else:
        filtered = all_lines
        
    sample = "".join(filtered[-lines_count:])
    if not sample:
        await event.reply("📭 No matching log entries.")
        return
        
    if len(sample) > 3800:
        sample = sample[-3800:]
    await event.reply(f"📋 **Bot Logs (Last {lines_count} lines):**\n```\n{sample}\n```", parse_mode='markdown')


async def handle_clearlogs(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Rotate and clear current log file."""
    if not await is_admin(event, admin_id):
        return
    if os.path.exists(LOG_FILE):
        backup_name = os.path.join(BACKUPS_DIR, f"bot_log_backup_{int(time.time())}.log")
        shutil.copy2(LOG_FILE, backup_name)
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(f"--- Log reset at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} ---\n")
        await event.reply(f"✅ Logs cleared. Backup saved to `{os.path.basename(backup_name)}`.")
    else:
        await event.reply("📭 No log file found to clear.")


async def handle_download_logs(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Download complete log file."""
    if not await is_admin(event, admin_id):
        return
    if not os.path.exists(LOG_FILE):
        await event.reply("📭 No log file found.")
        return
    await event.client.send_file(event.chat_id, LOG_FILE, force_document=True)


async def handle_loglevel(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Change runtime logging level."""
    if not await is_admin(event, admin_id):
        return
    args = event.text.split()
    if len(args) < 2 or args[1].upper() not in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
        await event.reply("❌ Usage: `/loglevel <DEBUG|INFO|WARNING|ERROR>`")
        return
    level = getattr(logging, args[1].upper())
    logger.setLevel(level)
    console_handler.setLevel(level)
    file_handler.setLevel(level)
    await event.reply(f"✅ Log level updated to `{args[1].upper()}`.")


async def handle_status(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Display system overview and statistics."""
    if not await is_admin(event, admin_id):
        return
    photos = videos = docs = total_size = 0
    for root, dirs, files in os.walk(MEDIA_DIR):
        for f in files:
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
                total_size += sz
                ext = os.path.splitext(f)[1].lower()
                if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                    photos += 1
                elif ext in ['.mp4', '.avi', '.mkv']:
                    videos += 1
                else:
                    docs += 1
            except Exception:
                pass
                
    q_stats = await MEDIA_QUEUE.get_queue_stats() if MEDIA_QUEUE else {}
    users_count = len(state.get("user_sessions", {}))
    
    await event.reply(
        f"📊 **System Status & Statistics:**\n\n"
        f"📁 **Media Storage:**\n"
        f"• Photos: {photos} | Videos: {videos} | Documents: {docs}\n"
        f"• Total Size: {total_size / (1024*1024):.2f} MB\n\n"
        f"👥 **Users & Sessions:**\n"
        f"• Active Connected Sessions: {users_count}\n"
        f"• Global Forwarding: {'✅ ON' if state.get('global_forwarding_enabled', True) else '❌ OFF'}\n\n"
        f"📥 **Queue State:**\n"
        f"• Pending: {q_stats.get('pending_count', 0)}\n"
        f"• Processed: {q_stats.get('total_processed', 0)}"
    )


async def handle_ping(event: Any):
    """Admin: Measure network ping latency."""
    start = time.perf_counter()
    msg = await event.reply("🏓 Pong...")
    elapsed = (time.perf_counter() - start) * 1000
    await msg.edit(f"🏓 **Pong!** Latency: `{elapsed:.2f} ms`")


async def handle_queue_stats_all(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Show queue statistics across all users."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    stats = await MEDIA_QUEUE.get_queue_stats() if MEDIA_QUEUE else {}
    msg = (
        f"📊 **Global Queue Statistics (Admin):**\n\n"
        f"• Pending: {stats.get('pending_count', 0)}\n"
        f"• Processing: {stats.get('processing_count', 0)}\n"
        f"• Processed: {stats.get('processed_count', 0)}\n"
        f"• Failed: {stats.get('failed_count', 0)}\n"
        f"• Total Completed: {stats.get('total_processed', 0)}\n\n"
    )
    if stats.get('pending_by_user'):
        msg += "**Pending by User:**\n"
        for uid, count in stats['pending_by_user'].items():
            uname = state.get("user_sessions", {}).get(str(uid), {}).get("username") or uid
            msg += f"• `{uname}`: {count}\n"
    await event.reply(msg)


async def handle_process_queue_all(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Trigger global queue processing for all users."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    await event.reply("🔄 Processing all pending items across all users...")
    processed, failed = await process_queue_items(user_id=None, limit=20, state=state)
    await event.reply(f"✅ Global queue processing completed: {processed} processed, {failed} failed.")


async def handle_checkmissed_all(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Scan missed media for all connected users."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    user_sessions = state.get("user_sessions", {})
    if not user_sessions:
        await event.reply("📭 No logged-in users.")
        return
    await event.reply(f"🔄 Scanning missed media for {len(user_sessions)} users...")
    total_found = 0
    for uid_str in user_sessions.keys():
        uid = int(uid_str)
        client = ACTIVE_USER_CLIENTS.get(uid_str)
        if client and client.is_connected():
            found = await check_missed_media(uid, client, state)
            total_found += found
            await asyncio.sleep(1)
    await event.reply(f"✅ Check complete: Found {total_found} total missed media items.")


async def handle_checkmissed_enhanced(event: Any, admin_id: Optional[int], state: dict):
    """Dispatcher for /checkmissed [id|all]."""
    args = event.text.split()
    if not await is_admin(event, admin_id):
        await handle_checkmissed(event, admin_id, state)
        return
    if len(args) > 1:
        if args[1].lower() == 'all':
            await handle_checkmissed_all(event, admin_id, state)
        elif args[1].isdigit():
            target_uid = int(args[1])
            client = ACTIVE_USER_CLIENTS.get(str(target_uid))
            if client and client.is_connected():
                found = await check_missed_media(target_uid, client, state)
                await event.reply(f"✅ Check complete for user `{target_uid}`: Found {found} missed items.")
            else:
                await event.reply(f"❌ User `{target_uid}` is not currently connected.")
        else:
            await event.reply("❌ Usage: `/checkmissed <user_id|all>`")
    else:
        await handle_checkmissed(event, admin_id, state)


# --- ADMIN USER MANAGEMENT COMMANDS ---

async def handle_users(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Display all users categorized by Administrator, Authorized, and Inactive."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
        
    effective_admin_id = parse_admin_id(admin_id) or CONFIGURED_ADMIN_ID
    user_sessions = state.get("user_sessions", {})
    
    # Administrator details
    admin_session = user_sessions.get(str(effective_admin_id))
    admin_username = admin_session.get('username') if admin_session else "Unknown"
    admin_status = "🟢 Active Session" if admin_session else "⚪ No active user session"
    
    msg = "👑 **Administrator (Bot Owner):**\n"
    msg += f"• User ID: `{effective_admin_id}` (@{admin_username})\n"
    msg += f"• Session: {admin_status}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    env_users = parse_allowed_users(os.getenv("ALLOWED_USERS", ""))
    db_users = await MEDIA_QUEUE.get_all_users_db() if MEDIA_QUEUE else []
    
    active_db_users = [u for u in db_users if u['is_active'] and u['user_id'] != effective_admin_id]
    inactive_db_users = [u for u in db_users if not u['is_active'] and u['user_id'] != effective_admin_id]
    
    all_auth = set(env_users)
    for u in active_db_users:
        all_auth.add(u['user_id'])
    for u_str in user_sessions.keys():
        try:
            all_auth.add(int(u_str))
        except Exception:
            pass
    all_auth.discard(effective_admin_id)
    
    msg += f"✅ **Authorized Users ({len(all_auth)}):**\n"
    if not all_auth:
        msg += "• _No other authorized users configured_\n\n"
    else:
        for uid in sorted(all_auth):
            sess = user_session_info = user_sessions.get(str(uid))
            uname = sess.get('username') if sess else "Unknown"
            status_tag = "🟢 Logged In" if sess else "⚪ Disconnected"
            msg += f"• `{uid}` (@{uname}) - {status_tag}\n"
        msg += "\n"
        
    if inactive_db_users:
        msg += f"⛔ **Inactive / Disallowed Users ({len(inactive_db_users)}):**\n"
        for u in inactive_db_users:
            msg += f"• `{u['user_id']}` (@{u.get('username') or 'No username'})\n"
        msg += "\n"
        
    await event.reply(msg, parse_mode='markdown')


async def handle_allow_user(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Authorize a user in the database."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    args = event.text.split()
    if len(args) < 2 or not args[1].strip().isdigit():
        await event.reply("❌ Usage: `/allow_user <user_id>`")
        return
    target_uid = int(args[1].strip())
    if is_admin_id(target_uid, admin_id):
        await event.reply(f"ℹ️ User `{target_uid}` is the Administrator (always authorized).")
        return
        
    username = "Unknown"
    try:
        ent = await event.client.get_entity(target_uid)
        username = getattr(ent, 'username', None) or getattr(ent, 'first_name', 'Unknown')
    except Exception:
        pass
        
    if MEDIA_QUEUE:
        await MEDIA_QUEUE.add_allowed_user(target_uid, username, event.sender_id)
        await event.reply(f"✅ User `{target_uid}` (@{username}) has been authorized.")
    else:
        await event.reply("❌ Database not initialized.")


async def handle_disallow_user(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Deactivate a user's authorization."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    args = event.text.split()
    if len(args) < 2 or not args[1].strip().isdigit():
        await event.reply("❌ Usage: `/disallow_user <user_id>`")
        return
    target_uid = int(args[1].strip())
    if is_admin_id(target_uid, admin_id):
        await event.reply("❌ The bot administrator cannot be disallowed.")
        return
    if MEDIA_QUEUE:
        await MEDIA_QUEUE.remove_allowed_user(target_uid)
        await event.reply(f"✅ User `{target_uid}` has been disallowed.")
    else:
        await event.reply("❌ Database not initialized.")


async def handle_allowed_users(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Display allowed users overview."""
    await handle_users(event, admin_id, state)


async def handle_help_full(event: Any, admin_id: Optional[int], state: dict):
    """Admin: Send complete documentation as a markdown file."""
    if not await is_admin(event, admin_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    doc_path = os.path.join(BASE_DIR, "README.md")
    if os.path.exists(doc_path):
        await event.client.send_file(event.chat_id, doc_path, caption="📘 Complete Bot Documentation")
    else:
        await event.reply("📭 Documentation file not found.")


async def handle_help(event: Any, admin_id: Optional[int], state: dict):
    """Show help documentation according to user role."""
    is_adm = await is_admin(event, admin_id)
    if is_adm:
        msg = (
            "👑 **Self-Destructing Bot - Administrator Help**\n\n"
            "**User Access Control:**\n"
            "• `/users` - List all users by status\n"
            "• `/allow_user <id>` - Authorize a user\n"
            "• `/disallow_user <id>` - Disallow a user\n\n"
            "**Channel Management:**\n"
            "• `/setgchannel <id>` - Set bot's global channel\n"
            "• `/currentchannel` - Show global channel config\n"
            "• `/testchannel` - Test global channel posting\n"
            "• `/globalforward <enable|disable|status>` - Toggle forwarding\n\n"
            "**File Management:**\n"
            "• `/files` - List files in Media folder\n"
            "• `/check` - Check recent files (24h)\n"
            "• `/download <path>` - Download a file\n"
            "• `/download_zip <folder>` - Download folder as ZIP\n"
            "• `/zip` - Download entire Media backup\n"
            "• `/delete <path>` - Delete a file\n\n"
            "**Queue & Diagnostics:**\n"
            "• `/queue_stats_all` - View all queues\n"
            "• `/process_queue_all` - Process all queues\n"
            "• `/checkmissed_all` - Scan all users' missed media\n"
            "• `/status` - Storage and system stats\n"
            "• `/logs` - View recent logs\n"
            "• `/loglevel <level>` - Change log level\n"
            "• `/help_full` - Download complete documentation"
        )
    else:
        msg = (
            "🤖 **Self-Destructing Bot - User Guide**\n\n"
            "**Account & Channel:**\n"
            "• `/login` - Connect your Telegram account\n"
            "• `/logout` - Disconnect your account\n"
            "• `/mystatus` - View your connection status\n"
            "• `/setmychannel <id>` - Set your personal channel\n"
            "• `/mychannel` - View personal channel\n"
            "• `/mychanneltest` - Test personal channel access\n\n"
            "**Media Recovery & Queue:**\n"
            "• `/checkmissed` - Scan last 48h for missed media\n"
            "• `/queue_stats` - View your pending media queue\n"
            "• `/process_queue` - Process your queue immediately\n"
            "• `/savetips` - Tips for self-destructing media"
        )
    await event.reply(msg, parse_mode='markdown')


# =====================================================================
# MAIN BOT LIFECYCLE & BACKGROUND WORKERS
# =====================================================================

async def main():
    """Main application lifecycle and entrypoint."""
    global MEDIA_QUEUE, CONFIGURED_ADMIN_ID, BOT_CLIENT
    
    # Initialize SQLite database and queue
    MEDIA_QUEUE = MediaQueue()
    
    # Load configuration
    api_id, api_hash, admin_id, bot_token, session_name, channel_id = await load_config()
    
    if not bot_token:
        console.print("[red]❌ Configuration Error: BOT_TOKEN is required in .env![/red]")
        logger.critical("BOT_TOKEN missing. Shutting down.")
        return
        
    if not api_id or not api_hash:
        console.print("[red]❌ Configuration Error: API_ID and API_HASH are required in .env![/red]")
        logger.critical("API_ID or API_HASH missing. Shutting down.")
        return
        
    if admin_id is None:
        console.print("[red]❌ Configuration Error: ADMIN_ID is required and must be a positive integer![/red]")
        logger.critical("ADMIN_ID missing or invalid. Shutting down.")
        return
        
    CONFIGURED_ADMIN_ID = admin_id
    state = await load_state()
    
    console.print(f"[green]✓ Authorization initialized. Administrator: {admin_id}[/green]")
    logger.info(f"Bot initialized. Administrator configured: {admin_id}")
    
    # Initialize bot client
    client = TelegramClient(session_name, api_id, api_hash)
    client.channel_id = channel_id
    client.admin_id = admin_id
    BOT_CLIENT = client
    
    # Register command handlers
    client.on(events.NewMessage(pattern=r'^/start$'))(lambda e: handle_start(e, admin_id))
    client.on(events.NewMessage(pattern=r'^/help$'))(lambda e: handle_help(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/ping$'))(handle_ping)
    client.on(events.NewMessage(pattern=r'^/status$'))(lambda e: handle_status(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/files$'))(lambda e: handle_files(e, admin_id))
    client.on(events.NewMessage(pattern=r'^/check$'))(lambda e: handle_check(e, admin_id))
    client.on(events.NewMessage(pattern=r'^/all$'))(lambda e: handle_all(e, admin_id))
    client.on(events.NewMessage(pattern=r'^/zip$'))(lambda e: handle_zip(e, admin_id))
    
    client.on(events.NewMessage(pattern=r'/download\s+(.+)?'))(lambda e: handle_download(e, admin_id))
    client.on(events.NewMessage(pattern=r'/download_zip\s+(.+)?'))(lambda e: handle_download_zip(e, admin_id))
    client.on(events.NewMessage(pattern=r'/delete\s+(.+)?'))(lambda e: handle_delete(e, admin_id))
    client.on(events.NewMessage(pattern=r'/confirm_delete\s+(.+)?'))(lambda e: handle_confirm_delete(e, admin_id))
    
    client.on(events.NewMessage(pattern=r'/globalforward(?:\s+\S+)?'))(lambda e: handle_globalforward(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'/globleforward(?:\s+\S+)?'))(lambda e: handle_globalforward(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'/setgchannel\s+(.+)?'))(lambda e: handle_setgchannel(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'/setmychannel\s+(.+)?'))(lambda e: handle_setmychannel(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/currentchannel$'))(lambda e: handle_currentchannel(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/testchannel$'))(lambda e: handle_testchannel(e, admin_id))
    client.on(events.NewMessage(pattern=r'^/mychannel$'))(lambda e: handle_mychannel(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/mychanneltest$'))(lambda e: handle_mychanneltest(e, admin_id, state))
    
    client.on(events.NewMessage(pattern=r'/logs(?:\s+\S+)*'))(lambda e: handle_logs(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/clearlogs$'))(lambda e: handle_clearlogs(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/download_logs$'))(lambda e: handle_download_logs(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'/loglevel\s+\S+'))(lambda e: handle_loglevel(e, admin_id, state))
    
    client.on(events.NewMessage(pattern=r'^/login$'))(lambda e: handle_login(e, admin_id, client, state))
    client.on(events.NewMessage(pattern=r'^/cancel$'))(lambda e: handle_cancel(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/logout$'))(lambda e: handle_logout(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/mystatus$'))(lambda e: handle_mystatus(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/savetips$'))(handle_savetips)
    client.on(events.NewMessage(pattern=r'^/help_full$'))(lambda e: handle_help_full(e, admin_id, state))
    
    client.on(events.NewMessage(pattern=r'^/queue_stats(?:\s|$)'))(lambda e: handle_queue_stats(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/process_queue(?:\s|$)'))(lambda e: handle_process_queue(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/queue_stats_all(?:\s|$)'))(lambda e: handle_queue_stats_all(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/process_queue_all(?:\s|$)'))(lambda e: handle_process_queue_all(e, admin_id, state))
    
    client.on(events.NewMessage(pattern=r'^/checkmissed(?:\s+\S+)?$'))(lambda e: handle_checkmissed_enhanced(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/checkmissed_all$'))(lambda e: handle_checkmissed_all(e, admin_id, state))
    
    client.on(events.NewMessage(pattern=r'^/users$'))(lambda e: handle_users(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/allow_user\s+\d+$'))(lambda e: handle_allow_user(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/disallow_user\s+\d+$'))(lambda e: handle_disallow_user(e, admin_id, state))
    client.on(events.NewMessage(pattern=r'^/allowed_users$'))(lambda e: handle_allowed_users(e, admin_id, state))
    
    # Plain text messages in private chat for login flow steps
    @client.on(events.NewMessage(func=lambda e: e.is_private and e.text and not e.text.startswith('/')))
    async def plain_message_handler(event):
        uid = event.sender_id
        user_data = state.get("login_sessions", {}).get(str(uid))
        if not user_data:
            return
        step = user_data.get("step")
        if step == "api_id":
            await handle_api_id(event, admin_id, state)
        elif step == "api_hash":
            await handle_api_hash(event, admin_id, state)
        elif step == "phone":
            await handle_phone(event, admin_id, state, client)
        elif step == "code":
            await handle_code(event, admin_id, state)
        elif step == "2fa":
            await handle_2fa(event, admin_id, state)
            
    # Universal handler for messages sent directly to bot token
    @client.on(events.NewMessage(func=lambda e: e.is_private))
    async def universal_media_handler(event):
        if event.text and event.text.startswith("/"):
            return
        if str(event.sender_id) in state.get("user_sessions", {}):
            return  # Handled by user client
            
        ttl = extract_ttl(event.message)
        if ttl and ttl > 0:
            await event.reply(
                "⚠️ **Self-destructing media detected**\n\n"
                "Telegram does not permit bots to download TTL media directly.\n"
                "To capture self-destructing media:\n"
                "1. Connect your account via `/login`\n"
                "2. Configure your channel via `/setmychannel`\n"
                "3. Media sent to your account will automatically be saved."
            )
            
    try:
        # Connect Bot Client
        await cast(Any, client.start(bot_token=bot_token))
        me = await client.get_me()
        console.print(f"[green]✓ Bot @{me.username} (ID: {me.id}) started successfully![/green]")
        logger.info(f"Bot @{me.username} connected.")
        
        # Restore active user sessions
        for uid_str, udata in state.get("user_sessions", {}).items():
            try:
                uid = int(uid_str)
                sfile = get_user_session_file(uid)
                if sfile and os.path.exists(sfile):
                    async with aiofiles.open(sfile, mode="r") as sf:
                        sstring = await sf.read()
                    if sstring:
                        uclient = TelegramClient(StringSession(sstring), udata["api_id"], udata["api_hash"])
                        await uclient.connect()
                        if await uclient.is_user_authorized():
                            await setup_user_client_handlers(uclient, uid, client, state)
                            ACTIVE_USER_CLIENTS[uid_str] = uclient
                            await cast(Any, uclient.start())
                            console.print(f"[green]✓ Restored user session: {udata.get('username') or uid}[/green]")
                        else:
                            await uclient.disconnect()
            except Exception as e:
                logger.error(f"Error restoring user session {uid_str}: {e}")
                
        # Background worker tasks
        queue_interval = int(os.getenv("QUEUE_INTERVAL", "300"))
        autocheck_interval = int(os.getenv("AUTOCHECK_INTERVAL", "3600"))
        
        async def periodic_queue_processor():
            while True:
                try:
                    await asyncio.sleep(queue_interval)
                    if client.is_connected():
                        await process_queued_media()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Periodic queue worker error: {e}")
                    await asyncio.sleep(10)
                    
        async def periodic_auto_check_missed():
            while True:
                try:
                    await asyncio.sleep(autocheck_interval)
                    user_sessions = state.get("user_sessions", {})
                    for uid_str in list(user_sessions.keys()):
                        uclient = ACTIVE_USER_CLIENTS.get(uid_str)
                        if uclient and uclient.is_connected():
                            await check_missed_media(int(uid_str), uclient, state)
                            await asyncio.sleep(15)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Periodic missed-media worker error: {e}")
                    await asyncio.sleep(10)
                    
        worker1 = asyncio.create_task(periodic_queue_processor())
        worker2 = asyncio.create_task(periodic_auto_check_missed())
        
        # Initial run of queue processing
        asyncio.create_task(process_queued_media())
        
        console.print("[green]✓ Background workers active. Bot is ready.[/green]")
        await client.run_until_disconnected()
        
    finally:
        # Graceful shutdown: cancel workers and disconnect all user clients
        for task in [worker1, worker2] if 'worker1' in locals() else []:
            task.cancel()
        for uid_str, uclient in list(ACTIVE_USER_CLIENTS.items()):
            try:
                await uclient.disconnect()
            except Exception:
                pass
        await client.disconnect()
        logger.info("Bot cleanly disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user.[/yellow]")
    except Exception as e:
        console.print(f"[red]Unhandled exception: {e}[/red]")
        logger.critical(f"Bot crashed: {e}", exc_info=True)