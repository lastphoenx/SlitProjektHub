"""Dokument- und Text-Sanitizer für Cloud-Nutzung (Stufe 1+2 via m16/m18)."""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .m09_docs import extract_text_from_docx, extract_text_from_pdf
from .m16_idea_visual import _EMAIL_RE, _PERSON_LINE_RE, _PHONE_RE
from .m18_cloud_pii import (
    apply_swiss_pii_anonymize_details,
    is_pii_analyzer_ready,
    pii_sanitize_enabled,
)

log = logging.getLogger(__name__)

_TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}
_DEFAULT_MAX_CHARS = 50_000
_DEFAULT_MAX_PDF_PAGES = 20
_DEFAULT_MAX_FILE_BYTES = 15 * 1024 * 1024

# Dokument-Stufe 1: strukturierte CH-PII (ohne Paar-Grossbuchstaben-Heuristik — die trifft Firmennamen)
_CH_UID_RE = re.compile(r"CHE[-–]?\d{3}\.\d{3}\.\d{3}", re.IGNORECASE)
_STREET_RE = re.compile(
    r"\b[A-ZÄÖÜ][\wäöüß-]*(?:strasse|straße|str\.|gasse|weg|platz|allee|ring)\s+\d+\w?\b",
    re.IGNORECASE,
)
_PLZ_CITY_RE = re.compile(
    r"\b\d{4}\s+[A-ZÄÖÜ][a-zäöüß-]+(?:\s+[A-ZÄÖÜ][a-zäöüß-]+)?\b"
)
_CONTACT_PERSON_RE = re.compile(
    r"(?:Kontaktperson|Ansprechpartner|Contact)\s*:?\s*"
    r"[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?",
    re.IGNORECASE,
)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def sanitize_max_chars() -> int:
    return _env_int("SANITIZE_MAX_CHARS", _DEFAULT_MAX_CHARS, minimum=2000)


def sanitize_max_pdf_pages() -> int:
    return _env_int("SANITIZE_MAX_PDF_PAGES", _DEFAULT_MAX_PDF_PAGES, minimum=1)


def sanitize_max_file_bytes() -> int:
    return _env_int("SANITIZE_MAX_FILE_BYTES", _DEFAULT_MAX_FILE_BYTES, minimum=1024 * 1024)


def _normalize_doc_whitespace(text: str) -> str:
    """Zeilenumbrüche behalten — nur horizontale Leerzeichen pro Zeile normalisieren."""
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())


def sanitize_document_stage1(text: str) -> str:
    """Regex für Offerten/Dokumente — ohne _NAME_PAIR_RE (Firmennamen bleiben erhalten)."""
    if not text:
        return ""
    t = text
    t = _EMAIL_RE.sub("[EMAIL_ADDRESS]", t)
    t = _PHONE_RE.sub("[CH_PHONE_NUMBER]", t)
    t = _CH_UID_RE.sub("[CH_UID]", t)
    t = _STREET_RE.sub("[ADDRESS]", t)
    t = _PLZ_CITY_RE.sub("[LOCATION]", t)
    t = _PERSON_LINE_RE.sub("[PERSON]", t)
    t = _CONTACT_PERSON_RE.sub("[PERSON]", t)
    return _normalize_doc_whitespace(t)


def sanitize_document_for_cloud_with_meta(text: str) -> tuple[str, list[dict[str, str | float]]]:
    """Dokument-Pipeline: Stage1 ohne Namens-Paar-Heuristik + Presidio/Flair."""
    if not text:
        return "", []
    t = sanitize_document_stage1(text)
    return apply_swiss_pii_anonymize_details(t)


def resolve_pdf_page_limit(requested: int | None) -> int:
    cap = sanitize_max_pdf_pages()
    if requested is None or requested <= 0:
        return cap
    return min(requested, cap)


def pii_pipeline_status() -> dict[str, Any]:
    """Kurzinfo für UI: Stufe 2 aktiv / nur Regex-Fallback."""
    enabled = pii_sanitize_enabled()
    return {
        "stage2_enabled": enabled,
        "stage2_ready": enabled and is_pii_analyzer_ready(),
        "max_chars": sanitize_max_chars(),
        "max_pdf_pages": sanitize_max_pdf_pages(),
    }


