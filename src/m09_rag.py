# src/m09_rag.py
"""Echtes RAG (Retrieval-Augmented Generation) mit Embeddings."""

from __future__ import annotations
import json
import logging
import os
import math

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from pathlib import Path
from sqlmodel import select

# PHASE 3: Config-basierte Retrieval-Parameter (statt Hardcoding)
from src.m01_retrieval_config import get_retrieval_config
from .m03_db import Role, Task, Context, Project, get_session, ChatMessage, Decision, Document, DocumentChunk, ProjectDocumentLink


# Lazy import um zirkuläre Abhängigkeiten zu vermeiden
def _get_generate_query_hypotheses():
    from src.m08_llm import generate_query_hypotheses
    return generate_query_hypotheses

def _get_rewrite_query():
    from src.m08_llm import rewrite_query_for_retrieval
    return rewrite_query_for_retrieval


EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536

_RAG_CACHE = {}  # Session-local cache: query_hash -> results

def _get_cache_key(query: str, project_key: str | None, limit: int, threshold: float,
                   exclude_classification: str | None = None) -> str:
    """Generiert Cache-Key aus Query-Parametern inkl. exclude_classification."""
    import hashlib
    key_str = f"{query}|{project_key}|{limit}|{threshold}|{exclude_classification}"
    return hashlib.md5(key_str.encode()).hexdigest()

def clear_rag_cache():
    """Löscht RAG-Cache (z.B. wenn Dokumente neu indexed)."""
    global _RAG_CACHE
    _RAG_CACHE.clear()

def _read_file_safe(file_path: str | None) -> str:
    """Liest eine Datei sicher (falls vorhanden), gibt leeren String zurück sonst."""
    if not file_path:
        return ""
    try:
        p = Path(file_path)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def embed_text(text: str) -> list[float] | None:
    """
    Erstellt ein Embedding für einen Text via OpenAI text-embedding-3-small.
    Returns: Liste von Floats (1536-dimensional) oder None bei Fehler.
    """
    text = (text or "").strip()
    if not text:
        return None
    
    # Token-Safety: OpenAI text-embedding-3-small max 8192 Tokens
    # Konservativ: max 6000 Zeichen (~1500 Tokens)
    MAX_CHARS = 6000
    if len(text) > MAX_CHARS:
        print(f"⚠️ Text zu lang ({len(text)} Zeichen), kürze auf {MAX_CHARS}")
        text = text[:MAX_CHARS]
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
            encoding_format="float"
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"❌ Embedding-Fehler: {str(e)}")
        return None


def embed_texts_batch(texts: list[str], batch_size: int = 512) -> list[list[float] | None]:
    """
    Erstellt Embeddings für mehrere Texte in Batches (1 API-Call pro Batch).
    Deutlich schneller als embed_text() in einer Schleife.
    
    WICHTIG: OpenAI API-Limit ist 8192 Tokens GESAMT pro Request (nicht pro Text).
    Diese Funktion splittet automatisch in kleinere Batches wenn nötig.
    
    Args:
        texts: Liste von Texten
        batch_size: Max. Inputs pro API-Call (wird dynamisch reduziert bei Token-Überlauf)
    Returns:
        Liste von Embeddings (gleiche Länge wie texts), None-Einträge bei Fehler.
    """
    results: list[list[float] | None] = [None] * len(texts)
    if not texts:
        return results

    # Token-Limit: OpenAI text-embedding-3-small max 8192 Tokens GESAMT pro Batch
    # Konservativ: max 6000 Zeichen pro Text (~1500 Tokens)
    # Und max 20 Texte pro Batch (20 × 300 Zeichen avg = ~1500 Tokens gesamt)
    MAX_CHARS_PER_TEXT = 6000
    MAX_BATCH_SIZE = 20  # Konservativ: 20 Texte à ~300 Zeichen = ~1500 Tokens

    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Verwende kleineren Batch-Size
        effective_batch_size = min(batch_size, MAX_BATCH_SIZE)

        for start in range(0, len(texts), effective_batch_size):
            batch = texts[start:start + effective_batch_size]
            clean_batch = [(t or "").strip() for t in batch]
            
            # Token-Safety: Truncate zu lange Texte
            safe_batch = []
            for i, t in enumerate(clean_batch):
                if len(t) > MAX_CHARS_PER_TEXT:
                    print(f"⚠️ Text {start + i} zu lang ({len(t)} Zeichen), kürze auf {MAX_CHARS_PER_TEXT}")
                    safe_batch.append(t[:MAX_CHARS_PER_TEXT])
                else:
                    safe_batch.append(t)
            
            # Leere Texte überspringen aber Index behalten
            non_empty = [(i, t) for i, t in enumerate(safe_batch) if t]
            if not non_empty:
                continue
            indices, inputs = zip(*non_empty)
            
            # API Call mit Fehlerbehandlung
            try:
                response = client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=list(inputs),
                    encoding_format="float"
                )
                for result_pos, global_idx in enumerate(indices):
                    results[start + global_idx] = response.data[result_pos].embedding
            except Exception as batch_error:
                # Falls Batch zu groß: Einzeln probieren (Fallback)
                if "maximum context length" in str(batch_error).lower():
                    print(f"⚠️ Batch zu groß, verarbeite {len(inputs)} Texte einzeln...")
                    for idx, input_text in zip(indices, inputs):
                        try:
                            single_response = client.embeddings.create(
                                model=EMBEDDING_MODEL,
                                input=input_text,
                                encoding_format="float"
                            )
                            results[start + idx] = single_response.data[0].embedding
                        except Exception as e:
                            print(f"❌ Embedding für Text {start + idx} fehlgeschlagen: {e}")
                else:
                    raise  # Anderen Fehler weiterwerfen
                    
    except Exception as e:
        print(f"❌ Batch-Embedding-Fehler: {str(e)}")

    return results


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Berechnet Cosine Similarity zwischen zwei Vektoren."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    magnitude_a = math.sqrt(sum(a ** 2 for a in vec_a))
    magnitude_b = math.sqrt(sum(b ** 2 for b in vec_b))
    
    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0
    
    return dot_product / (magnitude_a * magnitude_b)


# ═══════════════════════════════════════════════════════════════════════════
# RERANKING: Intelligente Neugewichtung der Top-K Kandidaten
# ═══════════════════════════════════════════════════════════════════════════

def _rerank_documents(
    documents: list[dict],
    query: str,
    mode: str = "score",
    final_k: int = 7,
    llm_provider: str = "openai",
    llm_model: str = "gpt-4o-mini",
    llm_temperature: float = 0.0,
    llm_batch_size: int = 5,
) -> list[dict]:
    """
    Rerankt Dokumente basierend auf Score oder LLM-Bewertung.
    
    Args:
        documents: Liste von Dokument-Dicts (mit similarity, match_score, etc.)
        query: Original-Query
        mode: "score" = Score-basiert | "llm" = LLM-Reranking
        final_k: Anzahl finaler Ergebnisse
        llm_provider, llm_model, llm_temperature: LLM-Config für mode="llm"
        llm_batch_size: Chunks pro LLM-Call
    
    Returns:
        Top-K Dokumente nach Reranking, sortiert nach Relevanz
    """
    if not documents:
        return []
    
    if mode == "score":
        return _rerank_by_score(documents, final_k)
    elif mode == "llm":
        return _rerank_by_llm(documents, query, final_k, llm_provider, llm_model, llm_temperature, llm_batch_size)
    else:
        # Fallback: Nimm einfach Top-K
        return documents[:final_k]


def _rerank_by_score(documents: list[dict], final_k: int) -> list[dict]:
    """
    Score-based Reranking: Gewichteter Score aus allen verfügbaren Signalen.
    
    Scoring-Strategie:
    - Semantic: 60% Gewicht (Hauptsignal)
    - BM25: 25% Gewicht (Keyword-Matching)
    - Filename-Match: 10% Boost
    - Chunk-Position: 5% Boost (frühere Chunks = wichtiger)
    """
    scored = []
    for doc in documents:
        # Hauptscores (0-1 Range)
        semantic = doc.get("similarity", 0.0)
        bm25_normalized = doc.get("normalized_match_score", 0.0)
        
        # Filename-Boost (0.0 oder 0.1)
        filename_boost = 0.1 if doc.get("filename_matched", False) else 0.0
        
        # Position-Boost: Frühe Chunks sind oft wichtiger (first 3 chunks get small boost)
        chunk_idx = doc.get("chunk_index", 999)
        position_boost = max(0, (3 - chunk_idx) * 0.02) if chunk_idx < 3 else 0.0
        
        # Gewichteter Final-Score
        rerank_score = (
            semantic * 0.60 +
            bm25_normalized * 0.25 +
            filename_boost +
            position_boost
        )
        
        doc["rerank_score"] = round(rerank_score, 4)
        scored.append((rerank_score, doc))
    
    # Sortiere nach Rerank-Score
    scored.sort(key=lambda x: x[0], reverse=True)
    
    return [doc for _, doc in scored[:final_k]]


def _rerank_by_llm(
    documents: list[dict],
    query: str,
    final_k: int,
    provider: str,
    model: str,
    temperature: float,
    batch_size: int,
) -> list[dict]:
    """
    LLM-based Reranking: LLM bewertet Relevanz jedes Chunks.
    
    Strategie:
    - Batch-Verarbeitung (mehrere Chunks pro LLM-Call für Cost-Optimierung)
    - LLM gibt Score 0-100 pro Chunk
    - Sortiere nach LLM-Score
    """
    from .m08_llm import try_models_with_messages
    
    scored = []
    
    # Batch-Verarbeitung
    for i in range(0, len(documents), batch_size):
        batch = documents[i:i + batch_size]
        
        # Prompt für LLM
        chunks_text = ""
        for idx, doc in enumerate(batch):
            preview = (doc.get("text") or "")[:300]
            chunks_text += f"\n## Chunk {idx+1}\n**Datei:** {doc.get('filename', '?')}\n**Text:** {preview}\n"
        
        system_prompt = """Du bist ein Retrieval-Relevanz-Experte. 
Bewerte die Relevanz jedes Chunks zur gegebenen Frage.

Score-Skala:
- 90-100: Perfekte Übereinstimmung (beantwortet Frage direkt)
- 70-89: Sehr relevant (enthält wichtige Infos)
- 50-69: Teilweise relevant (tangiert Thema)
- 30-49: Marginal relevant (verwandte Begriffe)
- 0-29: Irrelevant

Gib NUR eine JSON-Array zurück: [score1, score2, ...]
Beispiel: [85, 42, 91]"""
        
        user_prompt = f"""Frage: {query}

{chunks_text}

Bewerte alle {len(batch)} Chunks. Antworte NUR mit JSON-Array der Scores."""
        
        try:
            response = try_models_with_messages(
                provider=provider,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=100,
                temperature=temperature,
                model=model,
            )
            
            # Parse JSON-Array
            import json
            scores = json.loads(response.strip())
            
            # Validierung
            if not isinstance(scores, list) or len(scores) != len(batch):
                # Fallback: Use semantic scores
                scores = [doc.get("similarity", 0.5) * 100 for doc in batch]
            
            # Scores zu Dokumenten hinzufügen
            for doc, score in zip(batch, scores):
                doc["llm_relevance_score"] = int(score)
                doc["rerank_score"] = score / 100.0  # Normalisieren auf 0-1
                scored.append((score, doc))
        
        except Exception as e:
            logger.warning(f"LLM-Reranking Fehler: {e} - Fallback auf Semantic-Score")
            # Fallback
            for doc in batch:
                fallback_score = doc.get("similarity", 0.5) * 100
                doc["rerank_score"] = fallback_score / 100.0
                scored.append((fallback_score, doc))
    
    # Sortiere nach LLM-Score
    scored.sort(key=lambda x: x[0], reverse=True)
    
    return [doc for _, doc in scored[:final_k]]


