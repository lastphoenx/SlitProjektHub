"""Dokument- und Text-Sanitizer für Cloud-Nutzung (Stufe 1+2 via m16/m18)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .m09_docs import extract_text_from_docx, extract_text_from_pdf
from .m16_idea_visual import sanitize_for_cloud_text, sanitize_structured_field
from .m18_cloud_pii import (
    apply_swiss_pii_sanitize,
    is_pii_analyzer_ready,
    pii_findings_for_preview,
    pii_sanitize_enabled,
)


_TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}


def pii_pipeline_status() -> dict[str, Any]:
    """Kurzinfo für UI: Stufe 2 aktiv / nur Regex-Fallback."""
    enabled = pii_sanitize_enabled()
    return {
        "stage2_enabled": enabled,
        "stage2_ready": enabled and is_pii_analyzer_ready(),
    }


def extract_text_from_bytes(file_name: str, file_bytes: bytes) -> tuple[bool, str]:
    """PDF/DOCX/TXT/MD → Plaintext. Kein RAG-Ingest."""
    if not file_bytes:
        return False, "Leere Datei."
    name = (file_name or "upload").strip()
    ext = Path(name).suffix.lower()
    if not ext:
        return False, "Dateiendung fehlt (z. B. .pdf, .docx, .txt)."

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_bytes)
        path = Path(tmp.name)
    try:
        if ext == ".pdf":
            text = extract_text_from_pdf(path)
        elif ext == ".docx":
            text = extract_text_from_docx(path)
        elif ext in _TEXT_EXTENSIONS:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="latin-1", errors="replace")
        else:
            return False, f"Dateityp {ext} nicht unterstützt (PDF, DOCX, TXT, MD)."
        text = (text or "").strip()
        if not text:
            return False, "Kein Text extrahiert — ggf. gescanntes PDF ohne OCR."
        return True, text
    finally:
        path.unlink(missing_ok=True)


def sanitize_plaintext(
    text: str,
    *,
    full_pipeline: bool = True,
) -> dict[str, Any]:
    """Sanitize + erkannte Entitäten (Vorschau). full_pipeline=False = nur Regex-Stufe 1."""
    raw = (text or "").strip()
    if not raw:
        return {
            "original": "",
            "sanitized": "",
            "findings": [],
            "char_count": 0,
            "reduction": 0,
        }
    if full_pipeline:
        sanitized = sanitize_for_cloud_text(raw)
        findings = pii_findings_for_preview(raw)
    else:
        t = sanitize_structured_field(raw)
        sanitized = apply_swiss_pii_sanitize(t) if pii_sanitize_enabled() else t
        findings = []
    return {
        "original": raw,
        "sanitized": sanitized,
        "findings": findings,
        "char_count": len(raw),
        "reduction": max(0, len(raw) - len(sanitized)),
    }
