# src/m13_ki_detector.py
"""
KI-Erkennung für Ausschreibungsfragen.

Analysiert strukturierte Fragen (aus CSV) auf typische Merkmale KI-generierter Texte.
Keine zusätzlichen API-Aufrufe notwendig – rein textbasierte Heuristiken.

Stufe 1: Pro Lieferant → KI-Score + Detailbefund
Stufe 2: Gesamtübersicht + Ranking aller Lieferanten
"""
from __future__ import annotations

import re
import math
from typing import Optional


# ---------------------------------------------------------------------------
# Erkennungsmuster
# ---------------------------------------------------------------------------

# Kapitelreferenzen: "Kapitel 4.2", "Abschnitt 3", "Punkt 2.1.3", "Ziffer 5"
_STRUCTURAL_REF_PATTERN = re.compile(
    r"\b(kapitel|abschnitt|punkt|ziffer|anforderung|req|nr\.?|ziff\.?)\s*\d+(\.\d+)*\b",
    re.IGNORECASE
)

# Typische KI-Formulierungsfloskeln (Deutsch)
_KI_PHRASE_PATTERNS = [
    re.compile(r"\bbitte\s+(beschreiben|erläutern|erklären|nennen|schildern|geben\s+sie)\b", re.IGNORECASE),
    re.compile(r"\bwie\s+stellen\s+sie\s+sicher\b", re.IGNORECASE),
    re.compile(r"\bwelche\s+(massnahmen|schritte|methoden|verfahren|tools|systeme)\b", re.IGNORECASE),
    re.compile(r"\binwiefern\s+(ist|sind|kann|können|wird|werden)\b", re.IGNORECASE),
    re.compile(r"\bkönnen\s+sie\s+(bestätigen|nachweisen|beschreiben|erläutern)\b", re.IGNORECASE),
    re.compile(r"\bwie\s+(gewährleisten|sichern|stellen|gehen)\s+(sie|ihr)\b", re.IGNORECASE),
    re.compile(r"\bgem[äa]ss\s+(pflichtenheft|lastenheft|anforderung|dokument)", re.IGNORECASE),
    re.compile(r"\blaut\s+(pflichtenheft|lastenheft|anforderung|dokument)", re.IGNORECASE),
    re.compile(r"\bim\s+rahmen\s+von\b", re.IGNORECASE),
    re.compile(r"\bauf\s+welche\s+weise\b", re.IGNORECASE),
]

