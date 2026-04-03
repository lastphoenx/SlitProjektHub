"""Test ob BM25 korrekt funktioniert"""
import sys
sys.path.insert(0, ".")

from src.m09_rag import retrieve_relevant_chunks_hybrid

# Test Query
query = "Zusammenarbeit mit Subunternehmen"
project_key = "unisport-escada"

print("=" * 80)
print(f"TEST: BM25 Keyword-Suche")
print(f"Query: {query}")
print(f"Project: {project_key}")
print("=" * 80)

# Hybrid Search (enthält BM25)
results = retrieve_relevant_chunks_hybrid(
    query=query,
    project_key=project_key,
    limit=7,
    threshold=0.45,
    exclude_classification="FAQ/Fragen-Katalog"
)

docs = results.get("documents", [])

print(f"\n✓ Gefunden: {len(docs)} Dokumente\n")

for i, doc in enumerate(docs, 1):
    score = doc.get("similarity", doc.get("match_score", 0))
    score_type = "similarity" if "similarity" in doc else "match_score"
    
    print(f"{i}. {doc['filename']} ({score:.0%}) [{score_type}]")
    print(f"   Classification: {doc.get('classification', '?')}")
    
    # Zeige ob "Subunternehmen" im Text vorkommt
    text = doc.get("text", "")
    if "subunternehmen" in text.lower():
        print(f"   ✓ Enthält 'Subunternehmen'!")
        # Zeige den relevanten Absatz
        lines = text.split("\n")
        for line in lines:
            if "subunternehmen" in line.lower():
                print(f"   → {line.strip()[:150]}")
    else:
        print(f"   ✗ Enthält NICHT 'Subunternehmen'")
    
    print(f"   Text-Vorschau: {text[:200]}...")
    print()
