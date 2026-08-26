#!/usr/bin/env python3
"""Lädt spaCy lg + Flair-NER für swiss-pii-anonymizer (Server-Vorbereitung).

Auf dem Server einmalig nach pip install -r requirements.txt:

    export HF_HOME=/opt/slitprojekthub/.hf_cache   # APP_ROOT/.hf_cache
    .venv/bin/python scripts/maintenance/prefetch_pii_models.py

Lädt in APP_ROOT/.hf_cache/hub/ (HuggingFace Hub):
  - flair/ner-german-large + Basis FacebookAI/xlm-roberta-large (Tokenizer/Config)
  - oder flair/ner-german (kleiner, ohne xlm-roberta)

RAM: ner-german-large oft >6 GB Spitze beim Laden (8 GB CT empfohlen).
Disk: ~2–3 GB HF-Cache + spaCy lg + PyTorch-Abhängigkeiten.

Nach Prefetch: chown für Service-User, HF_HUB_OFFLINE=1 in .env, Backend-Neustart.
Siehe docs/SERVER_SETUP.md Abschnitt 7.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
# flair/ner-german-large (FLERT) nutzt diesen Transformer — muss im HF-Cache liegen
_LARGE_NER_BASE = "FacebookAI/xlm-roberta-large"


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


def _prefetch_large_ner_base() -> None:
    print(f"Basis-Transformer {_LARGE_NER_BASE} (für ner-german-large) …")
    from transformers import AutoConfig, AutoTokenizer

    AutoConfig.from_pretrained(_LARGE_NER_BASE)
    AutoTokenizer.from_pretrained(_LARGE_NER_BASE)


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
    if flair_model.endswith("ner-german-large"):
        _prefetch_large_ner_base()
    print("Lade Analyzer (Flair + Presidio) — kann mehrere Minuten dauern …")
    from flair.models import SequenceTagger
    from swiss_pii_anonymizer import anonymize
    from swiss_pii_anonymizer.engine import get_analyzer

    SequenceTagger.load(flair_model)
    get_analyzer(flair_model=flair_model)
    print("Smoke-Test …")
    r = anonymize("Kontakt Maria Muster, AHV 756.1234.5678.97")
    print("  Ergebnis:", r.text[:120])
    print(f"OK — PII-Stufe 2 bereit. Cache: {hf_hub}")
    print("Nächster Schritt: chown -R APP_USER:APP_USER", hf_hub.parent)


if __name__ == "__main__":
    main()
