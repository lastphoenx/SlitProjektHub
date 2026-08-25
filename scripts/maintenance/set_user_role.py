#!/usr/bin/env python3
"""App-Rolle eines Login-Users setzen (nicht Stammdaten-Rolle).

    python scripts/maintenance/set_user_role.py anna projektleiter_intern
    python scripts/maintenance/set_user_role.py anna --clear
    python scripts/maintenance/set_user_role.py --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m03_db import init_db  # noqa: E402
from src.m14_auth import list_app_roles, list_login_usernames, set_user_role  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", nargs="?", help="Login-Benutzer")
    parser.add_argument("role", nargs="?", help="App-Rolle (key)")
    parser.add_argument("--clear", action="store_true", help="Rolle entfernen (unassigned)")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    init_db()

    if args.list or not args.username:
        print("App-Rollen:")
        for r in list_app_roles():
            totp = "totp-pflicht" if r["totp_required"] else "totp-inherit"
            print(f"  {r['key']}\t{r['title']}\t{totp}")
        print("Benutzer:")
        for u in list_login_usernames():
            print(f"  {u['username']}\t{u['app_role_key'] or 'unassigned'}")
        if not args.username:
            return 0

    role = None if args.clear else args.role
    if not args.clear and not role:
        print("Rolle angeben oder --clear.")
        return 1
    try:
        set_user_role(args.username, role)
    except ValueError as exc:
        print(str(exc))
        return 1
    print(f"{args.username} → {role or 'unassigned'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
