"""List documents whose filename contains a substring (maintenance CLI)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlmodel import select

from src.m03_db import Document, get_session


def main() -> int:
    parser = argparse.ArgumentParser(description="Dokumente nach Dateiname filtern")
    parser.add_argument(
        "--contains",
        required=True,
        help="Substring im Dateinamen (case-sensitive)",
    )
    args = parser.parse_args()
    needle = args.contains

    session = get_session()
    try:
        docs = session.exec(select(Document).where(Document.is_deleted == False)).all()
        matched = [d for d in docs if needle in d.filename]
        if not matched:
            print(f"Keine Dokumente mit '{needle}' im Namen.")
            return 1
        for doc in matched:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
