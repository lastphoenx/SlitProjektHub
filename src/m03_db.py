# src/m03_db.py
from __future__ import annotations
from typing import Optional, List
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, create_engine, Session, Relationship
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Index, text, Float
from sqlalchemy import event  # WAL-Modus
from .m01_config import get_settings

S = get_settings()
engine = create_engine(S.db_url, echo=False)

# WAL-Modus: aktiviert Write-Ahead-Logging für robustere Concurrent-Zugriffe.
# Gesteuert via config.yaml → database.wal_mode.
# Zum Deaktivieren: wal_mode: false in config.yaml setzen.
if S.db_wal_mode:
    @event.listens_for(engine, "connect")
    def _set_wal_mode(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

DOCUMENT_CLASSIFICATIONS = [
    "Pflichtenheft (Projekt)",
    "Pflichtenheft (Rolle)",
    "Anforderung/Feature",
    "Standard/Richtlinie",
    "FAQ/Fragen-Katalog",
    "API-Dokumentation",
    "Tutorial/Anleitung",
    "Sonstiges"
]

DOC_LIMITS = {
    "max_chunks_per_document": 5000,
    "max_total_chunks_per_project": 10000,
    "max_documents_per_project": 50
}

# -------------------- Modelle --------------------

class Role(SQLModel, table=True):
    # Core Identifiers
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str                            # Slug für URLs/Dateien
    
    # Basic Info
    title: str                          # Rollenbezeichnung
    short_code: Optional[str] = None    # Kürzel (z.B. "CEO", "CTO")
    
    # Content
    description: Optional[str] = None   # Kurzbeschreibung (1-2 Sätze)
    responsibilities: Optional[str] = None  # Hauptverantwortlichkeiten
    qualifications: Optional[str] = None    # Qualifikationen & Anforderungen
    expertise: Optional[str] = None         # Expertise / Spezialwissen
    
    # File Paths & Documents
    markdown_content_path: str          # Pfad zur Markdown-Datei mit Rollenbeschreibung
    attached_docs: Optional[str] = None # JSON-Array mit Dokument-Pfaden
    
    # RAG & Embeddings
    rag_indexed_at: Optional[datetime] = None   # Letztes Indexing
    rag_chunk_count: Optional[int] = None       # Anzahl Chunks
    rag_status: Optional[str] = None            # Status: "indexed", "pending", "failed"
    embedding: Optional[str] = None             # JSON-Array (Vektor-Embedding für Similarity Search)
    embedding_model: Optional[str] = None       # z.B. "text-embedding-3-small"
    
    # System
    is_deleted: bool = False            # Soft-Delete
    created_at: Optional[datetime] = None       # Erstellungsdatum
    updated_at: Optional[datetime] = None       # Letzte Änderung

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str                            # Auto-generiert (slug)
    title: str                          # Vollständiger Titel
    short_title: Optional[str] = None   # Kurz-Titel (max 50) 
    short_code: Optional[str] = None    # Kürzel (max 14) - UI-eingegeben
    description: Optional[str] = None   # Kurzbeschreibung (optional)
    text_path: str                      # Pfad zur Markdown-Datei
    rag_path: str                       # RAG-Pfad
    is_deleted: bool = False            # Soft-Delete
    
    # RAG & Embeddings
    embedding: Optional[str] = None             # JSON-Array (Vektor-Embedding)
    embedding_model: Optional[str] = None       # z.B. "text-embedding-3-small"
    
    # Generierungs-Metadaten (für Aufgaben aus Rollen)
    source_role_key: Optional[str] = None       # Quelle: role.key
    source_responsibility: Optional[str] = None  # Welche Verantwortlichkeit
    generation_batch_id: Optional[str] = None    # UUID für Batch-Undo
    generated_at: Optional[datetime] = None      # Zeitstempel

class Context(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str                            # Auto-generiert (slug)
    title: str                          # Vollständiger Titel
    short_title: Optional[str] = None   # Kurz-Titel (max 50)
    short_code: Optional[str] = None    # Kürzel (max 14) - UI-eingegeben
    description: Optional[str] = None   # Kurzbeschreibung (optional)
    text_path: str                      # Pfad zur Markdown-Datei
    rag_path: str                       # RAG-Pfad
    is_deleted: bool = False            # Soft-Delete
    
    # RAG & Embeddings
    embedding: Optional[str] = None             # JSON-Array (Vektor-Embedding)
    embedding_model: Optional[str] = None       # z.B. "text-embedding-3-small"

class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str                            # Auto-generiert (slug)
    title: str                          # Vollständiger Titel
    description: str                    # Projektbeschreibung (required)
    short_title: Optional[str] = None   # Kurz-Titel (max 50)
    short_code: Optional[str] = None    # Kürzel (max 14) - UI-eingegeben
    type: str | None = None             # Projekt-Typ (optional)
    role_key: Optional[str] = None      # DEPRECATED: Use task_keys instead
    task_key: Optional[str] = None      # DEPRECATED: Use task_keys instead  
    task_keys: Optional[str] = None     # JSON array: ["task1", "task2"] for multi-select
    role_keys: Optional[str] = None     # JSON array: ["role1", "role2"] for multi-select
    context_keys: Optional[str] = None  # JSON array: ["ctx1", "ctx2"] for multi-select
    is_deleted: bool = False            # Soft-Delete
    context_key: Optional[str] = None
    text_path: Optional[str] = None
    rag_path: Optional[str] = None
    
    # RAG & Embeddings
    embedding: Optional[str] = None             # JSON-Array (Vektor-Embedding)
    embedding_model: Optional[str] = None       # z.B. "text-embedding-3-small"
    rag_priority_terms: Optional[str] = None    # JSON-Array: Projekt-spezifische BM25-Priority-Terms

class Document(SQLModel, table=True):
    __tablename__ = "document"
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str = Field(sa_column=Column(String(255), nullable=False))
    sha256_hash: str = Field(sa_column=Column(String(64), nullable=False, unique=True, index=True))
    classification: str = Field(sa_column=Column(String(50), nullable=False))
    file_path: str = Field(nullable=False)
    file_size: Optional[int] = None
    embedding_model: Optional[str] = None
    chunk_count: int = Field(default=0)
    chunk_size_used: Optional[int] = None  # Verwendete Chunk-Größe beim Upload
    linked_role_keys: Optional[str] = None  # JSON-Array: ["role-1", "role-2"] für Rollen-Dokumente
    uploaded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
    is_deleted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("0"))
    )


