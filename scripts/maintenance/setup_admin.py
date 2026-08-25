#!/usr/bin/env python3
"""Admin-Account anlegen, ohne dass /auth/setup im Netz erreichbar ist.

Aufruf im Repo-Root, bevor nginx/Ports öffentlich sind:

    python scripts/maintenance/setup_admin.py
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m03_db import init_db  # noqa: E402
from src.m14_auth import is_setup_required, setup_admin  # noqa: E402


def main() -> int:
    init_db()
    if not is_setup_required():
        print("Admin existiert bereits (config/auth.yaml). Abbruch.")
        return 1
    username = input("Admin-Benutzername [admin]: ").strip() or "admin"
    pw = getpass.getpass("Passwort (min. 10 Zeichen): ")
    pw2 = getpass.getpass("Passwort wiederholen: ")
    if pw != pw2:
        print("Passwörter stimmen nicht überein.")
        return 1
    if len(pw) < 10:
        print("Passwort zu kurz.")
        return 1
    totp = setup_admin(username, pw)
    print(f"Admin '{username}' angelegt.")
    print(f"TOTP-Secret (Authenticator, falls 2FA an): {totp}")
    print("Weitere Login-Benutzer: python scripts/maintenance/create_user.py")
    print("Projektrollen (Stammdaten) sind keine Accounts.")
    print("config/auth.yaml ist gitignored — nicht kopieren/committen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
