# src/m09_rag.py
"""Echtes RAG (Retrieval-Augmented Generation) mit Embeddings."""

from __future__ import annotations
import json
import os
import math
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
    import re
    
    # Stopwords (häufige, bedeutungsarme Wörter)
    STOPWORDS = {
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines",
        "und", "oder", "aber", "mit", "von", "zu", "bei", "für", "auf", "an", "in",
        "ich", "du", "er", "sie", "es", "wir", "ihr", "ist", "sind", "war", "waren",
        "wird", "werden", "hat", "haben", "kann", "können", "soll", "sollte", "muss",
        "wie", "was", "wer", "wo", "wann", "warum", "welche", "welcher", "welches",
        "bitte", "antworten", "antwort", "frage", "anbieter", "hier", "kurz", "bündig",
        "betrifft", "konkret", "gilt", "gelten", "stellt", "stelltsich", "nutzung",
        "interner", "interne", "unserer", "unsere", "unser", "einer", "einem", "einen"
    }

    def _strip_query_wrapper(text: str) -> str:
        """Entfernt UI-Wrapper wie 'Frage von Anbieter ...:' und behält die eigentliche Nutzfrage."""
        lowered = text.lower()
        marker = "frage von anbieter"
        if marker in lowered and ":" in text:
            return text.split(":", 1)[1].strip()
        return text.strip()
    
    def tokenize(text: str) -> list[str]:
        """
        Tokenisiert Text mit einfachem German Stemming.
        
        - Lowercase
        - Nur Buchstaben/Zahlen
        - Stopwords raus
        - Min 3 Zeichen
        - Stemming: Entferne häufige deutsche Suffixe
        """
        text = text.lower()
        # Split on non-alphanumeric, keep words
        words = re.findall(r'\b\w+\b', text)
        
        # German Stemmer (einfache Version)
        def stem_german(word: str) -> str:
            """Entfernt häufige deutsche Suffixe."""
            # Lange Wörter: mehrstufiges Stemming
            if len(word) >= 10:
                # Entferne lange Suffixe zuerst
                for suffix in ['ende', 'enden', 'ender', 'enden', 'ungen', 'schaft', 'heit', 'keit']:
                    if word.endswith(suffix):
                        word = word[:-len(suffix)]
                        break
            
            # Kurze Suffixe für alle Wörter >= 5 Zeichen
            if len(word) >= 5:
                for suffix in ['en', 'er', 'em', 'es', 'end', 'ung']:
                    if word.endswith(suffix):
                        word = word[:-len(suffix)]
                        break
            
            return word
        
        # Filter: min 3 chars, no stopwords, dann stemmen
        tokens = []
        for w in words:
            if len(w) >= 3 and w not in STOPWORDS:
                stemmed = stem_german(w)
                tokens.append(stemmed)
        
        return tokens

    # PHASE 3: Priority Terms aus Config laden (statt hardcoded)
    config = get_retrieval_config()
    priority_parts = config.bm25.priority_terms

    def _prepare_query_tokens(raw_query: str) -> list[str]:
        """Fokussiert lange UI-Fragen auf die inhaltlich relevanten Suchterme."""
        core_query = _strip_query_wrapper(raw_query)
        tokens = tokenize(core_query)

        # Deduplizieren, Reihenfolge beibehalten
        unique_tokens = []
        seen = set()
        for token in tokens:
            if token not in seen:
                seen.add(token)
                unique_tokens.append(token)

        if len(unique_tokens) <= 12:
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
            return (priority_tokens + supporting_tokens[:4])[:8]

        # Fallback für andere Fragen: die längsten, eindeutigsten Tokens behalten.
        ranked_tokens = sorted(unique_tokens, key=len, reverse=True)
        return ranked_tokens[:10]
    
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
        tokenized_corpus = []
        chunk_metadata = []  # (chunk_id, document_id, chunk_text, doc_object, token_set)
        
        for chunk in chunks:
            tokens = tokenize(chunk.chunk_text or "")
            if tokens:  # nur Chunks mit Content
                tokenized_corpus.append(tokens)
                chunk_metadata.append((chunk.id, chunk.document_id, chunk.chunk_text, docs_map.get(chunk.document_id), set(tokens)))
        
        if not tokenized_corpus:
            return []
        
        # BM25 Index erstellen
        bm25 = BM25Okapi(tokenized_corpus)
        
        # Query tokenisieren
        query_tokens = _prepare_query_tokens(query)
        
        if not query_tokens:
            # Fallback: wenn alle Wörter Stopwords sind, verwende Original
            query_tokens = [token for token in query.lower().split() if len(token) >= 3]
        
        # BM25 Scores berechnen
        scores = bm25.get_scores(query_tokens)
        
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
        
        return results[:limit]


