#!/usr/bin/env python
"""Tests PII Stufe 2 Integration (ohne echte Flair-Modelle)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m16_idea_visual import sanitize_for_cloud_text
from src.m18_cloud_pii import (
    apply_swiss_pii_sanitize,
    pii_sanitize_enabled,
    warmup_pii_analyzer,
)
import src.m18_cloud_pii as m18_cloud_pii


def _reset_pii_state() -> None:
    m18_cloud_pii._analyzer_ready = False
    m18_cloud_pii._circuit_open_until = 0.0
    m18_cloud_pii._pii_warned = False


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


def test_circuit_breaker_skips_repeated_load():
    _reset_pii_state()
    with patch(
        "swiss_pii_anonymizer.engine.get_analyzer",
        side_effect=RuntimeError("hf unreachable"),
    ) as mock_get:
        out1 = apply_swiss_pii_sanitize("Maria Muster")
        out2 = apply_swiss_pii_sanitize("Hans Beispiel")
        assert out1 == "Maria Muster"
        assert out2 == "Hans Beispiel"
        mock_get.assert_called_once()


def test_warmup_calls_ensure_once():
    _reset_pii_state()
    fake_result = MagicMock()
    fake_result.text = "Maria Muster"
    with patch(
        "swiss_pii_anonymizer.engine.get_analyzer",
        return_value=object(),
    ) as mock_get:
        with patch("swiss_pii_anonymizer.anonymize", return_value=fake_result):
            assert warmup_pii_analyzer() is True
            apply_swiss_pii_sanitize("Maria Muster")
            mock_get.assert_called_once()


if __name__ == "__main__":
    test_pii_enabled_default()
    test_sanitize_stage2_called()
    test_apply_swiss_pii_fallback_without_package()
    test_circuit_breaker_skips_repeated_load()
    test_warmup_calls_ensure_once()
    print("ok")
