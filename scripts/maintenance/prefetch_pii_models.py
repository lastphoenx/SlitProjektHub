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

Benötigt Internetzugang. Ohne Netz: APP_ROOT/.hf_cache/hub/ und spaCy de_core_news_lg kopieren.

Hinweis systemd (ProtectHome=true): Flair 0.15 lädt über HuggingFace Hub —
Caches müssen unter APP_ROOT/.hf_cache/hub liegen, nicht in /root/.cache.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _ensure_model_cache_paths() -> tuple[Path, Path]:
    flair_cache = Path(os.getenv("FLAIR_CACHE_ROOT", str(_ROOT / ".flair"))).expanduser()
    hf_home = Path(os.getenv("HF_HOME", str(_ROOT / ".hf_cache"))).expanduser()
    hf_hub = Path(
        os.getenv("HUGGINGFACE_HUB_CACHE", str(hf_home / "hub")).strip()
        or str(hf_home / "hub")
    )
    os.environ["FLAIR_CACHE_ROOT"] = str(flair_cache)
    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hf_hub)
    flair_cache.mkdir(parents=True, exist_ok=True)
    hf_hub.mkdir(parents=True, exist_ok=True)
    return flair_cache, hf_hub


def main() -> None:
    flair_cache, hf_hub = _ensure_model_cache_paths()
    flair_model = os.getenv("FLAIR_NER_MODEL", "flair/ner-german-large").strip()
    print(f"Flair-Cache: {flair_cache}")
    print(f"HF-Hub-Cache: {hf_hub}")
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
