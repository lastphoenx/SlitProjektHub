#!/usr/bin/env python3
"""IP aus der Blocklist nehmen — ohne laufenden Webserver.

    python scripts/maintenance/unblock_ip.py 203.0.113.10
    python scripts/maintenance/unblock_ip.py --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m03_db import init_db  # noqa: E402
from src.m14_auth import admin_unblock_ip, list_blocked_ips  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="IP-Sperre ohne Web-UI aufheben")
    parser.add_argument("ip", nargs="?", help="IPv4/IPv6 zum Entsperren")
    parser.add_argument("--list", action="store_true", help="Gesperrte IPs anzeigen")
    parser.add_argument("--note", default="cli unblock", help="admin_note")
    args = parser.parse_args()

    init_db()

    if args.list or not args.ip:
        rows = list_blocked_ips()
        if not rows:
            print("Keine aktiven Sperren.")
            return 0
        for r in rows:
            print(f"{r['ip']}\tlevel={r['level']}\tuntil={r['blocked_until']}\t{r['reason']}")
        if not args.ip:
            return 0

    if admin_unblock_ip(args.ip, note=args.note):
        print(f"Entsperrt: {args.ip}")
        return 0
    print(f"IP nicht in der Blocklist: {args.ip}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