def index_role(role_key: str, force: bool = False) -> bool:
    """
    Erstellt Embedding für eine Rolle und speichert es in der DB.
    
    Args:
        role_key: Key der Rolle
        force: Falls True, überschreibe existierendes Embedding
    
    Returns: True wenn erfolgreich, False sonst.
    """
    with get_session() as ses:
        role = ses.exec(select(Role).where(Role.key == role_key)).first()
        if not role or role.is_deleted:
            return False
        
        # Skip wenn bereits indexed und nicht force=True
        if role.embedding and not force:
            return True
        
        # Text zusammenstellen
        text_parts = [
            role.title or "",
            role.description or "",
            role.responsibilities or "",
            role.qualifications or "",
            role.expertise or ""
        ]
        text = "\n".join([p for p in text_parts if p])
        
        if not text:
            return False
        
        # Embedding generieren
        embedding = embed_text(text)
        if not embedding:
            return False
        
        # Speichern
        role.embedding = json.dumps(embedding)
        role.embedding_model = EMBEDDING_MODEL
        ses.add(role)
        ses.commit()
    
    return True


def index_context(context_key: str, force: bool = False) -> bool:
    """Erstellt Embedding für einen Kontext."""
    with get_session() as ses:
        context = ses.exec(select(Context).where(Context.key == context_key)).first()
        if not context or context.is_deleted:
            return False
        
        if context.embedding and not force:
            return True
        
        text_parts = [
            context.title or "",
            context.description or "",
            _read_file_safe(context.text_path)
        ]
        text = "\n".join([p for p in text_parts if p])
        
        if not text:
            return False
        
        embedding = embed_text(text)
        if not embedding:
            return False
        
        context.embedding = json.dumps(embedding)
        context.embedding_model = EMBEDDING_MODEL
        ses.add(context)
        ses.commit()
    
    return True


def index_project(project_key: str, force: bool = False) -> bool:
    """
    b) Erweitert: Erstellt Embedding für ein Projekt inkl. Rollen- und Kontext-Namen.
    
    Sammelt: Projekttitel, -beschreibung, -text, + Namen der verknüpften Rollen & Kontexte
    """
    with get_session() as ses:
        project = ses.exec(select(Project).where(Project.key == project_key)).first()
        if not project or project.is_deleted:
            return False
        
        if project.embedding and not force:
            return True
        
        text_parts = [
            project.title or "",
            project.description or "",
            _read_file_safe(project.text_path)
        ]
        
        if project.role_keys:
            try:
                role_keys = json.loads(project.role_keys) if isinstance(project.role_keys, str) else project.role_keys
                if isinstance(role_keys, list):
                    for role_key in role_keys:
                        role = ses.exec(select(Role).where(Role.key == role_key)).first()
                        if role:
                            text_parts.append(f"Rolle: {role.title}")
                            if role.description:
                                text_parts.append(role.description)
                            
                            # Aufgaben der Rolle hinzufügen
                            tasks = ses.exec(select(Task).where(Task.source_role_key == role.key)).all()
                            if tasks:
                                text_parts.append(f"Aufgaben für {role.title}:")
                                for task in tasks:
                                    text_parts.append(f"- {task.title}")
                                    if task.description:
                                        text_parts.append(f"  {task.description}")
            except Exception:
                pass
        
        if project.context_keys:
            try:
                context_keys = json.loads(project.context_keys) if isinstance(project.context_keys, str) else project.context_keys
                if isinstance(context_keys, list):
                    for context_key in context_keys:
                        context = ses.exec(select(Context).where(Context.key == context_key)).first()
                        if context:
                            text_parts.append(f"Kontext: {context.title}")
                            if context.description:
                                text_parts.append(context.description)
            except Exception:
                pass
        
        text = "\n".join([p for p in text_parts if p])
        
        # Token-Safety: Kürze zu langen Projekt-Text
        # Max 6000 Zeichen (~1500 Tokens) für embed_text()
        MAX_PROJECT_TEXT = 6000
        if len(text) > MAX_PROJECT_TEXT:
            print(f"⚠️ Projekt-Text zu lang ({len(text)} Zeichen), kürze auf {MAX_PROJECT_TEXT}")
            text = text[:MAX_PROJECT_TEXT]
        
        if not text:
            return False
        
        embedding = embed_text(text)
        if not embedding:
            return False
        
        project.embedding = json.dumps(embedding)
        project.embedding_model = EMBEDDING_MODEL
        ses.add(project)
        ses.commit()
    
    return True


def index_task(task_key: str, force: bool = False) -> bool:
    """Erstellt Embedding für eine Task."""
    with get_session() as ses:
        task = ses.exec(select(Task).where(Task.key == task_key)).first()
        if not task or task.is_deleted:
            return False
        
        if task.embedding and not force:
            return True
        
        text_parts = [
            task.title or "",
            task.description or "",
            _read_file_safe(task.text_path)
        ]
        text = "\n".join([p for p in text_parts if p])
        
        if not text:
            return False
        
        embedding = embed_text(text)
        if not embedding:
            return False
        
        task.embedding = json.dumps(embedding)
        task.embedding_model = EMBEDDING_MODEL
        ses.add(task)
        ses.commit()
    
    return True


# ---------------------------------------------------------------------------
# spaCy Lazy Singleton – wird beim ersten BM25-Aufruf geladen (nicht beim Import)
# ---------------------------------------------------------------------------
_SPACY_NLP = None

def _get_spacy_nlp():
    """Gibt das geladene spaCy-Modell zurück (Singleton, lazy init)."""
    global _SPACY_NLP
    if _SPACY_NLP is None:
        try:
            import spacy
            # Nur Tagger (für POS + Lemmatizer) – Parser + NER nicht nötig
            _SPACY_NLP = spacy.load("de_core_news_sm", disable=["parser", "ner"])
        except Exception as e:
            print(f"⚠️ spaCy-Modell nicht ladbar ({e}) – Fallback auf einfaches Stemming")
            _SPACY_NLP = False  # Sentinel: Fehler, nicht nochmal versuchen
    return _SPACY_NLP if _SPACY_NLP is not False else None


# Lazy-init für compound_split (einmalig laden)
_COMPOUND_SPLIT_AVAILABLE: bool | None = None

def _german_stem(lemma: str) -> str:
    """
    Einfacher deutscher Suffix-Stemmer für morphologische Varianten.
    
    Entfernt häufige Endungen die spaCy-Lemmatisierung nicht vereinheitlicht:
    - Partizipformen: anbieten**d** → anbiet, subunternehmen**d** → subunternehm
    - Nominalisierungen: anbieten**de** → anbiet
    - Stammformen: anbiete**e** → anbiet (seltener)
    
    Wird NACH spaCy-Lemmatisierung angewendet, um morphologische Lücken zu schließen.
    So matchen "Anbieter" und "Anbietende", "Subunternehmen" und "Subunternehmende".
    """
    if len(lemma) < 5:  # Zu kurze Wörter nicht stemmen
        return lemma
    
    # Suffix-Regeln: längste zuerst (greedy matching)
    for suffix in ['ende', 'end', 'e']:
        if lemma.endswith(suffix):
            stem = lemma[:-len(suffix)]
            if len(stem) >= 4:  # Stamm muss sinnvoll lang sein
                return stem
    
    return lemma


def _decompound(lemma: str) -> list[str]:
    """
    Zerlegt ein deutsches Kompositum in seine Bestandteile (zusätzlich zum Originalwort).
    Nutzt compound_split.doc_split.get_best_split.

    Regeln:
    - Wort muss >= 8 Zeichen haben (kurze Wörter nicht splitten)
    - Alle Teile müssen >= 4 Zeichen haben (verhindert 'Ange'+'Bot' für 'Angebot')
    - Gibt leere Liste zurück wenn kein sinnvoller Split (Original bleibt erhalten)
    - Lowercase, kein Lemmatizing der Teile (BM25 matcht case-insensitive)
    """
    global _COMPOUND_SPLIT_AVAILABLE

    if _COMPOUND_SPLIT_AVAILABLE is False:
        return []
    if len(lemma) < 8:
        return []

    try:
        if _COMPOUND_SPLIT_AVAILABLE is None:
            from compound_split.doc_split import get_best_split  # noqa
            _COMPOUND_SPLIT_AVAILABLE = True
        from compound_split.doc_split import get_best_split
        parts = get_best_split(lemma)
    except Exception:
        _COMPOUND_SPLIT_AVAILABLE = False
        return []

    if len(parts) <= 1:
        return []  # Kein Split gefunden

    parts_lower = [p.lower()[:-1]  # Fugen-s entfernen ("Ausschreibungs" -> "ausschreibung")
                   if p.lower().endswith('s') and len(p) > 5 else p.lower()
                   for p in parts]

    # Qualitäts-Filter: alle Teile >= 4 Zeichen
    if any(len(p) < 4 for p in parts_lower):
        return []

    return parts_lower


# Stopwords: häufige, bedeutungsarme Wörter (Modul-Level für Wiederverwendung)
_STOPWORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines",
    "und", "oder", "aber", "mit", "von", "zu", "bei", "für", "auf", "an", "in",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "ist", "sind", "war", "waren",
    "wird", "werden", "hat", "haben", "kann", "können", "soll", "sollte", "muss",
    "wie", "was", "wer", "wo", "wann", "warum", "welche", "welcher", "welches",
    "bitte", "antworten", "antwort", "frage", "anbieter", "hier", "kurz", "bündig",
    "betrifft", "konkret", "gilt", "gelten", "stellt", "stelltsich", "nutzung",
    "interner", "interne", "unserer", "unsere", "unser", "einer", "einem", "einen"
}


def _tokenize_text(text: str) -> list[str]:
    """
    Tokenisiert Text via spaCy-Lemmatisierung (de_core_news_sm).
    
    - Volltext-Analyse mit POS-Tagging für kontextuelle Lemmas
    - Lowercase Lemmas
    - Stopwords (spaCy + custom) raus
    - Nur Alpha-Tokens, min 3 Zeichen
    - Fallback auf einfaches Suffix-Stemming wenn spaCy nicht verfügbar
    
    Diese Funktion wird sowohl von BM25-Suche als auch von der Debug-Anzeige verwendet.
    """
    import re
    nlp = _get_spacy_nlp()
    
    if nlp is not None:
        # spaCy-Pfad: Volltext → POS + Lemma → Suffix-Stemming
        doc = nlp(text[:25000])  # Safety-Cap für sehr lange Chunks
        tokens = []
        for token in doc:
            # Erweiterte Prüfung: Alpha ODER Alphanumerisch (ISO9001, 24/7, SLA-1)
            # Entfernt Bindestriche/Slashes temporär für .isalnum() Check
            token_clean = token.text.replace('-', '').replace('/', '')
            if not (token.is_alpha or token_clean.isalnum()):
                continue
            lemma = token.lemma_.lower()
            if len(lemma) < 2:  # Reduziert von 3 auf 2 für Codes wie "V1"
                continue
            if lemma in _STOPWORDS or token.is_stop:
                continue
            # Suffix-Stemming für morphologische Varianten (Partizipien etc.)
            stemmed = _german_stem(lemma)
            tokens.append(stemmed)
            # Decompounding: nur bei Nomen/Eigennamen (Komposita sind im Deutschen
            # fast ausschliesslich Substantive; verhindert seltsame Verb-Splits)
            if token.pos_ in ("NOUN", "PROPN"):
                for part in _decompound(stemmed):
                    if part not in _STOPWORDS and part not in tokens:
                        tokens.append(part)
        return tokens
    else:
        # Fallback: einfaches Regex-Stemming (kein spaCy verfügbar)
        words = re.findall(r'\b[a-zA-ZäöüÄÖÜß]+\b', text.lower())
        tokens = []
        for w in words:
            if len(w) >= 3 and w not in _STOPWORDS:
                tokens.append(w)
        return tokens


