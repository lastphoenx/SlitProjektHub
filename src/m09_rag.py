# src/m09_rag.py
"""Echtes RAG (Retrieval-Augmented Generation) mit Embeddings."""

from __future__ import annotations
import json
import os
import math
from datetime import datetime, timezone
from pathlib import Path
from sqlmodel import select
from .m03_db import Role, Task, Context, Project, get_session, ChatMessage, Decision, Document, DocumentChunk, ProjectDocumentLink


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
    Keyword-basierte Suche (LIKE-Match in Text).
    Args:
        query: Die Nutzer-Query
        project_key: Optional: Filter für Dokumente
        limit: Max Resultate
        role_key: Optional: Filter für Rollen-spezifische Dokumente
        classification_filter: Optional: Filter für Dokument-Klassifizierung
        exclude_classification: Optional: Dokumente mit DIESER Klassifizierung ausschliessen
    Returns: Dict mit keyword-gefundenen Chunks
    """
    keywords = query.lower().split()
    
    with get_session() as ses:
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
        
        matches = []
        for chunk in chunks:
            text = (chunk.chunk_text or "").lower()
            match_count = sum(1 for kw in keywords if kw in text)
            
            if match_count > 0:
                # Fetch document directly via document_id
                doc = ses.get(Document, chunk.document_id)
                if doc:
                    matches.append({
                        "chunk_id": chunk.id,
                        "document_id": doc.id,
                        "filename": doc.filename,
                        "classification": doc.classification,
                        "text": chunk.chunk_text,
                        "match_score": match_count / len(keywords) if keywords else 0
                    })
        
        return sorted(matches, key=lambda x: x["match_score"], reverse=True)[:limit]


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
            
            # Berechne Similarity für jeden Chunk
            query_emb = embed_text(query)
            if not query_emb:
                result.append({
                    "document_id": doc.id,
                    "filename": doc.filename,
                    "classification": doc.classification,
                    "best_score": 0.0,
                    "included": False,
                    "reason": "Query-Embedding fehlgeschlagen"
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

    # Schwellen
    guaranteed_threshold = 0.10            # Absolut: jedes Dokument mit Score > 10% bekommt min. 1 Slot
    discovery_threshold = min(threshold * 0.35, 0.15)  # breites semantisches Netz
    max_per_doc = max(2, limit // 2)           # Diversity-Cap (z.B. limit=7 → max 3 pro Dok)

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

    # 3. Alle Kandidaten zusammenführen (dedupliziert nach chunk_id)
    all_docs_map: dict[int, dict] = {d["chunk_id"]: d for d in semantic_results.get("documents", [])}
    for kd in keyword_docs:
        if kd["chunk_id"] not in all_docs_map:
            all_docs_map[kd["chunk_id"]] = kd
    all_docs = list(all_docs_map.values())

    # 4. Dateiname-Boost: kleine Aufwertung wenn Query-Wörter im Dateinamen vorkommen
    query_words_long = {w.lower().rstrip(":") for w in query.split() if len(w) > 3}  # Strip Satzzeichen
    if query_words_long:
        for d in all_docs:
            fname_parts = set(
                d["filename"].lower().replace(".", " ").replace("_", " ").replace("-", " ").split()
            )
            # Teilwort-Übereinstimmung (z.B. "preis" in "preisblatt" oder "preisstruktur")
            # ODER gemeinsamer Präfix >= 5 Zeichen (z.B. "preisstruktur" vs "preisblatt" → "preis")
            matched = False
            for qw in query_words_long:
                for fp in fname_parts:
                    if (
                        qw in fp or fp in qw  # Exaktes Teilwort
                        or (len(qw) >= 5 and len(fp) >= 5 and qw[:5] == fp[:5])  # Gemeinsamer Präfix
                    ):
                        matched = True
                        break
                if matched:
                    break
            
            if matched:
                score_key = "similarity" if "similarity" in d else "match_score"
                # Erhöhter Boost: +10% statt +4%
                d[score_key] = min(1.0, d.get(score_key, 0) + 0.10)

    # 5. Pro Dokument: Chunks nach Score sortieren
    by_doc: dict[int, list] = {}
    for d in all_docs:
        by_doc.setdefault(d["document_id"], []).append(d)
    for did in by_doc:
        by_doc[did].sort(key=lambda x: x.get("similarity", x.get("match_score", 0)), reverse=True)

    # 6. Garantierter Mindest-1-Slot pro qualifizierendem Dokument
    guaranteed: list[dict] = []
    guaranteed_cids: set[int] = set()
    # Sortiert nach bestem Score, damit top-Dokumente zuerst kommen
    sorted_docs = sorted(
        by_doc.items(),
        key=lambda kv: kv[1][0].get("similarity", kv[1][0].get("match_score", 0)),
        reverse=True
    )
    for _did, chunks in sorted_docs:
        best = chunks[0]
        if best.get("similarity", best.get("match_score", 0)) >= guaranteed_threshold:
            guaranteed.append(best)
            guaranteed_cids.add(best["chunk_id"])

    # 7. Pflichtenheft-Fallback: "Pflichtenheft (Projekt)" immer vertreten (wenn Score > 0.10)
    PFLICHTENHEFT_CLS = "Pflichtenheft (Projekt)"
    if project_key and exclude_classification != PFLICHTENHEFT_CLS:
        pflicht_represented = any(c.get("classification") == PFLICHTENHEFT_CLS for c in guaranteed)
        if not pflicht_represented:
            pflicht_candidates = [
                d for d in all_docs if d.get("classification") == PFLICHTENHEFT_CLS
            ]
            if pflicht_candidates:
                best_pflicht = max(
                    pflicht_candidates,
                    key=lambda x: x.get("similarity", x.get("match_score", 0))
                )
                if best_pflicht.get("similarity", best_pflicht.get("match_score", 0)) > 0.10:
                    guaranteed.append(best_pflicht)
                    guaranteed_cids.add(best_pflicht["chunk_id"])

    # Garantierte Chunks nach Score sortieren
    guaranteed.sort(key=lambda x: x.get("similarity", x.get("match_score", 0)), reverse=True)

    # 8. Qualitäts-Füllung: restliche Slots mit top-Qualitäts-Chunks (>= threshold, diversity-cap)
    per_doc_count: dict[int, int] = {}
    for d in guaranteed:
        did = d["document_id"]
        per_doc_count[did] = per_doc_count.get(did, 0) + 1

    result_docs = list(guaranteed[:limit])
    result_cids: set[int] = {d["chunk_id"] for d in result_docs}

    if len(result_docs) < limit:
        all_sorted = sorted(
            all_docs,
            key=lambda x: x.get("similarity", x.get("match_score", 0)),
            reverse=True
        )
        for entry in all_sorted:
            if len(result_docs) >= limit:
                break
            cid = entry["chunk_id"]
            score = entry.get("similarity", entry.get("match_score", 0))
            if score < guaranteed_threshold:
                break  # Nur Chunks über Mindest-Qualitätsschwelle
            if cid in result_cids:
                continue
            did = entry["document_id"]
            if per_doc_count.get(did, 0) < max_per_doc:
                result_docs.append(entry)
                result_cids.add(cid)
                per_doc_count[did] = per_doc_count.get(did, 0) + 1

    # Finale Sortierung nach Score
    result_docs.sort(key=lambda x: x.get("similarity", x.get("match_score", 0)), reverse=True)
    semantic_results["documents"] = result_docs[:limit]

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


def format_chunk_preview(chunk_text: str, max_length: int = 200) -> str:
    """
    Formatiert einen Chunk für Preview (truncate bei max_length).
    Args:
        chunk_text: Der Chunk-Text
        max_length: Max Anzahl Zeichen
    Returns: Gekürzter Text mit "..." falls nötig
    """
    text = (chunk_text or "").strip()
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text


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