class DocumentChunk(SQLModel, table=True):
    __tablename__ = "document_chunk"
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    chunk_index: int = Field(sa_column=Column(Integer, nullable=False))
    chunk_text: str = Field(nullable=False)
    embedding: Optional[str] = None
    embedding_model: Optional[str] = None
    tokens_count: Optional[int] = None
    retrieval_keywords: Optional[str] = None  # JSON-Array: LLM-generierte Suchbegriffe für BM25
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )


class ProjectDocumentLink(SQLModel, table=True):
    __tablename__ = "project_document_link"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_key: str = Field(sa_column=Column(String(80), nullable=False, index=True))
    document_id: int = Field(foreign_key="document.id", index=True)
    added_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(sa_column=Column(String(20), nullable=False, index=True))
    model_name: Optional[str] = Field(default=None, sa_column=Column(String(50), nullable=True))
    model_temperature: Optional[float] = Field(default=None)
    session_id: str = Field(sa_column=Column(String(36), nullable=False, index=True))  # UUID als String
    project_key: Optional[str] = Field(sa_column=Column(String(80), nullable=True, index=True))  # Zugeordnetes Projekt
    role: str = Field(sa_column=Column(String(16), nullable=False))  # "user"/"assistant"/"system"
    content: str = Field(nullable=False)
    message_type: Optional[str] = Field(
        default=None,
        sa_column=Column(String(20), nullable=True)
    )  # Nachrichtentyp: "idea", "decision", "todo", "assumption", "info"
    message_status: Optional[str] = Field(
        default="ungeprüft",
        sa_column=Column(String(20), nullable=True)
    )  # Status: "ungeprüft", "bestätigt", "falsch"
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
    is_deleted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("0"))
    )
    rag_sources: Optional[str] = Field(
        default=None,
        sa_column=Column(String, nullable=True)
    )  # JSON string mit RAG-Quellen: [{"document_id": N, "filename": "...", "similarity": 0.8, "text": "..."}]

