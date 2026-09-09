"""
Migration: Legacy Ollama-Modelle (qwen3:32b, qwen3.6:27b) → qwen3.8:27b

Betrifft:
  - evaluation_project_config (vorgaben_ki_model, bewertung_ki_model)
  - project_idea.ai_model (nur Ollama-Provider in gespeicherten Jobs — ai_model allein)
  - visual_lab_run.llm_model (falls Tabelle existiert)
  - config/user_settings.yaml (provider ollama + legacy model)

Ausführen (Repo-Root oder Server /opt/slitprojekthub):
  .venv/bin/python scripts/migrations/migrate_ollama_qwen38.py
  .venv/bin/python scripts/migrations/migrate_ollama_qwen38.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m01_config import USER_SETTINGS_PATH, load_user_settings, save_user_settings  # noqa: E402
from src.m02_paths import db_file  # noqa: E402
from src.m08_llm import OLLAMA_DEFAULT_MODEL, OLLAMA_LEGACY_MODEL_MAP, normalize_ollama_model  # noqa: E402

LEGACY = set(OLLAMA_LEGACY_MODEL_MAP.keys())


def _migrate_column(conn: sqlite3.Connection, table: str, column: str, dry_run: bool) -> int:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    if column not in cols:
        return 0
    placeholders = ",".join("?" for _ in LEGACY)
    cur.execute(
        f"SELECT rowid, {column} FROM {table} WHERE {column} IN ({placeholders})",
        tuple(LEGACY),
    )
    rows = cur.fetchall()
    if not dry_run and rows:
        for rowid, old in rows:
            new = normalize_ollama_model(old)
            cur.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (new, rowid))
    return len(rows)


def migrate_db(dry_run: bool) -> dict[str, int]:
    path = db_file()
    if not path.is_file():
        print(f"DB nicht gefunden: {path}")
        return {}
    conn = sqlite3.connect(path)
    stats: dict[str, int] = {}
    try:
        for table, column in (
            ("evaluation_project_config", "vorgaben_ki_model"),
            ("evaluation_project_config", "bewertung_ki_model"),
            ("project_idea", "ai_model"),
            ("visual_lab_run", "llm_model"),
        ):
            key = f"{table}.{column}"
            n = _migrate_column(conn, table, column, dry_run)
            if n:
                stats[key] = n
                print(f"{'[dry-run] ' if dry_run else ''}{key}: {n} Zeile(n) → {OLLAMA_DEFAULT_MODEL}")
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return stats


def migrate_user_settings(dry_run: bool) -> bool:
    if not USER_SETTINGS_PATH.is_file():
        print(f"user_settings.yaml nicht gefunden: {USER_SETTINGS_PATH}")
        return False
    settings = load_user_settings()
    provider = (settings.get("provider") or "").strip().lower()
    model = (settings.get("model") or "").strip()
    if provider != "ollama" or model not in LEGACY:
        return False
    new_model = normalize_ollama_model(model)
    print(
        f"{'[dry-run] ' if dry_run else ''}user_settings.yaml: "
        f"model {model!r} → {new_model!r}"
    )
    if not dry_run:
        settings["model"] = new_model
        save_user_settings(settings)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Ollama-Modelle auf qwen3.8:27b migrieren")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts schreiben")
    args = parser.parse_args()

    print(f"Zielmodell: {OLLAMA_DEFAULT_MODEL}")
    print(f"Legacy: {', '.join(sorted(LEGACY))}\n")

    stats = migrate_db(args.dry_run)
    yaml_changed = migrate_user_settings(args.dry_run)

    total = sum(stats.values()) + (1 if yaml_changed else 0)
    if total == 0:
        print("\nKeine Legacy-Einträge gefunden — nichts zu tun.")
    else:
        print(f"\n{'Dry-run: ' if args.dry_run else ''}Fertig ({total} Änderung(en)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
