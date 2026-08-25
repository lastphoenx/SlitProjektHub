# Temporary file for reranking implementation - to be merged into m09_rag.py

def rerank_documents(
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
            print(f"⚠️ LLM-Reranking Fehler: {e} - Fallback auf Semantic-Score")
            # Fallback
            for doc in batch:
                fallback_score = doc.get("similarity", 0.5) * 100
                doc["rerank_score"] = fallback_score / 100.0
                scored.append((fallback_score, doc))
    
    # Sortiere nach LLM-Score
    scored.sort(key=lambda x: x[0], reverse=True)
    
    return [doc for _, doc in scored[:final_k]]
