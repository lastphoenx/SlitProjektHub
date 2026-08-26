#!/usr/bin/env python3
"""Lädt spaCy lg + Flair-NER für swiss-pii-anonymizer (Server-Vorbereitung).

Auf dem Server einmalig nach pip install -r requirements.txt:

    .venv/bin/python scripts/maintenance/prefetch_pii_models.py

Benötigt Internetzugang (~400 MB). Ohne Netz: Modelle von anderer Maschine kopieren
(~/.flair/ und spaCy-Paket de_core_news_lg).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    print("spaCy de_core_news_lg (Presidio-NLP für PII) …")
    subprocess.run(
        [sys.executable, "-m", "spacy", "download", "de_core_news_lg"],
        check=True,
    )
    print("Flair flair/ner-german-large …")
    from flair.models import SequenceTagger

    SequenceTagger.load("flair/ner-german-large")
    print("Smoke-Test swiss-pii-anonymizer …")
    from swiss_pii_anonymizer import anonymize

    r = anonymize("Kontakt Maria Muster, AHV 756.1234.5678.97")
    print("  Ergebnis:", r.text[:120])
    print("OK — PII-Stufe 2 bereit.")


if __name__ == "__main__":
    main()
