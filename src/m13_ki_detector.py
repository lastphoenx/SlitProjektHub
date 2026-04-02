# src/m13_ki_detector.py
"""
KI-Erkennung für Ausschreibungsfragen.

Analysiert strukturierte Fragen (aus CSV) auf typische Merkmale KI-generierter Texte.
Stufe 1: Pro Lieferant → KI-Score + Detailbefund  (rein textbasiert, keine API)
Stufe 2: Gesamtübersicht + Ranking aller Lieferanten
Stufe 3: Optionale KI-gestützte Tiefenanalyse (OpenAI / Anthropic)
"""
from __future__ import annotations

import re
import math
import random
from typing import Optional


# ---------------------------------------------------------------------------
# Erkennungsmuster
# ---------------------------------------------------------------------------

# Kapitelreferenzen: "Kapitel 4.2", "Abschnitt 3", "Punkt 2.1.3", "Ziffer 5"
_STRUCTURAL_REF_PATTERN = re.compile(
    r"\b(kapitel|abschnitt|punkt|ziffer|anforderung|req|nr\.?|ziff\.?)\s*\d+(\.\d+)*\b",
    re.IGNORECASE
)

# Typische KI-Floskeln Stufe 1: direkte Handlungsaufforderungen
_KI_PHRASE_PATTERNS = [
    re.compile(r"\bbitte\s+(beschreiben|erläutern|erklären|nennen|schildern|geben\s+sie)\b", re.IGNORECASE),
    re.compile(r"\bwie\s+stellen\s+sie\s+sicher\b", re.IGNORECASE),
    re.compile(r"\bwelche\s+(massnahmen|schritte|methoden|verfahren|tools|systeme|konzepte|mechanismen)\b", re.IGNORECASE),
    re.compile(r"\binwiefern\s+(ist|sind|kann|können|wird|werden)\b", re.IGNORECASE),
    re.compile(r"\bkönnen\s+sie\s+(bestätigen|nachweisen|beschreiben|erläutern|darlegen)\b", re.IGNORECASE),
    re.compile(r"\bwie\s+(gewährleisten|sichern|stellen|gehen)\s+(sie|ihr)\b", re.IGNORECASE),
    re.compile(r"\bgem[äa]ss\s+(pflichtenheft|lastenheft|anforderung|dokument|ausschreibung)", re.IGNORECASE),
    re.compile(r"\blaut\s+(pflichtenheft|lastenheft|anforderung|dokument|ausschreibung)", re.IGNORECASE),
    re.compile(r"\bim\s+rahmen\s+von\b", re.IGNORECASE),
    re.compile(r"\bauf\s+welche\s+weise\b", re.IGNORECASE),
    re.compile(r"\bwie\s+wird\s+(dies|das|dieser|dieses)\s+(sichergestellt|gewährleistet|umgesetzt)\b", re.IGNORECASE),
    re.compile(r"\bwelche\s+erfahrungen?\s+(haben|besitzen|verfügen)\b", re.IGNORECASE),
]

# Übergangsphrasing / Discourse-Marker (typisch für KI-generierten Fliesstext)
_TRANSITION_PHRASE_PATTERNS = [
    re.compile(r"\bdar[üu]ber\s+hinaus\b", re.IGNORECASE),
    re.compile(r"\bdes\s+weiteren\b", re.IGNORECASE),
    re.compile(r"\bim\s+weiteren\b", re.IGNORECASE),
    re.compile(r"\bzudem\b", re.IGNORECASE),
    re.compile(r"\bin\s+diesem\s+zusammenhang\b", re.IGNORECASE),
    re.compile(r"\binsbesondere\s+(ist|sind|m[öo]chten\s+wir|bitten\s+wir)\b", re.IGNORECASE),
    re.compile(r"\bwir\s+bitten\s+sie\b", re.IGNORECASE),
    re.compile(r"\bwir\s+m[öo]chten\s+wissen\b", re.IGNORECASE),
    re.compile(r"\bin\s+anlehnung\s+an\b", re.IGNORECASE),
    re.compile(r"\bvor\s+diesem\s+hintergrund\b", re.IGNORECASE),
    re.compile(r"\bim\s+hinblick\s+auf\b", re.IGNORECASE),
    re.compile(r"\bbasierend\s+auf\b", re.IGNORECASE),
    re.compile(r"\bim\s+kontext\s+(von|der|des)\b", re.IGNORECASE),
]

# Satzeinstiege
_OPENER_PATTERNS = [
    re.compile(r"^(bitte|wie|welche|inwiefern|können\s+sie|beschreiben\s+sie|erläutern\s+sie|nennen\s+sie)", re.IGNORECASE),
    re.compile(r"^(was|welche|warum|wann|wo)\s+\w+\s+(sie|ihr|ihre)\b", re.IGNORECASE),
    re.compile(r"^(stellen\s+sie|zeigen\s+sie|geben\s+sie)", re.IGNORECASE),
]

