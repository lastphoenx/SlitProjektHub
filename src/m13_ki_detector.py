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

# Bullet-Erkennung innerhalb einer Frage (KI macht gerne Unterlisten)
_BULLET_PATTERN = re.compile(r"(\n\s*[-•*]|\n\s*\d+\.)\s+\S", re.MULTILINE)

# Erschöpfende Aufzählungen: "X, Y, Z und/sowie W" — KI zählt gerne alles auf
_ENUMERATION_PATTERN = re.compile(r"\w[\w\s]+,\s*\w[\w\s]+,\s*\w[\w\s]+(,|\s+(und|sowie|oder)\s+)\w[\w\s]+", re.IGNORECASE)

# Informelle Marker die Menschen benutzen, KI aber nicht
# Anwesenheit dieser senkt den KI-Score
_INFORMAL_MARKERS = re.compile(
    r"\b(eigentlich|irgendwie|halt|eben|doch|mal|übrigens|btw|ps:|tipp:|frage:|anmerkung:)\b"
    r"|\b(wir haben|ich habe|unser|unsere|bei uns|in unserem)\b"
    r"|[!]{2,}|\?{2,}|\.{3,}",
    re.IGNORECASE
)


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


def _has_bullet_sublist(text: str) -> bool:
    """KI gliedert Fragen gerne in Unterpunkte."""
    return bool(_BULLET_PATTERN.search(text))


def _has_exhaustive_enumeration(text: str) -> bool:
    """KI listet Dinge erschöpfend auf: 'A, B, C und D'."""
    return bool(_ENUMERATION_PATTERN.search(text))


