"""Suche nach Subunternehm* im Pflichtenheft"""
import sys
sys.path.insert(0, ".")

from sqlmodel import select
from src.m03_db import get_session, DocumentChunk, Document, ProjectDocumentLink

project_key = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"

print("=" * 80)
print("Suche nach 'subunternehm' in allen Chunks")
print("=" * 80)

with get_session() as ses:
    # Alle Chunks für Projekt
    doc_query = select(DocumentChunk).join(Document).where(Document.is_deleted == False)
    doc_query = doc_query.join(ProjectDocumentLink, ProjectDocumentLink.document_id == Document.id)\
                         .where(ProjectDocumentLink.project_key == project_key)
    
    chunks = ses.exec(doc_query).all()
    
    print(f"\nTotal chunks: {len(chunks)}")
    
    # Filtere nach "subunternehm"
    matches = []
    for chunk in chunks:
        text = (chunk.chunk_text or "").lower()
        if "subunternehm" in text:
            matches.append(chunk)
    
    print(f"✓ Gefunden: {len(matches)} chunks mit 'subunternehm*'\n")
    
    for i, chunk in enumerate(matches, 1):
        doc = ses.get(Document, chunk.document_id)
        print(f"{i}. Chunk #{chunk.id} aus {doc.filename if doc else '?'}")
        
        # Zeige den relevanten Absatz
        text = chunk.chunk_text or ""
        lines = text.split("\n")
        for line in lines:
            if "subunternehm" in line.lower():
                print(f"   → {line.strip()}")
        print()
