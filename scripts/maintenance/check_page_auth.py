#!/usr/bin/env python3
"""Prüft, dass jede Streamlit-Page require_auth() aufruft."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / "app" / "nav_pages"
LABS = ROOT / "app" / "pages_labs"


def check_dir(folder: Path, required: bool) -> list[str]:
    missing = []
    if not folder.exists():
        return missing
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "require_auth()" not in text:
            missing.append(str(path.relative_to(ROOT)))
    return missing


def main() -> int:
    bad_pages = check_dir(PAGES, True)
    if bad_pages:
        print("Fehlt require_auth() in app/nav_pages/:")
        for p in bad_pages:
            print(f"  {p}")
        return 1
    print("OK: alle app/nav_pages/*.py rufen require_auth() auf.")
    lab_missing = check_dir(LABS, False)
    if lab_missing:
        print("Hinweis (Labs, werden beim Aktivieren injiziert):")
        for p in lab_missing:
            print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
