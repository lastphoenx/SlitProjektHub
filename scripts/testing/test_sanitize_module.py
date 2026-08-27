#!/usr/bin/env python
"""Tests für m19_sanitize (Text-Extraktion + Pipeline-Anbindung)."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m19_sanitize import extract_text_from_bytes, sanitize_plaintext


def test_extract_text_from_txt():
    ok, text = extract_text_from_bytes("note.txt", b"Hallo Welt")
    assert ok
    assert text == "Hallo Welt"


def test_extract_text_empty():
    ok, msg = extract_text_from_bytes("x.txt", b"")
    assert not ok
    assert "Leere" in msg


def test_sanitize_plaintext_calls_pipeline():
    with patch("src.m19_sanitize.sanitize_for_cloud_text", return_value="[PERSON]") as mock_s:
        with patch(
            "src.m19_sanitize.pii_findings_for_preview",
            return_value=[{"entity_type": "PERSON", "text": "Max", "score": 0.9}],
        ):
            out = sanitize_plaintext("Max Muster", full_pipeline=True)
    mock_s.assert_called_once_with("Max Muster")
    assert out["sanitized"] == "[PERSON]"
    assert len(out["findings"]) == 1


def test_sanitize_plaintext_regex_only():
    with patch("src.m19_sanitize.sanitize_structured_field", return_value="clean") as mock_r:
        with patch("src.m19_sanitize.pii_sanitize_enabled", return_value=False):
            out = sanitize_plaintext("raw", full_pipeline=False)
    mock_r.assert_called_once_with("raw")
    assert out["sanitized"] == "clean"
    assert out["findings"] == []
