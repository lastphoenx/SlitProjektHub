"""Prüfe short_code vs key"""
import sys
sys.path.insert(0, ".")

from sqlmodel import select
from src.m03_db import get_session, Project

with get_session() as ses:
    projects = ses.exec(select(Project).where(Project.is_deleted == False)).all()
    
    print("=" * 80)
    print("Projekte: key vs short_code")
    print("=" * 80)
    
    for p in projects:
        print(f"\nTitel: {p.title[:80]}...")
        print(f"  key: {p.key}")
        print(f"  short_code: {p.short_code}")
        print(f"  short_title: {p.short_title}")
