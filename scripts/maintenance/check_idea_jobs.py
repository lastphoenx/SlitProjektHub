#!/usr/bin/env python
"""CLI-Beweis: läuft wirklich mehr als ein KI-Job? Was hat Ollama geladen?

Auf dem Server:
  cd /opt/slitprojekthub
  .venv/bin/python scripts/maintenance/check_idea_jobs.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m01_config import get_settings

get_settings()  # lädt .env, bevor Ollama-URL gelesen wird

from src.m08_llm import _ollama_root_url, ollama_runtime_status


def _db_path() -> Path:
    s = get_settings()
    url = (s.db_url or "").replace("sqlite:///", "", 1)
    p = Path(url)
    if not p.is_absolute():
        p = ROOT / p
    return p


def main() -> int:
    print("=== Ollama /api/ps (im Speicher / evtl. beschäftigt) ===")
    root = _ollama_root_url()
    print(f"URL aus .env: {root or '(nicht gesetzt)'}")
    if not root:
        print("Ohne OLLAMA_BASE_URL in .env ist Ollama für App und CLI unsichtbar.")
        print("curl 127.0.0.1:11434 schlägt dann fehl, wenn Ollama auf einem anderen Host läuft.")
    st = ollama_runtime_status(None)
    print(f"erreichbar: {st.get('ok')}")
    loaded = st.get("loaded") or []
    if loaded:
        print("geladen:", ", ".join(loaded))
        print("Hinweis: geladen ≠ generiert. Ollama behält Modelle oft idle im RAM.")
        print("Es kann trotzdem nur EIN Generate gleichzeitig laufen — der Rest queued.")
    else:
        print("kein Modell im Speicher (frei oder nicht erreichbar)")
    print("Meldung:", st.get("message"))

    db = _db_path()
    print("\n=== SQLite KI-Jobs (project_idea.ki_job_json) ===")
    print(f"DB: {db}")
    if not db.exists():
        print("DB nicht gefunden.")
        return 1
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(project_idea)")}
    if "ki_job_json" not in cols:
        print("Spalte ki_job_json fehlt — Backend nach 5362db6 neu starten.")
        return 1
    rows = con.execute(
        "SELECT id, title, status, ki_job_json FROM project_idea WHERE is_deleted=0"
    ).fetchall()
    active = []
    errors = []
    idle_n = 0
    for r in rows:
        raw = r["ki_job_json"]
        if not raw:
            idle_n += 1
            continue
        try:
            job = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  idea={r['id']} ki_job_json ungültig")
            continue
        status = job.get("status") or "idle"
        kind = job.get("kind") or "?"
        model = job.get("model") or ""
        msg = (job.get("error") or job.get("message") or "")[:180]
        line = f"  idea={r['id']} {kind:7} {status:8} model={model}  {msg}"
        if status in ("queued", "running"):
            active.append(line)
        elif status == "error":
            errors.append(line)
        else:
            print(line)
    print(f"Ideen ohne Job: {idle_n}")
    print(f"Aktive Jobs (queued/running): {len(active)}")
    for line in active:
        print(line)
    if len(active) > 1:
        print("WARNUNG: mehr als ein aktiver Job in der DB — UI hat zweimal abgeschickt; Worker arbeitet trotzdem seriell.")
    elif len(active) == 1:
        print("OK: genau ein aktiver Job. Ollama bekommt davon nur diesen einen Generate.")
    else:
        print("Kein aktiver Job in der DB. Gelbe Banner im Browser sind dann nur Client-JS (Klick ohne Reload).")
    if errors:
        print("Letzte Fehler:")
        for line in errors:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
