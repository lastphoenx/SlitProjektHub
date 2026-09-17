#!/usr/bin/env python
"""Rangfolge-Diagnose: DB-Rohdaten vs. compute_rankings().

Auf dem Server (CT 136):
  cd /opt/slitprojekthub
  .venv/bin/python scripts/maintenance/list_projects.py
  PK='…'   # Key aus list_projects (nicht Projekttitel raten)

  .venv/bin/python scripts/maintenance/check_evaluation_rankings.py --project-key "$PK"
  .venv/bin/python scripts/maintenance/check_evaluation_rankings.py --project-key "$PK" --scores
  .venv/bin/python scripts/maintenance/check_evaluation_rankings.py --list-projects

SQLite direkt (Scores pro Bieter/Kriterium):
  sqlite3 -header -column data/db/slitproj.db \\
  "SELECT b.name AS bieter, c.referenz, c.name, s.source_key, s.value
   FROM score s
   JOIN bidder b ON b.id=s.bidder_id
   JOIN criterion c ON c.id=s.criterion_id
   WHERE b.project_key='$PK' AND b.is_deleted=0 AND c.is_deleted=0
   ORDER BY b.sort_order, b.name, c.sort_order, s.source_key;"
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m01_config import get_settings
from src.m15_evaluation import (
    compute_rankings,
    list_bidders,
    list_criteria,
    list_scores_for_project,
    official_score,
    rolled_up_score,
)


def _erreichte_werte(score: float | None) -> str:
    if score is None:
        return "—"
    return f"{score / 10:.2f}"


def _resolve_project_key(arg: str | None) -> str | None:
    if arg:
        return arg.strip()
    for env_name in ("PROJECT_KEY", "PK"):
        val = (os.environ.get(env_name) or "").strip()
        if val:
            return val
    return None


def _db_path() -> Path:
    url = (get_settings().db_url or "").replace("sqlite:///", "", 1)
    db = Path(url)
    if not db.is_absolute():
        db = ROOT / db
    return db


def _list_projects_with_eval() -> int:
    db = _db_path()
    if not db.exists():
        print(f"DB nicht gefunden: {db}")
        return 1
    con = sqlite3.connect(str(db))
    rows = con.execute(
        """
        SELECT b.project_key,
               COUNT(DISTINCT b.id) AS bidders,
               COUNT(DISTINCT c.id) AS criteria,
               COUNT(s.id) AS scores,
               MAX(s.updated_at) AS last_score
        FROM bidder b
        LEFT JOIN criterion c ON c.project_key = b.project_key AND c.is_deleted = 0
        LEFT JOIN score s ON s.bidder_id = b.id
        WHERE b.is_deleted = 0
        GROUP BY b.project_key
        ORDER BY last_score DESC NULLS LAST, b.project_key
        """
    ).fetchall()
    if not rows:
        print("Keine Bieter in der DB.")
        return 0
    print("Bieter  Kriterien  Scores  letzter_Score           project_key")
    print("------  ---------  ------  ------------------------  " + "-" * 36)
    for pk, bidders, criteria, scores, last_score in rows:
        last_s = last_score or "—"
        print(f"{bidders:6}  {criteria:9}  {scores:6}  {last_s:24}  {pk}")
    if len(rows) == 1:
        pk = rows[0][0]
        print()
        print(f"Detail: .venv/bin/python scripts/maintenance/check_evaluation_rankings.py --project-key '{pk}'")
    else:
        print()
        print("Detail: --project-key '<key>'  (oder PK='…' setzen)")
    return 0


def _score_breakdown(rows: list) -> str:
    parts: list[str] = []
    for s in rows:
        sk = s.source_key
        if sk == "ai":
            label = "KI"
        elif sk == "system":
            label = "system"
        elif sk.startswith("user:"):
            label = sk.replace("user:", "user#")
        else:
            label = sk
        parts.append(f"{label}={s.value:g}")
    return ", ".join(parts) if parts else "—"


def _print_rankings(project_key: str, *, show_scores: bool) -> int:
    bidders = list_bidders(project_key)
    if not bidders:
        print(f"Keine aktiven Bieter für project_key={project_key!r}")
        return 1

    criteria = list_criteria(project_key)
    scores = list_scores_for_project(project_key)
    scores_by_cell: dict[tuple[int, int], list] = {}
    for s in scores:
        scores_by_cell.setdefault((s.bidder_id, s.criterion_id), []).append(s)

    top_zuschlag = [
        c for c in criteria if c.kind == "zuschlag" and c.parent_id is None and not c.is_deleted
    ]
    top_eignung = [
        c for c in criteria if c.kind == "eignung" and c.parent_id is None and not c.is_deleted
    ]
    total_weight = sum(c.weight_pct for c in top_zuschlag if c.weight_pct > 0)
    has_phase2 = any(int(c.ranking_phase or 1) >= 2 for c in top_zuschlag)

    print(f"Projekt: {project_key}")
    print(f"Bieter: {len(bidders)}  |  Top-Zuschlag: {len(top_zuschlag)}  |  Gewichtssumme: {total_weight:g}%")
    if has_phase2:
        print("Zweistufige Bewertung (Phase 1 ZK + Phase 2) aktiv.")
    print()

    if show_scores:
        print("=== Offizielle Werte (Rangfolge-relevant) ===")
        print(
            "Hinweis: auto_price → source=system; sonst Ø aller user:* (KI zählt NICHT).\n"
        )
        for bidder in bidders:
            print(f"--- {bidder.name} (id={bidder.id}) ---")
            for crit in top_eignung:
                cell = scores_by_cell.get((bidder.id, crit.id), [])
                off = official_score(bidder.id, crit, cell)
                print(
                    f"  Eignung  {crit.referenz or '—':6}  {crit.name[:40]:40}  "
                    f"official={off if off is not None else '—'}  [{_score_breakdown(cell)}]"
                )
            for crit in top_zuschlag:
                off, ans, tot = rolled_up_score(bidder.id, crit, criteria, scores_by_cell)
                cell = scores_by_cell.get((bidder.id, crit.id), [])
                w = crit.weight_pct
                ph = int(crit.ranking_phase or 1)
                auto = " Preis" if crit.auto_price else ""
                print(
                    f"  Zuschlag P{ph} w={w:g}%  {crit.referenz or '—':6}  "
                    f"{crit.name[:32]:32}{auto}  official={off if off is not None else '—'}  "
                    f"({ans}/{tot})  [{_score_breakdown(cell)}]"
                )
            print()

        print("=== Fehlende Bewerter-Werte (blockieren teils Rangfolge) ===")
        missing_any = False
        for bidder in bidders:
            gaps: list[str] = []
            for crit in top_zuschlag:
                if crit.auto_price:
                    cell = scores_by_cell.get((bidder.id, crit.id), [])
                    if not any(s.source_key == "system" for s in cell):
                        gaps.append(f"{crit.referenz or crit.name} (system/Preis fehlt)")
                else:
                    children = [c for c in criteria if c.parent_id == crit.id and not c.is_deleted]
                    if children:
                        for ch in children:
                            cell = scores_by_cell.get((bidder.id, ch.id), [])
                            if not any(s.source_key.startswith("user:") for s in cell):
                                gaps.append(f"{ch.referenz or ch.name} (kein user:*, evtl. nur KI)")
                    else:
                        cell = scores_by_cell.get((bidder.id, crit.id), [])
                        if not any(s.source_key.startswith("user:") for s in cell):
                            only_ki = any(s.source_key == "ai" for s in cell)
                            hint = " (nur KI)" if only_ki else ""
                            gaps.append(f"{crit.referenz or crit.name} (kein user:*){hint}")
            if gaps:
                missing_any = True
                print(f"  {bidder.name}: " + "; ".join(gaps))
        if not missing_any:
            print("  Keine offensichtlichen Lücken bei Top-Zuschlag (user:* bzw. system).")
        print()

    rankings = compute_rankings(project_key)
    print("=== compute_rankings() (wie UI) ===")
    if has_phase2:
        print(f"{'Rang':>4}  {'Bieter':20}  {'Ph.1/9':>8}  {'Ges./10':>9}  {'K.O.':>4}")
        print("-" * 52)
        for r in rankings:
            ir = r.get("interim_rank") if r.get("interim_rank") else "—"
            ts = _erreichte_werte(r.get("total_score"))
            is_ = _erreichte_werte(r.get("interim_score"))
            ko = "ja" if r.get("ko") else "nein"
            print(f"{str(ir):>4}  {r['bidder_name']:20}  {is_:>8}  {ts:>9}  {ko:>4}")
    else:
        print(f"{'Rang':>4}  {'Bieter':20}  {'Ges./10':>9}  {'K.O.':>4}")
        print("-" * 42)
        for r in rankings:
            rk = r.get("rank") if r.get("rank") else "—"
            ts = _erreichte_werte(r.get("total_score"))
            ko = "ja" if r.get("ko") else "nein"
            print(f"{str(rk):>4}  {r['bidder_name']:20}  {str(ts):>9}  {ko:>4}")

    open_rank = [r for r in rankings if r.get("total_score") is None and not r.get("ko")]
    if open_rank:
        print()
        print("Offen (kein Gesamt %, nicht K.O.):")
        for r in open_rank:
            print(f"  - {r['bidder_name']}: noch nicht alle gewichteten Zuschlagskriterien bewertbar")
    return 0


def main() -> int:
    get_settings()
    parser = argparse.ArgumentParser(description="Rangfolge vs. DB (Offertbeurteilung)")
    parser.add_argument("--list-projects", action="store_true", help="Projekte mit Bieter/Score-Statistik")
    parser.add_argument("--project-key", help="Projekt-Key (Pflicht ohne --list-projects)")
    parser.add_argument(
        "--scores",
        action="store_true",
        help="Zusätzlich Roh-/Official-Werte pro Kriterium anzeigen",
    )
    parser.add_argument("--json", action="store_true", help="Nur compute_rankings() als JSON")
    args = parser.parse_args()

    if args.list_projects:
        return _list_projects_with_eval()

    project_key = _resolve_project_key(args.project_key)
    if not project_key:
        parser.error(
            "--project-key ist erforderlich (oder --list-projects; "
            "optional Umgebungsvariable PK/PROJECT_KEY)"
        )

    if args.json:
        print(json.dumps(compute_rankings(project_key), indent=2, ensure_ascii=False, default=str))
        return 0

    return _print_rankings(project_key, show_scores=args.scores)


if __name__ == "__main__":
    raise SystemExit(main())