# Satz-Splitter (für Burstiness)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# Kern-Analysefunktionen
# ---------------------------------------------------------------------------

def _count_structural_refs(text: str) -> int:
    return len(_STRUCTURAL_REF_PATTERN.findall(text))


def _count_ki_phrases(text: str) -> int:
    return sum(1 for p in _KI_PHRASE_PATTERNS if p.search(text))


def _count_transition_phrases(text: str) -> int:
    return sum(1 for p in _TRANSITION_PHRASE_PATTERNS if p.search(text))


def _has_uniform_opener(text: str) -> bool:
    stripped = text.strip()
    return any(p.match(stripped) for p in _OPENER_PATTERNS)


def _coefficient_of_variation(values: list[float]) -> float:
    """CV: 0 = perfekt uniform, 1+ = sehr variabel."""
    if len(values) < 2:
        return 0.0
    n = len(values)
    mean = sum(values) / n
    if mean == 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(variance) / mean


def _sentence_burstiness(questions: list[str]) -> float:
    """
    Misst Burstiness auf Satzebene über alle Fragen hinweg.
    Niedrig = alle Sätze gleich lang = KI-typisch.
    Gibt einen Score 0–1 zurück: 0 = hoch variabel (menschlich), 1 = sehr uniform (KI-typisch).
    """
    all_sentences = []
    for q in questions:
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(q) if s.strip()]
        all_sentences.extend(sentences)

    if len(all_sentences) < 3:
        return 0.0  # Zu wenig Sätze für Aussage

    lengths = [len(s) for s in all_sentences]
    cv = _coefficient_of_variation([float(l) for l in lengths])
    # CV < 0.3 → sehr uniform → Score 1.0; CV > 1.0 → menschlich variabel → 0.0
    return round(max(0.0, min(1.0, 1.0 - (cv / 0.8))), 3)


def _volume_signal(n: int, total_vendors: int, total_questions: int) -> float:
    """
    Fragenvolumen-Signal: hohe Fragenanzahl für einen Anbieter ist verdächtig.
    Gibt 0–1 zurück. Normiert gegen den Durchschnitt aller Anbieter.
    """
    if total_vendors == 0 or total_questions == 0:
        return 0.0
    avg_per_vendor = total_questions / total_vendors
    # Mehr als 2x Durchschnitt = voll verdächtig (Score 1.0)
    ratio = n / avg_per_vendor if avg_per_vendor > 0 else 1.0
    return round(min(1.0, max(0.0, (ratio - 1.0) / 1.5)), 3)


def analyze_vendor_questions(
    questions: list[str],
    total_vendors: int = 1,
    total_questions: int = 0,
) -> dict:
    """
    Analysiert die Fragen eines einzelnen Lieferanten.

    Args:
        questions: Liste von Fragetexten
        total_vendors: Gesamtzahl Anbieter (für Volume-Signal)
        total_questions: Gesamtzahl Fragen (für Volume-Signal)

    Returns:
        dict mit Score-Werten (0.0–1.0) und Metadaten
    """
    if not questions:
        return {
            "count": 0,
            "ki_score": 0.0,
            "structural_refs_ratio": 0.0,
            "ki_phrases_ratio": 0.0,
            "transition_phrases_ratio": 0.0,
            "uniform_openers_ratio": 0.0,
            "sentence_burstiness_score": 0.0,
            "length_cv": 0.0,
            "avg_length": 0.0,
            "volume_signal": 0.0,
            "verdict": "Keine Daten",
            "verdict_emoji": "❓",
            "ai_verdict": None,
        }

    n = len(questions)
    lengths = [len(q) for q in questions]
    avg_length = sum(lengths) / n

    # --- Feature 1: Strukturreferenzen (Kapitel X.Y) ---
    structural_refs_ratio = sum(min(_count_structural_refs(q), 1) for q in questions) / n

    # --- Feature 2: KI-Floskeln (Handlungsaufforderungen) ---
    ki_phrases_ratio = sum(min(_count_ki_phrases(q), 1) for q in questions) / n

    # --- Feature 3: Übergangsphrasing ("Darüber hinaus", "Des Weiteren") ---
    transition_phrases_ratio = sum(min(_count_transition_phrases(q), 1) for q in questions) / n

    # --- Feature 4: Uniforme Satzeinstiege ---
    uniform_openers_ratio = sum(1 for q in questions if _has_uniform_opener(q)) / n

    # --- Feature 5: Burstiness (Satzlängen-Uniformität über alle Fragen) ---
    sentence_burstiness_score = _sentence_burstiness(questions)

    # --- Feature 6: Fragelängen-Uniformität ---
    length_cv = _coefficient_of_variation([float(l) for l in lengths])
    length_uniformity_score = max(0.0, min(1.0, 1.0 - (length_cv / 0.6)))

    # --- Feature 7: Volumen-Signal ---
    vol_signal = _volume_signal(n, total_vendors, total_questions or n * total_vendors)

    # --- Gewichteter KI-Score ---
    ki_score = (
        0.25 * structural_refs_ratio
        + 0.20 * ki_phrases_ratio
        + 0.15 * transition_phrases_ratio
        + 0.15 * uniform_openers_ratio
        + 0.10 * sentence_burstiness_score
        + 0.10 * length_uniformity_score
        + 0.05 * vol_signal
    )
    ki_score = round(min(ki_score, 1.0), 3)

    # --- Urteil ---
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
        "transition_phrases_ratio": round(transition_phrases_ratio, 3),
        "uniform_openers_ratio": round(uniform_openers_ratio, 3),
        "sentence_burstiness_score": round(sentence_burstiness_score, 3),
        "length_cv": round(length_cv, 3),
        "avg_length": round(avg_length, 1),
        "volume_signal": round(vol_signal, 3),
        "verdict": verdict,
        "verdict_emoji": verdict_emoji,
        "ai_verdict": None,  # wird durch analyze_vendor_with_ai() befüllt
    }


