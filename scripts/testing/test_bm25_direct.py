"""Test BM25 direkt (ohne Embedding)"""
import sys
sys.path.insert(0, ".")

from src.m09_rag import _keyword_search

# Test Query
query = "Zusammenarbeit mit Subunternehmen"
project_key = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"  # Langer key!

print("=" * 80)
print(f"TEST: BM25 Keyword-Suche (DIREKT, kein Embedding)")
print(f"Query: {query}")
print(f"Project: {project_key}")
print("=" * 80)

# Direkt BM25 aufrufen
results = _keyword_search(
    query=query,
    project_key=project_key,
    limit=10,
    exclude_classification="FAQ/Fragen-Katalog"
)

print(f"\n✓ BM25 gefunden: {len(results)} Chunks\n")

for i, doc in enumerate(results, 1):
    score = doc.get("match_score", 0)
    
    print(f"{i}. {doc['filename']} ({score:.2%})")
    print(f"   Classification: {doc.get('classification', '?')}")
    
    # Zeige ob "Subunternehmen" im Text vorkommt
    text = doc.get("text", "")
    if "subunternehmen" in text.lower():
        print(f"   ✓ Enthält 'Subunternehmen'!")
        # Zeige den relevanten Absatz
        lines = text.split("\n")
        for line in lines:
            if "subunternehmen" in line.lower():
                print(f"   → {line.strip()[:200]}")
                break
    else:
        print(f"   ✗ Enthält NICHT 'Subunternehmen'")
    
    print()
