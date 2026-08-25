#!/usr/bin/env python
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from src.m03_db import init_db
from src.m01_config import get_settings

try:
    S = get_settings()
    print("✅ Config geladen")
    init_db()
    print("✅ Datenbank initialisiert")
    print("✅ Migrations erfolgreich ausgeführt")
    print("\n✨ Alle neuen Features sind bereit!")
except Exception as e:
    print(f"❌ Fehler: {e}")
    import traceback
    traceback.print_exc()
