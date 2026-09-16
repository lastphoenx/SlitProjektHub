#!/usr/bin/env python3
"""
Retroaktive PDF-Seitenvorschauen (WebP) für die Offertbeurteilung.

Findet Dokumente über die SQLite-DB (document.file_path) und rasterisiert fehlende
Seiten mit pdf2image/poppler. Kein Re-Chunk, keine Embedding-Änderung.

Usage (auf dem Server, als projekthub):
    cd /opt/slitprojekthub
    sudo -u projekthub .venv/bin/python scripts/maintenance/backfill_document_page_images.py
    sudo -u projekthub .venv/bin/python scripts/maintenance/backfill_document_page_images.py --dry-run
    sudo -u projekthub .venv/bin/python scripts/maintenance/backfill_document_page_images.py --force --limit 5

Voraussetzung: poppler-utils (pdftoppm) — siehe docs/tickets/pdf-ocr-ingest.md
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
from src.m03_db import Document, DocumentPage, init_db, get_session
from src.m09_docs import (
    extract_and_store_pdf_page_images,
    resolve_document_path,
)


def _default_log_path() -> Path:
    logs_dir = get_settings().data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return logs_dir / f"backfill-document-page-images-{ts}.log"


def _setup_logging(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("backfill_page_images")
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


def _is_pdf_document(doc: Document) -> bool:
    name = (doc.filename or "").lower()
    path_suffix = Path(doc.file_path or "").suffix.lower()
    return name.endswith(".pdf") or path_suffix == ".pdf"


def _pdf_documents(force: bool) -> list[Document]:
    with get_session() as session:
        docs = session.exec(
            select(Document)
            .where(Document.is_deleted == False)
            .order_by(Document.id)
        ).all()
        out: list[Document] = []
        for doc in docs:
            if not _is_pdf_document(doc):
                continue
            if force:
                out.append(doc)
                continue
            has_pages = session.exec(
                select(DocumentPage.id)
                .where(DocumentPage.document_id == doc.id)
                .limit(1)
            ).first()
            if has_pages is None:
                out.append(doc)
        return out


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF-Seitenvorschauen für bestehende Dokumente erzeugen")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nichts rasterisieren")
    parser.add_argument("--force", action="store_true", help="Auch PDFs mit bestehenden Vorschauen neu erzeugen")
    parser.add_argument("--limit", type=int, default=0, help="Max. Anzahl PDFs (0 = alle)")
    parser.add_argument("--document-id", type=int, default=0, help="Nur diese document.id")
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Log-Datei (Default: data/logs/backfill-document-page-images-<timestamp>.log)",
    )
    args = parser.parse_args()

    init_db()
    log_path = args.log_file or _default_log_path()
    log = _setup_logging(log_path)
    log.info("=== Backfill PDF-Seitenvorschauen ===")
    log.info("Log: %s", log_path)
    log.info("dry_run=%s force=%s limit=%s document_id=%s", args.dry_run, args.force, args.limit, args.document_id)

    if args.document_id:
        with get_session() as session:
            doc = session.get(Document, args.document_id)
            if not doc or doc.is_deleted:
                log.error("Dokument id=%s nicht gefunden oder gelöscht", args.document_id)
                return 1
            candidates = [doc]
    else:
        candidates = _pdf_documents(args.force)

    if args.limit > 0:
        candidates = candidates[: args.limit]

    log.info("Kandidaten: %d PDF(s)", len(candidates))
    if not candidates:
        log.info("Nichts zu tun.")
        return 0

    ok = 0
    skipped = 0
    failed = 0
    t0 = time.perf_counter()

    for doc in candidates:
        path = resolve_document_path(doc)
        if not path:
            log.warning("SKIP doc_id=%s — Datei fehlt (%s)", doc.id, doc.file_path)
            skipped += 1
            continue
        if path.suffix.lower() != ".pdf":
            log.info("SKIP doc_id=%s — kein PDF (%s)", doc.id, path.name)
            skipped += 1
            continue
        if args.dry_run:
            log.info("DRY-RUN doc_id=%s %s → %s", doc.id, doc.filename, path)
            ok += 1
            continue
        try:
            pages = extract_and_store_pdf_page_images(doc.id, path)
            if pages > 0:
                log.info("OK doc_id=%s %s — %d Seite(n) → data/rag/pages/%s/", doc.id, doc.filename, pages, doc.id)
                ok += 1
            else:
                log.warning("FAIL doc_id=%s %s — 0 Seiten (poppler/pdf2image?)", doc.id, doc.filename)
                failed += 1
        except Exception as exc:
            log.exception("FAIL doc_id=%s %s — %s", doc.id, doc.filename, exc)
            failed += 1

    elapsed = time.perf_counter() - t0
    log.info(
        "=== Fertig in %.1fs — ok=%d skipped=%d failed=%d (von %d) ===",
        elapsed, ok, skipped, failed, len(candidates),
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
