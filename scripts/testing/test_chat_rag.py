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

clear_rag_cache()

pkey = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"
query = "Antwort auf Frage von Anbieter 5 (Nr. 1)"

print(f"{'='*80}")
print(f"DIAGNOSE: Chat RAG - Warum 0% Scores?")
print(f"{'='*80}")
print(f"\nQuery: {query}\n")

# Check 1: Haben die Chunks überhaupt Embeddings?
print(f"{'='*80}")
print("CHECK 1: Embedding-Status der Chunks")
print(f"{'='*80}\n")

with get_session() as ses:
    # Hole ein paar Chunks
    chunks = ses.exec(
        select(DocumentChunk)
        .join(Document)
        .where(Document.is_deleted == False)
        .limit(5)
    ).all()
    
    for chunk in chunks:
        doc = ses.get(Document, chunk.document_id)
        has_emb = "✅" if chunk.embedding else "❌"
        emb_len = len(json.loads(chunk.embedding)) if chunk.embedding else 0
        print(f"{has_emb} | {doc.filename[:30]:30} | Chunk {chunk.id} | Embedding: {emb_len} dims")

# Check 2: RAG Retrieval Test
print(f"\n{'='*80}")
print("CHECK 2: RAG Retrieval Test")
print(f"{'='*80}\n")

results = retrieve_relevant_chunks_hybrid(
    query=query,
    project_key=pkey,
    limit=7,
    threshold=0.45,
    exclude_classification="FAQ/Fragen-Katalog"
)

docs = results.get("documents", [])
print(f"Gefundene Dokumente: {len(docs)}\n")

if docs:
    for doc in docs:
        score = doc.get("similarity", doc.get("match_score", 0))
        print(f"  {score:>5.0%} | {doc.get('filename', '?')[:40]}")
        print(f"         | Classification: {doc.get('classification', '?')}")
        print(f"         | Text: {doc.get('text', '')[:80]}...")
        print()
else:
    print("  ❌ Keine Dokumente gefunden!")

# Check 3: Direkter Similarity-Test
print(f"{'='*80}")
print("CHECK 3: Direkter Similarity-Test")
print(f"{'='*80}\n")

from src.m09_rag import embed_text, _cosine_similarity

query_emb = embed_text(query)
if query_emb:
    print(f"✅ Query-Embedding: {len(query_emb)} dims\n")
    
    with get_session() as ses:
        chunk = ses.exec(
            select(DocumentChunk)
            .join(Document)
            .where(Document.is_deleted == False)
            .where(DocumentChunk.embedding != None)
            .limit(1)
        ).first()
        
        if chunk:
            doc = ses.get(Document, chunk.document_id)
            chunk_emb = json.loads(chunk.embedding)
            sim = _cosine_similarity(query_emb, chunk_emb)
            print(f"Test-Chunk: {doc.filename}")
            print(f"Similarity: {sim:.0%}")
        else:
            print("❌ Kein Chunk mit Embedding gefunden!")
else:
    print("❌ Query-Embedding fehlgeschlagen!")

print(f"\n{'='*80}")
