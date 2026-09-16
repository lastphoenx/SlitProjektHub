"""Debug: Warum findet BM25 nichts?"""
import sys
sys.path.insert(0, ".")

from sqlmodel import select
from src.m03_db import get_session, DocumentChunk, Document, ProjectDocumentLink

project_key = "UNESC"  # Kurz-Key

print("=" * 80)
print("DEBUG: Chunks für Projekt")
print("=" * 80)

with get_session() as ses:
    # Chunks für dieses Projekt laden
    doc_query = select(DocumentChunk).join(Document).where(Document.is_deleted == False)
    doc_query = doc_query.join(ProjectDocumentLink, ProjectDocumentLink.document_id == Document.id)\
                         .where(ProjectDocumentLink.project_key == project_key)
    
    chunks = ses.exec(doc_query).all()
    
    print(f"\n✓ Gefunden: {len(chunks)} chunks für Projekt '{project_key}'\n")
    
    if len(chunks) == 0:
        print("❌ PROBLEM: Keine Chunks gefunden!")
        print("\nPrüfe ob Projekt existiert:")
        
        from src.m03_db import Project
        proj = ses.exec(select(Project).where(Project.key == project_key)).first()
        if proj:
            print(f"✓ Projekt existiert: {proj.title}")
        else:
            print(f"❌ Projekt '{project_key}' existiert NICHT!")
            
            # Zeige verfügbare Projekte
            all_projects = ses.exec(select(Project).where(Project.is_deleted == False)).all()
            print(f"\nVerfügbare Projekte ({len(all_projects)}):")
            for p in all_projects[:10]:
                print(f"  - {p.key}: {p.title}")
    else:
        # Zeige paar Beispiele
        print("Beispiel-Chunks:")
        for i, chunk in enumerate(chunks[:3], 1):
            doc = ses.get(Document, chunk.document_id)
            print(f"\n{i}. Chunk #{chunk.id}")
            print(f"   Document: {doc.filename if doc else '?'}")
            print(f"   Text: {(chunk.chunk_text or '')[:200]}...")
            
            # Prüfe ob "Subunternehmen" vorkommt
            if "subunternehmen" in (chunk.chunk_text or "").lower():
                print(f"   ✓ Enthält 'Subunternehmen'!")
