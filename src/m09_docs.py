# src/m09_docs.py
"""
Modul für Dokumenten-Management und RAG-Ingestion.
Behandelt Upload, Text-Extraktion, Chunking und Embedding von Dokumenten.
"""
import os
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlmodel import select, Session

# PDF-Handling: Versuche verschiedene Bibliotheken
try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

from .m01_config import get_settings
from .m03_db import (
    get_session, Document, DocumentChunk, ProjectDocumentLink, 
    DOC_LIMITS, DOCUMENT_CLASSIFICATIONS
)
from .m09_rag import embed_text, EMBEDDING_MODEL, clear_rag_cache

S = get_settings()

# Verzeichnis für hochgeladene Dokumente
DOCS_DIR = Path(S.data_dir) / "rag" / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)


def calculate_sha256(file_bytes: bytes) -> str:
    """Berechnet den SHA256-Hash von Bytes."""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(file_bytes)
    return sha256_hash.hexdigest()


def extract_text_from_pdf(file_path: Path) -> str:
    """Extrahiert Text aus einer PDF-Datei mit Fallback-Strategien."""
    text_content = []
    
    # Strategie 1: PyPDF2
    if PyPDF2:
        try:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        text_content.append(text)
            
            full_text = "\n".join(text_content)
            if full_text.strip():
                return full_text
        except Exception as e:
            print(f"PyPDF2 extraction failed for {file_path}: {e}")
    
    # Strategie 2: pdfplumber (besser für Layouts)
    if pdfplumber:
        try:
            text_content = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        text_content.append(text)
            
            full_text = "\n".join(text_content)
            if full_text.strip():
                return full_text
        except Exception as e:
            print(f"pdfplumber extraction failed for {file_path}: {e}")

    return ""


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """
    Teilt Text in überlappende Chunks auf.
    Einfache Implementierung basierend auf Zeichenlänge.
    """
    if not text:
        return []
    
    if chunk_size is None:
        chunk_size = 1000
    
    # Smart overlap calculation if not provided or default
    if overlap == 200:
        overlap = max(chunk_size // 5, 50)
    
    chunks = []
    start = 0
    text_len = len(text)
    
    while start < text_len:
        end = start + chunk_size
        chunk = text[start:end]
        
        # Versuche, an einem Satzende oder Zeilenumbruch zu schneiden, wenn möglich
        if end < text_len:
            last_period = chunk.rfind('.')
            last_newline = chunk.rfind('\n')
            cut_point = max(last_period, last_newline)
            
            if cut_point > chunk_size * 0.5:  # Nur schneiden, wenn wir nicht zu viel verlieren
                end = start + cut_point + 1
                chunk = text[start:end]
        
        chunks.append(chunk)
        start = end - overlap
        
    return chunks


def process_csv_to_chunks(file_path: Path, delimiter: str = ";") -> Tuple[bool, str, List[dict]]:
    """
    Verarbeitet CSV-Datei für Batch-QA:
    - Jede Zeile wird ein Chunk (als JSON gespeichert)
    - Validiert Pflicht-Spalten: Nr, Lieferant, Frage
    - Returns: (success, message, chunks_as_dicts)
    """
    if pd is None:
        return False, "pandas ist nicht installiert - CSV-Import nicht möglich", []
    
    try:
        df = pd.read_csv(file_path, sep=delimiter, encoding="utf-8")
    except Exception as e:
        try:
            df = pd.read_csv(file_path, sep=delimiter, encoding="latin-1")
        except Exception as e2:
            return False, f"CSV konnte nicht gelesen werden: {e2}", []
    
    # Validierung: Pflicht-Spalten (flexibel mit Punkt-Varianten)
    # Normalisiere Spaltennamen: entferne Punkte und Leerzeichen, lowercase
    df_normalized_cols = {col.strip().rstrip('.').lower(): col for col in df.columns}
    
    required_cols = ["nr", "lieferant", "frage"]
    missing = []
    col_mapping = {}  # Maps: required_name -> actual_column_name
    
    for req_col in required_cols:
        if req_col in df_normalized_cols:
            col_mapping[req_col] = df_normalized_cols[req_col]
        else:
            missing.append(req_col.capitalize())
    
    if missing:
        return False, f"CSV fehlen Pflicht-Spalten: {', '.join(missing)}. Gefunden: {list(df.columns)}", []
    
    # Stelle sicher dass "Antwort"-Spalte existiert (optional im Input, aber wir brauchen sie für Output)
    antwort_col = df_normalized_cols.get("antwort")
    if not antwort_col:
        df["Antwort"] = ""
        antwort_col = "Antwort"
    
    chunks = []
    for idx, row in df.iterrows():
        chunk_dict = {
            "Nr": str(row.get(col_mapping["nr"], idx)),
            "Lieferant": str(row.get(col_mapping["lieferant"], "")),
            "Frage": str(row.get(col_mapping["frage"], "")),
            "Antwort": str(row.get(antwort_col, ""))
        }
        chunks.append(chunk_dict)
    
    return True, f"CSV erfolgreich verarbeitet: {len(chunks)} Zeilen/Fragen", chunks


def ingest_document(
    file_name: str,
    file_bytes: bytes,
    classification: str,
    chunk_size: int = 1000,
    csv_delimiter: str = ";",
    linked_role_keys: list[str] | None = None
) -> Tuple[bool, str]:
    """
    Verarbeitet ein hochgeladenes Dokument:
    1. Hash prüfen (Duplikate)
    2. Speichern
    3. Text extrahieren
    4. Chunken & Embedden
    5. DB-Einträge erstellen
    
    Für CSV: csv_delimiter bestimmt das Trennzeichen (default: ";")
    linked_role_keys: Optional, für "Pflichtenheft (Rolle)" - Liste von role.key Werten
    """
    file_hash = calculate_sha256(file_bytes)
    
    with get_session() as session:
        # Check Duplikate
        existing = session.exec(select(Document).where(Document.sha256_hash == file_hash)).first()
        if existing:
            if existing.is_deleted:
                # Re-Activate
                existing.is_deleted = False
                existing.classification = classification # Update classification
                session.add(existing)
                session.commit()
                return True, f"Dokument '{file_name}' wiederhergestellt (war gelöscht)."
            return False, f"Dokument '{file_name}' existiert bereits (ID: {existing.id})."

        # Datei speichern
        safe_name = Path(file_name).name
        # Timestamp prefix to avoid filename collisions on disk
        ts_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_filename = f"{ts_prefix}_{safe_name}"
        file_path = DOCS_DIR / saved_filename
        
        try:
            file_path.write_bytes(file_bytes)
        except Exception as e:
            return False, f"Fehler beim Speichern der Datei: {e}"
        
        # Text extrahieren
        text_content = ""
        csv_chunks = None  # Für strukturierte CSV-Verarbeitung
        ext = file_path.suffix.lower()
        
        if ext == ".csv":
            # CSV: spezielle Behandlung - jede Zeile wird ein strukturierter Chunk
            success, message, csv_data = process_csv_to_chunks(file_path, delimiter=csv_delimiter)
            if not success:
                return False, f"CSV-Verarbeitung fehlgeschlagen: {message}"
            csv_chunks = csv_data  # Liste von dicts
        elif ext == ".pdf":
            text_content = extract_text_from_pdf(file_path)
        elif ext == ".docx":
            text_content = extract_text_from_docx(file_path)
        elif ext in [".md", ".txt", ".json", ".yaml", ".yml"]:
            try:
                text_content = file_path.read_text(encoding="utf-8")
            except:
                try:
                    text_content = file_path.read_text(encoding="latin-1")
                except:
                    text_content = "" 
        else:
            # Fallback: Versuche als Text zu lesen
            try:
                text_content = file_path.read_text(encoding="utf-8")
            except:
                pass

        # DB Eintrag erstellen
        linked_keys_json = None
        if linked_role_keys:
            linked_keys_json = json.dumps(linked_role_keys)
        
        doc = Document(
            filename=file_name,
            sha256_hash=file_hash,
            classification=classification,
            file_path=str(file_path),
            file_size=len(file_bytes),
            embedding_model=EMBEDDING_MODEL,
            chunk_count=0,
            chunk_size_used=chunk_size,
            linked_role_keys=linked_keys_json
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        
        # Chunken & Embedden
        if csv_chunks:
            # CSV: Jede Zeile ist ein Chunk (als JSON gespeichert)
            doc.chunk_count = len(csv_chunks)
            session.add(doc)
            
            for i, row_dict in enumerate(csv_chunks):
                chunk_text_json = json.dumps(row_dict, ensure_ascii=False)
                emb = embed_text(row_dict.get("Frage", ""))  # Embed nur die Frage für Suche
                chunk_entry = DocumentChunk(
                    document_id=doc.id,
                    chunk_index=i,
                    chunk_text=chunk_text_json,  # Strukturierte Daten als JSON
                    embedding=json.dumps(emb) if emb else None,
                    embedding_model=EMBEDDING_MODEL,
                    tokens_count=len(chunk_text_json) // 4
                )
                session.add(chunk_entry)
            
            session.commit()
            clear_rag_cache()
            return True, f"✅ CSV erfolgreich importiert: {doc.chunk_count} Fragen"
        elif text_content:
            chunks = chunk_text(text_content, chunk_size=chunk_size)
            doc.chunk_count = len(chunks)
            session.add(doc)
            
            for i, chunk_str in enumerate(chunks):
                emb = embed_text(chunk_str)
                chunk_entry = DocumentChunk(
                    document_id=doc.id,
                    chunk_index=i,
                    chunk_text=chunk_str,
                    embedding=json.dumps(emb) if emb else None,
                    embedding_model=EMBEDDING_MODEL,
                    tokens_count=len(chunk_str) // 4 # Grobe Schätzung
                )
                session.add(chunk_entry)
            
            session.commit()
            clear_rag_cache()
            return True, f"Dokument '{file_name}' erfolgreich importiert ({doc.chunk_count} Chunks)."
        else:
             return True, f"Dokument '{file_name}' gespeichert, aber kein Text extrahiert."


def delete_document(doc_id: int) -> bool:
    """Löscht ein Dokument (Soft-Delete)."""
    with get_session() as session:
        doc = session.get(Document, doc_id)
        if not doc:
            return False
        
        doc.is_deleted = True
        session.add(doc)
        session.commit()
        return True

def list_documents(include_deleted: bool = False) -> List[Document]:
    with get_session() as session:
        query = select(Document)
        if not include_deleted:
            query = query.where(Document.is_deleted == False)
        query = query.order_by(Document.uploaded_at.desc())
        return session.exec(query).all()

def get_project_documents(project_key: str) -> List[Document]:
    """Gibt alle Dokumente zurück, die einem Projekt zugeordnet sind."""
    with get_session() as session:
        # Join ProjectDocumentLink -> Document
        statement = (
            select(Document)
            .join(ProjectDocumentLink, ProjectDocumentLink.document_id == Document.id)
            .where(ProjectDocumentLink.project_key == project_key)
            .where(Document.is_deleted == False)
        )
        return session.exec(statement).all()

def link_document_to_project(project_key: str, doc_id: int) -> bool:
    with get_session() as session:
        # Check if already linked
        existing = session.exec(
            select(ProjectDocumentLink)
            .where(ProjectDocumentLink.project_key == project_key)
            .where(ProjectDocumentLink.document_id == doc_id)
        ).first()
        
        if existing:
            return True # Already linked
            
        link = ProjectDocumentLink(project_key=project_key, document_id=doc_id)
        session.add(link)
        session.commit()
        return True

def unlink_document_from_project(project_key: str, doc_id: int) -> bool:
    with get_session() as session:
        link = session.exec(
            select(ProjectDocumentLink)
            .where(ProjectDocumentLink.project_key == project_key)
            .where(ProjectDocumentLink.document_id == doc_id)
        ).first()
        
        if link:
            session.delete(link)
            session.commit()
        return True


def sync_documents_to_chromadb() -> Tuple[int, int, str]:
    """
    Synchronisiert alle Dokumente und deren Chunks aus SQLite zu ChromaDB.
    
    Returns:
        Tuple (synced_chunks_count, projects_count, status_message)
    """
    from .m07_chroma import get_chroma_client, get_or_create_project_collection, add_chunks_to_collection
    from .m03_db import Project
    
    total_chunks_synced = 0
    projects_synced = set()
    errors = []
    
    try:
        with get_session() as session:
            projects = session.exec(select(Project).where(Project.is_deleted == False)).all()
            
            for project in projects:
                try:
                    project_docs = get_project_documents(project.key)
                    if not project_docs:
                        continue
                    
                    collection = get_or_create_project_collection(project.id, EMBEDDING_MODEL)
                    
                    for doc in project_docs:
                        chunks = session.exec(
                            select(DocumentChunk)
                            .where(DocumentChunk.document_id == doc.id)
                            .order_by(DocumentChunk.chunk_index)
                        ).all()
                        
                        if not chunks:
                            continue
                        
                        chunk_texts = [c.chunk_text for c in chunks]
                        chunk_embeddings = []
                        for c in chunks:
                            if c.embedding:
                                try:
                                    emb = json.loads(c.embedding)
                                    chunk_embeddings.append(emb)
                                except:
                                    chunk_embeddings.append(None)
                            else:
                                chunk_embeddings.append(None)
                        
                        add_chunks_to_collection(
                            collection,
                            document_id=doc.id,
                            chunks=chunk_texts,
                            embeddings=chunk_embeddings
                        )
                        
                        total_chunks_synced += len(chunks)
                    
                    projects_synced.add(project.key)
                
                except Exception as e:
                    errors.append(f"Projekt '{project.key}': {str(e)}")
        
        status = f"✅ {total_chunks_synced} Chunks in {len(projects_synced)} Projekten synchronisiert"
        if errors:
            status += f". Fehler: {'; '.join(errors[:3])}"
        
        return total_chunks_synced, len(projects_synced), status
    
    except Exception as e:
        return 0, 0, f"❌ ChromaDB Sync fehlgeschlagen: {str(e)}"