class RAGFeedback(SQLModel, table=True):
    __tablename__ = "rag_feedback"
    id: Optional[int] = Field(default=None, primary_key=True)
    chat_message_id: int = Field(foreign_key="chat_message.id", index=True)
    chunk_id: Optional[int] = Field(foreign_key="document_chunk.id", nullable=True)
    document_id: Optional[int] = Field(foreign_key="document.id", nullable=True)
    
    helpful: bool = Field(default=False)  # True = hilfreich, False = nicht hilfreich
    comment: Optional[str] = None
    
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )

# Composite-Index für schnelle Session-Queries (provider + session_id + timestamp)
__chat_message_index = Index(
    "idx_chat_session",
    ChatMessage.provider,
    ChatMessage.session_id,
    ChatMessage.timestamp
)


class Decision(SQLModel, table=True):
    __tablename__ = "decision"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_key: str = Field(sa_column=Column(String(80), nullable=False, index=True))
    session_id: str = Field(sa_column=Column(String(36), nullable=False, index=True))
    message_id: int = Field(sa_column=Column(Integer, nullable=True, index=True))
    
    title: str = Field(nullable=False)
    description: str = Field(nullable=False)
    
    embedding: Optional[str] = None
    embedding_model: Optional[str] = None
    
    created_from_chat: bool = Field(default=True)
    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    is_deleted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("0"))
    )


# -------------------- Migration --------------------

def _column_exists(table: str, col: str) -> bool:
    with engine.connect() as conn:
        res = conn.execute(text(f"PRAGMA table_info({table});")).mappings().all()
    return any(r["name"] == col for r in res)

