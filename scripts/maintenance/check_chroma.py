from src.m03_db import get_session, Document, ProjectDocumentLink
from src.m07_chroma import get_chroma_client
from sqlmodel import select

session = get_session()
try:
    docs = session.exec(select(Document)).all()
    doc = next((d for d in docs if "Unibas" in d.filename), None)
    if not doc:
        print("Dokument nicht gefunden")
    else:
        print(f"Dokument: {doc.filename}")
        print(f"  ID: {doc.id}")
        print(f"  Chunks in DB: {doc.chunk_count}")
        print()
        
        links = session.exec(select(ProjectDocumentLink).where(ProjectDocumentLink.document_id == doc.id)).all()
        print(f"Projektzuordnungen: {len(links)}")
        for link in links:
            print(f"  - Projekt: {link.project_key}")
        print()
        
        if links:
            client = get_chroma_client()
            for link in links:
                try:
                    collection = client.get_collection(name=f"project_{link.project_key}")
                    all_items = collection.get()
                    doc_chunks = [id for id in all_items['ids'] if f"doc_{doc.id}_" in id]
                    print(f"ChromaDB '{link.project_key}': {len(doc_chunks)} Chunks indexiert")
                except Exception as e:
                    print(f"ChromaDB '{link.project_key}': Fehler - {e}")
        else:
            print("Keine Projektzuordnungen → Chunks nicht in ChromaDB!")
finally:
    session.close()
