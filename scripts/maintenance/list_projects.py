#!/usr/bin/env python
"""Aktive Projekte inkl. project_key (für CLI-Argumente).

  cd /opt/slitprojekthub
  .venv/bin/python scripts/maintenance/list_projects.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlmodel import select

from src.m01_config import get_settings
from src.m03_db import get_session, Project


def main() -> int:
    get_settings()
    with get_session() as session:
        projects = session.exec(select(Project).where(Project.is_deleted == False)).all()
        print(f"Projekte insgesamt: {len(projects)}\n")
        for p in projects:
            print(f"- {p.title}")
            print(f"  Key: {p.key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
