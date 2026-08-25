from typing import Optional, List
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, Relationship, create_engine
from sqlalchemy import Column, Integer, String, DateTime, Boolean

class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chunks: List["DocumentChunk"] = Relationship(back_populates="document")

class DocumentChunk(SQLModel, table=True):
    __tablename__ = "document_chunk"
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    chunk_index: int = Field(sa_column=Column(Integer, nullable=False))
    chunk_text: str = Field(nullable=False)
    embedding: Optional[str] = None
    embedding_model: Optional[str] = None
    tokens_count: Optional[int] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
    
    document: Optional[Document] = Relationship(back_populates="chunks")

try:
    print("Defining models...")
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    print("Models defined successfully.")
    
    print("Instantiating DocumentChunk...")
    chunk = DocumentChunk(document_id=1, chunk_index=0, chunk_text="test")
    print("Instantiated successfully.")
except Exception as e:
    print(f"Error: {e}")
