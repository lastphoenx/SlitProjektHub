from src.m03_db import get_session, Document
from sqlmodel import select

session = get_session()
try:
    docs = session.exec(select(Document).where(Document.is_deleted == False)).all()
    for doc in docs:
        if "Unibas" in doc.filename or "Strategie" in doc.filename:
            print(f"Datei: {doc.filename}")
            print(f"  ID: {doc.id}")
            print(f"  Hash: {doc.sha256_hash[:16]}...")
            print(f"  Klassifikation: {doc.classification}")
            print(f"  Chunks: {doc.chunk_count}")
            print(f"  Embedding-Modell: {doc.embedding_model}")
            print(f"  Upload-Zeit: {doc.uploaded_at}")
            print(f"  Dateigröße: {doc.file_size} bytes")
            print()
finally:
    session.close()
