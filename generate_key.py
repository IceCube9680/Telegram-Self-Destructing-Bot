#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_key.py - Secure Encryption Key Generator for Self-Destructing Bot

Generates production-grade cryptographic keys for:
1. ENCRYPTION_KEY: Fernet urlsafe base64 key (AES-128-CBC + HMAC-SHA256)
2. DB_ENCRYPTION_KEY: 64-character cryptographic hex string
"""

import os
import sys
import base64
import secrets
from pathlib import Path
from cryptography.fernet import Fernet


def generate_fernet_key() -> str:
    """Generate a standard URL-safe base64-encoded 32-byte Fernet key."""
    return Fernet.generate_key().decode('utf-8')


def generate_db_key() -> str:
    """Generate a 64-character cryptographically secure hex string."""
    return secrets.token_hex(32)


def main():
    force = "--force" in sys.argv or "-f" in sys.argv
    write_env = "--write" in sys.argv or "-w" in sys.argv

    fernet_key = generate_fernet_key()
    db_key = generate_db_key()

    print("=" * 60)
    print("  Telegram Self-Destructing Bot - Encryption Key Generator")
    print("=" * 60)
    print()
    print("Generated Keys:")
    print(f"ENCRYPTION_KEY={fernet_key}")
    print(f"DB_ENCRYPTION_KEY={db_key}")
    print()

    env_path = Path(".env")
    if write_env:
        if env_path.exists() and not force:
            content = env_path.read_text(encoding="utf-8")
            has_enc = "ENCRYPTION_KEY=" in content and not "ENCRYPTION_KEY=your_" in content and not "ENCRYPTION_KEY=32_" in content
            has_db = "DB_ENCRYPTION_KEY=" in content and not "DB_ENCRYPTION_KEY=your_" in content and not "DB_ENCRYPTION_KEY=64_" in content
            if has_enc or has_db:
                print("⚠️ .env file already contains active encryption keys.")
                print("   Use --force (-f) if you explicitly want to overwrite them.")
                print("   WARNING: Overwriting keys will make previously encrypted data unreadable!")
                return

        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()

        new_lines = []
        enc_found = False
        db_found = False

        for line in lines:
            if line.startswith("ENCRYPTION_KEY="):
                new_lines.append(f"ENCRYPTION_KEY={fernet_key}")
                enc_found = True
            elif line.startswith("DB_ENCRYPTION_KEY="):
                new_lines.append(f"DB_ENCRYPTION_KEY={db_key}")
                db_found = True
            else:
                new_lines.append(line)

        if not enc_found:
            new_lines.append(f"ENCRYPTION_KEY={fernet_key}")
        if not db_found:
            new_lines.append(f"DB_ENCRYPTION_KEY={db_key}")

        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass
        print("✅ Successfully updated .env file with generated keys.")
    else:
        print("To automatically write to .env, run:")
        print("  python3 generate_key.py --write")
        print("Or copy the variables above into your .env file.")


if __name__ == "__main__":
    main()
