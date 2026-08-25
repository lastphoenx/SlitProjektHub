#!/usr/bin/env python3
"""Login-Benutzernamen ändern (Passwort/TOTP bleiben).

    python scripts/maintenance/rename_login.py
    python scripts/maintenance/rename_login.py admin
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m03_db import init_db  # noqa: E402
from src.m14_auth import list_login_usernames, rename_login_user  # noqa: E402


def main() -> int:
    init_db()
    users = list_login_usernames()
    if not users:
        print("Keine Login-Benutzer. Zuerst: python scripts/maintenance/setup_admin.py")
        return 1
    print("Aktuelle Login-Benutzer:")
    for u in users:
        print(f"  {u['username']}\t{u.get('app_role_key') or 'unassigned'}")

    old = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not old:
        default = users[0]["username"]
        old = input(f"Alter Login [{default}]: ").strip() or default

    new = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not new:
        new = input("Neuer Login: ").strip()
    if not new:
        print("Neuer Name fehlt.")
        return 1

    try:
        new_name = rename_login_user(old, new)
    except ValueError as exc:
        print(str(exc))
        return 1
    print(f"{old} → {new_name}")
    print("Nächster Login mit dem neuen Namen, gleiches Passwort und TOTP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
