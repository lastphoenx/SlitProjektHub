#!/usr/bin/env python3
"""Lädt spaCy lg + Flair-NER für swiss-pii-anonymizer (Server-Vorbereitung).

Auf dem Server einmalig nach pip install -r requirements.txt:

    .venv/bin/python scripts/maintenance/prefetch_pii_models.py

RAM-Hinweis: flair/ner-german-large (~2.2 GB Gewichte) + de_core_news_lg (~570 MB)
+ PyTorch-Overhead → beim ersten Laden oft >4 GB RAM. Auf einem 4 GB CT ohne Swap
wird der Prozess vom Kernel getötet („Getötet“ / OOM).

Optionen:
  - Swap (4 GB): siehe docs/SERVER_SETUP.md Abschnitt Cloud-PII
  - Kleineres Modell: FLAIR_NER_MODEL=flair/ner-german .venv/bin/python ...
  - CT-RAM auf 8 GB erhöhen

Benötigt Internetzugang. Ohne Netz: ~/.flair/ und spaCy-Paket de_core_news_lg kopieren.
"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> None:
    flair_model = os.getenv("FLAIR_NER_MODEL", "flair/ner-german-large").strip()
    print(f"Flair-Modell: {flair_model}")
    print("spaCy de_core_news_lg (Presidio-NLP für PII) …")
    subprocess.run(
        [sys.executable, "-m", "spacy", "download", "de_core_news_lg"],
        check=True,
    )
    print("Lade Analyzer (Flair + Presidio) — kann mehrere Minuten dauern …")
    from swiss_pii_anonymizer import anonymize
    from swiss_pii_anonymizer.engine import get_analyzer

    get_analyzer(flair_model=flair_model)
    print("Smoke-Test …")
    r = anonymize("Kontakt Maria Muster, AHV 756.1234.5678.97")
    print("  Ergebnis:", r.text[:120])
    print("OK — PII-Stufe 2 bereit.")


if __name__ == "__main__":
    main()
