import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m09_rag import get_all_documents_with_best_scores, retrieve_relevant_chunks_hybrid, clear_rag_cache

# Cache leeren
clear_rag_cache()

pkey = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"

queries = [
    ("Q63 (Performance)", "Performance Anforderungen: Gibt es konkrete Zielwerte für Performance (Antwortzeiten, gleichzeitige Nutzer)?"),
    ("Q65 (Preisstruktur)", "Preisstruktur: Welche Preisstruktur wird erwartet (Fixpreis vs. Aufwandspositionen)?")
]

for label, query in queries:
    print(f"\n{'='*80}")
    print(f"{label}")
    print(f"{'='*80}")
    print(f"Query: {query[:60]}...")
    
    # DIAGNOSTICS: Alle Dokumente mit Scores
    diagnostics = get_all_documents_with_best_scores(
        query=query,
        project_key=pkey,
        threshold=0.45,
        exclude_classification="FAQ/Fragen-Katalog"
    )
    
    # Hybrid Retrieval
    hybrid = retrieve_relevant_chunks_hybrid(
        query=query,
        project_key=pkey,
        limit=7,
        threshold=0.45,
        exclude_classification="FAQ/Fragen-Katalog"
    )
    
    print(f"\n📊 DIAGNOSTICS (alle Dokumente):")
    print("-" * 80)
    
    included = [d for d in diagnostics if d["included"]]
    excluded = [d for d in diagnostics if not d["included"]]
    
    if included:
        print(f"✅ Eingeschlossen ({len(included)}):")
        for d in included:
            print(f"  {d['best_score']:>5.0%} | {d['filename'][:40]}")
    
    if excluded:
        print(f"\n⚠️ Ausgeschlossen ({len(excluded)}):")
        for d in excluded:
            print(f"  {d['best_score']:>5.0%} | {d['filename'][:30]:32} | {d['reason'][:35]}")
    
    print(f"\n🔍 HYBRID TOP-7:")
    print("-" * 80)
    
    by_doc = {}
    for doc in hybrid.get("documents", []):
        fname = doc["filename"]
        if fname not in by_doc:
            by_doc[fname] = []
        by_doc[fname].append(doc)
    
    for fname, chunks in by_doc.items():
        scores = [c.get("similarity", c.get("match_score", 0)) for c in chunks]
        print(f"  {fname[:40]:42} | {len(chunks)} Chunks | Max: {max(scores):.0%}")
    
    # Check: Ist Preisblatt dabei?
    preisblatt_diag = next((d for d in diagnostics if "Preisblatt" in d["filename"]), None)
    preisblatt_hybrid = any("Preisblatt" in d["filename"] for d in hybrid.get("documents", []))
    
    print(f"\n{'='*80}")
    if preisblatt_diag:
        status = "✅ IN" if preisblatt_hybrid else "❌ OUT"
        print(f"Preisblatt: {status} Top-7 | Best Score: {preisblatt_diag['best_score']:.0%}")
        if not preisblatt_hybrid:
            print(f"Grund: {preisblatt_diag['reason']}")
    print(f"{'='*80}")
