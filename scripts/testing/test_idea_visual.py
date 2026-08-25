#!/usr/bin/env python
"""Tests für Projektideen-Visualisierung (DSGVO-Prompt-Filter)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m16_idea_visual import (
    sanitize_for_cloud_text,
    sanitize_structured_field,
    build_dsgvo_illustration_prompt,
)
from src.m16_idea import ProjectIdea


def test_sanitize_removes_email():
    raw = "Kontakt herr.schmidt@beispiel.ch wegen Budget."
    out = sanitize_for_cloud_text(raw)
    assert "@" not in out


def test_structured_field_keeps_german_product_name():
    name = sanitize_structured_field("Digitaler Sportpass")
    assert "Digitaler Sportpass" == name
    assert "[Name entfernt]" not in name


def test_cloud_sanitize_strips_name_pairs_in_free_text():
    out = sanitize_for_cloud_text("Maria Muster plant den Digitalen Sportpass.")
    assert "Maria" not in out
    assert "[Name entfernt]" in out


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


if __name__ == "__main__":
    test_sanitize_removes_email()
    test_structured_field_keeps_german_product_name()
    test_cloud_sanitize_strips_name_pairs_in_free_text()
    test_fallback_prompt_excludes_raw_idea_text()
    print("ok")