def get_all_documents_with_best_scores(
    query: str,
    project_key: str | None = None,
    threshold: float = 0.5,
    exclude_classification: str | None = None
) -> list[dict]:
    """
    Gibt ALLE Projekt-Dokumente mit ihrem besten Chunk-Score zurück.
    
    Nützlich für Diagnostics: zeigt welche Dokumente geprüft wurden,
    aber nicht in Top-K kamen (Score < Threshold).
    
    Returns:
        Liste von {"document_id", "filename", "classification", "best_score", "included"}
    """
    from src.m09_docs import get_project_documents
    
    all_docs = get_project_documents(project_key) if project_key else []
    
    if exclude_classification:
        all_docs = [d for d in all_docs if d.classification != exclude_classification]
    
    result = []
    
    # CRITICAL FIX: Performance O(N) → O(1)
    # Query-Embedding EINMAL vor der Schleife berechnen
    query_emb = embed_text(query)
    if not query_emb:
        # Bei Embedding-Fehler: alle Dokumente als "fehlgeschlagen" markieren
        return [{
            "document_id": doc.id,
            "filename": doc.filename,
            "classification": doc.classification,
            "best_score": 0.0,
            "included": False,
            "reason": "Query-Embedding fehlgeschlagen"
        } for doc in all_docs]
    
    with get_session() as ses:
        for doc in all_docs:
            # Hole alle Chunks dieses Dokuments
            chunks = ses.exec(
                select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
            ).all()
            
            if not chunks:
                result.append({
                    "document_id": doc.id,
                    "filename": doc.filename,
                    "classification": doc.classification,
                    "best_score": 0.0,
                    "included": False,
                    "reason": "Keine Chunks vorhanden"
                })
                continue
            
            best_score = 0.0
            for chunk in chunks:
                if chunk.embedding:
                    chunk_emb = json.loads(chunk.embedding)
                    sim = _cosine_similarity(query_emb, chunk_emb)
                    best_score = max(best_score, sim)
            
            # Check if included (>= threshold)
            included = best_score >= threshold
            reason = ""
            if not included:
                if best_score < 0.05:
                    reason = "Semantisch irrelevant (<5%)"
                else:
                    reason = f"Score {best_score:.0%} < Threshold {threshold:.0%}"
            
            result.append({
                "document_id": doc.id,
                "filename": doc.filename,
                "classification": doc.classification,
                "best_score": best_score,
                "included": included,
                "reason": reason
            })
    
    # Sortiere nach Score (höchste zuerst)
    return sorted(result, key=lambda x: x["best_score"], reverse=True)


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
                doc_data[chunk_id] = hit
            
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
    exclude_classification: str | None = None
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
    cache_key = _get_cache_key(query, project_key, limit, threshold, exclude_classification)
    if cache_key in _RAG_CACHE:
        return _RAG_CACHE[cache_key]

    # PHASE 3: Config-basierte Schwellenwerte (statt Hardcoding)
    config = get_retrieval_config()
    guaranteed_threshold = config.hybrid.guaranteed_doc_threshold
    discovery_threshold = min(
        threshold * config.semantic.discovery_threshold_multiplier,
        config.semantic.min_discovery_threshold
    )
    max_per_doc = config.hybrid.max_chunks_per_document or max(2, limit // 2)

    # 1. Semantic-Suche mit niedrigerer Entdeckungs-Schwelle
    semantic_results = retrieve_relevant_chunks(
        query, project_key, limit * 5, discovery_threshold,
        role_key=role_key,
        classification_filter=classification_filter,
        exclude_classification=exclude_classification
    )

    # 2. Keyword-Suche mit grossem Limit (alle Dokumente abdecken)
    keyword_docs = _keyword_search(
        query, project_key, limit * 10,
        role_key=role_key,
        classification_filter=classification_filter,
        exclude_classification=exclude_classification
    )

    semantic_debug_docs = [dict(d) for d in semantic_results.get("documents", [])]

    # 3. PHASE 3: Multi-Hypothesis oder Standard RRF-Fusion
    if config.query.enable_multi_hypothesis:
        # Multi-Hypothesis: Parallele Query-Varianten -> mehr Rankings -> RRF
        # Limit pro Einzel-Suche reduziert (Performance-Trade-off)
        hyp_limit_sem = max(limit * 3, 10)   # weniger als Standard (limit*5)
        hyp_limit_kw  = max(limit * 5, 15)   # weniger als Standard (limit*10)

        hypotheses = _get_generate_query_hypotheses()(
            query,
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

    def _debug_entry(d: dict, source: str) -> dict:
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
            "matched_terms": d.get("matched_terms", []),
            "rrf_score": d.get("rrf_score", 0.0),
            "quality_score": round(_quality_score(d), 3),
            "combined_score": round(_combined_score(d), 3),
            "text_preview": _find_relevant_window((d.get("text", "") or "").replace("\n", " "), query, 220),
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
    per_doc_count: dict[int, int] = {}
    for d in guaranteed:
        did = d["document_id"]
        per_doc_count[did] = per_doc_count.get(did, 0) + 1

    result_docs = list(guaranteed[:limit])
    result_cids: set[int] = {d["chunk_id"] for d in result_docs}

    if len(result_docs) < limit:
        # Sortiere nach RRF Score, aber filtere nach quality threshold
        all_sorted = sorted(all_docs, key=_combined_score, reverse=True)
        for entry in all_sorted:
            if len(result_docs) >= limit:
                break
            cid = entry["chunk_id"]
            # Schwellenwert-Check mit original scores (nicht RRF)
            if _quality_score(entry) < guaranteed_threshold:
                break
            if cid in result_cids:
                continue
            did = entry["document_id"]
            if per_doc_count.get(did, 0) < max_per_doc:
                result_docs.append(entry)
                result_cids.add(cid)
                per_doc_count[did] = per_doc_count.get(did, 0) + 1

    # Finale Sortierung nach RRF Score
    result_docs.sort(key=_combined_score, reverse=True)
    for doc in result_docs:
        doc["combined_score"] = round(_combined_score(doc), 3)
        # Für Debug: Auch quality_score speichern
        doc["quality_score"] = round(_quality_score(doc), 3)
    semantic_results["documents"] = result_docs[:limit]
    semantic_results["debug"] = {
        "query": query,
        "semantic_candidates": [_debug_entry(d, "semantic") for d in semantic_debug_docs[:15]],
        "keyword_candidates": [_debug_entry(d, "keyword") for d in keyword_docs[:15]],
        "final_candidates": [_debug_entry(d, "final") for d in result_docs[:15]],
    }

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

        doc_scores = []
        for chunk in chunks:
            if not chunk.embedding:
                continue
            try:
                embedding = json.loads(chunk.embedding)
                similarity = _cosine_similarity(query_embedding, embedding)
                if similarity >= threshold:
                    doc = chunk.document
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
            "wird", "werden", "eine", "einer", "eines", "dieses", "dieser", "auch"
        }
        words = re.findall(r"\b\w+\b", (raw or "").lower())
        candidates = [w for w in words if len(w) >= 5 and w not in stopwords]
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
