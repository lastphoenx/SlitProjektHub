import streamlit as st
import uuid
import pandas as pd
from datetime import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m01_config import get_settings
from src.m03_db import init_db
from src.m06_ui import render_global_llm_settings
from src.m07_projects import list_projects_df, load_project, get_project_structure
from src.m08_llm import providers_available, have_key, try_models_with_messages, get_available_models
from src.m09_rag import retrieve_relevant_chunks_hybrid, build_rag_context_from_search, format_chunk_preview, deduplicate_results, filter_roles_by_query
from src.m10_chat import save_message, load_history, update_message_metadata, delete_history, purge_history, delete_message, find_latest_session_for_project, get_all_sessions_for_provider, format_rag_sources_for_display, save_rag_feedback, build_project_map

S = get_settings()
init_db()

st.set_page_config(page_title="AI Chat mit Projekt-Kontext", page_icon="💬", layout="wide")

with st.sidebar:
    render_global_llm_settings()
    st.markdown("---")

st.title("💬 AI Chat mit Projekt-Kontext")
st.markdown("**Strukturierte Konversation mit KI im Projekt-Rahmen**")

MESSAGE_TYPES = {
    "": "— Kein Typ —",
    "idea": "💡 Idee",
    "decision": "✅ Entscheidung",
    "todo": "📌 ToDo",
    "assumption": "❓ Annahme",
    "info": "ℹ️ Info / Fakt"
}

MESSAGE_STATUSES = {
    "ungeprüft": "⚪ Ungeprüft",
    "bestätigt": "✓ Bestätigt",
    "falsch": "✗ Falsch",
    "irrelevant": "⊘ Irrelevant"
}

st.session_state.setdefault("chat_project_key", None)
st.session_state.setdefault("chat_provider", None)
st.session_state.setdefault("chat_messages", [])
st.session_state.setdefault("project_sessions", {})
st.session_state.setdefault("show_deleted_messages", False)

try:
    query_params = dict(st.query_params)
    selected_project_param = query_params.get("project_key")
    chat_session_id_param = query_params.get("session_id")
    
    if selected_project_param and chat_session_id_param:
        st.session_state["chat_project_key"] = selected_project_param
        st.session_state["chat_session_id"] = chat_session_id_param
    else:
        if "chat_session_id" not in st.session_state or st.session_state.get("chat_session_id") is None:
            st.session_state["chat_session_id"] = str(uuid.uuid4())
except:
    if "chat_session_id" not in st.session_state or st.session_state.get("chat_session_id") is None:
        st.session_state["chat_session_id"] = str(uuid.uuid4())

try:
    projects_df = list_projects_df(include_deleted=False)
    has_projects = projects_df is not None and not projects_df.empty
except:
    has_projects = False

if not has_projects:
    st.warning("⚠️ **Keine Projekte vorhanden**")
    st.info("Bitte erstellen Sie zuerst ein Projekt in der **LabForms** Seite (Menü 'Projekte'), um einen Chat zu starten.")
    selected_project = None
    selected_provider = None
else:
    st.markdown("### 🗂️ Projekt auswählen")
    try:
        project_options = {}
        for _, row in projects_df.iterrows():
            key = row['Key']
            title = row['Titel']
            proj_type = row.get('Typ', '')
            label = f"{title}"
            if proj_type:
                label += f" [{proj_type}]"
            project_options[key] = label
        
        current_project = st.session_state.get("chat_project_key") or st.session_state.get("chat_project_select")
        index = 0
        if current_project and current_project in list(project_options.keys()):
            index = list(project_options.keys()).index(current_project)
        
        selected_project = st.selectbox(
            "Projekt",
            options=list(project_options.keys()),
            format_func=lambda x: project_options.get(x, x),
            key="chat_project_select",
            index=index
        )
        if selected_project:
            st.session_state["chat_project_key"] = selected_project
    except Exception as e:
        st.error(f"❌ Fehler beim Laden der Projekte: {str(e)}")
        selected_project = None
    
    selected_provider = st.session_state.get("global_llm_provider")
    
    if selected_provider and selected_provider != "none":
        model = st.session_state.get("global_llm_model", "—")
        temp = st.session_state.get("global_llm_temperature", "—")
        rag_top_k = st.session_state.get("global_llm_rag_top_k", 5)
        chunk_size = st.session_state.get("global_rag_chunk_size", 1000)
        threshold = st.session_state.get("global_rag_similarity_threshold", 0.5)
        st.info(f"🤖 **KI-Einstellungen**: {selected_provider} → {model} (T={temp}) | RAG: {rag_top_k} Chunks (Size: {chunk_size}, Threshold: {threshold})")
    
    if selected_project and selected_provider:
        project_sessions = st.session_state.get("project_sessions", {})
        session_key = f"{selected_provider}:{selected_project}"
        if session_key not in project_sessions or project_sessions[session_key] is None:
            latest_session = find_latest_session_for_project(selected_provider, selected_project)
            if latest_session:
                project_sessions[session_key] = latest_session
            else:
                project_sessions[session_key] = str(uuid.uuid4())
            st.session_state["project_sessions"] = project_sessions
        
        session_id = project_sessions[session_key]
        st.session_state["chat_session_id"] = session_id
        st.query_params.update({"project_key": selected_project, "session_id": session_id})