# Wiederholende Satzeinstiegsmuster
_OPENER_PATTERNS = [
    re.compile(r"^(bitte|wie|welche|inwiefern|können Sie|beschreiben Sie|erläutern Sie)", re.IGNORECASE),
    re.compile(r"^(was|welche|warum|wann|wo)\s+\w+\s+(sie|ihr|ihre)\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Kern-Analysefunktionen
# ---------------------------------------------------------------------------

def _count_structural_refs(text: str) -> int:
    """Zählt Kapitel/Abschnitt-Referenzen in einem Text."""
    return len(_STRUCTURAL_REF_PATTERN.findall(text))


def _count_ki_phrases(text: str) -> int:
    """Zählt typische KI-Formulierungsfloskeln."""
    count = 0
    for pattern in _KI_PHRASE_PATTERNS:
        if pattern.search(text):
            count += 1
    return count


def _has_uniform_opener(text: str) -> bool:
    """Prüft ob der Satz mit einem typischen KI-Einstieg beginnt."""
    stripped = text.strip()
    for pattern in _OPENER_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def _coefficient_of_variation(values: list[float]) -> float:
    """Variationskoeffizient (0 = perfekt uniform, 1+ = sehr variabel)."""
    if len(values) < 2:
        return 0.0
    n = len(values)
    mean = sum(values) / n
    if mean == 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(variance) / mean


def analyze_vendor_questions(questions: list[str]) -> dict:
    """
    Analysiert die Fragen eines einzelnen Lieferanten.

    Args:
        questions: Liste von Fragetexten (Strings)

    Returns:
        dict mit Score-Werten (0.0 – 1.0) und Metadaten
    """
    if not questions:
        return {
            "count": 0,
            "ki_score": 0.0,
            "structural_refs_ratio": 0.0,
            "ki_phrases_ratio": 0.0,
            "uniform_openers_ratio": 0.0,
            "length_cv": 0.0,
            "avg_length": 0.0,
            "verdict": "Keine Daten",
            "verdict_emoji": "❓",
        }

    n = len(questions)
    lengths = [len(q) for q in questions]
    avg_length = sum(lengths) / n

    # Feature 1: Strukturreferenzen
    refs_per_q = [min(_count_structural_refs(q), 1) for q in questions]  # 0/1 pro Frage
    structural_refs_ratio = sum(refs_per_q) / n

    # Feature 2: KI-Floskeln
    phrases_per_q = [min(_count_ki_phrases(q), 1) for q in questions]  # 0/1 pro Frage
    ki_phrases_ratio = sum(phrases_per_q) / n

    # Feature 3: Uniforme Einstiege
    uniform_openers = [1 if _has_uniform_opener(q) else 0 for q in questions]
    uniform_openers_ratio = sum(uniform_openers) / n

    # Feature 4: Längenuniformität (geringer CV = verdächtig einheitlich)
    length_cv = _coefficient_of_variation([float(l) for l in lengths])
    # Normieren: CV < 0.2 gilt als sehr uniform → Score 1.0; CV > 0.8 → Score 0.0
    length_uniformity_score = max(0.0, min(1.0, 1.0 - (length_cv / 0.6)))

    # Gewichteter KI-Score
    ki_score = (
        0.35 * structural_refs_ratio
        + 0.25 * ki_phrases_ratio
        + 0.20 * uniform_openers_ratio
        + 0.20 * length_uniformity_score
    )
    ki_score = round(min(ki_score, 1.0), 3)

    # Urteil
    if ki_score >= 0.70:
        verdict = "Sehr wahrscheinlich KI-generiert"
        verdict_emoji = "🤖"
    elif ki_score >= 0.45:
        verdict = "Verdächtig – möglicherweise KI-unterstützt"
        verdict_emoji = "⚠️"
    elif ki_score >= 0.25:
        verdict = "Teilweise KI-typische Merkmale"
        verdict_emoji = "🔍"
    else:
        verdict = "Wahrscheinlich manuell verfasst"
        verdict_emoji = "✅"

    return {
        "count": n,
        "ki_score": ki_score,
        "structural_refs_ratio": round(structural_refs_ratio, 3),
        "ki_phrases_ratio": round(ki_phrases_ratio, 3),
        "uniform_openers_ratio": round(uniform_openers_ratio, 3),
        "length_cv": round(length_cv, 3),
        "avg_length": round(avg_length, 1),
        "verdict": verdict,
        "verdict_emoji": verdict_emoji,
    }


def analyze_all_vendors(chunks: list[dict]) -> dict:
    """
    Gruppiert Chunks nach Lieferant und analysiert jeden Anbieter.

    Args:
        chunks: Liste von dicts mit mindestens {"Lieferant": "...", "Frage": "..."}

    Returns:
        dict mit:
            "vendors": {lieferant: analyze_vendor_questions(...)}
            "total_questions": int
            "total_vendors": int
            "overall_ki_score": float  (gewichtet nach Fragenanzahl)
            "ki_vendors_count": int    (Anbieter mit Score >= 0.45)
            "ki_vendors_ratio": float
            "ranking": list[tuple[str, dict]]  sortiert nach ki_score absteigend
    """
    # Gruppieren
    by_vendor: dict[str, list[str]] = {}
    for chunk in chunks:
        vendor = str(chunk.get("Lieferant", "Unbekannt")).strip()
        question = str(chunk.get("Frage", "")).strip()
        if question:
            by_vendor.setdefault(vendor, []).append(question)

    if not by_vendor:
        return {
            "vendors": {},
            "total_questions": 0,
            "total_vendors": 0,
            "overall_ki_score": 0.0,
            "ki_vendors_count": 0,
            "ki_vendors_ratio": 0.0,
            "ranking": [],
        }

    # Pro-Anbieter Analyse
    vendor_results: dict[str, dict] = {}
    for vendor, questions in by_vendor.items():
        vendor_results[vendor] = analyze_vendor_questions(questions)

    # Gesamtstatistik
    total_questions = sum(r["count"] for r in vendor_results.values())
    total_vendors = len(vendor_results)

    # Gewichteter Gesamt-KI-Score (nach Fragenanzahl)
    if total_questions > 0:
        overall_ki_score = sum(
            r["ki_score"] * r["count"] for r in vendor_results.values()
        ) / total_questions
    else:
        overall_ki_score = 0.0

    ki_vendors = [v for v, r in vendor_results.items() if r["ki_score"] >= 0.45]
    ki_vendors_count = len(ki_vendors)
    ki_vendors_ratio = ki_vendors_count / total_vendors if total_vendors > 0 else 0.0

    # Ranking (höchster KI-Score zuerst)
    ranking = sorted(vendor_results.items(), key=lambda x: x[1]["ki_score"], reverse=True)

    return {
        "vendors": vendor_results,
        "total_questions": total_questions,
        "total_vendors": total_vendors,
        "overall_ki_score": round(overall_ki_score, 3),
        "ki_vendors_count": ki_vendors_count,
        "ki_vendors_ratio": round(ki_vendors_ratio, 3),
        "ranking": ranking,
    }