def migrate_db() -> None:
    # leichte SQLite-Migrationen für neue Spalten
    with engine.begin() as conn:
        # ChatMessage table - neue Spalten für Nachrichtentypisierung
        if not _column_exists("chat_message", "project_key"):
            conn.execute(text("ALTER TABLE chat_message ADD COLUMN project_key VARCHAR;"))
        if not _column_exists("chat_message", "message_type"):
            conn.execute(text("ALTER TABLE chat_message ADD COLUMN message_type VARCHAR;"))
        if not _column_exists("chat_message", "message_status"):
            conn.execute(text("ALTER TABLE chat_message ADD COLUMN message_status VARCHAR DEFAULT 'ungeprüft';"))
        
        # ChatMessage table - neue Spalten für Modell-Tracking
        if not _column_exists("chat_message", "model_name"):
            conn.execute(text("ALTER TABLE chat_message ADD COLUMN model_name VARCHAR;"))
        if not _column_exists("chat_message", "model_temperature"):
            conn.execute(text("ALTER TABLE chat_message ADD COLUMN model_temperature FLOAT;"))
        
        # Role table - backwards compatibility
        if not _column_exists("role", "group_name"):
            conn.execute(text("ALTER TABLE role ADD COLUMN group_name VARCHAR;"))
        if not _column_exists("role", "is_deleted"):
            conn.execute(text("ALTER TABLE role ADD COLUMN is_deleted BOOLEAN DEFAULT 0;"))
        
        # Role table - konsistente UI-Felder
        if not _column_exists("role", "short_title"):
            conn.execute(text("ALTER TABLE role ADD COLUMN short_title VARCHAR;"))
        if not _column_exists("role", "short_code"):
            conn.execute(text("ALTER TABLE role ADD COLUMN short_code VARCHAR;"))
        if not _column_exists("role", "description"):
            conn.execute(text("ALTER TABLE role ADD COLUMN description VARCHAR;"))
        if not _column_exists("role", "responsibilities"):
            conn.execute(text("ALTER TABLE role ADD COLUMN responsibilities TEXT;"))
        if not _column_exists("role", "qualifications"):
            conn.execute(text("ALTER TABLE role ADD COLUMN qualifications TEXT;"))
        if not _column_exists("role", "expertise"):
            conn.execute(text("ALTER TABLE role ADD COLUMN expertise TEXT;"))
        
        # Role table - RAG & Embeddings
        if not _column_exists("role", "embedding"):
            conn.execute(text("ALTER TABLE role ADD COLUMN embedding TEXT;"))
        if not _column_exists("role", "embedding_model"):
            conn.execute(text("ALTER TABLE role ADD COLUMN embedding_model VARCHAR;"))

        # Task table - konsistente UI-Felder  
        if not _column_exists("task", "short_title"):
            conn.execute(text("ALTER TABLE task ADD COLUMN short_title VARCHAR;"))
        if not _column_exists("task", "short_code"):
            conn.execute(text("ALTER TABLE task ADD COLUMN short_code VARCHAR;"))
        if not _column_exists("task", "description"):
            conn.execute(text("ALTER TABLE task ADD COLUMN description VARCHAR;"))
        if not _column_exists("task", "is_deleted"):
            conn.execute(text("ALTER TABLE task ADD COLUMN is_deleted BOOLEAN DEFAULT 0;"))
        
        # Task table - Generierungs-Metadaten
        if not _column_exists("task", "source_role_key"):
            conn.execute(text("ALTER TABLE task ADD COLUMN source_role_key TEXT;"))
        if not _column_exists("task", "source_responsibility"):
            conn.execute(text("ALTER TABLE task ADD COLUMN source_responsibility TEXT;"))
        if not _column_exists("task", "generation_batch_id"):
            conn.execute(text("ALTER TABLE task ADD COLUMN generation_batch_id TEXT;"))
        if not _column_exists("task", "generated_at"):
            conn.execute(text("ALTER TABLE task ADD COLUMN generated_at DATETIME;"))
        
        # Task table - RAG & Embeddings
        if not _column_exists("task", "embedding"):
            conn.execute(text("ALTER TABLE task ADD COLUMN embedding TEXT;"))
        if not _column_exists("task", "embedding_model"):
            conn.execute(text("ALTER TABLE task ADD COLUMN embedding_model VARCHAR;"))

        # Context table - konsistente UI-Felder
        if not _column_exists("context", "short_title"):
            conn.execute(text("ALTER TABLE context ADD COLUMN short_title VARCHAR;"))
        if not _column_exists("context", "short_code"):
            conn.execute(text("ALTER TABLE context ADD COLUMN short_code VARCHAR;"))
        if not _column_exists("context", "description"):
            conn.execute(text("ALTER TABLE context ADD COLUMN description VARCHAR;"))
        if not _column_exists("context", "is_deleted"):
            conn.execute(text("ALTER TABLE context ADD COLUMN is_deleted BOOLEAN DEFAULT 0;"))
        
        # Context table - RAG & Embeddings
        if not _column_exists("context", "embedding"):
            conn.execute(text("ALTER TABLE context ADD COLUMN embedding TEXT;"))
        if not _column_exists("context", "embedding_model"):
            conn.execute(text("ALTER TABLE context ADD COLUMN embedding_model VARCHAR;"))

        # Project table - konsistente UI-Felder
        if not _column_exists("project", "short_title"):
            conn.execute(text("ALTER TABLE project ADD COLUMN short_title VARCHAR;"))
        if not _column_exists("project", "short_code"):
            conn.execute(text("ALTER TABLE project ADD COLUMN short_code VARCHAR;"))
        if not _column_exists("project", "type"):
            conn.execute(text("ALTER TABLE project ADD COLUMN type VARCHAR;"))
        if not _column_exists("project", "is_deleted"):
            conn.execute(text("ALTER TABLE project ADD COLUMN is_deleted BOOLEAN DEFAULT 0;"))
        # Neue Spalte für Project: task_keys (JSON array für Multi-Select)
        if not _column_exists("project", "task_keys"):
            conn.execute(text("ALTER TABLE project ADD COLUMN task_keys TEXT;"))
        if not _column_exists("project", "role_keys"):
            conn.execute(text("ALTER TABLE project ADD COLUMN role_keys TEXT;"))
        if not _column_exists("project", "context_keys"):
            conn.execute(text("ALTER TABLE project ADD COLUMN context_keys TEXT;"))
        
        # Project table - RAG & Embeddings
        if not _column_exists("project", "embedding"):
            conn.execute(text("ALTER TABLE project ADD COLUMN embedding TEXT;"))
        if not _column_exists("project", "embedding_model"):
            conn.execute(text("ALTER TABLE project ADD COLUMN embedding_model VARCHAR;"))
        if not _column_exists("project", "rag_priority_terms"):
            conn.execute(text("ALTER TABLE project ADD COLUMN rag_priority_terms TEXT;"))
        if not _column_exists("document_chunk", "retrieval_keywords"):
            conn.execute(text("ALTER TABLE document_chunk ADD COLUMN retrieval_keywords TEXT;"))

        # Projekt-spezifische Priority-Terms seeden (einmalig, nur wenn noch NULL)
        _UNISPORT_KEY = (
            "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport"
            "-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"
        )
        row = conn.execute(
            text("SELECT rag_priority_terms FROM project WHERE \"key\" = :k"),
            {"k": _UNISPORT_KEY},
        ).fetchone()
        if row is not None and row[0] is None:
            conn.execute(
                text("UPDATE project SET rag_priority_terms = :v WHERE \"key\" = :k"),
                {
                    "v": '["subunternehm","konzern","hosting","cloud","infrastruktur"]',
                    "k": _UNISPORT_KEY,
                },
            )
        
        # Document table - neue Tabelle für Document Management
        if not _column_exists("document", "filename"):
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS document (
                    id INTEGER PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    sha256_hash VARCHAR(64) NOT NULL UNIQUE,
                    classification VARCHAR(50) NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER,
                    embedding_model VARCHAR,
                    chunk_count INTEGER DEFAULT 0,
                    uploaded_at DATETIME NOT NULL,
                    is_deleted BOOLEAN DEFAULT 0
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_document_sha256 ON document(sha256_hash);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_document_uploaded ON document(uploaded_at);"))
        
        # DocumentChunk table
        if not _column_exists("document_chunk", "document_id"):
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS document_chunk (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_text TEXT NOT NULL,
                    embedding TEXT,
                    embedding_model VARCHAR,
                    tokens_count INTEGER,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES document(id)
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_document_chunk_doc ON document_chunk(document_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_document_chunk_created ON document_chunk(created_at);"))
        
        # ProjectDocument table (Many-to-Many)
        if not _column_exists("project_document", "project_id"):
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS project_document (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    document_id INTEGER NOT NULL,
                    added_at DATETIME NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES project(id),
                    FOREIGN KEY(document_id) REFERENCES document(id)
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_project_document_proj ON project_document(project_id);"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_project_document_doc ON project_document(document_id);"))

# -------------------- Setup/Session --------------------

def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    migrate_db()

def get_session() -> Session:
    return Session(engine)
