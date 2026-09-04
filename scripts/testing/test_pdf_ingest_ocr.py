"""
Tests für PDF-Ingest mit OCR/Vision-Fallback (Ticket 12).
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m09_docs import (
    IngestResult,
    extract_pdf_text_with_fallback,
    _ingest_status_from_meta,
    pdf_extracted_text_is_sparse,
)
from src.m11_vision import pdf_extracted_text_is_sparse as vision_sparse


def test_pdf_extracted_text_is_sparse():
    assert pdf_extracted_text_is_sparse("")
    assert pdf_extracted_text_is_sparse("kurz")
    assert not pdf_extracted_text_is_sparse("x" * 100)
    assert vision_sparse("x" * 50, min_chars=80)


def test_ingest_status_from_meta():
    assert _ingest_status_from_meta({}, has_chunks=True) == "ok"
    assert _ingest_status_from_meta({"ocr_success": True}, has_chunks=True) == "ocr_ok"
    assert _ingest_status_from_meta({"vision_success": True}, has_chunks=True) == "vision_ok"
    assert _ingest_status_from_meta({"ocr_attempted": True}, has_chunks=False) == "ocr_failed"
    assert _ingest_status_from_meta({}, has_chunks=False) == "no_text"


def test_extract_pdf_native_ok(tmp_path):
    pdf = tmp_path / "digital.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    long_text = "Dies ist ein durchsuchbares PDF mit genug Text für die Indexierung. " * 3

    with patch("src.m09_docs.extract_text_from_pdf", return_value=long_text):
        text, meta = extract_pdf_text_with_fallback(pdf, use_vision_fallback=False)

    assert text == long_text
    assert meta["extraction_method"] == "native"
    assert not meta["ocr_attempted"]


def test_extract_pdf_ocr_fallback(tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF scan")
    sparse = "ab"
    ocr_text = "Nach OCR ist hier genug Text für die Volltextsuche im Dokument vorhanden."
    state = {"pass": 0}

    def fake_extract(path, **kwargs):
        return ocr_text if state["pass"] >= 2 else sparse

    def fake_ocr(path):
        state["pass"] = 2
        return path

    with patch("src.m09_docs.extract_text_from_pdf", side_effect=fake_extract):
        with patch("src.m11_vision.is_pdf_scanned", return_value=True):
            with patch("src.m11_vision.apply_ocr_to_pdf", side_effect=fake_ocr):
                text, meta = extract_pdf_text_with_fallback(pdf, use_vision_fallback=False)

    assert "Nach OCR" in text
    assert meta["ocr_success"] is True
    assert meta["extraction_method"] == "ocrmypdf"


def test_extract_pdf_vision_fallback(tmp_path):
    pdf = tmp_path / "scan2.pdf"
    pdf.write_bytes(b"%PDF scan2")
    vision_text = "Vision-LLM hat den gescannten Text zuverlässig aus den Seitenbildern extrahiert."

    with patch("src.m09_docs.extract_text_from_pdf", return_value=""):
        with patch("src.m11_vision.is_pdf_scanned", return_value=True):
            with patch("src.m11_vision.apply_ocr_to_pdf", return_value=None):
                with patch("src.m09_docs._extract_pdf_text_via_vision", return_value=vision_text):
                    text, meta = extract_pdf_text_with_fallback(pdf, use_vision_fallback=True)

    assert text == vision_text
    assert meta["vision_success"] is True
    assert meta["extraction_method"] == "vision_llm"


def test_extract_pdf_all_fail(tmp_path):
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"%PDF empty")

    with patch("src.m09_docs.extract_text_from_pdf", return_value=""):
        with patch("src.m11_vision.is_pdf_scanned", return_value=True):
            with patch("src.m11_vision.apply_ocr_to_pdf", return_value=None):
                with patch("src.m09_docs._extract_pdf_text_via_vision", return_value=""):
                    text, meta = extract_pdf_text_with_fallback(pdf, use_vision_fallback=True)

    assert text == ""
    assert meta["ocr_attempted"] is True
    assert meta["vision_attempted"] is True
    assert not meta["ocr_success"]
    assert not meta["vision_success"]


def test_ingest_result_backward_compat():
    r = IngestResult(True, "ok msg", "ok")
    ok, msg = r
    assert ok is True
    assert msg == "ok msg"
    assert r.status == "ok"


def _run_all():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        tests = [
            ("sparse", test_pdf_extracted_text_is_sparse),
            ("status", test_ingest_status_from_meta),
            ("compat", test_ingest_result_backward_compat),
            ("native", lambda: test_extract_pdf_native_ok(base)),
            ("ocr", lambda: test_extract_pdf_ocr_fallback(base)),
            ("vision", lambda: test_extract_pdf_vision_fallback(base)),
            ("fail", lambda: test_extract_pdf_all_fail(base)),
        ]
        for name, fn in tests:
            fn()
            print(f"✅ {name}")


if __name__ == "__main__":
    _run_all()
    print("\nAlle PDF-OCR-Tests bestanden.")
