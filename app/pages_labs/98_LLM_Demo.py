"""
LLM Demo/Lab-Page
Hinweis: Die produktive Bibliothek liegt unter src/m08_llm.py.
Diese Page dient nur für Experimente und kann über toggle_lab_pages.ps1 ein-/ausgeblendet werden.
"""
from __future__ import annotations
import os
import re

class LLMError(Exception): ...

def have_key(provider: str) -> bool:
    env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "mistral": "MISTRAL_API_KEY",
    }[provider]
    return bool(os.getenv(env, ""))

def providers_available() -> list[str]:
    out = []
    for p in ["openai", "anthropic", "mistral"]:
        if have_key(p):
            out.append(p)
    return out or ["none"]

def generate_role_text(provider: str, title: str, function: str | None) -> str:
    """
    Gibt eine vorgeschlagene Rollenbeschreibung (Markdown) zurück.
    Wir nutzen je nach Provider die offizielle SDK.
    """
    if not title.strip():
        raise LLMError("Titel fehlt für die Generierung.")

    system = (
        "Du bist ein präziser Tech-Schreibassistent. "
        "Erzeuge kompakte, klare Rollenprofile im Markdown-Format: "
        "Abschnitte: Auftrag/Zweck, Verantwortlichkeiten (Bullet Points), "
        "Schnittstellen, KPIs, Risiken/Abgrenzung. Ton: neutral, sachlich."
    )
    user = (
        f"Erstelle ein Rollenprofil.\n"
        f"Titel: {title.strip()}\n"
        f"Funktion/Organisationsebene: {function or '—'}\n"
        f"Kontext: KI-gestützte Projektarbeit, Software/IT-Organisation.\n"
        f"Lieferformat: Markdown, max. ~250–300 Wörter."
    )

    if provider == "openai" and have_key("openai"):
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.6,
        )
        return resp.choices[0].message.content.strip()

    if provider == "anthropic" and have_key("anthropic"):
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-3-5-sonnet-latest",
            system=system,
            messages=[{"role":"user","content":user}],
            max_tokens=600,
            temperature=0.6,
        )
        return "".join(block.text for block in msg.content if getattr(block, "type", "")=="text").strip()

    if provider == "mistral" and have_key("mistral"):
        from mistralai import Mistral
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        resp = client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.6,
        )
        return resp.choices[0].message.content.strip()

    # Fallback (kein Key gesetzt): Stub-Text
    return (
        f"## {title.strip()}\n\n"
        f"**Funktion:** {function or '—'}\n\n"
        "- Zweck: (Platzhalter)\n"
        "- Verantwortlichkeiten: (Platzhalter)\n"
        "- Schnittstellen: (Platzhalter)\n"
        "- KPIs: (Platzhalter)\n"
        "- Risiken/Abgrenzung: (Platzhalter)\n"
        "\n> Hinweis: Kein API-Key gefunden – Stub-Text."
    )

def generate_summary(provider: str, title: str, facts: str) -> str:
    """Generate a short 3-5 sentence markdown summary from free-form facts.
    Falls back to a stub when no provider key is available.
    """
    title = (title or "").strip()
    facts = (facts or "").strip()
    if not title and not facts:
        return "(keine Zusammenfassung)"

    system = (
        "Du bist ein präziser Assistent. Fasse die folgenden Eckpunkte kurz zusammen. "
        "Stil: knapp, sachlich, 3–5 Sätze, Markdown-Absatz."
    )
    user = (
        f"Titel: {title or '—'}\n\n"
        f"Eckpunkte:\n{facts}\n\n"
        f"Aufgabe: Prägnante Zusammenfassung erstellen."
    )

    if provider == "openai" and have_key("openai"):
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()

    if provider == "anthropic" and have_key("anthropic"):
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-3-5-sonnet-latest",
            system=system,
            messages=[{"role":"user","content":user}],
            max_tokens=500,
            temperature=0.2,
        )
        return "".join(block.text for block in msg.content if getattr(block, "type", "")=="text").strip()

    if provider == "mistral" and have_key("mistral"):
        from mistralai import Mistral
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        resp = client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()

    # Fallback: simple stub from first lines
    if facts:
        lines = [ln.strip() for ln in facts.splitlines() if ln.strip()]
        snippet = " ".join(lines)[:400]
    else:
        snippet = title
    return f"{snippet} (Kurzfassung – Stub)"