def _cap_text(text: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    raw = (text or "").strip()
    if not raw:
        return "", warnings
    limit = sanitize_max_chars()
    if len(raw) > limit:
        warnings.append(
            f"Text auf {limit:,} Zeichen gekürzt (extrahiert: {len(raw):,}). "
            f"Für lange Offerten ggf. `SANITIZE_MAX_CHARS` in `.env` erhöhen (RAM beachten)."
        )
        raw = raw[:limit]
    return raw, warnings


def extract_text_from_bytes(
    file_name: str,
    file_bytes: bytes,
    *,
    max_pdf_pages: int | None = None,
) -> tuple[bool, str, list[str]]:
    """PDF/DOCX/TXT/MD → Plaintext. Kein RAG-Ingest."""
    warnings: list[str] = []
    if not file_bytes:
        return False, "Leere Datei.", warnings
    if len(file_bytes) > sanitize_max_file_bytes():
        mb = sanitize_max_file_bytes() // (1024 * 1024)
        return False, f"Datei zu gross (max. {mb} MB für Sanitizer).", warnings

    name = (file_name or "upload").strip()
    ext = Path(name).suffix.lower()
    if not ext:
        return False, "Dateiendung fehlt (z. B. .pdf, .docx, .txt).", warnings

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_bytes)
        path = Path(tmp.name)
    try:
        if ext == ".pdf":
            page_limit = resolve_pdf_page_limit(max_pdf_pages)
            text = extract_text_from_pdf(path, max_pages=page_limit)
            try:
                import pdfplumber

                with pdfplumber.open(path) as pdf:
                    total = len(pdf.pages)
                if total > page_limit:
                    warnings.append(
                        f"Nur die ersten {page_limit} von {total} PDF-Seiten extrahiert "
                        f"(Limit `SANITIZE_MAX_PDF_PAGES`, RAM-Schutz). "
                        f"Für mehr Seiten in `.env` erhöhen und erneut versuchen."
                    )
            except Exception:
                pass
        elif ext == ".docx":
            text = extract_text_from_docx(path)
        elif ext in _TEXT_EXTENSIONS:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="latin-1", errors="replace")
        else:
            return False, f"Dateityp {ext} nicht unterstützt (PDF, DOCX, TXT, MD).", warnings
        text = (text or "").strip()
        if not text:
            return False, "Kein Text extrahiert — ggf. gescanntes PDF ohne OCR.", warnings
        capped, cap_warnings = _cap_text(text)
        warnings.extend(cap_warnings)
        return True, capped, warnings
    finally:
        path.unlink(missing_ok=True)


def sanitize_plaintext(
    text: str,
    *,
    full_pipeline: bool = True,
) -> dict[str, Any]:
    """Sanitize + erkannte Entitäten (Vorschau). full_pipeline=False = nur Regex-Stufe 1."""
    raw, warnings = _cap_text(text)
    if not raw:
        return {
            "original": "",
            "sanitized": "",
            "findings": [],
            "char_count": 0,
            "reduction": 0,
            "warnings": warnings,
        }
    try:
        if full_pipeline:
            sanitized, findings = sanitize_document_for_cloud_with_meta(raw)
        else:
            t = sanitize_document_stage1(raw)
            sanitized, findings = (t, [])
    except Exception as exc:
        log.exception("sanitize_plaintext fehlgeschlagen (%d Zeichen)", len(raw))
        raise RuntimeError(
            "Sanitizer abgebrochen — vermutlich zu wenig RAM oder Text zu lang. "
            "Kürzeres Dokument oder `SANITIZE_MAX_CHARS` senken."
        ) from exc

    return {
        "original": raw,
        "sanitized": sanitized,
        "findings": findings,
        "char_count": len(raw),
        "reduction": max(0, len(raw) - len(sanitized)),
        "warnings": warnings,
    }


def run_sanitize_job(
    *,
    source_text: str = "",
    file_name: str | None = None,
    file_bytes: bytes | None = None,
    full_pipeline: bool = True,
    max_pdf_pages: int | None = None,
) -> tuple[str | None, dict[str, Any] | None, list[str]]:
    """Blocking-Job für Thread-Pool: (error, result, extract_warnings)."""
    label = "Eingabetext"
    text = (source_text or "").strip()
    warnings: list[str] = []
    error: str | None = None

    if file_bytes and file_name:
        ok, extracted, extract_warnings = extract_text_from_bytes(
            file_name, file_bytes, max_pdf_pages=max_pdf_pages
        )
        warnings.extend(extract_warnings)
        if not ok:
            error = extracted
            text = ""
        else:
            text = extracted
            label = file_name

    if not error and not text:
        error = "Bitte Text einfügen oder eine Datei hochladen."

    result = None
    if not error:
        result = sanitize_plaintext(text, full_pipeline=full_pipeline)
        result["input_label"] = label
        warnings.extend(result.get("warnings") or [])

    return error, result, warnings
