from __future__ import annotations
import os
import re
import logging

logger = logging.getLogger(__name__)

# Hinweis: Dies ist die kanonische LLM-Bibliothek für die App.
# Bitte keine Duplikate in scripts/ oder app/pages_* anlegen.
# Für Experimente existiert eine separate Lab-Page: app/pages_labs/98_LLM_Demo.py

# ==================== MODEL CONFIGURATION ====================

ANTHROPIC_MODEL_DEFAULT = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

AVAILABLE_MODELS = {
    "anthropic": {
        "opus-4.6":   "claude-opus-4-6",
        "sonnet-4.6": "claude-sonnet-4-6",
        "haiku-4.5":  "claude-haiku-4-5-20251001",
        "sonnet":     "claude-sonnet-4-6",
        "opus":       "claude-opus-4-6",
        "haiku":      "claude-3-5-haiku-20241022",
        "opus-3":     "claude-3-opus-20240229",
        "sonnet-3.5": "claude-3-5-sonnet-20241022",
        "haiku-3.5":  "claude-3-5-haiku-20241022",
    },
    "openai": {
        # GPT-5.x Frontier (Stand April 2026, Quelle: developers.openai.com/api/docs/models)
        # ChatGPT-UI-Namen: "Thinking 5.4" → gpt-5.4, "Instant 5.3" → gpt-5.3-instant
        "gpt-5.4":          "gpt-5.4",          # Thinking 5.4 – Flagship, komplex/agentic
        "gpt-5.3-instant":  "gpt-5.3-instant",  # Instant 5.3 – alltäglich, schnell
        "gpt-5.4-mini":     "gpt-5.4-mini",     # Stärkstes Mini; coding, subagents
        "gpt-5.4-nano":     "gpt-5.4-nano",     # Günstigstes GPT-5.4; high-volume
        "gpt-5.2":          "gpt-5.2",          # Vorheriges Frontier (5.2)
        "gpt-5.0":          "gpt-5.0",          # Älteres GPT-5 (5.0)
        "gpt-5-mini":       "gpt-5-mini",       # Günstig ($0.25/MTok)
        # o-Series Reasoning
        "o4-mini":          "o4-mini",
        "o3":               "o3",
        "o3-mini":          "o3-mini",
        "o1":               "o1",
        "o1-mini":          "o1-mini",
        # GPT-4 Legacy
        "gpt-4o":           "gpt-4o",
        "gpt-4o-mini":      "gpt-4o-mini",
        "gpt-4-turbo":      "gpt-4-turbo",
    },
    "mistral": {
        "small": "mistral-small-latest",
        "large": "mistral-large-latest",
    },
}

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
    "mistral": "mistral-large-latest",
}

def get_available_models(provider: str) -> list[str]:
    """Returns list of available model names (keys) for a provider."""
    return list(AVAILABLE_MODELS.get(provider, {}).keys())

def get_model_id(provider: str, model_name: str | None) -> str:
    """Resolves model name to actual API model ID. Falls back to default if not found."""
    if not model_name:
        return DEFAULT_MODELS.get(provider, "")
    
    model_id = AVAILABLE_MODELS.get(provider, {}).get(model_name)
    return model_id or DEFAULT_MODELS.get(provider, "")

def _resolve_anthropic_model(requested: str | None) -> str:
    """Legacy function - maps aliases to model IDs."""
    alias = (requested or "").strip().lower()
    mapping = {
        "sonnet-4.6":                 "claude-sonnet-4-6",
        "claude-sonnet-4.6-latest":   "claude-sonnet-4-6",
        "sonnet-latest":              "claude-sonnet-4-6",
        "sonnet":                     "claude-sonnet-4-6",
        "opus-4.6":                   "claude-opus-4-6",
        "claude-opus-4.6-latest":     "claude-opus-4-6",
        "opus-latest":                "claude-opus-4-6",
        "opus":                       "claude-opus-4-6",
        "haiku-4.5":                  "claude-haiku-4-5-20251001",
        "claude-haiku-4.5-latest":    "claude-haiku-4-5-20251001",
        "claude-3-5-sonnet-latest":   "claude-3-5-sonnet-20241022",
        "sonnet-3.5":                 "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-latest":    "claude-3-5-haiku-20241022",
        "haiku-latest":               "claude-3-5-haiku-20241022",
        "haiku":                      "claude-3-5-haiku-20241022",
        "claude-3-opus-latest":       "claude-3-opus-20240229",
        "opus-3":                     "claude-3-opus-20240229",
        "":                           ANTHROPIC_MODEL_DEFAULT,
    }
    return mapping.get(alias, ANTHROPIC_MODEL_DEFAULT)


