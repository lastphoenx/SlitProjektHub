#!/usr/bin/env python
"""Tests für m19_sanitize (Text-Extraktion + Pipeline-Anbindung)."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m19_sanitize import extract_text_from_bytes, sanitize_plaintext


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


def test_sanitize_plaintext_calls_pipeline():
    with patch(
        "src.m19_sanitize.sanitize_for_cloud_with_meta",
        return_value=("[PERSON]", [{"entity_type": "PERSON", "text": "Max", "score": 0.9}]),
    ) as mock_s:
        out = sanitize_plaintext("Max Muster", full_pipeline=True)
    mock_s.assert_called_once_with("Max Muster")
    assert out["sanitized"] == "[PERSON]"
    assert len(out["findings"]) == 1


def test_sanitize_plaintext_truncates_long_text():
    with patch("src.m19_sanitize.sanitize_max_chars", return_value=10):
        with patch(
            "src.m19_sanitize.sanitize_for_cloud_with_meta",
            return_value=("short", []),
        ):
            out = sanitize_plaintext("x" * 100, full_pipeline=True)
    assert len(out["original"]) == 10
    assert out["warnings"]


def test_sanitize_plaintext_regex_only():
    with patch("src.m19_sanitize.sanitize_structured_field", return_value="clean") as mock_r:
        with patch("src.m19_sanitize.pii_sanitize_enabled", return_value=False):
            out = sanitize_plaintext("raw", full_pipeline=False)
    mock_r.assert_called_once_with("raw")
    assert out["sanitized"] == "clean"
    assert out["findings"] == []
