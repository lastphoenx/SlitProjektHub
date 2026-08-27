"""Gemeinsame Stufe-1-PII-Regex für alle Cloud-Sanitize-Pfade (m16, m15, m19, …).

Keine Zwei-Grossbuchstaben-Paar-Heuristik — die trifft Firmen-/Produktnamen fälschlich
(siehe swiss-pii-anonymizer README). Stufe 2 (Flair/Presidio) übernimmt Personen-NER.
"""
from __future__ import annotations

import re

# Kontakt & Identifikatoren
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
PHONE_RE = re.compile(
    r"(?:\+41|0041)\s*(?:\(0\)\s*)?[\d\s./()-]{8,}"
    r"|"
    r"0\d{2}\s*[\d\s./()-]{6,}"
    r"|"
    r"(?:Tel\.?|Telefon|Phone)\s*:?\s*(?:\+41|0041|0)[\d\s./()-]{6,}",
    re.IGNORECASE,
)
PERSON_LINE_RE = re.compile(
    r"(?:Herr|Frau|Dr\.|Prof\.)\s+[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?",
    re.IGNORECASE,
)
CONTACT_PERSON_RE = re.compile(
    r"(?:Kontaktperson|Ansprechpartner|Contact)\s*:?\s*"
    r"[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?",
    re.IGNORECASE,
)

# Schweiz-spezifisch (bis CH_UID/Adresse im Paket sind — siehe swiss-pii-anonymizer)
CH_UID_RE = re.compile(
    r"CHE[-–]?\d{3}[.\s]?\d{3}[.\s]?\d{3}\b",
    re.IGNORECASE,
)
STREET_RE = re.compile(
    r"\b[A-ZÄÖÜ][\wäöüß-]*(?:strasse|straße|str\.|gasse|weg|platz|allee|ring)\s+\d+\w?\b",
    re.IGNORECASE,
)
PLZ_CITY_RE = re.compile(
    r"\b\d{4}\s+[A-ZÄÖÜ][a-zäöüß-]+(?:\s+[A-ZÄÖÜ][a-zäöüß-]+)?\b"
)


def normalize_pii_whitespace(text: str, *, preserve_newlines: bool = True) -> str:
    if not text:
        return ""
    if preserve_newlines:
        return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return re.sub(r"\s+", " ", text).strip()


def apply_pii_stage1(text: str, *, preserve_newlines: bool = True) -> str:
    """Entitätsspezifische Regex-Ersetzung — einheitlich für Sanitizer, Offerte, Ideen, Visual-Lab."""
    if not text:
        return ""
    t = text
    t = EMAIL_RE.sub("[EMAIL_ADDRESS]", t)
    t = PHONE_RE.sub("[CH_PHONE_NUMBER]", t)
    t = CH_UID_RE.sub("[CH_UID]", t)
    t = STREET_RE.sub("[ADDRESS]", t)
    t = PLZ_CITY_RE.sub("[LOCATION]", t)
    t = PERSON_LINE_RE.sub("[PERSON]", t)
    t = CONTACT_PERSON_RE.sub("[PERSON]", t)
    return normalize_pii_whitespace(t, preserve_newlines=preserve_newlines)


def sanitize_structured_field(text: str) -> str:
    """Kurze Metadaten-Felder (Projektname, Fachbereich): PII entfernen, kein Token-Spam."""
    if not text:
        return ""
    t = EMAIL_RE.sub("", text)
    t = PHONE_RE.sub("", t)
    t = PERSON_LINE_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()
