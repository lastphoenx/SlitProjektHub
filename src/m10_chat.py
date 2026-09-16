from __future__ import annotations
from datetime import datetime, timezone
from sqlmodel import select, func
from .m03_db import ChatMessage, Decision, get_session, Project, Role, Context, Task, Document
import json
from typing import Optional

def save_message(provider: str, session_id: str, role: str, content: str,
                 project_key: str | None = None, message_type: str | None = None,
                 message_status: str | None = None, model_name: str | None = None,
                 model_temperature: float | None = None, rag_sources: list | None = None) -> ChatMessage:
    """Speichert eine Chat-Message in der DB mit UTC-Timestamp und optionalen Metadaten."""
    rag_sources_json = None
    if rag_sources:
        try:
            rag_sources_json = json.dumps(rag_sources, ensure_ascii=False)
        except:
            pass
    
    msg = ChatMessage(
        provider=provider,
        session_id=session_id,
        role=role,
        content=content,
        project_key=project_key,
        message_type=message_type,
        message_status=message_status or "ungeprüft",
        model_name=model_name,
        model_temperature=model_temperature,
        is_deleted=False,
        rag_sources=rag_sources_json
    )
    with get_session() as ses:
        ses.add(msg)
        ses.commit()
        ses.refresh(msg)
    
    if message_type == "decision" and message_status == "bestätigt" and project_key:
        _create_decision_from_message(msg, project_key, session_id)
    
    return msg


def _create_decision_from_message(msg: ChatMessage, project_key: str, session_id: str) -> Decision | None:
    """
    Erstellt eine Decision aus einer Chat-Message.
    Wird aufgerufen wenn message_type="decision" und message_status="bestätigt".
    """
    try:
        lines = (msg.content or "").strip().split("\n")
        title = (lines[0] if lines else "Entscheidung")[:100]
        
        description = msg.content or ""
        
        decision = Decision(
            project_key=project_key,
            session_id=session_id,
            message_id=msg.id,
            title=title,
            description=description,
            created_from_chat=True
        )
        
        with get_session() as ses:
            ses.add(decision)
            ses.commit()
            ses.refresh(decision)
        
        try:
            from .m09_rag import index_decision
            index_decision(decision.id)
        except Exception:
            pass
        
        return decision
    except Exception:
        return None


def load_history(provider: str, session_id: str, limit: int | None = None, include_deleted: bool = False) -> list[dict]:
    """
    Lädt die Chat-Historie für einen Provider + Session.
    Bei limit: Gibt die jüngsten N Nachrichten zurück (in chronologischer Reihenfolge).
    include_deleted: Wenn True, auch is_deleted=True Messages zeigen (mit is_deleted Flag).
    Returns: [{"role":"user|assistant","content":"...","timestamp":"...","id":N,"message_type":"...","message_status":"...","is_deleted":False},...]
    """
    with get_session() as ses:
        if limit:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.provider == provider)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.timestamp.desc())
                .limit(limit)
            )
            rows = list(reversed(ses.exec(stmt).all()))
        else:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.provider == provider)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.timestamp.asc())
            )
            rows = ses.exec(stmt).all()
        
        if not include_deleted:
            rows = [r for r in rows if not r.is_deleted]
    
    return [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "message_type": r.message_type,
            "message_status": r.message_status,
            "model_name": r.model_name,
            "model_temperature": r.model_temperature,
            "timestamp": r.timestamp.isoformat() if hasattr(r.timestamp, "isoformat") else str(r.timestamp),
            "project_key": r.project_key,
            "is_deleted": r.is_deleted,
            "rag_sources": json.loads(r.rag_sources) if r.rag_sources else None
        }
        for r in rows
    ]


def delete_message(message_id: int) -> bool:
    """
    Hard-Delete einer einzelnen Message (wirklich aus DB löschen).
    Returns: True wenn erfolgreich gelöscht, False wenn Message nicht existiert.
    """
    with get_session() as ses:
        msg = ses.exec(select(ChatMessage).where(ChatMessage.id == message_id)).first()
        if not msg:
            return False
        ses.delete(msg)
        ses.commit()
    return True


def archive_message(message_id: int) -> ChatMessage | None:
    """
    Archiviert eine Message: Soft-Delete + revert metadata (message_type, message_status).
    Returns: Updated message oder None wenn nicht gefunden.
    """
    with get_session() as ses:
        msg = ses.exec(select(ChatMessage).where(ChatMessage.id == message_id)).first()
        if not msg:
            return None
        msg.is_deleted = True
        msg.message_type = None
        msg.message_status = "ungeprüft"
        ses.add(msg)
        ses.commit()
        ses.refresh(msg)
    return msg


