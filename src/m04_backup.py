"""
04_backup.py
Zippt das Projektverzeichnis in backups/.
"""
from __future__ import annotations
import shutil, datetime
from pathlib import Path
from .m01_config import get_settings

def create_backup() -> Path:
    S = get_settings()
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = S.backups_dir / f"backup-{ts}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    # make_archive liefert den Pfad zurück; kein Context-Manager nötig
    archive_base = str(out).removesuffix(".zip")
    shutil.make_archive(archive_base, "zip", root_dir=S.base_dir)
    return out

if __name__ == "__main__":
    print(f"Backup created: {create_backup()}")
