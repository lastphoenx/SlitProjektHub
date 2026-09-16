#!/usr/bin/env python
"""KI-Stapel-Protokoll der Offertbeurteilung aus der DB lesen.

Auf dem Server:
  cd /opt/slitprojekthub
  .venv/bin/python scripts/maintenance/check_evaluation_batch_log.py --list-projects
  .venv/bin/python scripts/maintenance/check_evaluation_batch_log.py \\
      --project-key demo-ausschreibung-webportal --days 7
  .venv/bin/python scripts/maintenance/check_evaluation_batch_log.py \\
      --project-key ... --bidder-id 5 --parent-id 17 --today
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m01_config import get_settings
from src.m15_evaluation import list_evaluation_batch_logs


def _resolve_project_key(arg: str | None) -> str | None:
    if arg:
        return arg.strip()
    for env_name in ("PROJECT_KEY", "PK"):
        val = (os.environ.get(env_name) or "").strip()
        if val:
            return val
    return None


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("Keine Einträge.")
        return
    for row in rows:
        ts = row.get("created_at")
        if isinstance(ts, datetime):
            ts_s = ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_s = str(ts or "")
        status = "ok" if row.get("ok") else "FEHLER"
        detail = row.get("value") if row.get("ok") else (row.get("error_message") or "")
        bidder = row.get("bidder_name") or f"#{row.get('bidder_id')}"
        if row.get("bidder_deleted"):
            bidder += " (entfernt)"
        parent = row.get("parent_referenz") or row.get("parent_name") or "—"
        crit = row.get("criterion_referenz") or row.get("criterion_name") or "—"
        step = row.get("step_index") or "—"
        user = row.get("triggered_by") or "—"
        run = (row.get("run_id") or "")[:8]
        print(
            f"{ts_s}  run={run}  {bidder:12}  {parent:6}  {crit:10}  "
            f"#{step:<2}  {status:6}  {detail}  ({user})"
        )


def _print_runs(rows: list[dict]) -> None:
    if not rows:
        print("Keine Einträge.")
        return
    by_run: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_run[str(row.get("run_id") or "")].append(row)
    for run_id, items in by_run.items():
        items_sorted = sorted(items, key=lambda r: (r.get("step_index") or 0, r.get("id") or 0))
        first = items_sorted[0]
        ok_n = sum(1 for r in items_sorted if r.get("ok"))
        err_n = len(items_sorted) - ok_n
        ts = first.get("created_at")
        ts_s = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else str(ts or "")
        bidder = first.get("bidder_name") or f"#{first.get('bidder_id')}"
        parent = first.get("parent_referenz") or first.get("parent_name") or "—"
        user = first.get("triggered_by") or "—"
        print(
            f"\n=== Run {run_id[:8]}…  {ts_s}  {bidder}  {parent}  "
            f"{ok_n} ok / {err_n} Fehler  ({user}) ==="
        )
        for row in items_sorted:
            crit = row.get("criterion_referenz") or row.get("criterion_name") or "—"
            if row.get("ok"):
                print(f"  #{row.get('step_index') or '?'}  {crit:12}  ok   🤖 {row.get('value')}")
            else:
                print(f"  #{row.get('step_index') or '?'}  {crit:12}  FEHLER  {row.get('error_message')}")


def main() -> int:
    get_settings()
    parser = argparse.ArgumentParser(description="KI-Stapel-Protokoll Offertbeurteilung")
    parser.add_argument("--list-projects", action="store_true", help="Projekt-Keys mit Log-Einträgen")
    parser.add_argument("--project-key", help="Projekt-Key (Pflicht ohne --list-projects)")
    parser.add_argument("--bidder-id", type=int, help="Nur einen Bieter")
    parser.add_argument("--parent-id", type=int, help="Nur ein Parent-Kriterium")
    parser.add_argument("--run-id", help="Nur einen Stapel-Lauf")
    parser.add_argument("--days", type=int, default=14, help="Nur letzte N Tage (default: 14)")
    parser.add_argument("--today", action="store_true", help="Nur heute (UTC-Datum)")
    parser.add_argument("--limit", type=int, default=200, help="Max. Zeilen (default: 200)")
    parser.add_argument("--group-runs", action="store_true", help="Nach run_id gruppieren")
    parser.add_argument("--json", action="store_true", help="JSON statt Tabelle")
    args = parser.parse_args()

    if args.list_projects:
        import sqlite3
        from src.m01_config import get_settings as gs

        url = (gs().db_url or "").replace("sqlite:///", "", 1)
        db = Path(url)
        if not db.is_absolute():
            db = ROOT / db
        if not db.exists():
            print(f"DB nicht gefunden: {db}")
            return 1
        con = sqlite3.connect(str(db))
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "evaluation_batch_log" not in tables:
            print("Tabelle evaluation_batch_log fehlt — Backend nach Deploy neu starten.")
            return 1
        rows = con.execute(
            "SELECT project_key, COUNT(*) AS n, MAX(created_at) AS last_at "
            "FROM evaluation_batch_log GROUP BY project_key ORDER BY last_at DESC"
        ).fetchall()
        if not rows:
            print("Noch keine Batch-Logs.")
            return 0
        print("Anzahl  letzter_Eintrag              project_key")
        print("------  --------------------------  " + "-" * 40)
        for pk, n, last_at in rows:
            print(f"{n:6}  {last_at}  {pk}")
        if len(rows) == 1:
            pk = rows[0][0]
            print()
            print("Detail (ein Projekt):")
            print(
                f"  .venv/bin/python scripts/maintenance/check_evaluation_batch_log.py "
                f"--project-key '{pk}' --group-runs --days 7"
            )
        else:
            print()
            print("Detail: --project-key '<key>' --group-runs  (oder PK='…' setzen)")
        return 0

    project_key = _resolve_project_key(args.project_key)
    if not project_key:
        parser.error(
            "--project-key ist erforderlich (oder --list-projects; "
            "optional Umgebungsvariable PK/PROJECT_KEY)"
        )

    rows = list_evaluation_batch_logs(
        project_key,
        bidder_id=args.bidder_id,
        parent_criterion_id=args.parent_id,
        run_id=args.run_id,
        since_days=args.days if not args.today else None,
        limit=args.limit,
    )
    if args.today:
        today = datetime.now(timezone.utc).date()
        rows = [
            r for r in rows
            if isinstance(r.get("created_at"), datetime)
            and (
                r["created_at"].astimezone(timezone.utc).date()
                if r["created_at"].tzinfo
                else r["created_at"].replace(tzinfo=timezone.utc).date()
            )
            == today
        ]

    if args.json:
        def _json_default(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError

        print(json.dumps(rows, ensure_ascii=False, indent=2, default=_json_default))
        return 0

    print(f"Projekt: {project_key}")
    print(f"Einträge: {len(rows)}")
    if args.group_runs:
        _print_runs(rows)
    else:
        _print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