def delete_history(provider: str, session_id: str) -> int:
    """
    Soft-Delete aller Messages eines Providers für eine Session (UI-Löschen).
    Returns: Anzahl gelöschter Messages.
    """
    with get_session() as ses:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.provider == provider)
            .where(ChatMessage.session_id == session_id)
            .where(ChatMessage.is_deleted == False)
        )
        msgs = ses.exec(stmt).all()
        count = len(msgs)
        for m in msgs:
            m.is_deleted = True
            ses.add(m)
        ses.commit()
    return count


def purge_history(provider: str, session_id: str) -> int:
    """
    Hard-Delete aller Messages einer Session (endgültiges Löschen aus DB).
    Returns: Anzahl gelöschter Messages.
    """
    with get_session() as ses:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.provider == provider)
            .where(ChatMessage.session_id == session_id)
        )
        msgs = ses.exec(stmt).all()
        count = len(msgs)
        for m in msgs:
            ses.delete(m)
        ses.commit()
    return count


def delete_all_history(session_id: str) -> int:
    """
    Soft-Delete aller Messages einer Session (alle Provider).
    Returns: Anzahl gelöschter Messages.
    """
    with get_session() as ses:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .where(ChatMessage.is_deleted == False)
        )
        msgs = ses.exec(stmt).all()
        count = len(msgs)
        for m in msgs:
            m.is_deleted = True
            ses.add(m)
        ses.commit()
    return count


def update_message_metadata(message_id: int, message_type: str | None = None, 
                           message_status: str | None = None) -> ChatMessage | None:
    """Aktualisiert Nachrichtentyp und/oder Status."""
    with get_session() as ses:
        msg = ses.exec(select(ChatMessage).where(ChatMessage.id == message_id)).first()
        if not msg:
            return None
        if message_type is not None:
            msg.message_type = message_type
        if message_status is not None:
            msg.message_status = message_status
        ses.add(msg)
        ses.commit()
        ses.refresh(msg)
    
    if msg.message_type == "decision" and msg.message_status == "bestätigt" and msg.project_key:
        with get_session() as ses:
            existing = ses.exec(select(Decision).where(Decision.message_id == message_id)).first()
        if not existing:
            _create_decision_from_message(msg, msg.project_key, msg.session_id)
    
    return msg


def find_latest_session_for_project(provider: str, project_key: str) -> str | None:
    """
    Findet die letzte Session-ID für ein Projekt + Provider.
    Berücksichtigt auch versteckte Nachrichten (is_deleted=True).
    Versucht zuerst mit project_key, dann Fallback auf Session OHNE project_key (alte Chats).
    Returns: session_id oder None wenn keine Nachrichten existieren.
    """
    with get_session() as ses:
        stmt = (
            select(ChatMessage.session_id)
            .where(ChatMessage.provider == provider)
            .where(ChatMessage.project_key == project_key)
            .order_by(ChatMessage.timestamp.desc())
            .limit(1)
        )
        result = ses.exec(stmt).first()
        
        if result:
            return result
        
        stmt_fallback = (
            select(ChatMessage.session_id)
            .where(ChatMessage.provider == provider)
            .where(ChatMessage.project_key.is_(None))
            .order_by(ChatMessage.timestamp.desc())
            .limit(1)
        )
        result_fallback = ses.exec(stmt_fallback).first()
    
    return result_fallback if result_fallback else None


def get_all_sessions_for_provider(provider: str) -> list[dict]:
    """
    Gibt alle Nachrichten-Sessions für einen Provider mit Details.
    Nützlich zum Debuggen: um zu sehen welche Sessions es gibt und ob project_key gesetzt ist.
    """
    with get_session() as ses:
        stmt = (
            select(
                ChatMessage.session_id,
                ChatMessage.project_key,
                func.count().label("message_count"),
                func.max(ChatMessage.timestamp).label("last_timestamp"),
            )
            .where(ChatMessage.provider == provider)
            .where(ChatMessage.is_deleted == False)
            .group_by(ChatMessage.session_id, ChatMessage.project_key)
            .order_by(func.max(ChatMessage.timestamp).desc())
        )
        rows = ses.exec(stmt).all()
    
    return [
        {
            "session_id": r.session_id,
            "project_key": r.project_key,
            "message_count": r.message_count,
            "last_timestamp": r.last_timestamp.isoformat() if hasattr(r.last_timestamp, "isoformat") else str(r.last_timestamp),
        }
        for r in rows
    ]


