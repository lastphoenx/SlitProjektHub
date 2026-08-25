#!/usr/bin/env python3
"""Kriterien (Eignung + Zuschlag, inkl. Unterfragen) aus einer JSON-Datei importieren.

Generischer Mechanismus, keine projektspezifischen Inhalte im Code - die echten
Ausschreibungstexte gehören in eine lokale, NICHT committete JSON-Datei
(siehe scripts/maintenance/criteria_example.json fuer das Format).

    python scripts/maintenance/import_evaluation_criteria.py --project-key <key> --file pfad/zu/kriterien.json

JSON-Format ("description" optional - voller Anforderungstext, "name" bleibt kurz):
{
  "eignung": [
    {"name": "EK1 ...", "description": "...", "children": [{"name": "EK1-01 ...", "scale_max": 1, "description": "..."}]}
  ],
  "zuschlag": [
    {"name": "W-01 Preis", "weight_pct": 25, "auto_price": true},
    {"name": "F-01 ...", "weight_pct": 20, "description": "...", "children": [{"name": "F01-001 ...", "description": "..."}]}
  ]
}

Idempotent: ueberspringt Kriterien, die (nach Name) im Projekt schon existieren.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.m03_db import init_db  # noqa: E402
from src.m15_evaluation import create_criterion, list_criteria  # noqa: E402


def _import_kind(project_key: str, kind: str, entries: list[dict], existing_names: set[str]) -> int:
    created = 0
    for entry in entries:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        if name in existing_names:
            print(f"  übersprungen (existiert bereits): {name}")
        else:
            parent = create_criterion(
                project_key,
                kind,
                name,
                weight_pct=float(entry.get("weight_pct") or 0),
                scale_max=int(entry.get("scale_max") or 10),
                auto_price=bool(entry.get("auto_price")),
                description=entry.get("description"),
            )
            existing_names.add(name)
            created += 1
            print(f"  angelegt: {kind} {name}")
        parent_id = None
        for c in list_criteria(project_key):
            if c.name == name:
                parent_id = c.id
                break
        for child in entry.get("children", []):
            cname = (child.get("name") or "").strip()
            if not cname:
                continue
            if cname in existing_names:
                print(f"    übersprungen (existiert bereits): {cname}")
                continue
            create_criterion(
                project_key,
                kind,
                cname,
                weight_pct=0,
                scale_max=int(child.get("scale_max") or 10),
                parent_id=parent_id,
                description=child.get("description"),
            )
            existing_names.add(cname)
            created += 1
            print(f"    angelegt: {cname}")
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-key", required=True, help="Ziel-Projekt (Key)")
    parser.add_argument("--file", required=True, help="Pfad zur JSON-Datei mit den Kriterien")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Datei nicht gefunden: {path}")
        return 1
    spec = json.loads(path.read_text(encoding="utf-8"))

    init_db()
    existing_names = {c.name for c in list_criteria(args.project_key)}

    total = 0
    if spec.get("eignung"):
        print("Eignungskriterien:")
        total += _import_kind(args.project_key, "eignung", spec["eignung"], existing_names)
    if spec.get("zuschlag"):
        print("Zuschlagskriterien:")
        total += _import_kind(args.project_key, "zuschlag", spec["zuschlag"], existing_names)

    print(f"\nFertig: {total} neue Kriterien angelegt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