def _keyword_search(
    query: str, 
    project_key: str | None = None, 
    limit: int = 5,
    role_key: str | None = None,
    classification_filter: str | None = None,
    exclude_classification: str | None = None
) -> dict:
    """
    BM25-basierte Keyword-Suche (Goldstandard für lexikalisches Matching).
    
    BM25 berechnet Relevance-Score basierend auf:
    - Term Frequency (TF): Wie oft kommt das Wort im Dokument vor?
    - Inverse Document Frequency (IDF): Wie selten ist das Wort im Korpus?
    - Document Length Normalization: Kurze Dokumente werden nicht benachteiligt
    
    Args:
        query: Die Nutzer-Query
        project_key: Optional: Filter für Dokumente
        limit: Max Resultate
        role_key: Optional: Filter für Rollen-spezifische Dokumente
        classification_filter: Optional: Filter für Dokument-Klassifizierung
        exclude_classification: Optional: Dokumente mit DIESER Klassifizierung ausschliessen
    Returns: List mit keyword-gefundenen Chunks
    """
    from rank_bm25 import BM25Okapi

    def _strip_query_wrapper(text: str) -> str:
        """Entfernt UI-Wrapper wie 'Frage von Anbieter ...:' und behält die eigentliche Nutzfrage."""
        lowered = text.lower()
        marker = "frage von anbieter"
        if marker in lowered and ":" in text:
            return text.split(":", 1)[1].strip()
        return text.strip()
    
    # Priority Terms: globale Defaults aus YAML + projekt-spezifische aus DB (merged, dedupliziert)
    config = get_retrieval_config()
    priority_parts: list[str] = list(config.bm25.priority_terms)
    if project_key:
        try:
            with get_session() as _ps:
                from sqlmodel import select as _sel
                from src.m03_db import Project as _Project
                _proj = _ps.exec(_sel(_Project).where(_Project.key == project_key)).first()
                if _proj and _proj.rag_priority_terms:
                    import json as _json
                    _proj_terms = _json.loads(_proj.rag_priority_terms)
                    for t in _proj_terms:
                        if t not in priority_parts:
                            priority_parts.append(t)
        except Exception:
            pass

    def _prepare_query_tokens(raw_query: str) -> list[str]:
        """Fokussiert lange UI-Fragen auf die inhaltlich relevanten Suchterme."""
        # ===== DEBUG LOGGING =====
        logger.debug(f"[_prepare_query_tokens] INPUT: {raw_query!r}")
        # ===== END DEBUG =====
        
        core_query = _strip_query_wrapper(raw_query)
        tokens = _tokenize_text(core_query)
        
        # ===== DEBUG LOGGING =====
        logger.debug(f"[_prepare_query_tokens] After tokenize: {tokens}")
        # ===== END DEBUG =====

        # Deduplizieren, Reihenfolge beibehalten
        unique_tokens = []
        seen = set()
        for token in tokens:
            if token not in seen:
                seen.add(token)
                unique_tokens.append(token)

        if len(unique_tokens) <= 12:
            # ===== DEBUG LOGGING =====
            logger.debug(f"[_prepare_query_tokens] OUTPUT (short query): {unique_tokens}")
            # ===== END DEBUG =====
            return unique_tokens

        priority_tokens = [
            token for token in unique_tokens
            if any(part in token for part in priority_parts)
        ]

        if priority_tokens:
            # Config-basierte Token-Länge
            min_token_len = config.bm25.min_token_length
            supporting_tokens = [
                token for token in unique_tokens
                if token not in priority_tokens and len(token) >= min_token_len
            ]
            result = (priority_tokens + supporting_tokens[:4])[:8]
            # ===== DEBUG LOGGING =====
            logger.debug(f"[_prepare_query_tokens] OUTPUT (priority path): {result}")
            # ===== END DEBUG =====
            return result

        # Fallback für andere Fragen: die längsten, eindeutigsten Tokens behalten.
        ranked_tokens = sorted(unique_tokens, key=len, reverse=True)
        result = ranked_tokens[:10]
        # ===== DEBUG LOGGING =====
        logger.debug(f"[_prepare_query_tokens] OUTPUT (fallback): {result}")
        # ===== END DEBUG =====
        return result
    
    with get_session() as ses:
        # Chunks laden mit allen Filtern
        doc_query = select(DocumentChunk).join(Document).where(Document.is_deleted == False)
        
        if project_key:
            doc_query = doc_query.join(ProjectDocumentLink, ProjectDocumentLink.document_id == Document.id)\
                                 .where(ProjectDocumentLink.project_key == project_key)
        
        if role_key:
            doc_query = doc_query.where(Document.linked_role_keys.like(f'%"{role_key}"%'))
        
        if classification_filter:
            doc_query = doc_query.where(Document.classification == classification_filter)

        if exclude_classification:
            doc_query = doc_query.where(Document.classification != exclude_classification)
        
        chunks = ses.exec(doc_query).all()
        
        if not chunks:
            return []
        
        # Pre-fetch alle Dokumente (für Metadata)
        doc_ids = list(set(c.document_id for c in chunks))
        docs_map = {d.id: d for d in ses.exec(select(Document).where(Document.id.in_(doc_ids))).all()}
        
        # Tokenisieren und BM25 Index erstellen
        # Wenn retrieval_keywords vorhanden (LLM-generiert beim Upload) → diese als BM25-Corpus.
        # Vorteil: der BM25-Index beschreibt WAS im Chunk steht, nicht WIE es steht.
        # Fallback auf chunk_text für ältere Chunks ohne Keywords.
        tokenized_corpus = []
        chunk_metadata = []  # (chunk_id, document_id, chunk_text, doc_object, token_set)
        
        for chunk in chunks:
            # WICHTIG: Wir ignorieren retrieval_keywords komplett!
            # Grund: DB-Keywords sind oft veraltet oder falsch.
            # BM25 auf dem Raw Text mit spaCy+Stemming ist zuverlässiger.
            tokens = _tokenize_text(chunk.chunk_text or "")
            
            # ===== DEBUG LOGGING für Chunk 939 =====
            if chunk.id == 939:
                logger.info(f"[BM25 CORPUS] Chunk 939 found! Text preview: {(chunk.chunk_text or '')[:150]}")
                logger.info(f"[BM25 CORPUS] Chunk 939 tokens: {tokens[:20]}")
                logger.info(f"[BM25 CORPUS] Chunk 939 has 'subunternehm' in tokens: {'subunternehm' in tokens}")
            # ===== END DEBUG =====
            
            if tokens:  # nur Chunks mit Content
                tokenized_corpus.append(tokens)
                chunk_metadata.append((chunk.id, chunk.document_id, chunk.chunk_text, docs_map.get(chunk.document_id), set(tokens)))
        
        if not tokenized_corpus:
            return []
        
        # ===== DEBUG LOGGING =====
        logger.info(f"[BM25 CORPUS] Tokenized {len(tokenized_corpus)} chunks total")
        chunk_939_in_corpus = any(meta[0] == 939 for meta in chunk_metadata)
        logger.info(f"[BM25 CORPUS] Chunk 939 in corpus: {chunk_939_in_corpus}")
        if chunk_939_in_corpus:
            idx_939 = [i for i, meta in enumerate(chunk_metadata) if meta[0] == 939][0]
            logger.info(f"[BM25 CORPUS] Chunk 939 corpus index: {idx_939}")
        # ===== END DEBUG =====
        
        # BM25 Index erstellen
        bm25 = BM25Okapi(tokenized_corpus)
        
        # Query tokenisieren
        query_tokens = _prepare_query_tokens(query)
        
        if not query_tokens:
            # Fallback: wenn alle Wörter Stopwords sind, verwende Original
            query_tokens = [token for token in query.lower().split() if len(token) >= 3]

        # Relativer IDF-Noise-Filter: Query-Tokens im untersten 25%-IDF-Quartil
        # haben wenig Unterscheidungskraft. Durch den relativen (nicht absoluten) Cutoff
        # ist der Filter corpus-agnostisch und braucht keine manuelle Schwellenwert-Pflege.
        # Mindestens 2 Tokens verbleiben (kein Query-Leerstand bei kurzen Queries).
        #
        # WICHTIG: IDF-Filter wird NUR bei langen Queries (>10 Tokens) angewendet!
        # Grund: Distillierte Queries sind kurz (3-8 Tokens) und bereits Noise-gefiltert.
        # Ein IDF-Filter auf bereits distillierten Keywords würde Signal-Begriffe entfernen.
        if len(query_tokens) > 10:
            idfs = sorted(bm25.idf.get(t, 0) for t in query_tokens)
            cutoff = idfs[len(idfs) // 4]  # 25. Perzentil
            filtered = [t for t in query_tokens if bm25.idf.get(t, cutoff + 1) > cutoff]
            if len(filtered) >= 2:  # mind. 2 Tokens erhalten
                query_tokens = filtered

        # BM25 Scores berechnen
        scores = bm25.get_scores(query_tokens)
        
        # ===== DEBUG LOGGING =====
        logger.info(f"[BM25 DEBUG] Original Query: {query!r}")
        logger.info(f"[BM25 DEBUG] Query Tokens AFTER tokenization: {query_tokens}")
        logger.info(f"[BM25 DEBUG] Corpus size: {len(tokenized_corpus)} chunks")
        logger.info(f"[BM25 DEBUG] Scores computed: {len(scores)} scores")
        
        # Check Chunk 939 Score
        chunk_939_in_corpus = any(meta[0] == 939 for meta in chunk_metadata)
        if chunk_939_in_corpus:
            idx_939 = [i for i, meta in enumerate(chunk_metadata) if meta[0] == 939][0]
            score_939 = scores[idx_939]
            logger.info(f"[BM25 DEBUG] Chunk 939 Score: {score_939}")
        
        scores_above_zero = [s for s in scores if s > 0]
        logger.info(f"[BM25 DEBUG] Scores > 0: {len(scores_above_zero)} hits")
        if scores_above_zero:
            top5_scores = sorted(scores_above_zero, reverse=True)[:5]
            logger.info(f"[BM25 DEBUG] Top-5 Scores: {top5_scores}")
        # ===== END DEBUG =====
        
        # Top-K Ergebnisse sammeln
        results = []
        lowered_query = _strip_query_wrapper(query).lower()
        active_priority_parts = [part for part in priority_parts if part in lowered_query]
        for idx, score in enumerate(scores):
            if score > 0:  # nur Matches
                chunk_id, doc_id, chunk_text, doc, chunk_tokens = chunk_metadata[idx]
                if doc:
                    coverage = 0.0
                    idf_bonus = 0.0
                    priority_hits = 0
                    matched_tokens = set()
                    if query_tokens:
                        matched_tokens = {token for token in query_tokens if token in chunk_tokens}
                        coverage = len(matched_tokens) / len(query_tokens)
                        idf_bonus = sum(max(0.0, bm25.idf.get(token, 0.0)) for token in matched_tokens)
                    lowered_chunk = (chunk_text or "").lower()
                    priority_hits = sum(1 for part in active_priority_parts if part in lowered_chunk)

                    # PHASE 3: Config-basierte Scoring-Gewichte (statt Magic Numbers)
                    keyword_score = float(score) + \
                                    (coverage * config.bm25.coverage_weight) + \
                                    (idf_bonus * config.bm25.idf_weight) + \
                                    (priority_hits * config.bm25.priority_boost)
                    results.append({
                        "chunk_id": chunk_id,
                        "document_id": doc_id,
                        "filename": doc.filename,
                        "classification": doc.classification,
                        "text": chunk_text,
                        "match_score": keyword_score,
                        "raw_bm25_score": float(score),
                        "keyword_idf_score": round(idf_bonus, 3),
                        "keyword_coverage": round(coverage, 3),
                        "priority_hits": priority_hits,
                        "matched_terms": sorted(matched_tokens),
                    })
        
        results.sort(key=lambda x: x["match_score"], reverse=True)
        
        # Normalisiere match_score auf [0,1] relativ zum besten Treffer im Batch
        if results:
            max_score = results[0]["match_score"]
            for r in results:
                r["normalized_match_score"] = round(r["match_score"] / max_score, 3) if max_score > 0 else 0.0
        
        # ===== DEBUG LOGGING =====
        logger.info(f"[BM25 DEBUG] Results collected: {len(results)} hits")
        logger.info(f"[BM25 DEBUG] Returning top {min(len(results), limit)} results (limit={limit})")
        if results:
            for i, r in enumerate(results[:3]):
                logger.info(f"[BM25 DEBUG] Result #{i+1}: chunk_id={r['chunk_id']}, score={r['match_score']:.3f}, terms={r['matched_terms']}")
        # ===== END DEBUG =====
        
        return results[:limit]


def get_all_documents_with_best_scores(
    query: str,
    project_key: str | None = None,
    threshold: float = 0.5,
    exclude_classification: str | None = None
) -> list[dict]:
    """
    Gibt ALLE Projekt-Dokumente mit ihrem besten Chunk-Score zurück (Hybrid: Semantic + BM25).
    
    Nützlich für Diagnostics: zeigt welche Dokumente geprüft wurden,
    aber nicht in Top-K kamen (Score < Threshold).
    
    Returns:
        Liste von {"document_id", "filename", "classification", "best_score", "included", "bm25_score", "matched_terms"}
    """
    from src.m09_docs import get_project_documents
    
    all_docs = get_project_documents(project_key) if project_key else []
    
    if exclude_classification:
        all_docs = [d for d in all_docs if d.classification != exclude_classification]
    
    # 1. BM25-Suche für alle Chunks (für Diagnostics)
    # Wir nutzen ein grosses Limit um möglichst alle Dokumente zu erwischen
    kw_results = _keyword_search(
        query, project_key, limit=2000, 
        exclude_classification=exclude_classification
    )
    kw_map = {} # doc_id -> (best_score, matched_terms)
    for res in kw_results:
        did = res["document_id"]
        score = res["match_score"]
        terms = res["matched_terms"]
        if did not in kw_map or score > kw_map[did][0]:
            kw_map[did] = (score, terms)

    # 2. Semantic Search Vorbereitung
    result = []
    query_emb = embed_text(query)
    
    with get_session() as ses:
        for doc in all_docs:
            # Hole alle Chunks dieses Dokuments
            chunks = ses.exec(
                select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
            ).all()
            
            best_semantic = 0.0
            if query_emb:
                for chunk in chunks:
                    if chunk.embedding:
                        chunk_emb = json.loads(chunk.embedding)
                        sim = _cosine_similarity(query_emb, chunk_emb)
                        best_semantic = max(best_semantic, sim)
            
            # BM25 Info
            kw_score, kw_terms = kw_map.get(doc.id, (0.0, []))
            
            # Best score (Hybrid)
            # Normalerweise nutzen wir RRF fürs Ranking, aber hier für die einfache Diagnose-Tabelle
            # zeigen wir einfach den besten Einzel-Score
            best_score = max(best_semantic, kw_score / 20.0 if kw_score > 0 else 0) # Grobe Normalisierung für die Ampel
            
            included = best_score >= threshold or best_semantic >= threshold or kw_score > 0
            
            reason = ""
            if not included:
                reason = f"Score {best_semantic:.0%} < Threshold {threshold:.0%}"
            
            result.append({
                "document_id": doc.id,
                "filename": doc.filename,
                "classification": doc.classification,
                "best_score": best_semantic, # Wir lassen best_score für Abwärtskompatibilität als semantischen Score
                "best_semantic": best_semantic,
                "bm25_score": kw_score,
                "matched_terms": kw_terms,
                "included": included,
                "reason": reason
            })
    
    return sorted(result, key=lambda x: max(x["best_semantic"], x["bm25_score"]/50.0), reverse=True)


def reciprocal_rank_fusion(results_list: list[list[dict]], k: int = 60) -> list[dict]:
    """
    Kombiniert mehrere Rankings (z.B. Semantic & BM25) ohne Gewichtungs-Bias.
    RRF Score = Σ(1 / (k + rank)) für alle Rankings
    
    **Phase 2: Mathematisch fundierte Fusion - eliminiert Magic Number Weights**
    
    Vorteile:
    - Robust gegen Score-Skalierung (BM25 vs. Cosine Similarity)
    - Keine manuellen Gewichtungen nötig
    - Bevorzugt Dokumente die in mehreren Rankings hoch stehen
    - Industrieller Standard für Multi-Retrieval Fusion
    
    Args:
        results_list: Liste von Rankings, z.B. [bm25_results, semantic_results]
                     Jeder Entry: [{"chunk_id": 1, "document_id": 5, ...}, ...]
        k: RRF Parameter (Standard: 60). Kontrolliert Falloff-Rate.
           Höher = flacher (weniger Unterschied zwischen Ranks)
    
    Returns:
        Fusionierte Ergebnisse sortiert nach RRF Score (höchste zuerst)
        
    Example:
        >>> bm25 = [{"chunk_id": 1, "score": 5.2}, {"chunk_id": 2, "score": 3.1}]
        >>> semantic = [{"chunk_id": 2, "score": 0.92}, {"chunk_id": 3, "score": 0.81}]
        >>> fused = reciprocal_rank_fusion([bm25, semantic], k=60)
        >>> # chunk_id=2 erscheint in beiden → höchster RRF Score
    """
    fused_scores: dict[int, float] = {}  # chunk_id -> RRF score
    doc_data: dict[int, dict] = {}       # chunk_id -> chunk dict (vollständige Daten)
    
    for results in results_list:
        for rank, hit in enumerate(results, start=1):
            chunk_id = hit["chunk_id"]
            
            # RRF Score akkumulieren
            if chunk_id not in fused_scores:
                fused_scores[chunk_id] = 0.0
                doc_data[chunk_id] = hit.copy()
            else:
                # Metadaten zusammenführen (Keyword-Treffer zu Semantic-Treffer hinzufügen)
                existing = doc_data[chunk_id]
                for key in ["matched_terms", "match_score", "raw_bm25_score", "keyword_coverage", "keyword_idf_score", "priority_hits", "similarity"]:
                    if hit.get(key) and not existing.get(key):
                        existing[key] = hit[key]
                    elif key == "matched_terms" and hit.get(key) and existing.get(key):
                        # Begriffe kombinieren
                        combined = list(set(existing[key] + hit[key]))
                        existing[key] = combined
            
            fused_scores[chunk_id] += 1.0 / (k + rank)
    
    # Sortieren nach RRF Score (höchste zuerst)
    sorted_chunks = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    
    # Ergebnisse zusammenbauen mit RRF Score
    final_results = []
    for chunk_id, rrf_score in sorted_chunks:
        hit = doc_data[chunk_id]
        hit["rrf_score"] = round(rrf_score, 4)
        hit["combined_score"] = round(rrf_score, 4)  # Für Kompatibilität mit bestehendem Code
        final_results.append(hit)
        
    return final_results


def retrieve_relevant_chunks_hybrid(
    query: str, 
    project_key: str | None = None, 
    limit: int = 5, 
    threshold: float = 0.5,
    role_key: str | None = None,
    classification_filter: str | None = None,
    exclude_classification: str | None = None,
    _expansion_attempt: int = 0,  # Internal: tracks query expansion retries
    _distilled_keywords: str | None = None,  # Internal: original distilled keywords for expansion
    _forced_expansion_terms: str | None = None,  # Internal: expansion terms appended directly to BM25 (bypass distillation)
    enable_expansion: bool | None = None,  # Optional: override config.query.enable_expansion
    enable_reranking: bool | None = None  # Optional: override config.reranking.enable
) -> dict:
    """
    Hybrid-Suche: Semantic + Keyword mit garantierter Dokument-Repräsentation.

    Algorithmus:
    1. Semantic-Suche mit niedrigerer Entdeckungs-Schwelle (breites Netz)
    2. Keyword-Suche mit grossem Limit (alle Dokumente werden berücksichtigt)
    3. Dateiname-Boost: Query-Wörter im Dateinamen → +0.04 Score
    4. Garantierter Mindest-1-Slot pro Dokument (wenn bestes Chunk >= guaranteed_threshold)
    5. Pflichtenheft-Fallback: "Pflichtenheft (Projekt)"-Dokumente immer vertreten
    6. Qualitäts-Füllung: restliche Slots mit top-Chunks (>= threshold, diversity-cap)

    Args:
        query: Die Nutzer-Query
        project_key: Optional: Filter für Projekt-Dokumente
        limit: Max Resultate total
        threshold: Min Similarity für Qualitäts-Füllung
        role_key: Optional: Filter für Rollen-spezifische Dokumente
        classification_filter: Optional: Nur Dokumente mit DIESER Klassifizierung
        exclude_classification: Optional: Dokumente mit DIESER Klassifizierung ausschliessen

    Returns: Dict mit kombinierten Ergebnissen
    """
    # PHASE 3: Config-basierte Schwellenwerte (statt Hardcoding)
    config = get_retrieval_config()
    guaranteed_threshold = config.hybrid.guaranteed_doc_threshold
    discovery_threshold = min(
        threshold * config.semantic.discovery_threshold_multiplier,
        config.semantic.min_discovery_threshold
    )
    max_per_doc = config.hybrid.max_chunks_per_document or max(2, limit // 2)

    # Query Distillation: LLM destilliert lange Nutzerfragen zu präzisen Suchbegriffen.
    # Nur für BM25-Suche — Semantic-Embedding profitiert vom vollen natürlichsprachlichen Text.
    # Zentralisiert hier damit alle Aufrufer (Chat, Batch QA, Reports) automatisch profitieren.
    
    # ===== DEBUG LOGGING =====
    logger.info("=" * 80)
    logger.info("[HYBRID RETRIEVAL] START")
    logger.info(f"[HYBRID RETRIEVAL] Original Query: {query!r}")
    logger.info(f"[HYBRID RETRIEVAL] Distillation Enabled: {config.query.enable_distillation}")
    if config.query.enable_distillation:
        logger.info(f"[HYBRID RETRIEVAL] Distillation Provider: {config.query.distillation_provider}")
        logger.info(f"[HYBRID RETRIEVAL] Distillation Model: {config.query.distillation_model}")
    # ===== END DEBUG =====
    
    keyword_query = query
    if _forced_expansion_terms:
        # Expansion-Lauf: Distillation NICHT nochmals aufrufen — Expansion-Terme direkt anhängen
        # (Distillation würde "Schweizerische Informatikkonferenz" als Rauschen filtern)
        keyword_query = (_distilled_keywords or query) + " " + _forced_expansion_terms
        logger.info(f"[HYBRID RETRIEVAL] Using distilled+expansion query for BM25: {keyword_query!r}")
    elif config.query.enable_distillation:
        try:
            # ===== DEBUG LOGGING =====
            logger.info("[HYBRID RETRIEVAL] Calling Query Distillation...")
            # ===== END DEBUG =====
            
            distilled = _get_rewrite_query()(
                query,
                provider=config.query.distillation_provider,
                model=config.query.distillation_model,
            )
            
            # ===== DEBUG LOGGING =====
            logger.info(f"[HYBRID RETRIEVAL] Distillation Result: {distilled!r}")
            # ===== END DEBUG =====
            
            if distilled and distilled != query:
                logger.info("RAG distillation: %r → %r", query[:80], distilled)
                keyword_query = distilled
                # Store distilled keywords for potential expansion
                if _distilled_keywords is None:
                    _distilled_keywords = distilled
                
                # ===== DEBUG LOGGING =====
                logger.info(f"[HYBRID RETRIEVAL] Using distilled query for BM25: {keyword_query!r}")
                # ===== END DEBUG =====
            else:
                logger.debug("RAG distillation: no change (result=%r)", distilled)
                # ===== DEBUG LOGGING =====
                logger.info(f"[HYBRID RETRIEVAL] No change from distillation, using original")
                # ===== END DEBUG =====
        except Exception as exc:
            logger.warning("RAG distillation failed, using original query: %s", exc)
            # ===== DEBUG LOGGING =====
            logger.error(f"[HYBRID RETRIEVAL] Distillation ERROR: {exc}", exc_info=True)
            # ===== END DEBUG =====

    # Cache-Check NACH Distillation: Key basiert auf keyword_query damit
    # Distillations-Ergebnisse korrekt gecacht werden und keine pre-distillation
    # Resultate aus dem Cache zurückgegeben werden.
    cache_key = _get_cache_key(keyword_query, project_key, limit, threshold, exclude_classification)
    if cache_key in _RAG_CACHE:
        return _RAG_CACHE[cache_key]

    # 1. Semantic-Suche mit niedrigerer Entdeckungs-Schwelle (Original-Query für Embeddings)
    # ===== DEBUG LOGGING =====
    logger.info(f"[HYBRID RETRIEVAL] Performing Semantic Search with query: {query!r}")
    # ===== END DEBUG =====
    
    semantic_results = retrieve_relevant_chunks(
        query, project_key, limit * 5, discovery_threshold,
        role_key=role_key,
        classification_filter=classification_filter,
        exclude_classification=exclude_classification
    )
    
    # ===== DEBUG LOGGING =====
    logger.info(f"[HYBRID RETRIEVAL] Semantic Search returned {len(semantic_results.get('documents', []))} results")
    # ===== END DEBUG =====

    # 2. Keyword-Suche mit grossem Limit (destillierte Query für BM25)
    # ===== DEBUG LOGGING =====
    logger.info(f"[HYBRID RETRIEVAL] Performing BM25 Keyword Search with query: {keyword_query!r}")
    logger.info(f"[HYBRID RETRIEVAL] (This is the DISTILLED query, not the original)")
    # ===== END DEBUG =====
    
    keyword_docs = _keyword_search(
        keyword_query, project_key, limit * 10,
        role_key=role_key,
        classification_filter=classification_filter,
        exclude_classification=exclude_classification
    )
    
    # ===== DEBUG LOGGING =====
    logger.info(f"[HYBRID RETRIEVAL] BM25 Keyword Search returned {len(keyword_docs)} results")
    # ===== END DEBUG =====

    semantic_debug_docs = [dict(d) for d in semantic_results.get("documents", [])]

    # 3. PHASE 3: Multi-Hypothesis oder Standard RRF-Fusion
    if config.query.enable_multi_hypothesis:
        # Multi-Hypothesis: Parallele Query-Varianten -> mehr Rankings -> RRF
        # Limit pro Einzel-Suche reduziert (Performance-Trade-off)
        hyp_limit_sem = max(limit * 3, 10)   # weniger als Standard (limit*5)
        hyp_limit_kw  = max(limit * 5, 15)   # weniger als Standard (limit*10)

        hypotheses = _get_generate_query_hypotheses()(
            keyword_query,
            count=config.query.hypothesis_count,
            provider=config.query.distillation_provider,
            model=config.query.distillation_model
        )

        all_rankings: list[list[dict]] = [
            semantic_results.get("documents", []),
            keyword_docs,
        ]
        for hyp in hypotheses:
            hyp_sem = retrieve_relevant_chunks(
                hyp, project_key, hyp_limit_sem, discovery_threshold,
                role_key=role_key,
                classification_filter=classification_filter,
                exclude_classification=exclude_classification
            )
            hyp_kw = _keyword_search(
                hyp, project_key, hyp_limit_kw,
                role_key=role_key,
                classification_filter=classification_filter,
                exclude_classification=exclude_classification
            )
            all_rankings.append(hyp_sem.get("documents", []))
            all_rankings.append(hyp_kw)

        fused_results = reciprocal_rank_fusion(
            [r for r in all_rankings if r],
            k=config.hybrid.rrf_k
        )
        # Aliase für Metadata-Merge unten (Quell-Listen aller Chunks)
        semantic_ranking = [d for r in all_rankings[::2] for d in r]   # gerade Indizes: semantisch
        keyword_ranking  = [d for r in all_rankings[1::2] for d in r]  # ungerade Indizes: keyword
    else:
        # Standard Single-Query RRF
        semantic_ranking = semantic_results.get("documents", [])
        keyword_ranking = keyword_docs
        if semantic_ranking or keyword_ranking:
            fused_results = reciprocal_rank_fusion(
                [semantic_ranking, keyword_ranking],
                k=config.hybrid.rrf_k
            )
        else:
            fused_results = []
    
    # Merge vollständige Metadaten (RRF gibt nur chunk_id + scores zurück)
    # Wir brauchen aber auch semantic similarity & keyword scores für Schwellenwert-Checks
    all_docs_map: dict[int, dict] = {}
    for d in semantic_ranking:
        all_docs_map[d["chunk_id"]] = d
    for kd in keyword_ranking:
        cid = kd["chunk_id"]
        if cid not in all_docs_map:
            all_docs_map[cid] = kd
        else:
            # Chunk in beiden: BM25-Scores hinzufügen
            all_docs_map[cid]["match_score"] = kd.get("match_score", 0)
            all_docs_map[cid]["normalized_match_score"] = kd.get("normalized_match_score", 0)
            all_docs_map[cid]["raw_bm25_score"] = kd.get("raw_bm25_score", 0)
            all_docs_map[cid]["keyword_coverage"] = kd.get("keyword_coverage", 0)
            all_docs_map[cid]["keyword_idf_score"] = kd.get("keyword_idf_score", 0)
            all_docs_map[cid]["priority_hits"] = kd.get("priority_hits", 0)
            all_docs_map[cid]["matched_terms"] = kd.get("matched_terms", [])
    
    # RRF Scores in all_docs_map übertragen
    all_docs = []
    for fused in fused_results:
        cid = fused["chunk_id"]
        if cid in all_docs_map:
            chunk = all_docs_map[cid]
            chunk["rrf_score"] = fused["rrf_score"]
            chunk["combined_score"] = fused["combined_score"]
            all_docs.append(chunk)

    def _combined_score(d: dict) -> float:
        """PHASE 2: RRF Score als kombinierter Score (statt Magic Number Weights)"""
        return d.get("combined_score", d.get("rrf_score", 0))
    
    def _quality_score(d: dict) -> float:
        """Original semantic/keyword score für Threshold-Checks.
        RRF ist für Ranking, aber Schwellenwerte basieren auf absoluten Similarity-Scores."""
        return max(d.get("similarity", 0), d.get("match_score", 0))

    def _compute_matched_terms(query_text: str, chunk_text: str) -> list[str]:
        """Berechnet die Schnittmenge zwischen Query-Tokens und Chunk-Text.
        
        Diese Funktion wird on-the-fly für JEDEN Kandidaten aufgerufen, unabhängig davon,
        ob er von BM25 oder Semantic Search stammt. Das stellt sicher, dass die Debug-Anzeige
        immer zeigt, welche Suchbegriffe im Text vorkommen.
        
        Args:
            query_text: Die (distillierte) Query
            chunk_text: Der Text des Chunks
            
        Returns:
            Sortierte Liste der übereinstimmenden Tokens
        """
        if not query_text or not chunk_text:
            return []
        
        # Tokenisiere Query und Chunk mit der selben Logik wie BM25
        query_tokens = set(_tokenize_text(query_text))
        chunk_tokens = set(_tokenize_text(chunk_text))
        
        # Schnittmenge berechnen
        matched = query_tokens & chunk_tokens
        
        return sorted(matched)

    def _debug_entry(d: dict, source: str, keyword_query_for_terms: str) -> dict:
        """Erstellt einen Debug-Entry für die UI.
        
        Args:
            d: Dokument-Dict mit Scores und Metadaten
            source: "semantic", "keyword", oder "final"
            keyword_query_for_terms: Die (distillierte) Query für Term-Matching
        """
        # Berechne matched_terms IMMER on-the-fly aus der Query und dem Chunk-Text
        # (statt auf BM25-Metadaten zu verlassen, die bei Fusion verloren gehen können)
        chunk_text = d.get("text", "") or ""
        matched_terms = _compute_matched_terms(keyword_query_for_terms, chunk_text)
        
        return {
            "source": source,
            "chunk_id": d.get("chunk_id"),
            "document_id": d.get("document_id"),
            "filename": d.get("filename"),
            "classification": d.get("classification"),
            "similarity": d.get("similarity", 0.0),
            "match_score": d.get("match_score", 0.0),
            "normalized_match_score": d.get("normalized_match_score", d.get("match_score", 0.0)),
            "raw_keyword_score": d.get("raw_keyword_score", d.get("raw_bm25_score", 0.0)),
            "raw_bm25_score": d.get("raw_bm25_score", 0.0),
            "keyword_coverage": d.get("keyword_coverage", 0.0),
            "keyword_idf_score": d.get("keyword_idf_score", 0.0),
            "priority_hits": d.get("priority_hits", 0),
            "matched_terms": matched_terms,  # ← On-the-fly berechnet!
            "rrf_score": d.get("rrf_score", 0.0),
            "quality_score": round(_quality_score(d), 3),
            "combined_score": round(_combined_score(d), 3),
            # Verwende distillierte Query für Preview → zeigt relevante Stelle, nicht Chunk-Anfang
            "text_preview": _find_relevant_window(chunk_text.replace("\n", " "), keyword_query_for_terms or query, 220),
        }

    # 4. Dateiname-Boost: kleine Aufwertung wenn Query-Wörter im Dateinamen vorkommen
    if config.filename_boost.enable:
        query_words_long = {w.lower().rstrip(":") for w in query.split() if len(w) > config.filename_boost.min_word_length}
        if query_words_long:
            for d in all_docs:
                fname_parts = set(
                    d["filename"].lower().replace(".", " ").replace("_", " ").replace("-", " ").split()
                )
                # Teilwort-Übereinstimmung (z.B. "preis" in "preisblatt" oder "preisstruktur")
                # ODER gemeinsamer Präfix >= config Zeichen
                matched = False
                for qw in query_words_long:
                    for fp in fname_parts:
                        if (
                            qw in fp or fp in qw  # Exaktes Teilwort
                            or (len(qw) >= config.filename_boost.min_prefix_match
                                and len(fp) >= config.filename_boost.min_prefix_match
                                and qw[:config.filename_boost.min_prefix_match] == fp[:config.filename_boost.min_prefix_match])
                        ):
                            matched = True
                            break
                    if matched:
                        break
                
                if matched:
                    score_key = "similarity" if "similarity" in d else "match_score"
                    # Config-basierter Boost
                    d[score_key] = min(1.0, d.get(score_key, 0) + config.filename_boost.boost_amount)

    # 5. Pro Dokument: Chunks nach RRF Score sortieren
    by_doc: dict[int, list] = {}
    for d in all_docs:
        by_doc.setdefault(d["document_id"], []).append(d)
    for did in by_doc:
        by_doc[did].sort(key=_combined_score, reverse=True)

    # 6. Garantierter Mindest-1-Slot pro qualifizierendem Dokument
    # WICHTIG: Schwellenwert basiert auf original quality scores, Ranking auf RRF
    guaranteed: list[dict] = []
    guaranteed_cids: set[int] = set()
    # Sortiert nach bestem RRF Score
    sorted_docs = sorted(
        by_doc.items(),
        key=lambda kv: _combined_score(kv[1][0]),
        reverse=True
    )
    for _did, chunks in sorted_docs:
        best = chunks[0]
        # Schwellenwert-Check mit original scores (nicht RRF)
        if _quality_score(best) >= guaranteed_threshold:
            guaranteed.append(best)
            guaranteed_cids.add(best["chunk_id"])

    # 7. Pflichtenheft-Fallback: "Pflichtenheft (Projekt)" immer vertreten (wenn Score > threshold)
    PFLICHTENHEFT_CLS = "Pflichtenheft (Projekt)"
    if project_key and exclude_classification != PFLICHTENHEFT_CLS and config.hybrid.pflichtenheft_fallback:
        pflicht_represented = any(c.get("classification") == PFLICHTENHEFT_CLS for c in guaranteed)
        if not pflicht_represented:
            pflicht_candidates = [
                d for d in all_docs if d.get("classification") == PFLICHTENHEFT_CLS
            ]
            if pflicht_candidates:
                # Ranking: RRF, Threshold: original score
                best_pflicht = max(pflicht_candidates, key=_combined_score)
                if _quality_score(best_pflicht) > config.hybrid.pflichtenheft_min_score:
                    guaranteed.append(best_pflicht)
                    guaranteed_cids.add(best_pflicht["chunk_id"])

    # Garantierte Chunks nach RRF Score sortieren (statt semantic/keyword)
    guaranteed.sort(key=_combined_score, reverse=True)

    # 8. Qualitäts-Füllung: restliche Slots mit top-Qualitäts-Chunks (>= threshold, diversity-cap)
    # WICHTIG: Wenn Reranking aktiviert, hole initial_k Kandidaten (z.B. 15), sonst nur limit (7)
    reranking_enabled = enable_reranking if enable_reranking is not None else config.reranking.enable
    target_count = config.reranking.initial_k if reranking_enabled else limit
    # Beim Reranking: höheres max_per_doc damit genug Kandidaten gesammelt werden können
    # Beispiel: target_count=15, max_per_doc normal=2 → 4 Docs × 2 = 8 (zu wenig!)
    # Mit max(2, 15//3) = 5 pro Doc → 4 Docs × 5 = 20 (genug für 15 Kandidaten)
    fill_max_per_doc = max(max_per_doc, target_count // 3) if reranking_enabled else max_per_doc

    per_doc_count: dict[int, int] = {}
    for d in guaranteed:
        did = d["document_id"]
        per_doc_count[did] = per_doc_count.get(did, 0) + 1

    result_docs = list(guaranteed[:target_count])
    result_cids: set[int] = {d["chunk_id"] for d in result_docs}

    if len(result_docs) < target_count:
        # Sortiere nach RRF Score, aber filtere nach quality threshold
        all_sorted = sorted(all_docs, key=_combined_score, reverse=True)
        for entry in all_sorted:
            if len(result_docs) >= target_count:
                break
            cid = entry["chunk_id"]
            # Schwellenwert-Check mit original scores (nicht RRF)
            if _quality_score(entry) < guaranteed_threshold:
                break
            if cid in result_cids:
                continue
            did = entry["document_id"]
            if per_doc_count.get(did, 0) < fill_max_per_doc:
                result_docs.append(entry)
                result_cids.add(cid)
                per_doc_count[did] = per_doc_count.get(did, 0) + 1

    # Finale Sortierung nach RRF Score
    result_docs.sort(key=_combined_score, reverse=True)
    for doc in result_docs:
        doc["combined_score"] = round(_combined_score(doc), 3)
        # Für Debug: Auch quality_score speichern
        doc["quality_score"] = round(_quality_score(doc), 3)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # RERANKING: Hole mehr Kandidaten (initial_k) und reranke auf final_k
    # ═══════════════════════════════════════════════════════════════════════════
    reranking_info = None
    
    if reranking_enabled and len(result_docs) > limit:
        initial_k = len(result_docs)  # Tatsächlich geholte Kandidaten
        final_k = limit  # User's rag_top_k
        
        # ▶▶ RERANKING DEBUG: Kandidaten VOR Reranking
        run_label = "[Lauf 2 - NACH EXPANSION]" if _forced_expansion_terms else "[Lauf 1 - ohne Expansion]"
        logger.info(f"[RERANKING] {run_label} Mode: {config.reranking.mode}, Initial: {initial_k}, Final: {final_k}")
        logger.info(f"[RERANKING] {run_label} ▶▶ KANDIDATEN VOR RERANKING ({initial_k} Stück):")
        for i, d in enumerate(result_docs):
            sem = d.get('similarity', 0)
            bm25 = d.get('normalized_match_score', 0)
            comb = d.get('combined_score', 0)
            src = 'KW' if d.get('match_score', 0) > 0 else 'SEM'
            fname = (d.get('filename') or '?')[:40]
            preview = (d.get('text') or '')[:80].replace('\n', ' ')
            logger.info(f"[RERANKING]   #{i+1:2d}: chunk={d.get('chunk_id'):4d} [{src}] comb={comb:.3f} sem={sem:.3f} bm25={bm25:.3f} | {fname} | {preview!r}")
        
        # Reranking durchführen
        reranked_docs = _rerank_documents(
            result_docs,  # Alle initial_k Kandidaten
            query=query,
            mode=config.reranking.mode,
            final_k=final_k,
            llm_provider=config.reranking.llm_provider,
            llm_model=config.reranking.llm_model,
            llm_temperature=config.reranking.llm_temperature,
            llm_batch_size=config.reranking.llm_batch_size,
        )
        
        # Score-Vergleich für Diagnostics
        if reranked_docs:
            top_before = result_docs[0].get("combined_score", 0)
            top_after = reranked_docs[0].get("rerank_score", reranked_docs[0].get("combined_score", 0))
            improvement = top_after - top_before
            
            logger.info(f"[RERANKING] {run_label} Score: {top_before:.3f} → {top_after:.3f} (Δ {improvement:+.3f})")
            
            # ▶▶ RERANKING DEBUG: Ergebnis NACH Reranking
            logger.info(f"[RERANKING] {run_label} ▶▶ ERGEBNIS NACH RERANKING (Top-{len(reranked_docs)}):")
            for i, d in enumerate(reranked_docs):
                rscore = d.get('rerank_score', d.get('combined_score', 0))
                fname = (d.get('filename') or '?')[:40]
                preview = (d.get('text') or '')[:80].replace('\n', ' ')
                logger.info(f"[RERANKING]   #{i+1:2d}: chunk={d.get('chunk_id'):4d} rerank={rscore:.3f} | {fname} | {preview!r}")
            
            reranking_info = {
                "enabled": True,
                "mode": config.reranking.mode,
                "initial_k": initial_k,
                "final_k": len(reranked_docs),
                "score_before": round(top_before, 3),
                "score_after": round(top_after, 3),
                "improvement": round(improvement, 3),
            }
            
            result_docs = reranked_docs
    
    semantic_results["documents"] = result_docs[:limit]
    if reranking_info:
        semantic_results["reranking"] = reranking_info
    
    semantic_results["debug"] = {
        "query": query,
        "keyword_query": keyword_query,
        "semantic_candidates": [_debug_entry(d, "semantic", keyword_query) for d in semantic_debug_docs[:15]],
        "keyword_candidates": [_debug_entry(d, "keyword", keyword_query) for d in keyword_docs[:15]],
        "final_candidates": [_debug_entry(d, "final", keyword_query) for d in result_docs[:15]],
    }

    # ===== DEBUG LOGGING =====
    logger.info(f"[HYBRID RETRIEVAL] Final results: {len(result_docs[:limit])} documents")
    logger.info(f"[HYBRID RETRIEVAL] Semantic candidates: {len(semantic_debug_docs)}")
    logger.info(f"[HYBRID RETRIEVAL] Keyword candidates: {len(keyword_docs)}")
    logger.info(f"[HYBRID RETRIEVAL] Final candidates (before limit): {len(result_docs)}")
    _run_label = f"[Lauf 2 - NACH EXPANSION]" if _forced_expansion_terms else "[Lauf 1 - ohne Expansion]"
    logger.info(f"[HYBRID RETRIEVAL] {_run_label} ▶▶ ALLE FINALE ERGEBNISSE (Top-{min(len(result_docs), limit)}):")
    for i, doc in enumerate(result_docs[:limit]):
        sem = doc.get('similarity', 0)
        bm25 = doc.get('normalized_match_score', 0)
        comb = doc.get('combined_score', 0)
        terms = ','.join(doc.get('matched_terms') or []) or '—'
        fname = (doc.get('filename') or '?')[:40]
        preview = (doc.get('text_preview') or doc.get('text') or '')[:100].replace('\n', ' ')
        logger.info(f"[HYBRID RETRIEVAL]   #{i+1:2d}: chunk={doc.get('chunk_id'):4d} comb={comb:.3f} sem={sem:.3f} bm25={bm25:.3f} terms=[{terms}] | {fname}")
        logger.info(f"[HYBRID RETRIEVAL]        {preview!r}")
    logger.info("[HYBRID RETRIEVAL] END")
    logger.info("=" * 80)
    # ===== END DEBUG =====

    # Query Expansion: Falls Scores niedrig UND Akronyme erkannt UND noch kein Retry
    expansion_enabled = enable_expansion if enable_expansion is not None else config.query.enable_expansion
    
    if (expansion_enabled and 
        _expansion_attempt < config.query.expansion_max_retries and
        result_docs and _distilled_keywords):
        
        # Check: Sind die Scores niedrig?
        max_sem = max(d.get("similarity", 0) for d in result_docs[:limit])
        
        if max_sem < threshold:
            # Erkenne Akronyme
            acronyms = _detect_acronyms(_distilled_keywords)
            
            if acronyms:
                logger.info(f"[QUERY EXPANSION] Low confidence (max={max_sem:.0%} < {threshold:.0%}) + Akronyme erkannt: {acronyms}")
                
                # LLM-Expansion
                expansions = _expand_acronyms_with_llm(
                    acronyms,
                    provider=config.query.expansion_provider,
                    model=config.query.expansion_model
                )
                
                if expansions:
                    logger.info(f"[QUERY EXPANSION] Expansions: {expansions}")
                    
                    # Baue erweiterte Query: Akronyme ERSETZEN (nicht nur anhängen!)
                    # Statt "...SIK-AGB? Schweizerische Informatikkonferenz"
                    # → "...Schweizerische Informatikkonferenz Geschäftsbedingungen (SIK-AGB)?"
                    # Das Embedding-Modell erhält damit einen viel prägnanteren Vektor.
                    import re as _re
                    substituted_query = query
                    for _acr, _exp in expansions.items():
                        substituted_query = _re.sub(
                            _re.escape(_acr),
                            f"{_exp} ({_acr})",
                            substituted_query,
                            flags=_re.IGNORECASE,
                        )
                    expansion_terms = " ".join(expansions.values())
                    expanded_query = substituted_query
                    
                    logger.info(f"[QUERY EXPANSION] Substituted query for semantic search: {expanded_query!r}")
                    logger.info(f"[QUERY EXPANSION] Retry with expanded query: {expanded_query!r}")
                    
                    # Rekursiver Call mit expansion_attempt = 1
                    # WICHTIG: _forced_expansion_terms übergeben damit Distillation NICHT nochmals
                    # läuft und die Expansion-Terme ("Schweizerische Informatikkonferenz") filtert!
                    expanded_results = retrieve_relevant_chunks_hybrid(
                        query=expanded_query,
                        project_key=project_key,
                        limit=limit,
                        threshold=threshold,
                        role_key=role_key,
                        classification_filter=classification_filter,
                        exclude_classification=exclude_classification,
                        _expansion_attempt=_expansion_attempt + 1,
                        _distilled_keywords=_distilled_keywords,  # Keep original for warning
                        _forced_expansion_terms=expansion_terms,  # Bypass distillation für BM25!
                        enable_expansion=expansion_enabled,  # Pass through
                        enable_reranking=reranking_enabled  # Pass through
                    )
                    
                    # Check ob Expansion geholfen hat
                    expanded_docs = expanded_results.get("documents", [])
                    if expanded_docs:
                        max_sem_expanded = max(d.get("similarity", 0) for d in expanded_docs)
                        improvement = max_sem_expanded - max_sem
                        
                        logger.info(f"[QUERY EXPANSION] Result: {max_sem:.0%} → {max_sem_expanded:.0%} (improvement: {improvement:+.0%})")
                        
                        # Speichere Expansion-Info in den Ergebnissen
                        expanded_results["expansion"] = {
                            "triggered": True,
                            "acronyms": acronyms,
                            "expansions": expansions,
                            "original_max_score": max_sem,
                            "expanded_max_score": max_sem_expanded,
                            "improvement": improvement
                        }
                        
                        # WICHTIG: 1. Lauf-Ergebnisse mitliefern für UI-Vergleich
                        # Der 1. Lauf zeigt was OHNE Expansion gefunden wurde
                        expanded_results["pre_expansion_documents"] = semantic_results.get("documents", [])
                        
                        # ▶▶ VERGLEICH 1. vs 2. Lauf
                        pre_docs = semantic_results.get("documents", [])
                        post_docs = expanded_results.get("documents", [])
                        logger.info("[QUERY EXPANSION] ▶▶ VERGLEICH LAUF 1 vs LAUF 2:")
                        logger.info(f"[QUERY EXPANSION]   BM25-Query Lauf 1: {semantic_results.get('debug', {}).get('keyword_query', '?')!r}")
                        logger.info(f"[QUERY EXPANSION]   BM25-Query Lauf 2: {expanded_results.get('debug', {}).get('keyword_query', '?')!r}")
                        logger.info(f"[QUERY EXPANSION]   Score Lauf 1 (max sem): {max_sem:.1%}")
                        logger.info(f"[QUERY EXPANSION]   Score Lauf 2 (max sem): {max_sem_expanded:.1%} (+{improvement:.1%})")
                        logger.info("[QUERY EXPANSION]   Lauf 1 Top-Chunks:")
                        for i, d in enumerate(pre_docs[:7]):
                            preview = (d.get('text') or '')[:80].replace('\n', ' ')
                            logger.info(f"[QUERY EXPANSION]     L1 #{i+1}: chunk={d.get('chunk_id'):4d} sem={d.get('similarity', 0):.3f} | {(d.get('filename') or '?')[:35]} | {preview!r}")
                        logger.info("[QUERY EXPANSION]   Lauf 2 Top-Chunks:")
                        for i, d in enumerate(post_docs[:7]):
                            preview = (d.get('text') or '')[:80].replace('\n', ' ')
                            logger.info(f"[QUERY EXPANSION]     L2 #{i+1}: chunk={d.get('chunk_id'):4d} sem={d.get('similarity', 0):.3f} | {(d.get('filename') or '?')[:35]} | {preview!r}")
                        
                        return expanded_results

    _RAG_CACHE[cache_key] = semantic_results
    return semantic_results


def retrieve_relevant_chunks(
    query: str, 
    project_key: str | None = None, 
    limit: int = 5, 
    threshold: float = 0.5,
    role_key: str | None = None,
    classification_filter: str | None = None,
    exclude_classification: str | None = None
) -> dict:
    """
    Sucht die relevantesten Chunks (Rollen, Kontexte, Projekte, Tasks, Decisions, Dokumente) für eine Query.
    Nutzt Similarity Search auf den Embeddings.
    
    Args:
        query: Die Nutzer-Query
        project_key: Optional: Filter für Projekt-Dokumente
        limit: Max Anzahl Resultate pro Typ
        threshold: Min Similarity Score (0-1)
        role_key: Optional: Filter für Rollen-spezifische Dokumente
        classification_filter: Optional: Nur Dokumente mit DIESER Klassifizierung
        exclude_classification: Optional: Dokumente mit DIESER Klassifizierung ausschliessen
    
    Returns: Dict mit relevanten Objekten:
        {
            "roles": [...],
            "contexts": [...],
            "projects": [...],
            "tasks": [...],
            "decisions": [...],
            "documents": [...]
        }
    """
    query = (query or "").strip()
    if not query:
        return {"roles": [], "contexts": [], "projects": [], "tasks": [], "decisions": [], "documents": []}
    
    # Query embedding generieren
    query_embedding = embed_text(query)
    if not query_embedding:
        return {"roles": [], "contexts": [], "projects": [], "tasks": [], "decisions": [], "documents": []}
    
    results = {"roles": [], "contexts": [], "projects": [], "tasks": [], "decisions": [], "documents": []}
    
    with get_session() as ses:
        # Rollen durchsuchen
        roles = ses.exec(select(Role).where(Role.is_deleted == False)).all()
        role_scores = []
        for role in roles:
            if not role.embedding:
                continue
            try:
                embedding = json.loads(role.embedding)
                similarity = _cosine_similarity(query_embedding, embedding)
                if similarity >= threshold:
                    role_scores.append({
                        "key": role.key,
                        "title": role.title,
                        "description": role.description,
                        "similarity": round(similarity, 3)
                    })
            except Exception:
                continue
        
        results["roles"] = sorted(role_scores, key=lambda x: x["similarity"], reverse=True)[:limit]
        
        # Kontexte durchsuchen
        contexts = ses.exec(select(Context).where(Context.is_deleted == False)).all()
        context_scores = []
        for context in contexts:
            if not context.embedding:
                continue
            try:
                embedding = json.loads(context.embedding)
                similarity = _cosine_similarity(query_embedding, embedding)
                if similarity >= threshold:
                    context_scores.append({
                        "key": context.key,
                        "title": context.title,
                        "description": context.description,
                        "similarity": round(similarity, 3)
                    })
            except Exception:
                continue
        
        results["contexts"] = sorted(context_scores, key=lambda x: x["similarity"], reverse=True)[:limit]
        
        # Projekte durchsuchen
        projects = ses.exec(select(Project).where(Project.is_deleted == False)).all()
        project_scores = []
        for project in projects:
            if not project.embedding:
                continue
            try:
                embedding = json.loads(project.embedding)
                similarity = _cosine_similarity(query_embedding, embedding)
                if similarity >= threshold:
                    project_scores.append({
                        "key": project.key,
                        "title": project.title,
                        "description": project.description,
                        "similarity": round(similarity, 3)
                    })
            except Exception:
                continue
        
        results["projects"] = sorted(project_scores, key=lambda x: x["similarity"], reverse=True)[:limit]
        
        # Tasks durchsuchen
        tasks = ses.exec(select(Task).where(Task.is_deleted == False)).all()
        task_scores = []
        for task in tasks:
            if not task.embedding:
                continue
            try:
                embedding = json.loads(task.embedding)
                similarity = _cosine_similarity(query_embedding, embedding)
                if similarity >= threshold:
                    task_scores.append({
                        "key": task.key,
                        "title": task.title,
                        "description": task.description,
                        "similarity": round(similarity, 3)
                    })
            except Exception:
                continue
        
        results["tasks"] = sorted(task_scores, key=lambda x: x["similarity"], reverse=True)[:limit]
        
        # Decisions durchsuchen (nur nicht-gelöschte)
        decisions = ses.exec(select(Decision).where(Decision.is_deleted == False)).all()
        decision_scores = []
        for decision in decisions:
            if not decision.embedding:
                continue
            try:
                embedding = json.loads(decision.embedding)
                similarity = _cosine_similarity(query_embedding, embedding)
                if similarity >= threshold:
                    decision_scores.append({
                        "id": decision.id,
                        "title": decision.title,
                        "description": decision.description,
                        "similarity": round(similarity, 3)
                    })
            except Exception:
                continue
        
        results["decisions"] = sorted(decision_scores, key=lambda x: x["similarity"], reverse=True)[:limit]

        # Dokumente durchsuchen (Chunks)
        doc_query = select(DocumentChunk).join(Document).where(Document.is_deleted == False)
        
        # Filter: Projekt-Zuordnung
        if project_key:
            doc_query = doc_query.join(ProjectDocumentLink, ProjectDocumentLink.document_id == Document.id)\
                                 .where(ProjectDocumentLink.project_key == project_key)
        
        # Filter: Rollen-Zuordnung (für Task-Gen) - NUR Rollen-spezifische Dokumente
        if role_key:
            doc_query = doc_query.where(Document.linked_role_keys.like(f'%"{role_key}"%'))
        
        # Filter: Klassifizierung
        if classification_filter:
            doc_query = doc_query.where(Document.classification == classification_filter)
        
        # Filter: Klassifizierung ausschliessen (z.B. FAQ/Fragen-Katalog im Batch-Modus)
        if exclude_classification:
            doc_query = doc_query.where(Document.classification != exclude_classification)
        
        chunks = ses.exec(doc_query).all()

        # Pre-fetch alle Dokumente (für Metadata) – 'chunk.document' ist kein Relationship
        chunk_doc_ids = list(set(c.document_id for c in chunks))
        docs_by_id: dict[int, Document] = {}
        if chunk_doc_ids:
            docs_by_id = {d.id: d for d in ses.exec(
                select(Document).where(Document.id.in_(chunk_doc_ids))
            ).all()}

        doc_scores = []
        for chunk in chunks:
            if not chunk.embedding:
                continue
            try:
                embedding = json.loads(chunk.embedding)
                similarity = _cosine_similarity(query_embedding, embedding)
                if similarity >= threshold:
                    doc = docs_by_id.get(chunk.document_id)
                    if not doc:
                        continue
                    doc_scores.append({
                        "chunk_id": chunk.id,
                        "document_id": doc.id,
                        "filename": doc.filename,
                        "classification": doc.classification,
                        "text": chunk.chunk_text,
                        "similarity": round(similarity, 3)
                    })
            except Exception:
                continue

        # Diversity-Filter: max. 3 Chunks pro Dokument, damit kein einzelnes Dokument
        # alle Top-K-Plätze belegt (z.B. Pflichtenheft mit 124 Chunks)
        doc_scores_sorted = sorted(doc_scores, key=lambda x: x["similarity"], reverse=True)
        max_per_doc = max(2, limit // 2)  # z.B. limit=7 → max 3 pro Dokument
        per_doc_count: dict[int, int] = {}
        diverse_scores = []
        for entry in doc_scores_sorted:
            did = entry["document_id"]
            if per_doc_count.get(did, 0) < max_per_doc:
                diverse_scores.append(entry)
                per_doc_count[did] = per_doc_count.get(did, 0) + 1
            if len(diverse_scores) >= limit:
                break

        results["documents"] = diverse_scores
    
    return results


def _find_relevant_window(text: str, query: str | None, max_length: int) -> str:
    """Extrahiert nach Möglichkeit einen Ausschnitt um den ersten relevanten Query-Treffer."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    if not query:
        return cleaned[:max_length] + "..." if len(cleaned) > max_length else cleaned

    import re

    def _terms(raw: str) -> list[str]:
        stopwords = {
            "frage", "anbieter", "bitte", "kurz", "bündig", "antworten", "antwort",
            "konkret", "betrifft", "gilt", "gelten", "unsere", "unserer", "hier",
            "wird", "werden", "werde", "wurde", "wurden", "einer", "eines",
            "dieses", "dieser", "diesem", "diesen", "nicht", "haben", "hatte",
            "damit", "durch", "dabei", "davon", "daran", "daher", "darum",
            "auch", "eine", "keinen", "keine", "keins", "falls", "sowie",
        }
        words = re.findall(r"\b\w+\b", (raw or "").lower())
        candidates = [w for w in words if len(w) >= 6 and w not in stopwords]
        seen = set()
        ordered = []
        for item in candidates:
            stem = item[:10]
            if stem not in seen:
                seen.add(stem)
                ordered.append(stem)
        return ordered[:12]

    lowered = cleaned.lower()
    for term in _terms(query):
        idx = lowered.find(term)
        if idx >= 0:
            start = max(0, idx - max_length // 3)
            end = min(len(cleaned), start + max_length)
            snippet = cleaned[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(cleaned):
                snippet = snippet + "..."
            return snippet

    return cleaned[:max_length] + "..." if len(cleaned) > max_length else cleaned


def format_chunk_preview(chunk_text: str, max_length: int = 200, query: str | None = None) -> str:
    """
    Formatiert einen Chunk für Preview (truncate bei max_length).
    Args:
        chunk_text: Der Chunk-Text
        max_length: Max Anzahl Zeichen
        query: Optionaler Query-Text für Treffer-zentrierte Preview
    Returns: Gekürzter Text mit "..." falls nötig
    """
    return _find_relevant_window(chunk_text, query, max_length)


def filter_roles_by_query(query: str, project_key: str | None = None, limit: int = 3) -> list:
    """
    Filtert Top-N Rollen basierend auf Query-Relevanz.
    Nutzt Semantic + Keyword Matching.
    
    Args:
        query: Die Nutzer-Query
        project_key: Optional: Filter nur Rollen dieses Projekts
        limit: Anzahl Rollen (default 3)
    
    Returns: Liste von Rollen mit Similarity-Score
    """
    query = (query or "").strip().lower()
    if not query:
        return []
    
    # Semantic: Query-Embedding
    query_embedding = embed_text(query)
    if not query_embedding:
        return []
    
    with get_session() as ses:
        roles = ses.exec(select(Role).where(Role.is_deleted == False)).all()
        
        if project_key:
            proj = ses.exec(select(Project).where(Project.key == project_key)).first()
            role_keys = []
            if proj and proj.role_keys:
                try:
                    role_keys = json.loads(proj.role_keys) if isinstance(proj.role_keys, str) else proj.role_keys
                except:
                    role_keys = []
            roles = [r for r in roles if r.key in role_keys]
        
        role_scores = []
        for role in roles:
            if not role.embedding:
                continue
            
            try:
                embedding = json.loads(role.embedding)
                semantic_sim = _cosine_similarity(query_embedding, embedding)
            except:
                semantic_sim = 0
            
            # Keyword boost
            text = f"{role.title} {role.description or ''} {role.responsibilities or ''}".lower()
            keywords = query.split()
            keyword_score = sum(1 for kw in keywords if len(kw) > 2 and kw in text) / max(len(keywords), 1)
            
            # Combine: 70% semantic, 30% keyword
            combined_score = (semantic_sim * 0.7) + (keyword_score * 0.3)
            
            if combined_score > 0:
                role_scores.append({
                    "key": role.key,
                    "title": role.title,
                    "description": role.description,
                    "expertise": role.expertise,
                    "similarity": round(combined_score, 3)
                })
        
        return sorted(role_scores, key=lambda x: x["similarity"], reverse=True)[:limit]


def index_decision(decision_id: int, force: bool = False) -> bool:
    """
    Erstellt Embedding für eine Entscheidung und speichert es in der DB.
    
    Args:
        decision_id: ID der Decision
        force: Falls True, überschreibe existierendes Embedding
    
    Returns: True wenn erfolgreich, False sonst.
    """
    with get_session() as ses:
        decision = ses.exec(select(Decision).where(Decision.id == decision_id)).first()
        if not decision or decision.is_deleted:
            return False
        
        if decision.embedding and not force:
            return True
        
        text_parts = [
            decision.title or "",
            decision.description or ""
        ]
        text = "\n".join([p for p in text_parts if p])
        
        if not text:
            return False
        
        embedding = embed_text(text)
        if not embedding:
            return False
        
        decision.embedding = json.dumps(embedding)
        decision.embedding_model = EMBEDDING_MODEL
        ses.add(decision)
        ses.commit()
    
    return True


def build_rag_context_from_search(search_results: dict, project_key: str | None = None) -> str:
    """
    Baut einen Kontext-String aus den Suchergebnissen für den System-Prompt.
    
    Args:
        search_results: Dict von retrieve_relevant_chunks()
        project_key: Optional: Load detailed content für diese Role/Context/Task/Project
    
    Returns: Markdown-String mit relevanten Kontextinformationen
    """
    parts = []
    
    # Rollen
    if search_results.get("roles"):
        parts.append("## 👤 Relevante Rollen")
        for role in search_results["roles"][:3]:
            sim = role.get('similarity', 0)
            parts.append(f"- **{role['title']}** (Relevanz: {sim:.0%})" if sim > 0 else f"- **{role['title']}**")
            if role.get('description'):
                parts.append(f"  > {role['description']}")
    
    # Kontexte
    if search_results.get("contexts"):
        parts.append("\n## 🎓 Relevante Kontexte")
        for context in search_results["contexts"][:3]:
            sim = context.get('similarity', 0)
            parts.append(f"- **{context['title']}** (Relevanz: {sim:.0%})" if sim > 0 else f"- **{context['title']}**")
            if context.get('description'):
                parts.append(f"  > {context['description']}")
    
    # Projekte
    if search_results.get("projects"):
        parts.append("\n## 📋 Relevante Projekte")
        for project in search_results["projects"][:3]:
            sim = project.get('similarity', 0)
            parts.append(f"- **{project['title']}** (Relevanz: {sim:.0%})" if sim > 0 else f"- **{project['title']}**")
            if project.get('description'):
                parts.append(f"  > {project['description']}")
    
    # Tasks
    if search_results.get("tasks"):
        parts.append("\n## ✅ Relevante Tasks")
        for task in search_results["tasks"][:3]:
            sim = task.get('similarity', 0)
            parts.append(f"- **{task['title']}** (Relevanz: {sim:.0%})" if sim > 0 else f"- **{task['title']}**")
            if task.get('description'):
                parts.append(f"  > {task['description']}")
    
    # Decisions
    if search_results.get("decisions"):
        parts.append("\n## 💾 Relevante Entscheidungen")
        for decision in search_results["decisions"][:3]:
            sim = decision.get('similarity', 0)
            parts.append(f"- **{decision['title']}** (Relevanz: {sim:.0%})" if sim > 0 else f"- **{decision['title']}**")
            if decision.get('description'):
                parts.append(f"  > {decision['description']}")

    # Dokumente
    if search_results.get("documents"):
        parts.append("\n## 📄 Relevante Dokument-Auszüge")
        for doc in search_results["documents"][:3]:
            sim = doc.get('similarity', 0)
            filename = doc.get('filename', 'Unbekannt')
            parts.append(f"### Auszug aus '{filename}' (Relevanz: {sim:.0%})" if sim > 0 else f"### Auszug aus '{filename}'")
            parts.append(f"> {doc.get('text', '')}")
    
    return "\n".join(parts) if parts else ""


def _normalize_text(text: str) -> str:
    """Normalisiert Text für Dedup-Vergleich."""
    import re
    text = (text or "").lower().strip()
    text = re.sub(r'\s+', ' ', text)
    return text[:200]  # Erste 200 Zeichen vergleichen

def deduplicate_results(results: dict) -> dict:
    """
    Entfernt duplizierte Chunks aus RAG-Ergebnissen.
    Vergleicht Text-Ähnlichkeit, nicht nur IDs.
    """
    if not results.get("documents"):
        return results
    
    seen_texts = set()
    deduped = []
    
    for doc in results["documents"]:
        norm_text = _normalize_text(doc.get("text", ""))
        if norm_text not in seen_texts:
            seen_texts.add(norm_text)
            deduped.append(doc)
    
    results["documents"] = deduped
    return results


def _detect_acronyms(distilled_keywords: str) -> list[str]:
    """
    Erkennt potentielle Akronyme in den distilled keywords.
    
    Patterns:
    - Buchstaben-Kombinationen mit Bindestrichen: sik-agb, http-api, SIK-AGB
    - Kurze Großbuchstaben-Wörter (original): API, DSGVO, REST
    - Min. 2 Buchstaben, max. 10 Zeichen
    
    Returns:
        Liste der erkannten Akronyme (in Originalschreibweise)
    """
    import re
    tokens = distilled_keywords.split()
    acronyms = []
    
    for token in tokens:
        # Pattern 1: xxx-yyy (mehrere Buchstaben mit Bindestrichen, case-insensitive)
        if re.match(r'^[a-zA-ZÄÖÜäöü]{2,}(-[a-zA-ZÄÖÜäöü]{2,})+$', token):
            acronyms.append(token)
        # Pattern 2: API, REST (kurze Großbuchstaben-Wörter)
        elif 2 <= len(token) <= 10 and token.isupper() and token.isalpha():
            acronyms.append(token)
    
    return acronyms


def _expand_acronyms_with_llm(acronyms: list[str], provider: str = "openai", model: str = "gpt-4o-mini") -> dict[str, str]:
    """
    Verwendet LLM um Akronyme/Abkürzungen zu expandieren.
    
    Args:
        acronyms: Liste der zu expandierenden Akronyme
        provider: LLM Provider
        model: LLM Modell
    
    Returns:
        Dict mit {akronym: expansion} Mapping
    """
    from src.m08_llm import try_models_with_messages
    
    if not acronyms:
        return {}
    
    system_prompt = """Du bist ein Experte für deutsche Verwaltungs-, IT- und Rechts-Terminologie.
Erkläre folgende Begriffe/Akronyme KURZ (max. 3-5 Ersatzwörter pro Begriff).
Fokus auf deutsche/schweizerische Verwaltung, IT-Verträge, Ausschreibungen.

Wenn du einen Begriff nicht kennst, gib "unbekannt" zurück.

Antworte NUR mit einem JSON-Dict (keine Erklärung):
{"BEGRIFF": "ersatzwort1 ersatzwort2", ...}

Beispiel:
{"SIK-AGB": "Schweizerische Informatikkonferenz Geschäftsbedingungen", "DSGVO": "Datenschutz Grundverordnung"}"""
    
    user_prompt = f"Begriffe: {json.dumps(acronyms, ensure_ascii=False)}"
    
    try:
        response = try_models_with_messages(
            provider=provider,
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.0,
            max_tokens=200
        )
        
        # Parse JSON response — robust: extract JSON even if model adds prose extract JSON even if model adds prose
        import re as _re2
        _json_match = _re2.search(r'\{[^{}]*\}', response, _re2.DOTALL)
        if not _json_match:
            logger.warning(f"Query Expansion: Kein JSON in Antwort gefunden: {response!r}")
            return {}
        expansions = json.loads(_json_match.group())
        
        # Filter "unbekannt" entries
        return {k: v for k, v in expansions.items() if v.lower() != "unbekannt"}
    
    except Exception as e:
        logger.warning(f"Query Expansion fehlgeschlagen: {e}")
        return {}


def rag_low_confidence_warning(rr: dict, threshold: float) -> str | None:
    """
    Returns a warning string if RAG results are likely irrelevant.

    Triggers when ALL final documents have semantic score below `threshold`
    (i.e. nothing was found above the bar — only low-quality fallback results),
    or when no documents were found at all.
    
    If query expansion was triggered, includes expansion details in warning.
    
    Returns None if results look fine.
    """
    docs = rr.get("documents", [])
    if not docs:
        return "⚠️ RAG: Keine Dokumente gefunden. Das Thema ist möglicherweise nicht im Corpus vorhanden."
    
    # Use ONLY semantic similarity (0-1 absolute range)
    # normalized_match_score is relative to current result set, not absolute!
    max_sem = max(d.get("similarity", 0) for d in docs)
    
    # Check if query expansion was used
    expansion_info = rr.get("expansion", {})
    
    if max_sem < threshold:
        warning = f"⚠️ RAG: Niedriger Confidence-Score (max. {max_sem:.0%} < Threshold {threshold:.0%}). "
        warning += "Die gefundenen Passagen passen möglicherweise nicht zum Thema — "
        warning += "das entsprechende Dokument könnte im Corpus fehlen."
        
        # Wenn Expansion aktiv war, zeige Details
        if expansion_info.get("triggered"):
            expansions = expansion_info.get("expansions", {})
            improvement = expansion_info.get("improvement", 0)
            original_score = expansion_info.get("original_max_score", 0)
            
            warning += f"\n\n🔄 Query-Expansion wurde versucht:"
            for acronym, expansion in expansions.items():
                warning += f"\n  • {acronym} → {expansion}"
            warning += f"\n  Verbesserung: {original_score:.0%} → {max_sem:.0%} ({improvement:+.0%})"
            
            if improvement <= 0.05:  # Weniger als 5% Verbesserung
                warning += "\n  ⚠️ Die Expansion hat nicht ausreichend geholfen."
        
        return warning
    
    # Expansion war erfolgreich (Score jetzt über Threshold) → Info-Meldung
    elif expansion_info.get("triggered"):
        expansions = expansion_info.get("expansions", {})
        improvement = expansion_info.get("improvement", 0)
        original_score = expansion_info.get("original_max_score", 0)
        
        info = f"✅ Query-Expansion erfolgreich! Score verbessert von {original_score:.0%} → {max_sem:.0%} ({improvement:+.0%})"
        info += "\n\n🔄 Expandierte Begriffe:"
        for acronym, expansion in expansions.items():
            info += f"\n  • {acronym} → {expansion}"
        return info
    
    return None
