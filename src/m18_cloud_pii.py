"""Stufe-2 PII-Sanitize für Cloud-Prompts — swiss-pii-anonymizer (Presidio + Flair).

Ergänzt Regex-Stufe 1 in m16_idea_visual.sanitize_for_cloud_text().
Bei fehlendem Paket oder Modell-Fehler: stiller Fallback auf Stufe 1.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_pii_warned = False
_DEFAULT_FLAIR = "flair/ner-german-large"


def pii_sanitize_enabled() -> bool:
    return os.getenv("SWISS_PII_ANONYMIZER", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def flair_ner_model() -> str:
    """Flair-Modell für Personen-NER (env FLAIR_NER_MODEL)."""
    return (os.getenv("FLAIR_NER_MODEL", _DEFAULT_FLAIR).strip() or _DEFAULT_FLAIR)


def _ensure_pii_analyzer() -> None:
    from swiss_pii_anonymizer.engine import get_analyzer

    get_analyzer(flair_model=flair_ner_model())


def apply_swiss_pii_sanitize(text: str) -> str:
    """Presidio + Flair-NER — ersetzt erkannte PII mit [ENTITY_TYPE]."""
    if not text or not pii_sanitize_enabled():
        return text or ""
    global _pii_warned
    try:
        from swiss_pii_anonymizer import anonymize

        _ensure_pii_analyzer()
        result = anonymize(text)
        out = (result.text or "").strip()
        return out if out else text
    except ImportError:
        if not _pii_warned:
            log.warning(
                "swiss-pii-anonymizer nicht installiert — Cloud-Sanitize nur Stufe 1 (Regex)"
            )
            _pii_warned = True
        return text
    except Exception as exc:
        if not _pii_warned:
            log.warning(
                "swiss-pii-anonymizer Fehler — Cloud-Sanitize nur Stufe 1 (Regex): %s",
                exc,
            )
            _pii_warned = True
        return text


def pii_findings_for_preview(text: str) -> list[dict[str, str | float]]:
    """Erkannte Entitäten ohne Textänderung (z.B. für künftige UI-Vorschau)."""
    if not text or not pii_sanitize_enabled():
        return []
    try:
        from swiss_pii_anonymizer import analyze

        _ensure_pii_analyzer()
        return [
            {
                "entity_type": f.entity_type,
                "text": f.text,
                "score": round(f.score, 3),
            }
            for f in analyze(text)
        ]
    except Exception:
        return []
