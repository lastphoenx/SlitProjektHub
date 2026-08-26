#!/usr/bin/env python
"""Tests PII Stufe 2 Integration (ohne echte Flair-Modelle)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m16_idea_visual import sanitize_for_cloud_text
from src.m18_cloud_pii import apply_swiss_pii_sanitize, pii_sanitize_enabled


def test_pii_enabled_default():
    assert pii_sanitize_enabled()


def test_sanitize_stage2_called():
    fake_result = MagicMock()
    fake_result.text = "Kontakt [PERSON] wegen Budget."
    with patch("swiss_pii_anonymizer.anonymize", return_value=fake_result) as mock_anon:
        out = sanitize_for_cloud_text("Kontakt herr.schmidt@beispiel.ch Maria Muster.")
        mock_anon.assert_called_once()
        assert "[PERSON]" in out
        assert "@" not in out


def test_apply_swiss_pii_fallback_without_package():
    with patch.dict("os.environ", {"SWISS_PII_ANONYMIZER": "0"}):
        assert apply_swiss_pii_sanitize("Maria Muster") == "Maria Muster"


if __name__ == "__main__":
    test_pii_enabled_default()
    test_sanitize_stage2_called()
    test_apply_swiss_pii_fallback_without_package()
    print("ok")
