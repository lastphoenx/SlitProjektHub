from src.m03_db import get_session, Document, DocumentChunk
from sqlmodel import select

session = get_session()
try:
    docs = session.exec(select(Document)).all()
    doc = next((d for d in docs if "Unibas" in d.filename), None)
    
    if doc:
        print(f"Dokument: {doc.filename}")
        print(f"  Embedding-Modell: {doc.embedding_model}")
        print(f"  Chunks: {doc.chunk_count}")
        print()
        
        chunks = session.exec(select(DocumentChunk).where(DocumentChunk.document_id == doc.id)).all()
        if chunks:
            print("Erste 3 Chunks (Sample):")
            for i, chunk in enumerate(chunks[:3]):
                print(f"\n  Chunk {i}:")
                print(f"    Länge: {len(chunk.chunk_text)} Zeichen")
                print(f"    Text: {chunk.chunk_text[:200]}...")
                print(f"    Hat Embedding: {chunk.embedding is not None}")
finally:
    session.close()
