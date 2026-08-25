#!/usr/bin/env python3
"""YAML-Login-User nach app_user migrieren (idempotent).

    python scripts/maintenance/migrate_auth_yaml_to_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m03_db import init_db  # noqa: E402
from src.m14_auth import list_login_usernames, migrate_yaml_users_to_db  # noqa: E402


def main() -> int:
    init_db()
    pending = migrate_yaml_users_to_db()
    print("Login-Benutzer in der DB:")
    for u in list_login_usernames():
        print(f"  {u['username']}\t{u['app_role_key'] or 'unassigned'}")
    if pending:
        print("Bitte App-Rolle zuordnen (nicht automatisch Projektleiter intern):")
        for name in pending:
            print(f"  python scripts/maintenance/set_user_role.py {name} projektleiter_intern")
        print("Rollen: super_user | projektleiter_intern | product_owner | auftraggeber")
    else:
        print("Keine offenen Zuordnungen (oder YAML war schon leer).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
