"""Stufe-2 PII-Sanitize für Cloud-Prompts — swiss-pii-anonymizer (Presidio + Flair).

Ergänzt Regex-Stufe 1 in m16_idea_visual.sanitize_for_cloud_text().
Bei fehlendem Paket oder Modell-Fehler: stiller Fallback auf Stufe 1.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_pii_warned = False
_analyzer_ready = False
_analyzer_lock = threading.Lock()
_circuit_open_until = 0.0
_DEFAULT_FLAIR = "flair/ner-german-large"
_DEFAULT_CIRCUIT_SECONDS = 300


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


def _project_root() -> Path:
    env_root = os.getenv("APP_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1]


def _ensure_model_cache_paths() -> None:
    """Model-Caches unter APP_ROOT (systemd ProtectHome blockiert ~/.flair und ~/.cache)."""
    root = _project_root()
    flair_cache = (
        os.getenv("FLAIR_CACHE_ROOT", str(root / ".flair")).strip()
        or str(root / ".flair")
    )
    hf_home = (
        os.getenv("HF_HOME", str(root / ".hf_cache")).strip()
        or str(root / ".hf_cache")
    )
    hf_hub = (
        os.getenv("HUGGINGFACE_HUB_CACHE", str(Path(hf_home) / "hub")).strip()
        or str(Path(hf_home) / "hub")
    )

    os.environ.setdefault("FLAIR_CACHE_ROOT", flair_cache)
    os.environ.setdefault("HF_HOME", hf_home)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", hf_hub)

    # transformers liest TRANSFORMERS_OFFLINE bei HF_HUB_OFFLINE
    if os.getenv("HF_HUB_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on"):
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

    Path(flair_cache).mkdir(parents=True, exist_ok=True)
    Path(hf_hub).mkdir(parents=True, exist_ok=True)


def circuit_breaker_seconds() -> float:
    raw = os.getenv("PII_CIRCUIT_BREAKER_SECONDS", str(_DEFAULT_CIRCUIT_SECONDS)).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(_DEFAULT_CIRCUIT_SECONDS)


def _is_circuit_open() -> bool:
    return time.monotonic() < _circuit_open_until


def _open_circuit() -> None:
    global _circuit_open_until
    _circuit_open_until = time.monotonic() + circuit_breaker_seconds()


def _log_pii_fallback(reason: str, exc: Exception | None = None) -> None:
    global _pii_warned
    if _pii_warned:
        return
    if exc is not None:
        log.warning(
            "swiss-pii-anonymizer %s — Cloud-Sanitize nur Stufe 1 (Regex): %s",
            reason,
            exc,
        )
    else:
        log.warning("swiss-pii-anonymizer %s — Cloud-Sanitize nur Stufe 1 (Regex)", reason)
    _pii_warned = True


def _ensure_pii_analyzer() -> bool:
    """Lädt Analyzer einmalig. True = bereit, False = Stufe-1-Fallback."""
    global _analyzer_ready

    if _analyzer_ready:
        return True
    if _is_circuit_open():
        return False

    with _analyzer_lock:
        if _analyzer_ready:
            return True
        if _is_circuit_open():
            return False
        _ensure_model_cache_paths()
        try:
            from swiss_pii_anonymizer.engine import get_analyzer

            get_analyzer(flair_model=flair_ner_model())
            _analyzer_ready = True
            return True
        except ImportError as exc:
            _open_circuit()
            _log_pii_fallback("nicht installiert", exc)
            return False
        except Exception as exc:
            _open_circuit()
            _log_pii_fallback("Fehler beim Laden", exc)
            return False


def is_pii_analyzer_ready() -> bool:
    """True wenn Stufe-2-Analyzer bereits geladen ist (ohne erneutes Laden)."""
    return _analyzer_ready


def warmup_pii_analyzer() -> bool:
    """Einmalig beim App-Start (Background-Thread) — Modelle vor erstem Request laden."""
    _ensure_model_cache_paths()
    if not pii_sanitize_enabled():
        return False
    ok = _ensure_pii_analyzer()
    if ok:
        log.info("PII-Stufe 2 (Presidio+Flair) bereit")
    return ok


def apply_swiss_pii_sanitize(text: str) -> str:
    """Presidio + Flair-NER — ersetzt erkannte PII mit [ENTITY_TYPE]."""
    global _analyzer_ready
    _ensure_model_cache_paths()
    if not text or not pii_sanitize_enabled():
        return text or ""
    if not _ensure_pii_analyzer():
        return text
    try:
        from swiss_pii_anonymizer import anonymize

        result = anonymize(text)
        out = (result.text or "").strip()
        return out if out else text
    except ImportError as exc:
        _analyzer_ready = False
        _open_circuit()
        _log_pii_fallback("nicht installiert", exc)
        return text
    except Exception as exc:
        _analyzer_ready = False
        _open_circuit()
        _log_pii_fallback("Fehler bei Anonymisierung", exc)
        return text


def pii_findings_for_preview(text: str) -> list[dict[str, str | float]]:
    """Erkannte Entitäten ohne Textänderung (z.B. für künftige UI-Vorschau)."""
    if not text or not pii_sanitize_enabled():
        return []
    if not _ensure_pii_analyzer():
        return []
    try:
        from swiss_pii_anonymizer import analyze

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
