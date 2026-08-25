"""Embedding-Sample für ein Dokument anzeigen (maintenance CLI)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlmodel import select

from src.m03_db import Document, DocumentChunk, get_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Chunk-Sample für ein Dokument")
    parser.add_argument("--contains", required=True, help="Substring im Dateinamen")
    args = parser.parse_args()

    session = get_session()
    try:
        docs = session.exec(select(Document)).all()
        doc = next((d for d in docs if args.contains in d.filename), None)
        if not doc:
            print(f"Dokument mit '{args.contains}' nicht gefunden")
            return 1
        print(f"Dokument: {doc.filename}")
        print(f"  Embedding-Modell: {doc.embedding_model}")
        print(f"  Chunks: {doc.chunk_count}")
        print()

        chunks = session.exec(
            select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
        ).all()
        if chunks:
            print("Erste 3 Chunks (Sample):")
            for i, chunk in enumerate(chunks[:3]):
                print(f"\n  Chunk {i}:")
                print(f"    Länge: {len(chunk.chunk_text)} Zeichen")
                print(f"    Text: {chunk.chunk_text[:200]}...")
                print(f"    Hat Embedding: {chunk.embedding is not None}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
