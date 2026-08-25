import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m09_rag import retrieve_relevant_chunks_hybrid, clear_rag_cache
import json

# Cache leeren
clear_rag_cache()

pkey = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"

queries = [
    ("Frage 63", "Performance Anforderungen: Gibt es konkrete Zielwerte für Performance (Antwortzeiten, gleichzeitige Nutzer)? (Gemäss Anhang Ausschreibung, Kapitel T01 Performance)"),
    ("Frage 65", "Preisstruktur: Welche Preisstruktur wird erwartet (z. B. Fixpreis vs. Aufwandspositionen, Trennung nach Entwicklung/Betrieb)? (Gemäss Pflichtenheft, S. 51, Kapitel 14.1.2.1 Preisbewertung)")
]

for label, query in queries:
    print(f"\n{'='*80}")
    print(f"{label}: {query[:80]}...")
    print('='*80)
    
    res = retrieve_relevant_chunks_hybrid(
        query, 
        project_key=pkey, 
        limit=7, 
        threshold=0.45, 
        exclude_classification="FAQ/Fragen-Katalog"
    )
    
    docs = res.get("documents", [])
    
    # Gruppiert nach Dokument
    by_doc = {}
    for d in docs:
        fname = d["filename"]
        if fname not in by_doc:
            by_doc[fname] = []
        by_doc[fname].append(d)
    
    print(f"\nTreffer: {len(docs)}\n")
    for fname, chunks in by_doc.items():
        scores = [c.get("similarity", c.get("match_score", 0)) for c in chunks]
        avg = sum(scores) / len(scores)
        print(f"  {fname[:45]:45} | {len(chunks)} Chunks | Avg: {round(avg*100):3}% | Max: {round(max(scores)*100):3}%")
    
    # Detaillierte RAG-Ausgabe für Preisblatt
    preisblatt_chunks = [c for c in docs if "Preisblatt" in c["filename"]]
    if preisblatt_chunks:
        print(f"\n  📄 Preisblatt gefunden ({len(preisblatt_chunks)} Chunks):")
        for c in preisblatt_chunks:
            sim = c.get("similarity", c.get("match_score", 0))
            text_preview = c["text"][:100].replace("\n", " ")
            print(f"    {round(sim*100):3}% | {text_preview}...")
    else:
        print(f"\n  ❌ Preisblatt NICHT in Top-7!")
        
        # Prüfe niedrigeren Threshold
        res_low = retrieve_relevant_chunks_hybrid(
            query, 
            project_key=pkey, 
            limit=20, 
            threshold=0.05, 
            exclude_classification="FAQ/Fragen-Katalog"
        )
        all_docs = res_low.get("documents", [])
        preisblatt_all = [c for c in all_docs if "Preisblatt" in c["filename"]]
        if preisblatt_all:
            best = max(preisblatt_all, key=lambda x: x.get("similarity", x.get("match_score", 0)))
            best_score = best.get("similarity", best.get("match_score", 0))
            print(f"  ⚠️ Bester Preisblatt-Score (bei threshold=0.05): {round(best_score*100)}%")
            print(f"     → Unter guaranteed_threshold (10%), daher kein Slot")
        else:
            print(f"  ⚠️ Preisblatt hat gar keinen Score >5%!")