def _has_informal_markers(text: str) -> bool:
    """Menschliche Marker: 'eigentlich', 'bei uns', 'Ich habe...', '...', '!!' etc."""
    return bool(_INFORMAL_MARKERS.search(text))


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
    Benötigt mind. 5 Fragen und 8 Sätze — sonst ist das Signal zu rauschig.
    """
    if len(questions) < 5:
        return 0.0  # Zu wenig Fragen für belastbare Aussage

    all_sentences = []
    for q in questions:
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(q) if s.strip()]
        all_sentences.extend(sentences)

    if len(all_sentences) < 8:
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
            "bullet_sublists_ratio": 0.0,
            "exhaustive_enum_ratio": 0.0,
            "informal_markers_ratio": 0.0,
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

    # --- Kleine Stichproben dämpfen ---
    # Bei n < 10 sind Ratio-Signale statistisch unzuverlässig (1 Frage = 100% Struct.Refs ist bedeutungslos).
    # Der Faktor skaliert alle ratio-basierten Signale linear: n=1 → 0.1, n=5 → 0.5, n=10+ → 1.0
    sample_weight = min(n, 10) / 10

    # --- Feature 1: Strukturreferenzen (Kapitel X.Y) ---
    structural_refs_ratio = sum(min(_count_structural_refs(q), 1) for q in questions) / n * sample_weight

    # --- Feature 2: KI-Floskeln (Handlungsaufforderungen) ---
    ki_phrases_ratio = sum(min(_count_ki_phrases(q), 1) for q in questions) / n * sample_weight

    # --- Feature 3: Übergangsphrasing ("Darüber hinaus", "Des Weiteren") ---
    transition_phrases_ratio = sum(min(_count_transition_phrases(q), 1) for q in questions) / n * sample_weight

    # --- Feature 4: Uniforme Satzeinstiege ---
    uniform_openers_ratio = sum(1 for q in questions if _has_uniform_opener(q)) / n * sample_weight

    # --- Feature 5: Bullet-Unterlisten innerhalb von Fragen ---
    bullet_sublists_ratio = sum(1 for q in questions if _has_bullet_sublist(q)) / n * sample_weight

    # --- Feature 6: Erschöpfende Aufzählungen ("A, B, C und D") ---
    exhaustive_enum_ratio = sum(1 for q in questions if _has_exhaustive_enumeration(q)) / n * sample_weight

    # --- Feature 7: Informelle Marker (negativ-Signal: senkt Score) ---
    informal_count = sum(1 for q in questions if _has_informal_markers(q))
    informal_markers_ratio = informal_count / n
    # Wenn >20% der Fragen informelle Marker haben → starkes menschliches Signal
    informal_penalty = min(informal_markers_ratio / 0.2, 1.0)  # 0–1

    # --- Feature 8: Burstiness (Satzlängen-Uniformität über alle Fragen) ---
    # Hat eigene Mindestschwelle (≥5 Fragen / ≥8 Sätze), kein extra sample_weight nötig
    sentence_burstiness_score = _sentence_burstiness(questions)

    # --- Feature 9: Fragelängen-Uniformität ---
    # Nicht sinnvoll bei < 5 Fragen (n=1 → cv=0 → score=1.0 ist immer ein false positive)
    length_cv = _coefficient_of_variation([float(l) for l in lengths])
    length_uniformity_score = max(0.0, min(1.0, 1.0 - (length_cv / 0.6))) if n >= 5 else 0.0

    # --- Feature 10: Volumen-Signal ---
    vol_signal = _volume_signal(n, total_vendors, total_questions or n * total_vendors)

    # --- Gewichteter KI-Score ---
    # Gewichte angepasst: struct↓ (legitime Beschaffungstexte referenzieren naturgemäss Kapitel),
    # ki_phrases↑ (stärkstes Einzelsignal), vol↑, openers↑
    raw_score = (
        0.15 * structural_refs_ratio    # war 0.20 – struct.refs auch in manuellen Texten häufig
        + 0.20 * ki_phrases_ratio       # war 0.15 – stärkstes KI-Signal
        + 0.10 * transition_phrases_ratio
        + 0.12 * uniform_openers_ratio  # war 0.10
        + 0.10 * bullet_sublists_ratio  # war 0.12
        + 0.08 * exhaustive_enum_ratio  # war 0.10
        + 0.08 * sentence_burstiness_score
        + 0.05 * length_uniformity_score
        + 0.12 * vol_signal             # war 0.10 – Volumen ist starkes Indiz
    )
    # Summe = 1.00

    # --- Kombinationsbonus: Volumen + mehrere Signale gleichzeitig ---
    # Viele Fragen mit mehreren gleichzeitigen (aber je einzeln moderaten) KI-Merkmalen
    # sind ein stärkeres Indiz als die Summe der Einzelsignale vermuten lässt.
    _active_signals = sum(1 for v in [
        structural_refs_ratio, ki_phrases_ratio, transition_phrases_ratio,
        uniform_openers_ratio, bullet_sublists_ratio, exhaustive_enum_ratio,
        sentence_burstiness_score,
    ] if v > 0.05)
    if n >= 15 and vol_signal >= 0.50 and _active_signals >= 2:
        raw_score += 0.15

    # Informelle Marker reduzieren den Score (max. -20%)
    ki_score = round(min(1.0, max(0.0, raw_score * (1.0 - 0.20 * informal_penalty))), 3)

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
        "uniform_openers_ratio": round(uniform_openers_ratio, 3),        "bullet_sublists_ratio": round(bullet_sublists_ratio, 3),
        "exhaustive_enum_ratio": round(exhaustive_enum_ratio, 3),
        "informal_markers_ratio": round(informal_markers_ratio, 3),        "sentence_burstiness_score": round(sentence_burstiness_score, 3),
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

_AI_SYSTEM_PROMPT = """You are an expert in detecting AI-generated text in public procurement question sets.
Analyze the sample and give an HONEST, calibrated assessment. Avoid defaulting to high scores.

CRITICAL CONTEXT: Professional procurement questions are inherently formal and structured.
Do NOT flag questions as AI-generated just because they use technical terms, chapter references,
or formal sentence structure — these are EXPECTED and NORMAL in procurement.

