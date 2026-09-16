#!/usr/bin/env python3
"""Weiteren Login-Benutzer anlegen — nicht eine Projektrolle.

Projektrollen (CEO, …) leben in SQLite (`role`).
Login + App-Rolle: Tabellen `app_user` / `app_role`.

    python scripts/maintenance/create_user.py
    python scripts/maintenance/create_user.py --admin
    python scripts/maintenance/create_user.py --role projektleiter_intern
    python scripts/maintenance/create_user.py --list
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m03_db import init_db  # noqa: E402
from src.m14_auth import (  # noqa: E402
    create_user,
    is_setup_required,
    list_login_usernames,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin", action="store_true", help="App-Rolle super_user")
    parser.add_argument("--role", default="", help="App-Rolle (sonst unassigned)")
    parser.add_argument("--list", action="store_true", help="Login-Benutzer auflisten")
    args = parser.parse_args()

    init_db()

    if args.list:
        users = list_login_usernames()
        if not users:
            print("Keine Login-Benutzer. Zuerst: python scripts/maintenance/setup_admin.py")
            return 1
        for u in users:
            role = u.get("app_role_key") or "unassigned"
            print(f"{u['username']}\t{role}")
        return 0

    if is_setup_required():
        print("Noch kein Admin. Zuerst: python scripts/maintenance/setup_admin.py")
        return 1

    username = input("Login-Benutzername: ").strip()
    pw = getpass.getpass("Passwort (min. 10 Zeichen): ")
    pw2 = getpass.getpass("Passwort wiederholen: ")
    if pw != pw2:
        print("Passwörter stimmen nicht überein.")
        return 1
    if len(pw) < 10:
        print("Passwort zu kurz.")
        return 1
    try:
        totp = create_user(
            username,
            pw,
            is_admin=args.admin,
            app_role_key=(args.role.strip() or None),
        )
    except ValueError as exc:
        print(str(exc))
        return 1
    kind = "Admin" if args.admin else "Login-Benutzer"
    print(f"{kind} '{username}' angelegt.")
    print(f"TOTP-Secret (Authenticator, falls 2FA an): {totp}")
    print("Das ist kein Projektrolle-Eintrag. Rollen weiter unter Stammdaten.")
    print("config/auth.yaml ist gitignored — nicht kopieren/committen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