def _anthropic_try_models(system: str, user: str, *, max_tokens: int, temperature: float, model: str | None = None, preferred_alias: str = "claude-3-5-sonnet-latest") -> str | None:
    """Versucht nacheinander mehrere Anthropic-Modelle aufzurufen.
    Gibt den Text-Output zurück oder None bei Fehlschlag.
    model: Specific model ID to use (e.g. "claude-3-5-haiku-20241022"). If provided, tries this first.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception:
        return None

    candidates = []
    
    if model:
        candidates.append(model)
    
    try:
        candidates_env = [s.strip() for s in os.getenv("ANTHROPIC_MODEL_CANDIDATES", "").split(",") if s.strip()]
    except Exception:
        candidates_env = []
    
    primary = _resolve_anthropic_model(preferred_alias)
    if primary not in candidates:
        candidates.append(primary)
    if ANTHROPIC_MODEL_DEFAULT not in candidates:
        candidates.append(ANTHROPIC_MODEL_DEFAULT)
    for c in candidates_env:
        if c not in candidates:
            candidates.append(c)
    
    hard_fallbacks = [
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ]
    for c in hard_fallbacks:
        if c not in candidates:
            candidates.append(c)

    for model_id in candidates:
        try:
            msg = client.messages.create(
                model=model_id,
                system=system,
                messages=[{"role":"user","content":user}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return "".join(block.text for block in msg.content if getattr(block, "type", "")=="text").strip()
        except Exception:
            continue
    return None


def _anthropic_try_models_with_messages(system: str, messages: list[dict], *, max_tokens: int, temperature: float, model: str | None = None, preferred_alias: str = "claude-3-5-sonnet-latest") -> str | None:
    """Wie _anthropic_try_models, aber mit vollständigem Message-Array (Chatverlauf).
    messages: [{"role":"user|assistant","content":"..."},...]
    model: Specific model ID to use (e.g. "claude-3-5-haiku-20241022"). If provided, tries this first.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception:
        return None

    candidates = []
    
    if model:
        candidates.append(model)
    
    try:
        candidates_env = [s.strip() for s in os.getenv("ANTHROPIC_MODEL_CANDIDATES", "").split(",") if s.strip()]
    except Exception:
        candidates_env = []
    
    primary = _resolve_anthropic_model(preferred_alias)
    if primary not in candidates:
        candidates.append(primary)
    if ANTHROPIC_MODEL_DEFAULT not in candidates:
        candidates.append(ANTHROPIC_MODEL_DEFAULT)
    for c in candidates_env:
        if c not in candidates:
            candidates.append(c)
    
    hard_fallbacks = [
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
    ]
    for c in hard_fallbacks:
        if c not in candidates:
            candidates.append(c)

    for model_id in candidates:
        try:
            msg = client.messages.create(
                model=model_id,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return "".join(block.text for block in msg.content if getattr(block, "type", "")=="text").strip()
        except Exception:
            continue
    return None


def try_models_with_messages(provider: str, system: str, messages: list[dict], *, max_tokens: int, temperature: float, model: str | None = None, _used_model: list | None = None) -> str | None:
    """
    Provider-agnostische Chat-Funktion mit Model & Temperature Support.

    Args:
        provider: "openai", "anthropic", "mistral"
        system: System prompt
        messages: Chat history [{"role": "user|assistant", "content": "..."},...]
        max_tokens: Max output tokens
        temperature: 0.0 (deterministic) to 1.0+ (creative)
        model: Model name (e.g. "gpt-4o", "sonnet", "large"). If None, uses default.
        _used_model: Optionale Liste – wenn übergeben, wird [model_id, fallback_warning] eingetragen.
                     Beispiel: buf = []; result = try_models_with_messages(..., _used_model=buf)
                     Danach: buf[0] = tatsächlich genutztes Modell, buf[1] = Warnung oder ""

    Returns: Response text or None on error
    """
    if provider == "openai" and have_key("openai"):
        from openai import OpenAI
        client = OpenAI()
        model_id = get_model_id("openai", model) or "gpt-4o-mini"
        all_messages = [{"role": "system", "content": system}] + messages
        kwargs: dict = dict(model=model_id, messages=all_messages, temperature=temperature)
        kwargs["max_tokens"] = max_tokens
        try:
            resp = client.chat.completions.create(**kwargs)
            if _used_model is not None:
                _used_model.clear()
                _used_model += [model_id, ""]
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err_str = str(e).lower()
            # CRITICAL FIX: O1-Modelle und moderne OpenAI-Modelle verwenden max_completion_tokens statt max_tokens
            if "max_tokens" in err_str and ("unexpected keyword argument" in err_str or "not supported" in err_str):
                try:
                    resp = client.chat.completions.create(
                        model=model_id,
                        messages=all_messages,
                        max_completion_tokens=max_tokens,  # Korrigierter Parameter für O1/moderne Modelle
                        temperature=temperature,
                    )
                    if _used_model is not None:
                        _used_model.clear()
                        _used_model += [model_id, ""]
                    return resp.choices[0].message.content.strip()
                except Exception as e_retry:
                    raise LLMError(f"OpenAI ({model_id}): {e_retry}") from e_retry
            # Fallback auf gpt-4o-mini wenn Modell nicht gefunden oder Parameter unbekannt
            if model_id != "gpt-4o-mini" and ("model_not_found" in err_str or "unsupported_parameter" in err_str or "does not exist" in err_str):
                try:
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=all_messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    if _used_model is not None:
                        _used_model.clear()
                        _used_model += ["gpt-4o-mini", f"⚠️ Fallback: '{model_id}' nicht verfügbar → gpt-4o-mini verwendet"]
                    return resp.choices[0].message.content.strip()
                except Exception as e2:
                    raise LLMError(f"OpenAI (gpt-4o-mini Fallback): {e2}") from e2
            raise LLMError(f"OpenAI ({model_id}): {e}") from e
    
    if provider == "anthropic" and have_key("anthropic"):
        model_id = get_model_id("anthropic", model) or DEFAULT_MODELS["anthropic"]
        result = _anthropic_try_models_with_messages(system, messages, max_tokens=max_tokens, temperature=temperature, model=model_id)
        if result:
            return result
    
    if provider == "mistral" and have_key("mistral"):
        try:
            from mistralai import Mistral
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            model_id = get_model_id("mistral", model) or "mistral-large-latest"
            resp = client.chat.complete(
                model=model_id,
                messages=messages,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            raise LLMError(f"Mistral ({model_id}): {e}") from e
    
    if not have_key(provider or ""):
        raise LLMError(f"Kein API-Key für Provider '{provider}' konfiguriert")
    return None

class LLMError(Exception): ...

def have_key(provider: str) -> bool:
    env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "mistral": "MISTRAL_API_KEY",
    }[provider]
    return bool(os.getenv(env, ""))

def test_connection(provider: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Testet echte Verbindung zur KI-API durch minimalen API-Call.
    Returns (success: bool, error_msg: str)"""
    if provider == "anthropic":
        try:
            import anthropic
            key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not key:
                return (False, "ANTHROPIC_API_KEY nicht in .env")
            if len(key) < 10:
                return (False, "ANTHROPIC_API_KEY zu kurz (ungültig?)")
            
            client = anthropic.Anthropic(api_key=key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=5,
            )
            if msg and hasattr(msg, 'content'):
                return (True, "")
            return (False, "Leere Antwort")
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "Unauthorized" in err_str:
                return (False, "API-Key ungültig (401 Unauthorized)")
            if "400" in err_str:
                return (False, "Ungültiger Request (API-Key format?)")
            if "invalid" in err_str.lower() and "key" in err_str.lower():
                return (False, "Ungültiger API-Key")
            return (False, err_str[:100])
    
    if provider == "openai":
        try:
            from openai import OpenAI
            client = OpenAI(timeout=timeout)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10,
            )
            return (True, "")
        except Exception as e:
            return (False, str(e))
    
    if provider == "mistral":
        try:
            from mistralai import Mistral
            client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY", ""))
            resp = client.chat.complete(
                model="mistral-small-latest",
                messages=[{"role": "user", "content": "hi"}],
            )
            return (True, "")
        except Exception as e:
            return (False, str(e))
    
    return (False, "Provider unbekannt")

def providers_available() -> list[str]:
    out = []
    for p in ["openai", "anthropic", "mistral"]:
        if have_key(p):
            out.append(p)
    return out or ["none"]


def rewrite_query_for_retrieval(query: str, provider: str = "openai", model: str = "gpt-4o-mini") -> str:
    """
    Destilliert eine Nutzerfrage auf ihre Signal-Keywords für BM25-Retrieval.
    Verwendet denselben Signal/Noise-Ansatz wie generate_chunk_keywords():
    Nur Begriffe, die den Kern der Frage EINDEUTIG benennen — kein Dokumentkontext,
    keine Verfahrensbegriffe, keine omnipräsenten Rahmenwörter.

    Returns: Leerzeichen-getrennte Keyword-Liste (lowercase), oder Original bei Fehler.

    Example:
        >>> rewrite_query_for_retrieval("In den Ausschreibungsunterlagen wird Subunternehmen untersagt...")
        "subunternehmen konzernverbund tochtergesellschaft konzernprivilegierung"
    """
    import json as _json
    
    # ===== DEBUG LOGGING =====
    logger.info("=" * 80)
    logger.info("[QUERY DISTILLATION] START")
    logger.info(f"[QUERY DISTILLATION] Input Query: {query!r}")
    logger.info(f"[QUERY DISTILLATION] Provider: {provider}")
    logger.info(f"[QUERY DISTILLATION] Model: {model}")
    # ===== END DEBUG =====
    
    system_prompt = (
        "Du bist ein Experte fuer Informations-Retrieval. Aus einer Frage extrahierst du "
        "genau die Begriffe, die das KERNTHEMA dieser Frage benennen -- und nur die.\n\n"
        "SIGNAL vs. RAUSCHEN:\n"
        "RAUSCHEN (nie als Keyword): Omnipraesente Woerter, die in fast jeder Frage des Korpus "
        "vorkommen -- z.B. Dokumenttypen (Pflichtenheft, Ausschreibung, Anhang), "
        "Verfahrensbegriffe (Anfrage, Klaerung, Verbot, Regelung), "
        "Kapitelreferenzen (Kap. 2.1), Projektnamen.\n"
        "SIGNAL (als Keyword): Die spezifischen Fachbegriffe, technischen Konzepte oder "
        "rechtlichen Tatbestaende, um die es in der Frage KONKRET geht.\n\n"
        "ANALOGIE -- Frage in einem Werkstatthandbuch-Forum: "
        "'Laut Handbuch Abschnitt 3.2 soll man beim Golf 4 die Zuendkerzen wechseln. "
        "Gilt das auch fuer den Polo 6N?'\n"
        "Keywords: ['zuendkerzen', 'wechseln', 'polo 6n']\n"
        "Verboten: ['handbuch', 'abschnitt', 'gilt', 'laut']\n\n"
        "Antworte NUR mit einem JSON-Array aus 3-6 deutschen Strings (lowercase). "
        "Keine Erklaerung."
    )
    
    # ===== DEBUG LOGGING =====
    logger.info("[QUERY DISTILLATION] System Prompt:")
    logger.info(system_prompt)
    logger.info("=" * 80)
    # ===== END DEBUG =====
    
    try:
        result = try_models_with_messages(
            provider=provider,
            system=system_prompt,
            messages=[{"role": "user", "content": query[:1500]}],
            max_tokens=80,
            temperature=0.0,
            model=model,
        )
        
        # ===== DEBUG LOGGING =====
        logger.info(f"[QUERY DISTILLATION] Raw LLM Response: {result!r}")
        # ===== END DEBUG =====
        
        if not result:
            logger.warning("[QUERY DISTILLATION] No result from LLM, returning original query")
            return query
        match = re.search(r'\[.*?\]', result, re.DOTALL)
        if not match:
            logger.warning(f"[QUERY DISTILLATION] No JSON array found in response, returning original query")
            return query
        
        # Fix: LLM liefert manchmal Python-Syntax ['...'] statt JSON ["..."]
        json_str = match.group().replace("'", '"')
        keywords = _json.loads(json_str)
        
        # ===== DEBUG LOGGING =====
        logger.info(f"[QUERY DISTILLATION] Parsed Keywords: {keywords}")
        # ===== END DEBUG =====
        
        cleaned = [str(k).lower().strip() for k in keywords if k and str(k).strip()]
        final_result = " ".join(cleaned) if cleaned else query
        
        # ===== DEBUG LOGGING =====
        logger.info(f"[QUERY DISTILLATION] Final Output: {final_result!r}")
        logger.info("[QUERY DISTILLATION] END")
        logger.info("=" * 80)
        # ===== END DEBUG =====
        
        return final_result
    except Exception as e:
        logger.error(f"[QUERY DISTILLATION] ERROR: {e}", exc_info=True)
        logger.info("[QUERY DISTILLATION] Returning original query due to error")
        return query


def generate_chunk_keywords(
    chunk_text: str,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
) -> list[str]:
    """
    Generiert BM25-Suchbegriffe fuer einen Dokumenten-Chunk (Index-time Enrichment).

    Fuer Q&A-Chunks (JSON mit 'Frage'-Feld): Keywords werden aus der Frage extrahiert,
    da der Nutzer nach dem Inhalt der Frage sucht, nicht nach Metadaten.
    Fuer regulaere Textchunks: Keywords aus dem Gesamttext.

    Returns: Liste von 5-8 Fachbegriffen (lowercase), leer bei Fehler.
    """
    import json as _json

    # Q&A-Format erkennen: Keywords aus der Frage, nicht aus JSON-Metadaten
    text_for_keywords = chunk_text
    try:
        data = _json.loads(chunk_text)
        if isinstance(data, dict) and "Frage" in data:
            text_for_keywords = str(data["Frage"])
    except (ValueError, TypeError):
        pass

    system_prompt = (
        "Du bist ein Experte fuer Informations-Retrieval. Aus einem Text extrahierst du "
        "genau die Begriffe, die diesen Text von Tausenden aehnlichen Texten im selben "
        "Korpus UNTERSCHEIDEN.\n\n"
        "SIGNAL vs. RAUSCHEN:\n"
        "RAUSCHEN (nie als Keyword): Omnipraesente Woerter, die in fast jedem Text des Korpus "
        "vorkommen -- z.B. Dokumenttypen, Projektnamen, Kapitelreferenzen, Verfahrensbegriffe "
        "wie Pflichtenheft, Ausschreibung, Anfrage, Anhang, Kapitel.\n"
        "SIGNAL (als Keyword): Fachliche Konzepte, technische Spezifika, konkrete Regelungen "
        "oder Tatbestaende, die nur in wenigen Texten auftauchen.\n\n"
        "ANALOGIE -- Werkstatthandbuch, Abschnitt 'Zuendkerzen Golf 4':\n"
        "Keywords: ['zuendkerze', 'elektrodenabstand', 'drehmoment', 'anzugsmoment']\n"
        "Verboten: ['handbuch', 'werkstatt', 'kapitel', 'fahrzeug', 'golf'] "
        "(das ganze Buch handelt davon)\n\n"
        "Antworte NUR mit einem JSON-Array aus 5-8 deutschen Strings (lowercase). "
        "Keine Komposita-Zerlegung. Keine Erklaerung."
    )

    try:
        result = try_models_with_messages(
            provider=provider,
            system=system_prompt,
            messages=[{"role": "user", "content": text_for_keywords[:1500]}],
            max_tokens=120,
            temperature=0.0,
            model=model,
        )
        if not result:
            return []
        match = re.search(r'\[.*?\]', result, re.DOTALL)
        if not match:
            return []
        keywords = _json.loads(match.group())
        return [str(k).lower().strip() for k in keywords if k and str(k).strip()]
    except Exception:
        return []



def generate_query_hypotheses(
    query: str,
    count: int = 3,
    provider: str = "openai",
    model: str = "gpt-4o-mini"
) -> list[str]:
    """
    Generiert mehrere parallele Query-Varianten für Multi-Hypothesis Retrieval.
    Jede Variante optimiert für eine andere Suchstrategie:
      1. KEYWORD: Kurze Fachbegriffe für exakte BM25-Suche
      2. SEMANTIC: Umformulierung mit Synonymen für Embedding-Suche
      3. CONTEXT: Kontextuelle Erweiterung für breite Abdeckung

    Robust gegen verschiedene LLM-Ausgabeformate (Nummerierung optional).

    Args:
        query: Original Nutzer-Query
        count: Anzahl Hypothesen (default: 3)
        provider: LLM Provider
        model: Model name

    Returns:
        Liste von Query-Varianten; fällt auf [query] zurück bei Fehler.
    """
    system_prompt = (
        f"Du bist ein Retrieval-Experte. Generiere {count} verschiedene Suchstrategien "
        "für die folgende Nutzeranfrage. Jede Strategie auf einer eigenen Zeile:\n"
        "1. KEYWORD: Kompakte Fachbegriffe für exakte Suche (BM25-optimiert)\n"
        "2. SEMANTIC: Sachliche Umformulierung mit Synonymen (Embedding-optimiert)\n"
        "3. CONTEXT: Erweiterte Beschreibung mit relevantem Fachkontext\n"
        "Antwort: Nur die Suchstrategien, eine pro Zeile, keine Erklärungen."
    )
    try:
        response = try_models_with_messages(
            provider=provider,
            system=system_prompt,
            messages=[{"role": "user", "content": query}],
            max_tokens=200,
            temperature=0.3,  # Leichte Kreativität für Diversität
            model=model
        )
        if not response:
            return [query]

        # Robustes Parsing: verschiedene Ausgabeformate abfangen
        hypotheses = []
        for line in response.splitlines():
            line = line.strip()
            if not line:
                continue
            # Nummerierung entfernen: "1.", "1)", "KEYWORD:", "- ", "* "
            cleaned = re.sub(r'^[\d]+[.)\s]+', '', line)   # "1. ", "2) "
            cleaned = re.sub(r'^[A-Z]+:\s*', '', cleaned)  # "KEYWORD: ", "SEMANTIC: "
            cleaned = re.sub(r'^[-*]\s+', '', cleaned)     # "- ", "* "
            cleaned = cleaned.strip()
            if cleaned and cleaned.lower() != query.lower():
                hypotheses.append(cleaned)

        # Deduplizieren, Reihenfolge beibehalten
        seen: set[str] = set()
        unique: list[str] = []
        for h in hypotheses:
            if h not in seen:
                seen.add(h)
                unique.append(h)

        return unique[:count] if unique else [query]

    except Exception:
        return [query]


def generate_role_text(
    provider: str, title: str, function: str | None, role_key: str | None = None) -> str:
    """
    Gibt eine vorgeschlagene Rollenbeschreibung (Markdown) zurück.
    Wir nutzen je nach Provider die offizielle SDK.
    
    Falls role_key vorhanden: Hole RAG-Kontext (Projekte, zugeordnete Tasks, etc.)
    """
    if not title.strip():
        raise LLMError("Titel fehlt für die Generierung.")

    system = (
        "Du bist ein präziser Tech-Schreibassistent. "
        "Erzeuge kompakte, klare Rollenprofile im Markdown-Format: "
        "Abschnitte: Auftrag/Zweck, Verantwortlichkeiten (Bullet Points), "
        "Schnittstellen, KPIs, Risiken/Abgrenzung. Ton: neutral, sachlich."
    )
    
    rag_context = ""
    if role_key:
        try:
            from .m09_rag import rag_context_for_role
            rag_context = rag_context_for_role(role_key)
            if rag_context:
                rag_context = f"\n\nZUSÄTZLICHER KONTEXT AUS SYSTEM:\n{rag_context}"
        except Exception:
            pass
    
    user = (
        f"Erstelle ein Rollenprofil.\n"
        f"Titel: {title.strip()}\n"
        f"Funktion/Organisationsebene: {function or '—'}\n"
        f"Kontext: KI-gestützte Projektarbeit, Software/IT-Organisation.{rag_context}\n"
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
        txt = _anthropic_try_models(system, user, max_tokens=600, temperature=0.6, preferred_alias="claude-3-5-sonnet-latest")
        if txt:
            return txt

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
        txt = _anthropic_try_models(system, user, max_tokens=500, temperature=0.2, preferred_alias="claude-3-5-sonnet-latest")
        if txt:
            return txt

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

def generate_role_details(provider: str, description: str, role_key: str | None = None, model: str | None = None, temperature: float | None = None) -> tuple[str, str, str, str, str, str]:
    """Generate role details based on user description.
    Returns (title, short_code, responsibilities_md, qualifications_md, expertise_md, prose_intro).
    Falls back to stubs when no provider is available.
    
    Args:
        provider: "openai", "anthropic", "mistral"
        description: Role description text
        role_key: Optional, for RAG context
        model: Optional, model name (e.g. "gpt-4o"). If None, uses default.
        temperature: Optional, default 0.6
    """
    description = (description or "").strip()
    if not description:
        return ("Neue Rolle", "NR", "- (Bitte Beschreibung angeben)", "- (Bitte Beschreibung angeben)", "- (Bitte Beschreibung angeben)", "")

    if temperature is None:
        temperature = 0.6

    system = (
        "Du bist ein präziser HR-Assistent. Erstelle professionelle Rollenprofile für IT/Business-Organisationen. "
        "Befolge das Format EXAKT wie vorgegeben."
    )
    
    rag_context = ""
    if role_key:
        try:
            from .m09_rag import retrieve_relevant_chunks, build_rag_context_from_search
            search_results = retrieve_relevant_chunks(description, limit=5, threshold=0.5)
            context_text = build_rag_context_from_search(search_results)
            if context_text:
                rag_context = f"\n\nZUSÄTZLICHER KONTEXT AUS SYSTEM:\n{context_text}"
        except Exception:
            pass
    
    user = (
        "Erstelle ein Rollenprofil basierend auf dieser Beschreibung:\n\n"
        f"{description}{rag_context}\n\n"
        "Gib EXAKT in diesem Format zurück (mit den Überschriften, jeweils gefolgt von der Antwort):\n\n"
        "TITEL:\n"
        "(Ein prägnanter Rollen-Titel, z.B. 'Chief Information Officer')\n\n"
        "KÜRZEL:\n"
        "(Max 5 Zeichen, z.B. 'CIO')\n\n"
        "EINLEITUNG:\n"
        "(2-3 zusammenhängende Absätze als Prosa-Text: Was macht diese Rolle? Wozu ist sie da? Kontext und Bedeutung.)\n\n"
        "VERANTWORTLICHKEITEN:\n"
        "(3-5 Bulletpoints als Markdown-Liste, Zeilen beginnen mit '- ')\n\n"
        "QUALIFIKATIONEN:\n"
        "(3-5 Bulletpoints als Markdown-Liste, Zeilen beginnen mit '- ')\n\n"
        "EXPERTISE:\n"
        "(3-5 Bulletpoints als Markdown-Liste, Zeilen beginnen mit '- ')\n\n"
        "Wichtig: Verwende GENAU diese Überschriften (TITEL:, KÜRZEL:, EINLEITUNG:, etc.) und gib NUR diese 6 Abschnitte zurück!"
    )

    if provider == "openai" and have_key("openai"):
        from openai import OpenAI
        client = OpenAI()
        model_id = get_model_id("openai", model) or "gpt-4o-mini"
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=temperature,
        )
        txt = resp.choices[0].message.content.strip()
        return _split_role_details(txt, description)

    if provider == "anthropic" and have_key("anthropic"):
        model_id = get_model_id("anthropic", model) or DEFAULT_MODELS["anthropic"]
        txt = _anthropic_try_models(system, user, max_tokens=800, temperature=temperature, model=model_id)
        if txt:
            return _split_role_details(txt, description)

    if provider == "mistral" and have_key("mistral"):
        from mistralai import Mistral
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        model_id = get_model_id("mistral", model) or "mistral-large-latest"
        resp = client.chat.complete(
            model=model_id,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=temperature,
        )
        txt = resp.choices[0].message.content.strip()
        return _split_role_details(txt, description)

    # Fallback stub
    return (
        description[:50] or "Neue Rolle",
        "NR",
        "- Strategische Planung\n- Teamführung\n- Budgetverantwortung",
        "- Studium oder vergleichbare Qualifikation\n- 5+ Jahre Berufserfahrung\n- Führungserfahrung",
        "- Fachkenntnisse im Bereich\n- Projektmanagement\n- Change Management",
        ""
    )

def _split_role_details(text: str, description: str) -> tuple[str, str, str, str, str, str]:
    """Split LLM output into 6 parts: title, short_code, responsibilities, qualifications, expertise, prose_intro.
    Expects format with headers: TITEL:, KÜRZEL:, EINLEITUNG:, VERANTWORTLICHKEITEN:, QUALIFIKATIONEN:, EXPERTISE:
    """
    t = (text or "").strip()
    if not t:
        return (description[:50] or "Neue Rolle", "NR", "- (leer)", "- (leer)", "- (leer)", "")
    
    # Extract sections using regex
    import re
    
    title_match = re.search(r'TITEL:\s*\n(.+?)(?=\n\n|KÜRZEL:|$)', t, re.IGNORECASE | re.DOTALL)
    code_match = re.search(r'KÜRZEL:\s*\n(.+?)(?=\n\n|EINLEITUNG:|$)', t, re.IGNORECASE | re.DOTALL)
    intro_match = re.search(r'EINLEITUNG:\s*\n(.+?)(?=\n\n|VERANTWORTLICHKEITEN:|$)', t, re.IGNORECASE | re.DOTALL)
    resp_match = re.search(r'VERANTWORTLICHKEITEN:\s*\n(.+?)(?=\n\n|QUALIFIKATIONEN:|$)', t, re.IGNORECASE | re.DOTALL)
    qual_match = re.search(r'QUALIFIKATIONEN:\s*\n(.+?)(?=\n\n|EXPERTISE:|$)', t, re.IGNORECASE | re.DOTALL)
    exp_match = re.search(r'EXPERTISE:\s*\n(.+?)$', t, re.IGNORECASE | re.DOTALL)
    
    title = title_match.group(1).strip() if title_match else (description[:50] or "Neue Rolle")
    short_code = code_match.group(1).strip()[:14] if code_match else "NR"
    prose_intro = intro_match.group(1).strip() if intro_match else ""
    responsibilities = resp_match.group(1).strip() if resp_match else "- (nicht generiert)"
    qualifications = qual_match.group(1).strip() if qual_match else "- (nicht generiert)"
    expertise = exp_match.group(1).strip() if exp_match else "- (nicht generiert)"
    
    # Clean up: remove any remaining section headers from content
    def clean_section(s: str) -> str:
        s = re.sub(r'^(TITEL|KÜRZEL|EINLEITUNG|VERANTWORTLICHKEITEN|QUALIFIKATIONEN|EXPERTISE):\s*', '', s, flags=re.IGNORECASE | re.MULTILINE)
        return s.strip()
    
    title = clean_section(title)
    short_code = clean_section(short_code)
    prose_intro = clean_section(prose_intro)
    responsibilities = clean_section(responsibilities)
    qualifications = clean_section(qualifications)
    expertise = clean_section(expertise)
    
    return (title, short_code, responsibilities, qualifications, expertise, prose_intro)

def generate_task_bullets(provider: str, title: str, model: str | None = None, temperature: float | None = None) -> tuple[str, str]:
    """Generate 3–5 bullet points for Expertise and for Aufgaben/Pflichten based on the task title.
    Returns (expertise_md, duties_md). Falls back to stubs when no provider is available.
    
    Args:
        provider: "openai", "anthropic", "mistral"
        title: Task title
        model: Optional, model name. If None, uses default.
        temperature: Optional, default 0.6
    """
    title = (title or "").strip()
    if not title:
        return ("- (Bitte Titel angeben)", "- (Bitte Titel angeben)")

    if temperature is None:
        temperature = 0.6

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
        model_id = get_model_id("openai", model) or "gpt-4o-mini"
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=temperature,
        )
        txt = resp.choices[0].message.content.strip()
        return _split_bullets(txt)

    if provider == "anthropic" and have_key("anthropic"):
        model_id = get_model_id("anthropic", model) or DEFAULT_MODELS["anthropic"]
        txt = _anthropic_try_models(system, user, max_tokens=600, temperature=temperature, model=model_id)
        if txt:
            return _split_bullets(txt)

    if provider == "mistral" and have_key("mistral"):
        from mistralai import Mistral
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        model_id = get_model_id("mistral", model) or "mistral-large-latest"
        resp = client.chat.complete(
            model=model_id,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=temperature,
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


def generate_short_title(provider: str, hint: str, max_len: int = 50, model: str | None = None, temperature: float | None = None) -> str:
    """Erzeugt eine prägnante Aufgabenbezeichnung (Kurz-Titel) basierend auf einem Hint.
    Garantiert, dass die Rückgabe höchstens max_len Zeichen lang ist (hart gekürzt, falls nötig).
    Fallback liefert eine getrimmte Variante des Hints.
    
    Args:
        provider: "openai", "anthropic", "mistral"
        hint: Source text for title generation
        max_len: Max characters (default 50)
        model: Optional, model name. If None, uses default.
        temperature: Optional, default 0.2 (keep titles consistent/precise)
    """
    hint = (hint or "").strip()
    if not hint:
        return ""

    if temperature is None:
        temperature = 0.2

    system = (
        "Du bist präzise. Erzeuge einen sehr kurzen, prägnanten Aufgabentitel (3–6 Wörter). "
        "Keine Satzzeichen am Ende, keine Anführungszeichen."
    )
    user = (
        "Erzeuge einen sehr kurzen, prägnanten Aufgabentitel.\n"
        f"Maximale Länge: {max_len} Zeichen.\n"
        "Form: 3–6 Wörter, keine Satzzeichen am Ende, keine Anführungszeichen.\n"
        f"Hinweis: {hint}\n"
        "Gib NUR den Titel zurück."
    )

    text = None
    try:
        if provider == "openai" and have_key("openai"):
            from openai import OpenAI
            client = OpenAI()
            model_id = get_model_id("openai", model) or "gpt-4o-mini"
            resp = client.chat.completions.create(
                model=model_id,
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=temperature,
            )
            text = resp.choices[0].message.content.strip()
        elif provider == "anthropic" and have_key("anthropic"):
            model_id = get_model_id("anthropic", model) or DEFAULT_MODELS["anthropic"]
            text = _anthropic_try_models(system, user, max_tokens=60, temperature=temperature, model=model_id)
        elif provider == "mistral" and have_key("mistral"):
            from mistralai import Mistral
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            model_id = get_model_id("mistral", model) or "mistral-large-latest"
            resp = client.chat.complete(
                model=model_id,
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=temperature,
            )
            text = resp.choices[0].message.content.strip()
    except Exception:
        text = None

    if not text:
        # simple fallback: nimm die ersten Wörter aus dem Hint
        text = re.sub(r"\s+", " ", hint)
        # Post-Processing: Markdown/Quotes entfernen und kosmetische Säuberung
        text = text.strip()
        # Entferne führende Heading-/Quote-/Listenzeichen
        text = re.sub(r'^[`>#*\-\s]+', '', text)
        # Entferne umschließende ** ** oder " "
        text = re.sub(r'^\*+(.+?)\*+$', r'\1', text)
        text = re.sub(r'^["“”\']+(.+?)["“”\']+$', r'\1', text)
        # Mehrfache Whitespaces normalisieren
        text = re.sub(r"\s+", " ", text)
    # harte Kürzung auf max_len
    out = text[:max_len].strip()
    # kosmetik: keine losen Trennzeichen am Ende
    out = re.sub(r"[-–—,:;\s]+$", "", out)
    return out


def generate_suggestions(provider: str, title: str, body_text: str, topic: str = "Projekt") -> str:
    """
    Erzeugt Anpassungs-/Verbesserungsvorschläge für ein Projekt/Rolle/Aufgabe.
    Liefert Markdown-Bulletpoints zurück, die Optimierungen (Struktur, Klarheit, Vollständigkeit) vorschlagen.
    """
    title = (title or "").strip()
    body = (body_text or "").strip()
    if not title and not body:
        return "_(keine Basis für Vorschläge)_"

    system = (
        "Du bist ein erfahrener Reviewer. "
        "Gib 3–5 konkrete Vorschläge zur Verbesserung (Struktur, Klarheit, Vollständigkeit, Praxisnähe). "
        "Format: Markdown-Bulletpoints."
    )
    user = (
        f"Gib Verbesserungsvorschläge für folgendes {topic}:\n\n"
        f"**Titel:** {title or '—'}\n\n"
        f"**Inhalt:**\n{body or '—'}\n\n"
        f"Aufgabe: 3–5 konkrete Anpassungsvorschläge als Bulletpoints."
    )

    if provider == "openai" and have_key("openai"):
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()

    if provider == "anthropic" and have_key("anthropic"):
        txt = _anthropic_try_models(system, user, max_tokens=600, temperature=0.3, preferred_alias="claude-3-5-sonnet-latest")
        if txt:
            return txt

    if provider == "mistral" and have_key("mistral"):
        from mistralai import Mistral
        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        resp = client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()

    # Fallback
    return (
        "- Struktur überprüfen und ggf. gliedern\n"
        "- Klarere Formulierungen verwenden\n"
        "- Vollständigkeit prüfen (Ziele, Verantwortlichkeiten)\n"
        "- Praxisbezug stärken\n"
        "\n> Hinweis: Kein API-Key – Stub-Vorschläge."
    )


def test_provider_connection(provider: str) -> tuple[bool, str]:
    """
    Testet die Verbindung zu einem Provider (ob API-Key vorhanden und eine Basis-Anfrage funktioniert).
    Gibt (success: bool, message: str) zurück.
    """
    if provider == "none":
        return (False, "Stub-Provider (kein Key)")
    
    if not have_key(provider):
        return (False, f"Kein API-Key für {provider.upper()}")

    # Minimal-Test: kurze Anfrage mit sehr simplen Parametern
    system = "Du bist ein Test-Assistent."
    user = "Antworte nur mit 'OK'."
    
    try:
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                max_tokens=10,
                temperature=0.0,
            )
            ans = resp.choices[0].message.content.strip()
            return (True, f"Verbunden ({ans[:20]})")
        
        elif provider == "anthropic":
            txt = _anthropic_try_models(system, user, max_tokens=10, temperature=0.0, preferred_alias="claude-3-5-sonnet-latest")
            if txt:
                return (True, f"Verbunden ({txt[:20]})")
            return (False, "Kein Modell verfügbar")
        
        elif provider == "mistral":
            from mistralai import Mistral
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            resp = client.chat.complete(
                model="mistral-large-latest",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                max_tokens=10,
                temperature=0.0,
            )
            ans = resp.choices[0].message.content.strip()
            return (True, f"Verbunden ({ans[:20]})")
        
        return (False, "Unbekannter Provider")
    
    except Exception as ex:
        msg = str(ex).lower()
        # Fehlertyp-Erkennung für präzisere Diagnose
        if "rate" in msg or "quota" in msg or "limit" in msg:
            hint = "Rate Limit"
        elif "auth" in msg or "unauthorized" in msg or "401" in msg or "api key" in msg or "invalid" in msg:
            hint = "Auth/Key"
        elif "timeout" in msg or "timed out" in msg:
            hint = "Timeout"
        elif "404" in msg or "not found" in msg:
            hint = "Modell nicht gefunden"
        elif "network" in msg or "connection" in msg:
            hint = "Network"
        else:
            hint = "Fehler"
        return (False, f"{hint}: {str(ex)[:60]}")


def test_provider_prompt(provider: str, prompt: str) -> str:
    """
    Sendet einen benutzerdefinierten Prompt an den Provider und gibt die Antwort zurück.
    Wirft LLMError bei Fehler.
    """
    if provider == "none":
        return "(Stub-Provider – keine echte Antwort)"
    
    if not have_key(provider):
        raise LLMError(f"Kein API-Key für {provider.upper()}")

    system = "Du bist ein hilfsbereiter Assistent."
    user = (prompt or "").strip() or "Hallo"

    try:
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=0.7,
                max_tokens=800,  # Token-Limit für vergleichbare Kurztests
            )
            return resp.choices[0].message.content.strip()
        
        elif provider == "anthropic":
            txt = _anthropic_try_models(system, user, max_tokens=800, temperature=0.7, preferred_alias="claude-3-5-sonnet-latest")
            if txt:
                return txt
            raise LLMError("Anthropic: Kein Modell verfügbar")
        
        elif provider == "mistral":
            from mistralai import Mistral
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            resp = client.chat.complete(
                model="mistral-large-latest",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=0.7,
                max_tokens=800,  # Token-Limit für vergleichbare Kurztests
            )
            return resp.choices[0].message.content.strip()
        
        raise LLMError("Unbekannter Provider")
    
    except Exception as ex:
        raise LLMError(f"Anfrage fehlgeschlagen: {str(ex)[:100]}")


def chat_provider_messages(provider: str, messages: list[dict]) -> str:
    """
    Sendet eine Message-History (OpenAI-Format) an den Provider.
    Format: [{"role":"user|assistant|system","content":"..."},...]
    Gibt die Antwort zurück. Wirft LLMError bei Fehler.
    """
    if provider == "none":
        return "(Stub-Provider – keine echte Chat-Antwort)"
    
    if not have_key(provider):
        raise LLMError(f"Kein API-Key für {provider.upper()}")

    if not messages:
        raise LLMError("Keine Messages übergeben")

    try:
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
            )
            return resp.choices[0].message.content.strip()
        
        elif provider == "anthropic":
            # Anthropic erwartet system separat, messages ohne system-role
            system_msgs = [m["content"] for m in messages if m.get("role") == "system"]
            system = "\n".join(system_msgs) if system_msgs else "Du bist ein hilfsbereiter Assistent."
            
            conv_msgs = [m for m in messages if m.get("role") != "system"]
            # Anthropic format: {"role":"user|assistant", "content":"..."}
            anthropic_msgs = []
            for m in conv_msgs:
                role = m.get("role", "user")
                if role not in ["user", "assistant"]:
                    role = "user"
                anthropic_msgs.append({"role": role, "content": m.get("content", "")})
            
            # Falls leer oder letztes nicht user → ergänze dummy user
            if not anthropic_msgs or anthropic_msgs[-1]["role"] != "user":
                anthropic_msgs.append({"role": "user", "content": "Bitte fortfahren."})
            
            import anthropic
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=_resolve_anthropic_model("claude-3-5-sonnet-latest"),
                system=system,
                messages=anthropic_msgs,
                max_tokens=1500,
                temperature=0.7,
            )
            return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text").strip()
        
        elif provider == "mistral":
            from mistralai import Mistral
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            resp = client.chat.complete(
                model="mistral-large-latest",
                messages=messages,
                temperature=0.7,
            )
            return resp.choices[0].message.content.strip()
        
        raise LLMError("Unbekannter Provider")
    
    except Exception as ex:
        raise LLMError(f"Chat-Anfrage fehlgeschlagen: {str(ex)[:100]}")


def generate_context_details(provider: str, description: str, context_key: str | None = None, model: str | None = None, temperature: float | None = None) -> tuple[str, str, str, str, str]:
    """
    Generate context details based on user description.
    Returns (title, short_code, short_title, description_text, body_markdown).
    Falls back to stubs when no provider is available.
    
    If context_key is provided, will fetch RAG context (projects using this context, etc.)
    model and temperature are optional parameters for LLM configuration.
    """
    description = (description or "").strip()
    if not description:
        return ("Neuer Kontext", "NK", "", "", "# Kontext\n\n(Bitte Beschreibung angeben)")

    system = (
        "Du bist ein präziser Assistent für Kontext-Dokumentation. "
        "Erstelle professionelle Kontext-Profile für Projektarbeit mit KI. "
        "Befolge das Format EXAKT wie vorgegeben."
    )
    
    rag_context = ""
    if context_key:
        try:
            from .m09_rag import retrieve_relevant_chunks, build_rag_context_from_search
            search_results = retrieve_relevant_chunks(description, limit=5, threshold=0.5)
            context_text = build_rag_context_from_search(search_results)
            if context_text:
                rag_context = f"\n\nZUSÄTZLICHER KONTEXT AUS SYSTEM:\n{context_text}"
        except Exception:
            pass
    
    user = (
        "Erstelle ein Kontext-Profil basierend auf dieser Beschreibung:\n\n"
        f"{description}{rag_context}\n\n"
        "Gib EXAKT in diesem Format zurück (mit den Überschriften, jeweils gefolgt von der Antwort):\n\n"
        "TITEL:\n"
        "(Ein prägnanter Kontext-Titel, z.B. 'Unternehmensrichtlinien zur KI-Nutzung')\n\n"
        "KURZ-TITEL:\n"
        "(3-5 Wörter, prägnante Kurzform des Titels, z.B. 'KI-Richtlinien')\n\n"
        "KÜRZEL:\n"
        "(Max 5 Zeichen, z.B. 'AIMR')\n\n"
        "KURZBESCHREIBUNG:\n"
        "(1-2 Sätze: Was ist dieser Kontext? Wozu dient er?)\n\n"
        "INHALTE:\n"
        "(Strukturierter Markdown-Text: Definitionen, Richtlinien, Rahmenbedingungen, Fachbegriffe, Systeme, etc. 300-400 Wörter.)\n\n"
        "Wichtig: Verwende GENAU diese Überschriften und gib NUR diese 5 Abschnitte zurück!"
    )

    try:
        if provider == "openai" and have_key("openai"):
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=0.6,
            )
            text = resp.choices[0].message.content.strip()
            result = _parse_context_response(text)
            if len(result) == 5:
                return result

        if provider == "anthropic" and have_key("anthropic"):
            txt = _anthropic_try_models(system, user, max_tokens=800, temperature=0.6, preferred_alias="claude-3-5-sonnet-latest")
            if txt:
                result = _parse_context_response(txt)
                if len(result) == 5:
                    return result

        if provider == "mistral" and have_key("mistral"):
            from mistralai import Mistral
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            resp = client.chat.complete(
                model="mistral-large-latest",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=0.6,
            )
            text = resp.choices[0].message.content.strip()
            result = _parse_context_response(text)
            if len(result) == 5:
                return result
    except Exception as e:
        pass
    
    return ("Neuer Kontext", "NK", "", "", f"# Kontext\n\n> ⚠️ KI-Generierung fehlgeschlagen (Provider '{provider}' nicht verfügbar oder kein API-Key). Bitte manuell eintragen.")


def _parse_context_response(text: str) -> tuple[str, str, str, str, str]:
    """Parse KI response for context generation."""
    lines = (text or "").split("\n")
    sections = {"TITEL": "", "KURZ-TITEL": "", "KÜRZEL": "", "KURZBESCHREIBUNG": "", "INHALTE": ""}
    current_section = None
    
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.rstrip(":") in sections:
            current_section = line_stripped.rstrip(":")
        elif current_section and line_stripped:
            if sections[current_section]:
                sections[current_section] += "\n" + line_stripped
            else:
                sections[current_section] = line_stripped
    
    title = sections.get("TITEL", "").strip() or "Neuer Kontext"
    short_title = sections.get("KURZ-TITEL", "").strip()
    short_code = sections.get("KÜRZEL", "").strip() or "NK"
    desc = sections.get("KURZBESCHREIBUNG", "").strip()
    body = sections.get("INHALTE", "").strip()
    
    if not body:
        body = f"# {title}\n\n{desc}"
    
    return (title, short_code, short_title, desc, body)


def generate_project_details(provider: str, description: str, 
                            role_descriptions: str = "", 
                            context_descriptions: str = "",
                            project_key: str | None = None,
                            model: str | None = None,
                            temperature: float | None = None) -> tuple[str, str, str, str, str]:
    """
    Generate project details based on project description, assigned roles, and contexts.
    Returns (title, short_code, short_title, description_text, body_markdown).
    Falls back to stubs when no provider is available.
    
    If project_key is provided, will fetch RAG context (role/context/task details) from DB.
    model and temperature are optional parameters for LLM configuration.
    """
    description = (description or "").strip()
    if not description:
        return ("Neues Projekt", "NP", "", "", "# Projekt\n\n(Bitte Projektbeschreibung angeben)")

    system = (
        "Du bist ein präziser Projekt-Manager und Assistent. "
        "Erstelle professionelle Projekt-Briefings für KI-gestützte Arbeit. "
        "Befolge das Format EXAKT wie vorgegeben."
    )
    
    role_context = ""
    if role_descriptions.strip():
        role_context = f"\n\nZugeordnete Rollen:\n{role_descriptions}"
    
    context_context = ""
    if context_descriptions.strip():
        context_context = f"\n\nZugeordnete Kontexte:\n{context_descriptions}"
    
    # RAG-Kontext: Falls project_key gegeben ist, hole zusätzliche Details
    rag_context = ""
    if project_key:
        try:
            from .m09_rag import retrieve_relevant_chunks, build_rag_context_from_search
            search_results = retrieve_relevant_chunks(description, limit=5, threshold=0.5)
            context_text = build_rag_context_from_search(search_results, project_key=project_key)
            if context_text:
                rag_context = f"\n\nAUS DER SYSTEM-DATENBANK VERFÜGBARE KONTEXTE:\n{context_text}"
        except Exception:
            pass
    
    user = (
        "Erstelle ein DETAILLIERTES, STRUKTURIERTES Projekt-Briefing basierend auf dieser Projektbeschreibung:\n\n"
        f"{description}"
        f"{role_context}"
        f"{context_context}"
        f"{rag_context}"
        "\n\nGib EXAKT in diesem Format zurück (mit den Überschriften, jeweils gefolgt von der Antwort):\n\n"
        "TITEL:\n"
        "(Ein prägnanter Projekt-Titel, z.B. 'Entwicklung einer modularen Verwaltungssoftware für Service-Weiterbildung')\n\n"
        "KURZ-TITEL:\n"
        "(3-5 Wörter, prägnante Kurzform des Titels, z.B. 'Modulare Verwaltungssoftware Service-Weiterbildung')\n\n"
        "KÜRZEL:\n"
        "(Max 5 Zeichen, z.B. 'MVSWB')\n\n"
        "KURZBESCHREIBUNG:\n"
        "(1-2 Sätze: Was ist das Ziel des Projekts? Scope und Abgrenzung, z.B. 'Ziel des Projekts ist die Entwicklung einer modernen App zur Abbildung aller Geschäftsprozesse des Teams Service-Weiterbildung unter Verwendung einer headless-Architektur, um Flexibilität und Zukunftsfähigkeit zu gewährleisten.')\n\n"
        "BRIEFING:\n"
        "(WICHTIG: Sehr detailliertes, strukturiertes Markdown-Projektbriefing mit ALLEN folgenden Sections. Mind. 1500-2000 Wörter.\n"
        "Struktur EXAKT so:\n\n"
        "1. Projektsteckbrief\n"
        "  - Titel\n"
        "  - Projekttyp\n"
        "  - Ausgangslage (detailliert: Was ist das Problem? Warum ist Änderung nötig?)\n\n"
        "2. Ziele\n"
        "  2.1 Hauptziel (umfassend, klar definiert)\n"
        "  2.2 Teilziele (fachlich) - 3-4 konkrete fachliche Teilziele\n"
        "  2.3 Teilziele (technisch) - 4-5 konkrete technische Teilziele (z.B. Architektur, Modularität, Wartbarkeit, Sicherheit)\n"
        "  2.4 Erfolgskriterien - Konkrete, messbare Erfolgskriterien\n\n"
        "3. Scope\n"
        "  3.1 Im Scope - Detaillierte Liste ALLER Aufgaben/Komponenten die im Projekt enthalten sind\n"
        "  3.2 Nicht im Scope (Abgrenzung) - Explizite Abgrenzung: Was wird NICHT gemacht?\n\n"
        "4. Meilensteine und Zeitplan - Detaillierte Meilensteine mit Zeitleisten und Ergebnissen\n\n"
        "5. Deliverables - Vollständige Liste aller Liefergegenstände\n\n"
        "6. Risiken und Maßnahmen - Detaillierte Tabelle oder Liste mit Risiken, Auswirkungen und Mitigations-Maßnahmen\n\n"
        "7. Nächste sinnvolle Schritte - Konkrete nächste Schritte die logisch folgen\n\n"
        "Sein Sie KONKRET, SPEZIFISCH und DETAILLIERT. Nutzen Sie Beispiele und spezifische Anforderungen aus der Projektbeschreibung.)\n\n"
        "Wichtig: Verwende GENAU diese 5 Überschriften (TITEL, KURZ-TITEL, KÜRZEL, KURZBESCHREIBUNG, BRIEFING) und gib NUR diese 5 Abschnitte zurück!"
    )

    try:
        if provider == "openai" and have_key("openai"):
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=0.6,
            )
            text = resp.choices[0].message.content.strip()
            result = _parse_project_response(text)
            if len(result) == 5:
                return result

        if provider == "anthropic" and have_key("anthropic"):
            txt = _anthropic_try_models(system, user, max_tokens=2500, temperature=0.6, preferred_alias="claude-3-5-sonnet-latest")
            if txt:
                result = _parse_project_response(txt)
                if len(result) == 5:
                    return result

        if provider == "mistral" and have_key("mistral"):
            from mistralai import Mistral
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            resp = client.chat.complete(
                model="mistral-large-latest",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=0.6,
            )
            text = resp.choices[0].message.content.strip()
            result = _parse_project_response(text)
            if len(result) == 5:
                return result
    except Exception as e:
        pass

    return ("Neues Projekt", "NP", "", "", f"# Projekt\n\n> ⚠️ KI-Generierung fehlgeschlagen (Provider '{provider}' nicht verfügbar oder kein API-Key). Bitte manuell eintragen.")


def _parse_project_response(text: str) -> tuple[str, str, str, str, str]:
    """Parse KI response for project generation."""
    lines = (text or "").split("\n")
    sections = {"TITEL": "", "KURZ-TITEL": "", "KÜRZEL": "", "KURZBESCHREIBUNG": "", "BRIEFING": ""}
    current_section = None
    
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.rstrip(":") in sections:
            current_section = line_stripped.rstrip(":")
        elif current_section and line_stripped:
            if sections[current_section]:
                sections[current_section] += "\n" + line_stripped
            else:
                sections[current_section] = line_stripped
    
    title = sections.get("TITEL", "").strip() or "Neues Projekt"
    short_title = sections.get("KURZ-TITEL", "").strip()
    short_code = sections.get("KÜRZEL", "").strip() or "NP"
    desc = sections.get("KURZBESCHREIBUNG", "").strip()
    briefing = sections.get("BRIEFING", "").strip()
    
    if not briefing:
        briefing = f"# {title}\n\n{desc}"
    
    return (title, short_code, short_title, desc, briefing)


# ==================== EMBEDDINGS ====================

EMBEDDING_PROVIDERS = {
    "openai": "text-embedding-3-small",
    "sentence-transformers": "all-MiniLM-L6-v2",
    "huggingface": "sentence-transformers/all-MiniLM-L6-v2",
}

def get_embedding_provider() -> str:
    """Returns configured embedding provider or default."""
    provider = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
    return provider if provider in EMBEDDING_PROVIDERS else "openai"


def embed_text(text: str, provider: str | None = None) -> list[float] | None:
    """
    Embeds text using configured provider.
    
    Args:
        text: Text to embed
        provider: Embedding provider ("openai", "sentence-transformers", "huggingface")
                 If None, uses default from config
    
    Returns:
        Embedding vector as list[float] or None on failure
    """
    if not text or not text.strip():
        return None
    
    provider = provider or get_embedding_provider()
    
    if provider == "openai" and have_key("openai"):
        return _embed_openai(text)
    elif provider == "sentence-transformers":
        return _embed_sentence_transformers(text)
    elif provider == "huggingface" and have_key("huggingface"):
        return _embed_huggingface(text)
    
    return None


def _embed_openai(text: str) -> list[float] | None:
    """Embed text using OpenAI API."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model=EMBEDDING_PROVIDERS["openai"],
            input=text.strip()
        )
        return response.data[0].embedding
    except Exception as e:
        return None


def _embed_sentence_transformers(text: str) -> list[float] | None:
    """Embed text using sentence-transformers locally."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBEDDING_PROVIDERS["sentence-transformers"])
        embedding = model.encode(text.strip(), convert_to_tensor=False)
        return embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)
    except Exception as e:
        return None


def _embed_huggingface(text: str) -> list[float] | None:
    """Embed text using HuggingFace API."""
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=os.environ.get("HUGGINGFACE_API_KEY"))
        response = client.text_to_embedding(text.strip(), model=EMBEDDING_PROVIDERS["huggingface"])
        return response
    except Exception as e:
        return None


def batch_embed_texts(texts: list[str], provider: str | None = None) -> list[list[float] | None]:
    """
    Embeds multiple texts in batch.
    
    Args:
        texts: List of texts to embed
        provider: Embedding provider
    
    Returns:
        List of embeddings (or None for failed texts)
    """
    provider = provider or get_embedding_provider()
    
    if provider == "openai" and have_key("openai"):
        return _batch_embed_openai(texts)
    elif provider == "sentence-transformers":
        return _batch_embed_sentence_transformers(texts)
    elif provider == "huggingface" and have_key("huggingface"):
        return _batch_embed_huggingface(texts)
    
    return [None] * len(texts)


def _batch_embed_openai(texts: list[str]) -> list[list[float] | None]:
    """Batch embed using OpenAI API."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model=EMBEDDING_PROVIDERS["openai"],
            input=[t.strip() for t in texts if t.strip()]
        )
        embeddings_map = {d.index: d.embedding for d in response.data}
        return [embeddings_map.get(i) for i in range(len(texts))]
    except Exception as e:
        return [None] * len(texts)


def _batch_embed_sentence_transformers(texts: list[str]) -> list[list[float] | None]:
    """Batch embed using sentence-transformers locally."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBEDDING_PROVIDERS["sentence-transformers"])
        embeddings = model.encode([t.strip() for t in texts if t.strip()], convert_to_tensor=False)
        result = []
        embed_idx = 0
        for text in texts:
            if text.strip():
                emb = embeddings[embed_idx]
                result.append(emb.tolist() if hasattr(emb, 'tolist') else list(emb))
                embed_idx += 1
            else:
                result.append(None)
        return result
    except Exception as e:
        return [None] * len(texts)


def _batch_embed_huggingface(texts: list[str]) -> list[list[float] | None]:
    """Batch embed using HuggingFace API."""
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=os.environ.get("HUGGINGFACE_API_KEY"))
        result = []
        for text in texts:
            if text.strip():
                emb = client.text_to_embedding(text.strip(), model=EMBEDDING_PROVIDERS["huggingface"])
                result.append(emb)
            else:
                result.append(None)
        return result
    except Exception as e:
        return [None] * len(texts)