Signs that INCREASE likelihood of AI generation:
- Nearly IDENTICAL sentence openers repeated across ALL questions (robotically uniform)
- Exhaustive comma-separated lists covering every possible aspect ("A, B, C, D und E")
- Transition phrases that read like GPT continuations ("Darüber hinaus", "Des Weiteren", "Im Weiteren", "Basierend auf")
- Zero variation in sentence length and zero personal voice across many questions
- Questions that paraphrase the RFP requirements back word-for-word as questions
- Unusually high total question count (>40) with near-identical length and complexity

Signs that DECREASE likelihood of AI generation (human indicators):
- Typos, abbreviations, or clipped phrasing
- Company-specific, person-specific, or product-specific references
- Colloquial, informal or conversational language mixed in
- Highly varied sentence length, rhythm, and style
- Questions that presuppose internal knowledge or prior conversations
- Deliberately provocative or niche questions off the standard procurement template

CALIBRATION: A score of 85% means you are highly certain. Reserve 70%+ for cases with multiple
clear AI signals. If the sample is ambiguous or too small, reflect that with a lower score and
confidence "niedrig" or "mittel". It is perfectly valid to output a score of 10-30% for
vendors whose questions look genuinely human.

IMPORTANT: Respond ONLY with a JSON object. No preamble, no explanation, no markdown fences.
Start your response with { and end with }.

Required format:
{"ki_score": <integer 0-100>, "confidence": "<hoch|mittel|niedrig>", "hauptmerkmale": ["<feature>", "<feature>"], "fazit": "<one sentence in German>"}"""


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
    import json as _json

    # Stichprobe: verteilt über die Fragenliste (nicht nur erste N)
    if len(questions) > max_sample:
        step = len(questions) // max_sample
        sample = [questions[i * step] for i in range(max_sample)]
    else:
        sample = questions

    sample_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(sample))
    user_prompt = (
        f"Vendor: {vendor}\n"
        f"Total questions: {len(questions)}\n"
        f"Sample ({len(sample)} questions):\n\n{sample_text}"
    )
    all_messages = [
        {"role": "user", "content": user_prompt},
    ]

    raw = None
    try:
        if provider == "openai":
            from src.m08_llm import have_key
            if not have_key("openai"):
                return {"error": "Kein OpenAI API-Key konfiguriert", "ki_score": None}
            from openai import OpenAI
            client = OpenAI()
            # Immer gpt-4o-mini: stabil, günstig, unterstützt JSON-Mode
            # Das globale UI-Modell wird hier NICHT verwendet (kann experimental/preview sein)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": _AI_SYSTEM_PROMPT}] + all_messages,
                max_tokens=600,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content.strip()

        elif provider == "anthropic":
            from src.m08_llm import have_key, get_model_id, DEFAULT_MODELS
            if not have_key("anthropic"):
                return {"error": "Kein Anthropic API-Key konfiguriert", "ki_score": None}
            import anthropic
            import os
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            model_id = get_model_id("anthropic", model) or DEFAULT_MODELS.get("anthropic", "claude-3-5-haiku-20241022")
            msg = client.messages.create(
                model=model_id,
                system=_AI_SYSTEM_PROMPT,
                messages=all_messages,
                max_tokens=600,
                temperature=temperature,
            )
            raw = msg.content[0].text.strip()

        else:
            return {"error": f"Provider '{provider}' nicht unterstützt für KI-Analyse", "ki_score": None}

    except Exception as e:
        return {"error": str(e), "raw_response": raw, "ki_score": None}

    if not raw:
        return {"error": "Leere Antwort vom Modell", "ki_score": None}

    # JSON extrahieren: finde erstes '{' und letztes '}'
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            parsed = _json.loads(raw[start:end + 1])
            parsed["raw_response"] = raw
            return parsed
        except _json.JSONDecodeError as e:
            return {"error": f"JSON-Parse-Fehler: {e}", "raw_response": raw, "ki_score": None}

    return {"error": "Kein JSON in Antwort", "raw_response": raw, "ki_score": None}


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