def list_sessions(provider: str | None = None, limit: int = 20) -> list[dict]:
    """
    Listet alle Sessions mit Aggregat-Daten (ohne N+1-Queries).
    Returns: [{"session_id":"...","provider":"...","last_timestamp":"...","message_count":N},...]
    """
    with get_session() as ses:
        stmt = (
            select(
                ChatMessage.session_id,
                ChatMessage.provider,
                func.count().label("message_count"),
                func.max(ChatMessage.timestamp).label("last_timestamp"),
            )
            .where(ChatMessage.is_deleted == False)
            .group_by(ChatMessage.session_id, ChatMessage.provider)
            .order_by(func.max(ChatMessage.timestamp).desc())
            .limit(limit)
        )
        if provider:
            stmt = stmt.where(ChatMessage.provider == provider)
        
        rows = ses.exec(stmt).all()
    
    return [
        {
            "session_id": r.session_id,
            "provider": r.provider,
            "last_timestamp": r.last_timestamp.isoformat() if hasattr(r.last_timestamp, "isoformat") else str(r.last_timestamp),
            "message_count": r.message_count,
        }
        for r in rows
    ]


def retrieve_rag_context(
    project_key: str,
    user_query: str,
    top_k: int = 5
) -> list[dict]:
    """
    Ruft alle relevanten Chunks aus ChromaDB ab:
    - Document-Chunks
    - Projekt-Daten (Beschreibung, Rollen, Kontexte, Tasks)
    
    Alles kommt aus ChromaDB - einheitliche Source of Truth.
    
    Args:
        project_key: Project identifier
        user_query: User's query to find relevant context for
        top_k: Number of top chunks to return (default 5)
    
    Returns:
        List of dicts: [{"chunk_text": "...", "similarity": 0.8, "source": "..."}, ...]
    """
    try:
        from .m08_llm import embed_text
        from .m07_chroma import get_or_create_project_collection, query_collection
        
        session = get_session()
        try:
            proj = session.exec(select(Project).where(Project.key == project_key)).first()
            if not proj:
                return []
        finally:
            session.close()
        
        query_embedding = embed_text(user_query)
        if not query_embedding:
            return []
        
        collection = get_or_create_project_collection(proj.id, "text-embedding-3-small")
        scored_chunks = query_collection(collection, query_embedding, top_k)
        
        return scored_chunks
    
    except Exception as e:
        return []


def format_rag_context_prompt(chunks: list[dict]) -> str:
    """
    Formats retrieved chunks from ChromaDB into system prompt context.
    
    Args:
        chunks: List of chunks from retrieve_rag_context()
    
    Returns:
        Formatted string for inclusion in system prompt
    """
    if not chunks:
        return ""
    
    context_parts = ["## 📚 Projekt-Kontext (Dokumente, Rollen, Kontexte, Aufgaben):\n"]
    
    for i, chunk in enumerate(chunks, 1):
        similarity = chunk.get('similarity', 0)
        context_parts.append(f"\n### Quelle {i} (Relevanz: {similarity:.0%})")
        
        chunk_text = chunk.get('chunk_text', '')
        if len(chunk_text) > 500:
            context_parts.append(f"```\n{chunk_text[:500]}...\n```")
        else:
            context_parts.append(f"```\n{chunk_text}\n```")
    
    context_parts.append("\n---\n")
    return "\n".join(context_parts)


def format_rag_sources_for_display(results: dict | list) -> str:
    """
    Formatiert RAG-Quellen für Debug-Info-Anzeige im Chat.
    Unterstützt sowohl alte Liste von Chunks als auch neues Dict-Format.
    """
    if not results:
        return ""
    
    # Fall 1: Neues Dict-Format (aus retrieve_relevant_chunks)
    if isinstance(results, dict):
        parts = []
        # Zähle Treffer pro Kategorie
        if results.get("roles"): parts.append(f"{len(results['roles'])} Rollen")
        if results.get("contexts"): parts.append(f"{len(results['contexts'])} Kontexte")
        if results.get("projects"): parts.append(f"{len(results['projects'])} Projekte")
        if results.get("tasks"): parts.append(f"{len(results['tasks'])} Tasks")
        if results.get("decisions"): parts.append(f"{len(results['decisions'])} Entscheidungen")
        if results.get("documents"): parts.append(f"{len(results['documents'])} Dokumente")
        
        return " | ".join(parts) if parts else "Keine relevanten Quellen gefunden"

    # Fall 2: Altes Listen-Format (Fallback)
    sources = []
    for i, chunk in enumerate(results, 1):
        if isinstance(chunk, dict):
            source = chunk.get('source', 'Unbekannt')
            score = chunk.get('score', 0)
            sources.append(f"{i}. {source} ({int(score*100)}%)")
        else:
            sources.append(f"{i}. {str(chunk)[:20]}...")
            
    return " | ".join(sources)


def save_rag_feedback(chat_message_id: int, document_id: int, helpful: bool, comment: str = "") -> bool:
    """Speichert Feedback zu einer RAG-Quelle."""
    from .m03_db import RAGFeedback
    try:
        with get_session() as ses:
            feedback = RAGFeedback(
                chat_message_id=chat_message_id,
                document_id=document_id,
                helpful=helpful,
                comment=comment
            )
            ses.add(feedback)
            ses.commit()
        return True
    except Exception:
        return False

