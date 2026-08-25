"""
scripts/maintenance/seed_retrieval_keywords.py
Retroaktives Seeding von DocumentChunk.retrieval_keywords via LLM.

Verarbeitet alle Chunks bei denen retrieval_keywords IS NULL.
Läuft sicher mehrfach (idempotent): bereits geseedete Chunks werden übersprungen.

Usage:
    python scripts/maintenance/seed_retrieval_keywords.py
    python scripts/maintenance/seed_retrieval_keywords.py --project-key <key>
    python scripts/maintenance/seed_retrieval_keywords.py --dry-run
    python scripts/maintenance/seed_retrieval_keywords.py --batch-size 20
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlmodel import select
from src.m03_db import init_db, get_session, DocumentChunk, Document, ProjectDocumentLink
from src.m08_llm import generate_chunk_keywords


def main():
    parser = argparse.ArgumentParser(description="Seed retrieval_keywords for DocumentChunks")
    parser.add_argument("--project-key", help="Nur Chunks dieses Projekts verarbeiten")
    parser.add_argument("--dry-run", action="store_true", help="Keine DB-Schreiboperationen")
    parser.add_argument("--limit", type=int, default=0, help="Max. Anzahl Chunks verarbeiten (0 = alle)")
    parser.add_argument("--force", action="store_true", help="Auch bereits geseedete Chunks neu verarbeiten")
    parser.add_argument("--batch-size", type=int, default=10, help="Chunks pro Batch (default: 10)")
    parser.add_argument("--provider", default="openai", help="LLM Provider (default: openai)")
    parser.add_argument("--model", default="gpt-4o-mini", help="LLM Model (default: gpt-4o-mini)")
    parser.add_argument("--delay", type=float, default=0.3, help="Sekunden zwischen API-Calls (default: 0.3)")
    args = parser.parse_args()

    init_db()

    with get_session() as ses:
        q = (
            select(DocumentChunk)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(Document.is_deleted == False)
        )
        if not args.force:
            q = q.where(DocumentChunk.retrieval_keywords == None)
        if args.project_key:
            q = q.join(
                ProjectDocumentLink,
                ProjectDocumentLink.document_id == Document.id
            ).where(ProjectDocumentLink.project_key == args.project_key)

        chunks = ses.exec(q).all()

    if args.limit > 0:
        chunks = chunks[:args.limit]

    total = len(chunks)
    if total == 0:
        print("Alle Chunks haben bereits retrieval_keywords. Nichts zu tun. (--force um neu zu seeden)")
        return

    if args.dry_run:
        sample = chunks[:5]
        print(f"[DRY-RUN] {total} Chunks zu verarbeiten. Zeige Sample ({len(sample)}) — keine DB-Writes.\n")
        for chunk in sample:
            text_preview = (chunk.chunk_text or "")[:80].replace("\n", " ")
            print(f"Chunk {chunk.id} | {text_preview}...")
            keywords = generate_chunk_keywords(chunk.chunk_text or "", provider=args.provider, model=args.model)
            print(f"  → {keywords}\n")
        return

    print(f"Zu verarbeiten: {total} Chunks")
    ok = 0
    err = 0

    for i, chunk in enumerate(chunks, 1):
        text_preview = (chunk.chunk_text or "")[:80].replace("\n", " ")
        print(f"[{i}/{total}] Chunk {chunk.id} | {text_preview}...")

        keywords = generate_chunk_keywords(
            chunk.chunk_text or "",
            provider=args.provider,
            model=args.model,
        )

        if not keywords:
            print(f"  → FEHLER: keine Keywords generiert, übersprungen")
            err += 1
            continue

        print(f"  → {keywords}")

        with get_session() as ses:
            db_chunk = ses.get(DocumentChunk, chunk.id)
            if db_chunk:
                db_chunk.retrieval_keywords = json.dumps(keywords, ensure_ascii=False)
                ses.add(db_chunk)
                ses.commit()
        ok += 1

        if args.delay > 0 and i < total:
            time.sleep(args.delay)

    print(f"\nFertig: {ok} geseedet, {err} Fehler, {total - ok - err} übersprungen")


if __name__ == "__main__":
    main()
