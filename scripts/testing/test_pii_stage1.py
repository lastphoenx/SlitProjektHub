#!/usr/bin/env python
"""Tests für gemeinsame PII-Stufe-1 (m20_pii_stage1)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m20_pii_stage1 import apply_pii_stage1, sanitize_structured_field

# Auszug-Stil Unisport/Apps-with-love-Angebot (vereinfacht)
_OFFER_SNIPPET = (
    "Apps with love AG, Utengasse 52, 4058 Basel. "
    "Landoltstrasse 63, 3007 Bern. Gründungsjahr 2010. "
    "Kontaktperson: Stephan Klaus. E-Mail info@appswithlove.ch. "
    "Tel +41 (0)31 333 01 51. UID CHE-116.029.116."
)


def test_stage1_keeps_company_name():
    out = apply_pii_stage1(_OFFER_SNIPPET)
    assert "Apps with love" in out
    assert "[Name entfernt]" not in out


def test_stage1_redacts_phone_uid_address():
    out = apply_pii_stage1(_OFFER_SNIPPET)
    assert "[CH_PHONE_NUMBER]" in out
    assert "+41" not in out
    assert "[CH_UID]" in out
    assert "CHE-116.029.116" not in out
    assert "[EMAIL_ADDRESS]" in out
    assert "@" not in out
    assert "Utengasse 52" not in out
    assert "4058 Basel" not in out or "[LOCATION]" in out


def test_stage1_contact_person():
    out = apply_pii_stage1("Ansprechpartner Maria Muster für das Projekt.")
    assert "Maria Muster" not in out
    assert "[PERSON]" in out


def test_structured_field_keeps_product_name():
    assert sanitize_structured_field("Digitaler Sportpass") == "Digitaler Sportpass"


def test_phone_with_parentheses():
    out = apply_pii_stage1("Ruf +41 (0)31 333 01 51 an.")
    assert "[CH_PHONE_NUMBER]" in out
    assert "333 01 51" not in out


def test_uid_before_phone():
    out = apply_pii_stage1("UID CHE-116.029.116, Tel +41 31 333 01 51.")
    assert "[CH_UID]" in out
    assert "CHE-116" not in out
    assert "[CH_PHONE_NUMBER]" in out


def test_date_range_not_phone():
    out = apply_pii_stage1("Projektzeitraum 07/2021 – 08/2023.")
    assert "[CH_PHONE_NUMBER]" not in out
    assert "07/2021" in out
