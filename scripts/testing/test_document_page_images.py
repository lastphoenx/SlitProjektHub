"""
Tests für PDF-Seitenvorschau beim Ingest (Offertbeurteilung Quellen-UI).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m09_docs import (
    delete_document_page_images,
    extract_and_store_pdf_page_images,
    infer_chunk_page_number,
    infer_chunk_page_number_from_neighbors,
    resolve_document_page_image_path,
)


def test_infer_chunk_page_number():
    segments = [
        {"page_number": 1, "text": "Einleitung und Überblick über das Projekt."},
        {"page_number": 2, "text": "F02-001 Nutzerfreundliche Darstellung der Geschäftsprozesse im Portal."},
    ]
    chunk = (
        "[Anforderung/Feature | vorgabe.pdf]\n"
        "F02-001 Nutzerfreundliche Darstellung der Geschäftsprozesse im Portal mit Beispielen."
    )
    assert infer_chunk_page_number(chunk, segments) == 2

    by_ref_only = "[x]\nF02-001 Kurz"
    assert infer_chunk_page_number(by_ref_only, segments) == 2


def test_infer_chunk_page_number_mid_body_probe():
    segments = [
        {"page_number": 3, "text": "Kapitel Einführung und Rahmenbedingungen des Projekts."},
        {
            "page_number": 4,
            "text": "Die Anforderung an die Benutzeroberfläche umfasst klare Navigation und Filter.",
        },
    ]
    filler = "Allgemeine Hinweise ohne Treffer. " * 8
    chunk = (
        "[x | doc.pdf]\n"
        + filler
        + "Die Anforderung an die Benutzeroberfläche umfasst klare Navigation und Filter."
    )
    assert infer_chunk_page_number(chunk, segments) == 4


def test_infer_chunk_page_number_cross_page():
    segments = [
        {"page_number": 7, "text": "Ende von Abschnitt A mit wichtigen Details zum Thema."},
        {"page_number": 8, "text": "Beginn Abschnitt B: Vertragliche Regelungen und Fristen gelten."},
    ]
    chunk = (
        "[x]\n"
        "Ende von Abschnitt A mit wichtigen Details zum Thema. "
        "Beginn Abschnitt B: Vertragliche Regelungen und Fristen gelten."
    )
    assert infer_chunk_page_number(chunk, segments) == 7


def test_infer_chunk_page_number_from_neighbors():
    known = [5, 5, None, 5, None, 6, 8]
    assert infer_chunk_page_number_from_neighbors(2, known) == 5
    assert infer_chunk_page_number_from_neighbors(4, known) == 5
    assert infer_chunk_page_number_from_neighbors(5, known) is None
    boundary = [3, None, 4]
    assert infer_chunk_page_number_from_neighbors(1, boundary) == 3


def _fake_save_page(pil, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"RIFFFAKEWEBP")
    return 800, 1100


def test_extract_and_store_pdf_page_images(tmp_path, monkeypatch):
    from src.m03_db import Document, get_session

    monkeypatch.setattr("src.m09_docs.PAGES_DIR", tmp_path / "pages")
    (tmp_path / "pages").mkdir(parents=True)

    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    fake_img = MagicMock()

    with patch("pdf2image.convert_from_path", return_value=[fake_img, fake_img]):
        with patch("src.m09_docs._save_page_preview_image", side_effect=_fake_save_page):
            with get_session() as session:
                doc = Document(
                    filename="sample.pdf",
                    sha256_hash="abc123",
                    classification="Angebot (Bieter)",
                    file_path=str(pdf),
                    file_size=10,
                )
                session.add(doc)
                session.commit()
                session.refresh(doc)
                doc_id = doc.id

            count = extract_and_store_pdf_page_images(doc_id, pdf)
            assert count == 2
            assert resolve_document_page_image_path(doc_id, 1) is not None
            assert resolve_document_page_image_path(doc_id, 2) is not None
            delete_document_page_images(doc_id)
            assert resolve_document_page_image_path(doc_id, 1) is None


if __name__ == "__main__":
    test_infer_chunk_page_number()
    test_infer_chunk_page_number_mid_body_probe()
    test_infer_chunk_page_number_cross_page()
    test_infer_chunk_page_number_from_neighbors()
    print("OK")
