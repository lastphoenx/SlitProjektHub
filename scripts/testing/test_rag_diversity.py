import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m09_rag import retrieve_relevant_chunks_hybrid, clear_rag_cache
from src.m03_db import get_session, DocumentChunk, Document
from sqlmodel import select
import json

# Cache leeren
clear_rag_cache()

# Projekt-Key
pkey = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"

# Test 1: Prüfe ob Preisblatt Contextual Prefix hat
print("=== TEST 1: Preisblatt Chunks prüfen ===")
with get_session() as s:
    doc = s.exec(select(Document).where(Document.filename.like('%Preisblatt%'))).first()
    if doc:
        print(f"Dokument gefunden: ID={doc.id}, {doc.filename}")
        print(f"Chunks: {doc.chunk_count}, Klassifizierung: {doc.classification}")
        chunks = s.exec(select(DocumentChunk).where(DocumentChunk.document_id == doc.id).limit(3)).all()
        for i, c in enumerate(chunks):
            print(f"\nChunk {i}:")
            print(c.chunk_text[:200])
    else:
        print("❌ Preisblatt nicht gefunden!")

# Test 2: RAG Query
print("\n\n=== TEST 2: RAG Query ===")
query = "Preisstruktur: Welche Preisstruktur wird erwartet (Fixpreis vs. Aufwandspositionen)?"
print(f"Query: {query}\n")

res = retrieve_relevant_chunks_hybrid(
    query, 
    project_key=pkey, 
    limit=7, 
    threshold=0.45, 
    exclude_classification="FAQ/Fragen-Katalog"
)

docs = res.get("documents", [])
print(f"Treffer: {len(docs)}\n")

# Gruppiert nach Dokument
by_doc = {}
for d in docs:
    fname = d["filename"]
    if fname not in by_doc:
        by_doc[fname] = []
    by_doc[fname].append(d)

for fname, chunks in by_doc.items():
    print(f"\n📄 {fname} ({len(chunks)} Chunks):")
    for c in chunks:
        sim = c.get("similarity", c.get("match_score", 0))
        print(f"  {round(sim*100):3}% | {c['text'][:80]}...")

# Test 3: Niedrigerer Threshold
print("\n\n=== TEST 3: Mit threshold=0.20 ===")
res2 = retrieve_relevant_chunks_hybrid(
    query, 
    project_key=pkey, 
    limit=10, 
    threshold=0.20, 
    exclude_classification="FAQ/Fragen-Katalog"
)

docs2 = res2.get("documents", [])
print(f"Treffer: {len(docs2)}\n")

by_doc2 = {}
for d in docs2:
    fname = d["filename"]
    if fname not in by_doc2:
        by_doc2[fname] = []
    by_doc2[fname].append(d)

for fname, chunks in by_doc2.items():    
    scores = [c.get("similarity", c.get("match_score", 0)) for c in chunks]
    avg_score = sum(scores) / len(scores) if scores else 0
    print(f"  {fname[:50]:50} | {len(chunks):2} Chunks | Avg: {round(avg_score*100)}% | Max: {round(max(scores)*100)}%")
