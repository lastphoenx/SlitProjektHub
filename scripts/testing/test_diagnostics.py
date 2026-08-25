import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m09_rag import get_all_documents_with_best_scores, clear_rag_cache

# Cache leeren
clear_rag_cache()

pkey = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"
query = "Preisstruktur: Welche Preisstruktur wird erwartet?"

print(f"{'='*80}")
print(f"TEST: Document Diagnostics")
print(f"{'='*80}")
print(f"\nQuery: {query}")
print(f"\n{'='*80}")
print("ALLE PROJEKT-DOKUMENTE (mit Scores):")
print(f"{'='*80}\n")

diagnostics = get_all_documents_with_best_scores(
    query=query,
    project_key=pkey,
    threshold=0.45,
    exclude_classification="FAQ/Fragen-Katalog"
)

# Gruppiere: Included vs. Excluded
included = [d for d in diagnostics if d["included"]]
excluded = [d for d in diagnostics if not d["included"]]

print(f"✅ EINGESCHLOSSEN (>= 45%):")
print("-" * 80)
for d in included:
    print(f"  {d['best_score']:>5.0%} | {d['filename']}")

print(f"\n⚠️ AUSGESCHLOSSEN:")
print("-" * 80)
for d in excluded:
    print(f"  {d['best_score']:>5.0%} | {d['filename'][:40]:40} | {d['reason']}")

print(f"\n{'='*80}")
print(f"Total: {len(diagnostics)} Dokumente ({len(included)} eingeschlossen, {len(excluded)} ausgeschlossen)")
print(f"{'='*80}")