# ---------------------------------------------------------------------------
# Optionale KI-gestützte Tiefenanalyse
# ---------------------------------------------------------------------------

_AI_SYSTEM_PROMPT = """Du bist Experte für die Erkennung KI-generierter Texte in Ausschreibungsverfahren.
Du analysierst Bieterfragen aus einer öffentlichen Ausschreibung und bewertest ob sie KI-generiert wurden.

Typische Merkmale KI-generierter Ausschreibungsfragen:
- Systematische Kapitelreferenzen ("Gemäss Kapitel X.Y des Pflichtenhefts...")
- Uniform strukturierte Formulierungen ohne persönliche Note
- Gleiche Satzeinstiegsmuster (Bitte beschreiben Sie / Wie stellen Sie sicher / Welche Massnahmen...)
- Sehr gleichmässige Länge und Komplexität
- Übergang-Marker wie "Darüber hinaus", "Des Weiteren", "Im Weiteren"
- Keine Rechtschreibfehler, keine umgangssprachlichen Ausdrücke
- Keine spezifischen Unternehmens- oder Personenreferenzen

Antworte ausschliesslich mit einem JSON-Objekt, keine weiteren Erklärungen:
{"ki_score": <0-100>, "confidence": "<hoch|mittel|niedrig>", "hauptmerkmale": ["...", "..."], "fazit": "<1 Satz>"}"""


def analyze_vendor_with_ai(
    questions: list[str],
    vendor: str,
    provider: str,
    model: str | None,
    temperature: float = 0.2,
    max_sample: int = 8,
) -> dict:
    """
    Lässt OpenAI/Anthropic eine Stichprobe der Fragen bewerten.

    Args:
        questions: Alle Fragen des Anbieters
        vendor: Name des Anbieters (für Logging)
        provider: "openai" oder "anthropic"
        model: Modellname (None = Standardmodell des Providers)
        temperature: Sampling-Temperatur (niedrig = konsistenter)
        max_sample: Maximale Anzahl Beispielfragen

    Returns:
        dict mit: ki_score (0–100), confidence, hauptmerkmale, fazit, raw_response
    """
    try:
        from src.m08_llm import try_models_with_messages
    except ImportError:
        return {"error": "m08_llm nicht verfügbar", "ki_score": None}

    # Stichprobe: verteilt über die Fragenliste (nicht nur erste N)
    if len(questions) > max_sample:
        step = len(questions) // max_sample
        sample = [questions[i * step] for i in range(max_sample)]
    else:
        sample = questions

    sample_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(sample))
    user_prompt = (
        f"Anbieter: {vendor}\n"
        f"Anzahl Fragen insgesamt: {len(questions)}\n"
        f"Stichprobe ({len(sample)} Fragen):\n\n{sample_text}"
    )

    try:
        raw = try_models_with_messages(
            provider=provider,
            system=_AI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=400,
            temperature=temperature,
            model=model,
        )
        if not raw:
            return {"error": "Keine Antwort", "ki_score": None}

        # JSON aus Antwort extrahieren
        import json as _json
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            parsed = _json.loads(json_match.group())
            parsed["raw_response"] = raw
            return parsed
        return {"error": "Kein JSON in Antwort", "raw_response": raw, "ki_score": None}

    except Exception as e:
        return {"error": str(e), "ki_score": None}


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

    # Pro-Anbieter Analyse (mit Volumen-Kontext)
    # Erst Gesamtzahlen bestimmen
    total_questions_pre = sum(len(qs) for qs in by_vendor.values())
    total_vendors_pre = len(by_vendor)

    vendor_results: dict[str, dict] = {}
    for vendor, questions in by_vendor.items():
        vendor_results[vendor] = analyze_vendor_questions(
            questions,
            total_vendors=total_vendors_pre,
            total_questions=total_questions_pre,
        )

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