def generate_task_bullets(provider: str, title: str) -> tuple[str, str]:
    """Generate 3–5 bullet points for Expertise and for Aufgaben/Pflichten based on the task title.
    Returns (expertise_md, duties_md). Falls back to stubs when no provider is available.
    """
    title = (title or "").strip()
    if not title:
        return ("- (Bitte Titel angeben)", "- (Bitte Titel angeben)")

    system = (
        "Du bist ein präziser Assistent. Liefere knappe, praxisnahe Bulletpoints."
    )
    user = (
        "Erzeuge je 3–5 Bulletpoints für die folgenden zwei Bereiche auf Basis des Aufgabentitels.\n"
        "1) Expertise / Spezialwissen\n"
        "2) Aufgaben / Pflichten\n\n"
        f"Titel der Aufgabe: {title}\n\n"
        "Gib nur die Bulletlisten zurück, jeweils als Markdown-Liste (Zeilen beginnen mit '- ')."
    )

    if provider == "openai" and have_key("openai"):
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.6,
        )
        txt = resp.choices[0].message.content.strip()
        return _split_bullets(txt)

    if provider == "anthropic" and have_key("anthropic"):
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-3-5-sonnet-latest",
            system=system,
            messages=[{"role":"user","content":user}],
            max_tokens=600,
            temperature=0.6,
        )
        txt = "".join(block.text for block in msg.content if getattr(block, "type", "")=="text").strip()
        return _split_bullets(txt)

    if provider == "mistral" and have_key("mistral"):
        from mistralai import Mistral
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        resp = client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.6,
        )
        txt = resp.choices[0].message.content.strip()
        return _split_bullets(txt)

    # Fallback stub
    return (
        "- Relevante Tools/Methoden\n- Domänenwissen\n- Prozesskenntnis",
        "- Kernaufgaben\n- Qualitätschecks\n- Dokumentation"
    )

def _split_bullets(text: str) -> tuple[str, str]:
    """Try to split model output into two lists. Heuristic: look for headings or blank line separation."""
    t = (text or "").strip()
    if not t:
        return ("- (leer)", "- (leer)")
    # Try to split by two sections
    parts = re.split(r"(?im)^\s*\d\)\s|^\s*\#|^\s*Expertise|^\s*Aufgaben", t)
    # Fallback: split by two consecutive newlines
    if len(parts) < 3:
        chunks = [s.strip() for s in t.split("\n\n") if s.strip()]
        if len(chunks) >= 2:
            return (chunks[0], chunks[1])
        return (t, t)
    # parts[0] is prefix, parts[1] and parts[2] likely contain lists
    exp = parts[1].strip() if len(parts) > 1 else t
    dut = parts[2].strip() if len(parts) > 2 else t
    return (exp, dut)


def generate_short_title(provider: str, hint: str, max_len: int = 50) -> str:
    """Erzeugt eine prägnante Aufgabenbezeichnung (Kurz-Titel) basierend auf einem Hint.
    Garantiert, dass die Rückgabe höchstens max_len Zeichen lang ist (hart gekürzt, falls nötig).
    Fallback liefert eine getrimmte Variante des Hints.
    """
    hint = (hint or "").strip()
    if not hint:
        return ""

    system = (
        "Du bist präzise. Erzeuge einen sehr kurzen, prägnanten Aufgabentitel (3–6 Wörter). "
        "Keine Satzzeichen am Ende, keine Anführungszeichen."
    )
    user = (
        f"Beschreibe in max. {max_len} Zeichen einen kompakten Titel für diese Aufgabe.\n"
        f"Hinweis: {hint}\n"
        "Gib NUR den Titel zurück, ohne Zusatztext."
    )

    text = None
    try:
        if provider == "openai" and have_key("openai"):
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=0.2,
            )
            text = resp.choices[0].message.content.strip()
        elif provider == "anthropic" and have_key("anthropic"):
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model="claude-3-5-sonnet-latest",
                system=system,
                messages=[{"role":"user","content":user}],
                max_tokens=60,
                temperature=0.2,
            )
            text = "".join(block.text for block in msg.content if getattr(block, "type", "")=="text").strip()
        elif provider == "mistral" and have_key("mistral"):
            from mistralai import Mistral
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            resp = client.chat.complete(
                model="mistral-large-latest",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=0.2,
            )
            text = resp.choices[0].message.content.strip()
    except Exception:
        text = None

    if not text:
        # simple fallback: nimm die ersten Wörter aus dem Hint
        text = re.sub(r"\s+", " ", hint)
    # harte Kürzung auf max_len
    out = text[:max_len].strip()
    # kosmetik: keine losen Trennzeichen am Ende
    out = re.sub(r"[-–—,:;\s]+$", "", out)
    return out