def get_source_feedback_stats(document_id: int) -> dict:
    """Gibt Feedback-Statistiken für ein Dokument."""
    from .m03_db import RAGFeedback
    with get_session() as ses:
        total = ses.exec(
            select(func.count(RAGFeedback.id)).where(RAGFeedback.document_id == document_id)
        ).first() or 0
        helpful = ses.exec(
            select(func.count(RAGFeedback.id)).where(
                (RAGFeedback.document_id == document_id) & (RAGFeedback.helpful == True)
            )
        ).first() or 0
    
    return {
        "total": total,
        "helpful": helpful,
        "helpful_rate": helpful / total if total > 0 else 0
    }


def build_project_map(project_key: str, query: str | None = None) -> str:
    """
    Erstellt eine strukturierte Übersicht aller Projekt-Ressourcen (Landkarte).
    Diese wird dem KI-Prompt vorangestellt, damit die KI die Projekt-Struktur kennt
    und nicht halluziniert, wenn RAG-Suche nichts findet.
    
    Args:
        project_key: Projekt-Schlüssel
        query: Optional, um Rollen nach Relevanz zu filtern
    
    Returns:
        Formatted Markdown-String mit Projekt-Struktur
    """
    try:
        with get_session() as ses:
            proj = ses.exec(select(Project).where(Project.key == project_key)).first()
            if not proj:
                return ""
            
            parts = [f"## 🗺️ PROJEKT-STRUKTUR: {proj.title}\n"]
            
            task_keys = []
            role_keys = []
            context_keys = []
            
            if proj.task_keys:
                try:
                    task_keys = json.loads(proj.task_keys) if isinstance(proj.task_keys, str) else proj.task_keys
                    if not isinstance(task_keys, list):
                        task_keys = []
                except:
                    task_keys = []
            
            if proj.role_keys:
                try:
                    role_keys = json.loads(proj.role_keys) if isinstance(proj.role_keys, str) else proj.role_keys
                    if not isinstance(role_keys, list):
                        role_keys = []
                except:
                    role_keys = []
            
            if proj.context_keys:
                try:
                    context_keys = json.loads(proj.context_keys) if isinstance(proj.context_keys, str) else proj.context_keys
                    if not isinstance(context_keys, list):
                        context_keys = []
                except:
                    context_keys = []
            
            if task_keys:
                parts.append(f"\n### 📋 Aufgaben ({len(task_keys)}):")
                for task_key in task_keys[:50]:
                    task = ses.exec(select(Task).where(Task.key == task_key)).first()
                    if task and not task.is_deleted:
                        parts.append(f"- **{task.title}**")
            else:
                parts.append("\n### 📋 Aufgaben: (keine verknüpft)")
            
            if query:
                # Query-aware Filtering
                from .m09_rag import filter_roles_by_query
                relevant_roles = filter_roles_by_query(query, project_key, limit=5)
                if relevant_roles:
                    parts.append(f"\n### 👤 Relevante Rollen (zu Query):")
                    for role in relevant_roles:
                        sim = role.get('similarity', 0)
                        parts.append(f"- **{role['title']}** ({sim:.0%})")
                        if role.get('description'):
                            parts.append(f"  > {role['description']}")
                else:
                    parts.append("\n### 👤 Relevante Rollen: (keine gefunden)")
            else:
                # Standard: alle verknüpften Rollen zeigen
                if role_keys:
                    parts.append(f"\n### 👤 Rollen ({len(role_keys)}):")
                    for role_key in role_keys[:20]:
                        role = ses.exec(select(Role).where(Role.key == role_key)).first()
                        if role and not role.is_deleted:
                            parts.append(f"- **{role.title}**")
                else:
                    parts.append("\n### 👤 Rollen: (keine verknüpft)")
            
            if context_keys:
                parts.append(f"\n### 📚 Kontexte ({len(context_keys)}):")
                for ctx_key in context_keys[:20]:
                    ctx = ses.exec(select(Context).where(Context.key == ctx_key)).first()
                    if ctx and not ctx.is_deleted:
                        parts.append(f"- **{ctx.title}**")
            else:
                parts.append("\n### 📚 Kontexte: (keine verknüpft)")
            
            docs = ses.exec(
                select(Document)
                .where(Document.is_deleted == False)
            ).all()
            
            if docs:
                parts.append(f"\n### 📄 Dokumente ({len(docs)}):")
                for doc in docs[:20]:
                    doc_name = doc.filename or f"Dokument {doc.id}"
                    parts.append(f"- {doc_name}")
            else:
                parts.append("\n### 📄 Dokumente: (keine vorhanden)")
            
            parts.append("\n---\n")
            return "\n".join(parts)
    
    except Exception:
        return ""
