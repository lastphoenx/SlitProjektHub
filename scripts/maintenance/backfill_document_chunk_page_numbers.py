#!/usr/bin/env python3
"""
document_chunk.page_number aus PDF-Seitentext nachziehen (Altdaten ohne S.-Metadaten).

Ohne page_number zeigt die Offertbeurteilung keine Seitenvorschau-Thumbnails,
auch wenn WebP-Bilder (backfill_document_page_images.py) existieren.

Usage:
    sudo -u projekthub .venv/bin/python scripts/maintenance/backfill_document_chunk_page_numbers.py --dry-run
    sudo -u projekthub .venv/bin/python scripts/maintenance/backfill_document_chunk_page_numbers.py
    sudo -u projekthub .venv/bin/python scripts/maintenance/backfill_document_chunk_page_numbers.py --document-id 12
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlmodel import select

from src.m01_config import get_settings
from src.m03_db import Document, DocumentChunk, init_db, get_session
from src.m09_docs import backfill_document_chunk_page_numbers, resolve_document_path


def _default_log_path() -> Path:
    logs_dir = get_settings().data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return logs_dir / f"backfill-chunk-page-numbers-{ts}.log"


def _setup_logging(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("backfill_chunk_pages")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def _pdf_doc_ids(missing_only: bool) -> list[int]:
    with get_session() as session:
        docs = session.exec(
            select(Document).where(Document.is_deleted == False).order_by(Document.id)
        ).all()
        out: list[int] = []
        for doc in docs:
            path = resolve_document_path(doc)
            if not path or path.suffix.lower() != ".pdf":
                continue
            if missing_only:
                null_page = session.exec(
                    select(DocumentChunk.id)
                    .where(
                        DocumentChunk.document_id == doc.id,
                        DocumentChunk.page_number == None,
                    )
                    .limit(1)
                ).first()
                if null_page is None:
                    continue
            out.append(int(doc.id))
        return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Chunk page_number aus PDF-Seiten nachziehen")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Auch Chunks mit bestehender page_number neu zuordnen")
    parser.add_argument("--document-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--log-file", type=Path, default=None)
    args = parser.parse_args()

    init_db()
    log_path = args.log_file or _default_log_path()
    log = _setup_logging(log_path)
    log.info("=== Backfill chunk.page_number ===")
    log.info("Log: %s", log_path)

    if args.document_id:
        doc_ids = [args.document_id]
    else:
        doc_ids = _pdf_doc_ids(missing_only=not args.force)
    if args.limit > 0:
        doc_ids = doc_ids[: args.limit]

    log.info("PDF-Dokumente: %d", len(doc_ids))
    total_up = total_skip = total_fail = 0
    t0 = time.perf_counter()

    with get_session() as session:
        for doc_id in doc_ids:
            doc = session.get(Document, doc_id)
            name = doc.filename if doc else "?"
            up, skip, fail = backfill_document_chunk_page_numbers(
                doc_id, dry_run=args.dry_run, force=args.force,
            )
            total_up += up
            total_skip += skip
            total_fail += fail
            if fail:
                log.warning("FAIL doc_id=%s %s", doc_id, name)
            else:
                log.info(
                    "%s doc_id=%s %s — updated=%d skipped=%d",
                    "DRY-RUN" if args.dry_run else "OK",
                    doc_id, name, up, skip,
                )

    log.info(
        "=== Fertig in %.1fs — updated=%d skipped=%d failed=%d ===",
        time.perf_counter() - t0, total_up, total_skip, total_fail,
    )
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