if selected_project and selected_provider:
    try:
        proj_obj, proj_body = load_project(selected_project)
        
        if proj_obj:
            st.markdown("---")
            st.markdown(f"**📋 Projekt Brief:**")
            with st.expander("Projekt-Details anzeigen", expanded=False):
                st.markdown(f"**Titel:** {proj_obj.title}")
                st.markdown(f"**Typ:** {proj_obj.type or '—'}")
                st.markdown(f"**Beschreibung:** {proj_obj.description or '—'}")
                st.markdown("---")
                st.markdown("**Auftrag/Brief:**")
                st.markdown(proj_body or "*Kein Brief eingegeben*")
            
            st.markdown("---")
            st.markdown("### 🗂️ Chat-History verwalten")
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                if st.button("👁️ Verstecken", width="stretch", help="Chat verstecken, Daten bleiben"):
                    count = delete_history(selected_provider, st.session_state["chat_session_id"])
                    st.success(f"✅ {count} Nachrichten versteckt")
                    st.rerun()
            
            with col2:
                toggle_label = "👀 Versteckte ausblenden" if st.session_state.show_deleted_messages else "👁️ Versteckte einblenden"
                if st.button(toggle_label, width="stretch"):
                    st.session_state.show_deleted_messages = not st.session_state.show_deleted_messages
                    st.rerun()
            
            with col3:
                if st.button("🗑️ Endgültig löschen", width="stretch", help="Aus DB entfernen (Bestätigung erforderlich)"):
                    if st.session_state.get("confirm_purge"):
                        count = purge_history(selected_provider, st.session_state["chat_session_id"])
                        st.session_state.confirm_purge = False
                        st.success(f"✅ {count} Nachrichten endgültig gelöscht")
                        st.rerun()
                    else:
                        st.warning("⚠️ Bestätigung erforderlich - nochmal klicken!")
                        st.session_state.confirm_purge = True
            
            st.markdown("### 💬 Chat")
            
            history = load_history(selected_provider, st.session_state["chat_session_id"], include_deleted=st.session_state.show_deleted_messages)
            
            with st.expander("🔧 Debug-Info", expanded=False):
                st.caption(f"Provider: {selected_provider}")
                st.caption(f"Modell: {st.session_state.get('global_llm_model', 'N/A')}")
                st.caption(f"Temperatur: {st.session_state.get('global_llm_temperature', 0.7)}")
                st.caption(f"Projekt: {selected_project}")
                st.caption(f"Session-ID: {st.session_state['chat_session_id']}")
                st.caption(f"Versteckte anzeigen: {st.session_state.show_deleted_messages}")
                st.caption(f"Geladene Nachrichten: {len(history)}")
                
                st.divider()
                st.caption("**RAG-Einstellungen:**")
                rag_status = "✅ Aktiv" if st.session_state.get("global_rag_enabled", True) else "❌ Deaktiviert"
                st.caption(f"RAG-Status: {rag_status}")
                st.caption(f"RAG-Kontexte (Top-K): {st.session_state.get('global_llm_rag_top_k', 5)}")
                st.caption(f"Chunk-Größe: {st.session_state.get('global_rag_chunk_size', 1000)} Zeichen")
                st.caption(f"Similarity-Threshold: {st.session_state.get('global_rag_similarity_threshold', 0.5)}")
                
                st.divider()
                st.caption("**Alle Sessions für diesen Provider in DB:**")
                try:
                    all_sessions = get_all_sessions_for_provider(selected_provider)
                    if all_sessions:
                        for sess in all_sessions:
                            is_match = "✓" if sess["session_id"] == st.session_state['chat_session_id'] else " "
                            st.caption(f"{is_match} Session: {sess['session_id'][:16]}... | Projekt: {sess['project_key']} | Nachrichten: {sess['message_count']}")
                    else:
                        st.caption("Keine Sessions gefunden")
                except Exception as e:
                    st.caption(f"Fehler beim Laden: {str(e)}")
            
            if not history:
                if st.session_state.show_deleted_messages:
                    st.info("ℹ️ Keine Nachrichten (auch keine versteckten)")
                else:
                    st.info("ℹ️ Noch keine sichtbaren Nachrichten. Schreiben Sie eine neue Nachricht oder blenden Sie versteckte ein.")
            
            for msg in history:
                is_deleted = msg.get("is_deleted", False)
                role_label = "👤 Sie" if msg["role"] == "user" else "🤖 KI"
                msg_type_label = MESSAGE_TYPES.get(msg.get("message_type", ""), "")
                msg_status_label = MESSAGE_STATUSES.get(msg.get("message_status", "ungeprüft"), "")
                
                with st.container(border=True):
                    col_role, col_meta = st.columns([2, 3])
                    with col_role:
                        deleted_badge = " ⊘ versteckt" if is_deleted else ""
                        st.markdown(f"**{role_label}**{deleted_badge}")
                        if msg_type_label:
                            st.caption(msg_type_label)
                    with col_meta:
                        if msg_status_label:
                            st.caption(msg_status_label)
                        meta_parts = [f"🕐 {msg['timestamp']}"]
                        if msg.get("model_name"):
                            meta_parts.append(f"🧠 {msg['model_name']}")
                        if msg.get("model_temperature") is not None:
                            meta_parts.append(f"🌡️ {msg['model_temperature']}")
                        st.caption(" • ".join(meta_parts))
                        
                        # RAG-Info (sofern vorhanden in session_state)
                        rag_info_parts = []
                        if st.session_state.get("global_rag_enabled"):
                            rag_info_parts.append(f"📖 RAG: ✅")
                            rag_info_parts.append(f"Top-K: {st.session_state.get('global_llm_rag_top_k', 5)}")
                            rag_info_parts.append(f"Chunk: {st.session_state.get('global_rag_chunk_size', 1000)}")
                            rag_info_parts.append(f"Threshold: {st.session_state.get('global_rag_similarity_threshold', 0.5)}")
                        else:
                            rag_info_parts.append(f"📖 RAG: ❌")
                        if rag_info_parts:
                            st.caption(" • ".join(rag_info_parts))
                    
                    content_style = "color: gray;" if is_deleted else ""
                    st.markdown(f"<div style='{content_style}'>{msg['content']}</div>", unsafe_allow_html=True)
                    
                    col_type, col_status, col_delete = st.columns([1.5, 1.5, 0.5])
                    with col_type:
                        new_type = st.selectbox(
                            "Typ",
                            options=list(MESSAGE_TYPES.keys()),
                            format_func=lambda x: MESSAGE_TYPES.get(x, x),
                            index=list(MESSAGE_TYPES.keys()).index(msg.get("message_type") or ""),
                            key=f"msg_type_{msg['id']}",
                            label_visibility="collapsed",
                            disabled=is_deleted
                        )
                    with col_status:
                        new_status = st.selectbox(
                            "Status",
                            options=list(MESSAGE_STATUSES.keys()),
                            format_func=lambda x: MESSAGE_STATUSES.get(x, x),
                            index=list(MESSAGE_STATUSES.keys()).index(msg.get("message_status", "ungeprüft")),
                            key=f"msg_status_{msg['id']}",
                            label_visibility="collapsed",
                            disabled=is_deleted
                        )
                    with col_delete:
                        if st.button("🗑️", key=f"delete_{msg['id']}", help="Nachricht endgültig löschen"):
                            delete_message(msg['id'])
                            st.success("✅ Gelöscht")
                            st.rerun()
                    
                    if not is_deleted and (new_type != msg.get("message_type") or new_status != msg.get("message_status")):
                        if st.button("💾 Speichern", key=f"save_{msg['id']}", type="secondary"):
                            update_message_metadata(
                                msg['id'],
                                message_type=new_type if new_type else None,
                                message_status=new_status
                            )
                            st.success("✅ Gespeichert")
                            st.rerun()
                    
                    # Feedback UI für existierende Nachrichten
                    # Zeige Feedback-Buttons nur für KI-Antworten (assistant)
                    if msg["role"] == "assistant" and not is_deleted:
                        sources = msg.get("rag_sources")
                        if sources:
                            with st.expander("👍 Quellen-Feedback", expanded=False):
                                st.markdown("**📚 Verwendete Dokumente:**")
                                for idx, doc in enumerate(sources):
                                    col1, col2 = st.columns([3, 1])
                                    with col1:
                                        preview = format_chunk_preview(doc.get("text", ""), max_length=200)
                                        st.markdown(f"**{doc.get('filename')}** ({doc.get('similarity', 0):.0%})\n\n{preview}")
                                    with col2:
                                        if st.button("👍", key=f"feedback_up_{msg['id']}_{doc.get('document_id')}_{idx}"):
                                            save_rag_feedback(msg['id'], doc.get('document_id'), True)
                                            st.toast("✅ Positives Feedback gespeichert!")
                                        if st.button("👎", key=f"feedback_down_{msg['id']}_{doc.get('document_id')}_{idx}"):
                                            save_rag_feedback(msg['id'], doc.get('document_id'), False)
                                            st.toast("❌ Negatives Feedback gespeichert!")
            
            st.markdown("---")
            
            st.markdown("### ✍️ Neue Nachricht")
            with st.form(key="chat_form", clear_on_submit=False):
                user_input = st.text_area(
                    "Ihre Nachricht",
                    height=100,
                    placeholder="Geben Sie Ihre Frage oder Anweisung ein...",
                    key="chat_input_field",
                    value=st.session_state.get("pending_input", "")
                )
                
                col1, col2 = st.columns([3, 1])
                with col2:
                    send_button = st.form_submit_button("📤 Absenden", type="primary", use_container_width=True)
            
            if send_button and user_input:
                # Speichere Input temporär
                st.session_state["pending_input"] = user_input
                
                with st.spinner("🤖 KI antwortet..."):
                    save_message(
                        provider=selected_provider,
                        session_id=st.session_state["chat_session_id"],
                        role="user",
                        content=user_input,
                        project_key=selected_project,
                        message_type=None,
                        message_status="ungeprüft",
                        model_name=st.session_state.get("global_llm_model"),
                        model_temperature=st.session_state.get("global_llm_temperature")
                    )
                    
                    try:
                        # Erweiterte Variante mit Query-Filtering
                        project_map_enhanced = build_project_map(selected_project, query=user_input)
                        
                        rag_enabled = st.session_state.get("global_rag_enabled", True)
                        rag_results = {}
                        rag_section_text = ""

                        if rag_enabled:
                            rag_top_k = st.session_state.get("global_llm_rag_top_k", 5)
                            rag_threshold = st.session_state.get("global_rag_similarity_threshold", 0.5)
                            rag_results = retrieve_relevant_chunks_hybrid(user_input, project_key=selected_project, limit=rag_top_k, threshold=rag_threshold)
                            rag_results = deduplicate_results(rag_results)
                            rag_section_text = build_rag_context_from_search(rag_results)
                        
                        rag_section = ""
                        if rag_section_text:
                            rag_section = f"\n\n{rag_section_text}"
                        
                        system_prompt = f"""Du bist ein erfahrener Projekt-Assistent und technischer Berater für ein Projekt namens "{proj_obj.title}".

PROJEKT-STRUKTUR (Landkarte):
{project_map_enhanced}

PROJEKT-BRIEF:
{proj_body}{rag_section}

DEINE AUFGABE:
1. Analysiere den Projekt-Brief und verstehe die SPEZIFISCHEN Anforderungen, Ziele und Constraints
2. Antworte IMMER mit Bezug zu den im Brief dokumentierten Zielen, Anforderungen und Principles
3. Erkläre deine Empfehlungen und Entscheidungen basierend auf diesem speziellen Kontext
4. Vermeide generische "Template-Antworten" - sei konkret und projekt-spezifisch
5. Beachte den bisherigen Chatverlauf und baue darauf auf
6. Nutze die zusätzlichen Kontexte (Rollen, Tasks, Chat-Historie) um kontextbewusster zu antworten

LEITLINIEN FÜR DEINE ANTWORTEN:
- Beziehe dich auf dokumentierte Anforderungen und Ziele des Projekts
- Berücksichtige Architektur-Prinzipien und langfristige Wartbarkeit
- Erkläre Trade-offs und Auswahlkriterien, nicht nur Empfehlungen
- Bei technischen Entscheidungen: Begründe, warum eine Option zu diesem Projekt passt
- Wenn der Brief Constraints oder spezifische Anforderungen nennt: Diese sind bindend für deine Empfehlungen

Antworte prägnant, strukturiert und mit direktem Bezug zu den Projekt-Anforderungen."""
                        
                        chat_history = load_history(selected_provider, st.session_state["chat_session_id"])
                        messages = [
                            {"role": msg["role"], "content": msg["content"]}
                            for msg in chat_history
                        ]
                        messages.append({"role": "user", "content": user_input})
                        
                        response = try_models_with_messages(
                            provider=selected_provider,
                            system=system_prompt,
                            messages=messages,
                            max_tokens=2000,
                            temperature=st.session_state.get("global_llm_temperature", 0.7),
                            model=st.session_state.get("global_llm_model")
                        )
                        
                        if response:
                            saved_msg = save_message(
                                provider=selected_provider,
                                session_id=st.session_state["chat_session_id"],
                                role="assistant",
                                content=response,
                                project_key=selected_project,
                                message_type=None,
                                message_status="ungeprüft",
                                model_name=st.session_state.get("global_llm_model"),
                                model_temperature=st.session_state.get("global_llm_temperature"),
                                rag_sources=rag_results.get("documents", [])[:3] if rag_results else None
                            )
                            
                            st.success("✅ Nachricht gespeichert")
                            
                            if rag_results:
                                rag_sources = format_rag_sources_for_display(rag_results)
                                st.caption(f"_📚 RAG-Quellen: {rag_sources}_")
                            
                            with st.expander("📚 RAG-Chunk-Preview"):
                                if rag_results and rag_results.get("documents"):
                                    st.markdown("### 👍 Feedback zu Quellen:")
                                    for idx, doc in enumerate(rag_results["documents"][:3]):
                                        col1, col2 = st.columns([3, 1])
                                        with col1:
                                            preview = format_chunk_preview(doc.get("text", ""), max_length=300)
                                            st.markdown(f"**{doc.get('filename')}** ({doc.get('similarity', 0):.0%})\n\n{preview}")
                                        with col2:
                                            if st.button("👍", key=f"up_{saved_msg.id}_{doc['document_id']}_{idx}"):
                                                save_rag_feedback(saved_msg.id, doc['document_id'], True)
                                                st.toast("Positives Feedback gespeichert!")
                                            if st.button("👎", key=f"down_{saved_msg.id}_{doc['document_id']}_{idx}"):
                                                save_rag_feedback(saved_msg.id, doc['document_id'], False)
                                                st.toast("Negatives Feedback gespeichert!")
                                else:
                                    st.info("Keine Dokument-Chunks gefunden")
                            
                            # Leere Input erst nach erfolgreicher Verarbeitung
                            st.session_state["pending_input"] = ""
                            st.rerun()
                        else:
                            st.error("❌ KI konnte keine Antwort generieren")
                    except Exception as e:
                        st.error(f"❌ Fehler bei der KI-Antwort: {str(e)}")
    
    except Exception as e:
        st.error(f"❌ Fehler: {str(e)}")
        import traceback
        with st.expander("Fehler-Details"):
            st.code(traceback.format_exc())

else:
    st.info("📌 Bitte wählen Sie ein Projekt und einen KI-Provider aus, um zu starten.")
