#!/usr/bin/env python
"""Tests für Projektideen-Visualisierung (DSGVO-Prompt-Filter)."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m16_idea_visual import (
    sanitize_for_cloud_text,
    sanitize_structured_field,
    build_dsgvo_illustration_prompt,
    _nfc_text,
    _resolve_diagram_font_path,
    build_vertical_process_diagram_png,
)
from src.m16_idea import ProjectIdea


def test_sanitize_removes_email():
    raw = "Kontakt herr.schmidt@beispiel.ch wegen Budget."
    out = sanitize_for_cloud_text(raw)
    assert "@" not in out


def test_nfc_normalizes_decomposed_umlaut():
    assert _nfc_text("A\u0308nderungen") == "Änderungen"


def test_diagram_font_resolves_on_system():
    # DejaVu on Linux, Arial on Windows — either is fine; None only if no TTF at all
    path = _resolve_diagram_font_path()
    assert path is None or path.endswith(".ttf")


def test_vertical_diagram_png_with_umlauts():
    details = [
        {
            "title": "Überwachung",
            "bullets": ["Änderungen steuern", "Qualität prüfen"],
            "parallel_note": "",
        }
    ]
    png = build_vertical_process_diagram_png(details, "Prüfung")
    assert len(png) > 500


def test_structured_field_keeps_german_product_name():
    name = sanitize_structured_field("Digitaler Sportpass")
    assert "Digitaler Sportpass" == name
    assert "[Name entfernt]" not in name


def test_cloud_sanitize_no_false_positive_product_names():
    with patch.dict("os.environ", {"SWISS_PII_ANONYMIZER": "0"}):
        out = sanitize_for_cloud_text("Maria Muster plant den Digitalen Sportpass.")
    assert "[Name entfernt]" not in out
    assert "Digitaler Sportpass" in out


def test_fallback_prompt_excludes_raw_idea_text():
    idea = ProjectIdea(
        id=1,
        idea_text="Sensible Rohtext mit Herr Hans Zimmer — nur lokal.",
        status="bewertet",
        ai_project_name="Digitaler Sportpass",
        fachabteilung="Unisport",
        ai_summary="Modernisierung der Anmeldung.",
    )
    prompt = build_dsgvo_illustration_prompt(idea, "none", "")
    assert "Hans" not in prompt
    assert "Sensible Rohtext" not in prompt
    assert "Sportpass" in prompt or "public administration" in prompt.lower()


def test_is_cloud_llm_provider():
    from src.m16_idea_visual import is_cloud_llm_provider

    assert is_cloud_llm_provider("openai")
    assert is_cloud_llm_provider("anthropic")
    assert not is_cloud_llm_provider("ollama")


def test_validate_assess_cloud_gate():
    from src.m16_idea_visual import validate_assess_cloud_gates
    from src.m17_visual_lab_refs import DEFAULT_SOURCE_TASKS

    idea = ProjectIdea(
        id=1,
        idea_text="Test",
        source_reference_text="Budget intern",
        source_attachments_json='[{"path":"x.pdf","kind":"pdf"}]',
    )
    err = validate_assess_cloud_gates(
        idea, "openai", "gpt-4o-mini", "openai", set(DEFAULT_SOURCE_TASKS), False, False,
    )
    assert err == "cloud_confirm"
    ok = validate_assess_cloud_gates(
        idea, "ollama", "qwen3:32b", "ollama", set(DEFAULT_SOURCE_TASKS), False, False,
    )
    assert ok is None


def test_build_user_prompt_cloud_sanitizes_email():
    from src.m16_idea import _build_user_prompt
    from src.m17_visual_lab_refs import DEFAULT_SOURCE_TASKS

    idea = ProjectIdea(id=2, idea_text="Mail herr.test@beispiel.ch")
    out = _build_user_prompt(idea, set(DEFAULT_SOURCE_TASKS), assess_cloud=True)
    assert "@" not in out


def test_max_attachments_is_ten():
    from src.m17_visual_lab_refs import MAX_ATTACHMENTS
    assert MAX_ATTACHMENTS == 10


def test_clean_phase_title_strips_numbering():
    from src.m16_idea_visual import _clean_phase_title
    assert _clean_phase_title("1. Anforderungsdefinition (2-3 Monate)") == "Anforderungsdefinition (2-3 Monate)"
    assert _clean_phase_title("Phase 2: Systemdesign") == "Systemdesign"
    assert _clean_phase_title("Monitoring") == "Monitoring"


def test_html_report_has_nav_and_escapes():
    from src.m16_idea_visual import DeckContent, build_html_report
    html = build_html_report(DeckContent(
        title='Test <script>alert(1)</script>',
        subtitle="Team Kreditoren",
        summary_lines=["Einführung OCR"],
        phase_details=[{
            "title": "1. Anforderungsdefinition",
            "bullets": ["SAP-Szenarien klären"],
            "parallel_note": "2-3 Monate",
        }],
        recommendation_lines=["Weiterverfolgen"],
    ))
    assert "<script>alert(1)</script>" not in html
    assert "Test" in html
    assert 'id="summary"' in html
    assert 'id="phase-1"' in html
    assert "Anforderungsdefinition" in html
    assert "href=\"#phase-1\"" in html
    assert "SAP-Szenarien klären" in html


def test_vertical_diagram_does_not_truncate_long_bullet():
    from src.m16_idea_visual import _clean_phase_title, build_vertical_process_diagram_png
    details = [{
        "title": "1. Anforderungsdefinition (2-3 Monate)",
        "bullets": [
            "Erfassung von Prozessanforderungen, SAP-Integrationsszenarien und OCR-Funktionen."
        ],
        "parallel_note": "",
    }]
    png = build_vertical_process_diagram_png(details, "Kreditoren")
    assert len(png) > 800
    assert _clean_phase_title(details[0]["title"]).startswith("Anforderungsdefinition")


def test_list_source_attachment_views_keeps_saved_files():
    import json
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from src.m16_idea import list_source_attachment_views, source_preview_kind

    assert source_preview_kind("a.pdf", "pdf") == "pdf"
    assert source_preview_kind("foto.png", "image") == "image"
    assert source_preview_kind("note.txt", "text") == "text"

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "att_abc_brief.pdf").write_bytes(b"%PDF-1.4 mock")
        idea = ProjectIdea(
            id=9,
            idea_text="x",
            source_attachments_json=json.dumps([
                {
                    "path": "att_abc_brief.pdf",
                    "original_name": "Brief.pdf",
                    "kind": "pdf",
                    "bytes": 13,
                },
                {
                    "path": "att_missing.docx",
                    "original_name": "Alt.docx",
                    "kind": "docx",
                },
            ]),
        )
        with patch("src.m16_idea.idea_source_attachments_dir", return_value=base):
            views = list_source_attachment_views(idea)
        assert [v["original_name"] for v in views] == ["Brief.pdf", "Alt.docx"]
        assert views[0]["exists"] is True
        assert views[0]["previewable"] is True
        assert views[1]["exists"] is False
        assert views[1]["kind_label"] == "Word"


if __name__ == "__main__":
    test_sanitize_removes_email()
    test_nfc_normalizes_decomposed_umlaut()
    test_diagram_font_resolves_on_system()
    test_vertical_diagram_png_with_umlauts()
    test_structured_field_keeps_german_product_name()
    test_cloud_sanitize_no_false_positive_product_names()
    test_fallback_prompt_excludes_raw_idea_text()
    test_is_cloud_llm_provider()
    test_validate_assess_cloud_gate()
    test_build_user_prompt_cloud_sanitizes_email()
    test_max_attachments_is_ten()
    test_clean_phase_title_strips_numbering()
    test_html_report_has_nav_and_escapes()
    test_vertical_diagram_does_not_truncate_long_bullet()
    test_list_source_attachment_views_keeps_saved_files()
    print("ok")
