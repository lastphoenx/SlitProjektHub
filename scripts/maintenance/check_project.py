from src.m03_db import get_session, Project, Document, ProjectDocumentLink
from src.m07_chroma import get_chroma_client
from sqlmodel import select
import json

session = get_session()
try:
    projects = session.exec(select(Project).where(Project.is_deleted == False)).all()
    mvws = next((p for p in projects if "modularen" in p.title.lower()), None)
    
    if not mvws:
        print("Projekt nicht gefunden")
        print(f"Verfügbare Projekte: {[p.title for p in projects]}")
    else:
        print(f"Projekt: {mvws.title}")
        print(f"  Key: {mvws.key}")
        print(f"  Beschreibung: {mvws.description[:100]}...")
        print()
        
        print(f"a) ZUORDNUNGEN:")
        print(f"  Task Keys: {mvws.task_keys}")
        if mvws.task_keys:
            try:
                task_keys = json.loads(mvws.task_keys)
                print(f"    → {len(task_keys)} Tasks: {task_keys}")
            except:
                pass
        print(f"  Context Key: {mvws.context_key}")
        print()
        
        docs = session.exec(
            select(Document)
            .join(ProjectDocumentLink)
            .where(ProjectDocumentLink.project_key == mvws.key)
            .where(Document.is_deleted == False)
        ).all()
        print(f"  Zugeordnete Dokumente: {len(docs)}")
        for doc in docs:
            print(f"    - {doc.filename} ({doc.chunk_count} chunks)")
        print()
        
        print(f"b) CHROMADB INDEXIERUNG:")
        client = get_chroma_client()
        try:
            collection = client.get_collection(name=f"project_{mvws.key}")
            all_items = collection.get()
            print(f"  ChromaDB Collection: '{f'project_{mvws.key}'}'")
            print(f"  Gesamt Chunks indexiert: {len(all_items['ids'])}")
            
            by_doc = {}
            for id in all_items['ids']:
                doc_id = id.split('_')[1]
                by_doc[doc_id] = by_doc.get(doc_id, 0) + 1
            
            for doc_id, count in by_doc.items():
                doc = next((d for d in docs if str(d.id) == doc_id), None)
                if doc:
                    print(f"    - Doc {doc_id} ({doc.filename}): {count} chunks")
                else:
                    print(f"    - Doc {doc_id}: {count} chunks (nicht in DB)")
        except Exception as e:
            print(f"  ChromaDB Collection nicht vorhanden oder Fehler: {e}")
            print(f"  (Dokumente müssen noch dem Projekt zugeordnet werden)")
            
finally:
    session.close()
