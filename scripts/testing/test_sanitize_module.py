#!/usr/bin/env python
"""Tests für m19_sanitize (Text-Extraktion + Pipeline-Anbindung)."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m19_sanitize import (
    extract_text_from_bytes,
    sanitize_document_stage1,
    sanitize_plaintext,
)


def test_extract_text_from_txt():
    ok, text, warnings = extract_text_from_bytes("note.txt", b"Hallo Welt")
    assert ok
    assert text == "Hallo Welt"
    assert warnings == []


def test_extract_text_empty():
    ok, msg, warnings = extract_text_from_bytes("x.txt", b"")
    assert not ok
    assert "Leere" in msg
    assert warnings == []


def test_sanitize_document_stage1_phone_and_uid():
    raw = (
        "Apps with love AG, Utengasse 52, 4058 Basel. "
        "Tel +41 (0)31 333 01 51, UID CHE-116.029.116, mail test@beispiel.ch"
    )
    out = sanitize_document_stage1(raw)
    assert "Apps with love" in out
    assert "[CH_PHONE_NUMBER]" in out
    assert "[CH_UID]" in out
    assert "[EMAIL_ADDRESS]" in out
    assert "[ADDRESS]" in out or "[LOCATION]" in out
    assert "+41" not in out


def test_sanitize_plaintext_calls_pipeline():
    with patch(
        "src.m19_sanitize.sanitize_document_for_cloud_with_meta",
        return_value=("[PERSON]", [{"entity_type": "PERSON", "text": "Max", "score": 0.9}]),
    ) as mock_s:
        out = sanitize_plaintext("Max Muster", full_pipeline=True)
    mock_s.assert_called_once_with("Max Muster")
    assert out["sanitized"] == "[PERSON]"
    assert len(out["findings"]) == 1


def test_sanitize_plaintext_truncates_long_text():
    with patch("src.m19_sanitize.sanitize_max_chars", return_value=10):
        with patch(
            "src.m19_sanitize.sanitize_document_for_cloud_with_meta",
            return_value=("short", []),
        ):
            out = sanitize_plaintext("x" * 100, full_pipeline=True)
    assert len(out["original"]) == 10
    assert out["warnings"]


def test_sanitize_plaintext_regex_only():
    with patch("src.m19_sanitize.sanitize_document_stage1", return_value="clean") as mock_r:
        out = sanitize_plaintext("raw", full_pipeline=False)
    mock_r.assert_called_once_with("raw")
    assert out["sanitized"] == "clean"
    assert out["findings"] == []
