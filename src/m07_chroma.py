from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

from .m02_paths import db_file


def chroma_db_dir() -> Path:
    """Returns path to ChromaDB data directory."""
    db_root = db_file().parent
    chroma_dir = db_root / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chroma_dir


_CHROMA_CLIENT = None


def get_chroma_client():
    """Gets or creates ChromaDB persistent client."""
    global _CHROMA_CLIENT
    if _CHROMA_CLIENT is None:
        try:
            import chromadb
        except ImportError:
            raise ImportError(
                "chromadb is not installed. Please install it with:\n"
                "pip install chromadb>=0.3.21"
            )
        chroma_path = str(chroma_db_dir())
        _CHROMA_CLIENT = chromadb.PersistentClient(path=chroma_path)
    return _CHROMA_CLIENT


def get_or_create_project_collection(project_id: int, embedding_model: str) -> chromadb.Collection:
    """
    Gets or creates a ChromaDB collection for a project.
    
    Args:
        project_id: Project database ID
        embedding_model: Embedding model name (e.g., "text-embedding-3-small")
    
    Returns:
        ChromaDB Collection
    """
    client = get_chroma_client()
    collection_name = f"project_{project_id}"
    
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        collection = client.create_collection(
            name=collection_name,
            metadata={
                "embedding_model": embedding_model,
                "project_id": str(project_id)
            }
        )
    
    return collection


def add_chunks_to_collection(
    collection,
    document_id: int,
    chunks: list[str],
    embeddings: list[list[float] | None]
) -> None:
    """
    Adds document chunks to ChromaDB collection with embeddings.
    
    Args:
        collection: ChromaDB collection
        document_id: Document DB ID
        chunks: List of chunk texts
        embeddings: List of embeddings (can contain None for chunks without embeddings)
    """
    chunk_ids = []
    chunk_embeddings = []
    chunk_texts = []
    chunk_metadatas = []
    
    for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
        chunk_id = f"doc_{document_id}_chunk_{idx}"
        chunk_ids.append(chunk_id)
        chunk_texts.append(chunk_text)
        chunk_metadatas.append({
            "document_id": str(document_id),
            "chunk_index": str(idx)
        })
        
        if embedding:
            chunk_embeddings.append(embedding)
        else:
            chunk_embeddings.append(None)
    
    collection.add(
        ids=chunk_ids,
        embeddings=chunk_embeddings,
        documents=chunk_texts,
        metadatas=chunk_metadatas
    )


def query_collection(
    collection,
    query_embedding: list[float],
    top_k: int = 5
) -> list[dict]:
    """
    Queries ChromaDB collection for similar chunks.
    
    Args:
        collection: ChromaDB collection
        query_embedding: Query embedding vector
        top_k: Number of top results to return
    
    Returns:
        List of dicts: [{"chunk_text": "...", "similarity": 0.8, "source_doc_id": 1, "chunk_id": "..."}, ...]
    """
    if not query_embedding:
        return []
    
    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k
        )
        
        scored_chunks = []
        if results and results["documents"] and len(results["documents"]) > 0:
            for doc, metadata, distance in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0] if results.get("distances") else []
            ):
                similarity = 1 - distance
                scored_chunks.append({
                    "chunk_text": doc,
                    "similarity": similarity,
                    "source_doc_id": int(metadata.get("document_id", 0)),
                    "chunk_id": metadata.get("chunk_index", "0")
                })
        
        return scored_chunks
    except Exception as e:
        return []


def delete_document_from_collection(
    collection,
    document_id: int
) -> None:
    """
    Deletes all chunks of a document from ChromaDB collection.
    
    Args:
        collection: ChromaDB collection
        document_id: Document DB ID
    """
    try:
        results = collection.get(
            where={"document_id": str(document_id)}
        )
        if results and results["ids"]:
            collection.delete(ids=results["ids"])
    except Exception:
        pass


def delete_collection(project_id: int) -> None:
    """Deletes entire ChromaDB collection for a project."""
    try:
        client = get_chroma_client()
        collection_name = f"project_{project_id}"
        client.delete_collection(name=collection_name)
    except Exception:
        pass
