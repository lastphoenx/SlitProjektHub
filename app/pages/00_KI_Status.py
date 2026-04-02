# app/pages/00_KI_Status.py
from __future__ import annotations
import streamlit as st
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import uuid
from src.m06_ui import render_global_llm_settings
from src.m08_llm import providers_available, test_provider_connection, test_provider_prompt, chat_provider_messages, get_available_models
from src.m10_chat import save_message, load_history, delete_history, delete_all_history, list_sessions

st.set_page_config(page_title="KI-Status", page_icon="🚦", layout="wide")

with st.sidebar:
    render_global_llm_settings()
    st.markdown("---")

st.markdown("""
<style>
.status-box { padding: 1rem; border-radius: 10px; margin: 0.5rem 0; }
.status-green { background: #d1fae5; border-left: 4px solid #10b981; }
.status-red { background: #fee2e2; border-left: 4px solid #ef4444; }

/* Resizing aktivieren + sinnvolle Defaults */
.stTextArea textarea {
  resize: vertical !important;
  overflow: auto !important;
  min-height: 200px;
  max-height: 80vh;
  font-family: ui-monospace, monospace;
  font-size: 0.9rem;
}

/* Individuelle Größen für Test-Antworten */
textarea[aria-label*="Antwort von OPENAI"] { min-height: 400px; }
textarea[aria-label*="Antwort von ANTHROPIC"] { min-height: 450px; }
textarea[aria-label*="Antwort von MISTRAL"] { min-height: 400px; }
</style>
""", unsafe_allow_html=True)

st.title("🚦 KI-Provider: Status & Test")

# ============ BEREICH 1: AMPEL-STATUS + PARALLEL-TEST ============
st.header("1️⃣ Verbindungs-Status (Ampel)")

# State für Test-Ergebnisse
st.session_state.setdefault("ki_test_results", {})
st.session_state.setdefault("ki_test_timestamp", None)

# Ampel-Status für alle drei Provider
cols = st.columns(3)
providers_list = ["openai", "anthropic", "mistral"]
for i, prov in enumerate(providers_list):
    with cols[i]:
        ok, msg = test_provider_connection(prov)
        status_class = "status-green" if ok else "status-red"
        icon = "🟢" if ok else "🔴"
        st.markdown(
            f'<div class="status-box {status_class}">'
            f'<strong>{icon} {prov.upper()}</strong><br/>'
            f'<small>{msg}</small>'
            f'</div>',
            unsafe_allow_html=True
        )

st.markdown("---")
st.subheader("Parallel-Test: Alle Provider testen")

# Standard-Testprompt (editierbar)
default_prompt = (
    "Bist du bereit mit mir eine App zu bauen, bei welcher ich die KI einbinde. "
    "Ziel ist es bessere chats/prompts zu haben durch kontext. "
    "Bist du fit das mit python, streamlit in VSC zu machen?"
)
test_prompt = st.text_area(
    "Testprompt (editierbar)",
    value=st.session_state.get("ki_test_prompt", default_prompt),
    height=120,
    key="ki_test_prompt"
)

# Checkboxen für Provider-Auswahl
st.markdown("**Provider auswählen:**")
c1, c2, c3 = st.columns(3)
with c1:
    test_openai = st.checkbox("OpenAI", value=True, key="test_openai")
with c2:
    test_anthropic = st.checkbox("Claude (Anthropic)", value=True, key="test_anthropic")
with c3:
    test_mistral = st.checkbox("Mistral", value=True, key="test_mistral")

if st.button("🚀 Alle ausgewählten Provider testen", type="primary"):
    st.session_state["ki_test_results"] = {}
    st.session_state["ki_test_timestamp"] = datetime.now().strftime("%H:%M:%S")
    
    selected = []
    if test_openai: selected.append("openai")
    if test_anthropic: selected.append("anthropic")
    if test_mistral: selected.append("mistral")
    
    if not selected:
        st.warning("Bitte mindestens einen Provider auswählen.")
    else:
        with st.spinner("Teste ausgewählte Provider parallel..."):
            def run_test(prov: str):
                t0 = time.time()
                try:
                    answer = test_provider_prompt(prov, test_prompt)
                    dt = time.time() - t0
                    return prov, True, answer, None, dt
                except Exception as ex:
                    dt = time.time() - t0
                    return prov, False, None, str(ex), dt
            
            # ThreadPool mit Guardrail (max 3 parallel)
            with ThreadPoolExecutor(max_workers=min(len(selected), 3)) as pool:
                futures = [pool.submit(run_test, p) for p in selected]
                for fut in as_completed(futures):
                    prov, ok, ans, err, dt = fut.result()
                    st.session_state["ki_test_results"][prov] = {
                        "success": ok,
                        "answer": ans,
                        "error": err,
                        "latency_ms": int(dt * 1000)
                    }
        st.success(f"Test abgeschlossen um {st.session_state['ki_test_timestamp']}")
        st.rerun()

# Antworten anzeigen (wenn vorhanden)
if st.session_state.get("ki_test_results"):
    st.markdown("---")
    st.subheader(f"📬 Antworten (Stand: {st.session_state.get('ki_test_timestamp', '—')})")
    
    for idx, prov in enumerate(["openai", "anthropic", "mistral"]):
        if prov not in st.session_state["ki_test_results"]:
            continue
        
        result = st.session_state["ki_test_results"][prov]
        # Provider-Labels schöner formatieren
        prov_labels = {"openai": "ChatGPT (OpenAI)", "anthropic": "Claude (Anthropic)", "mistral": "Mistral"}
        label = prov_labels.get(prov, prov.upper())
        
        # Nur ersten Expander aufgeklappt lassen
        with st.expander(f"**{label}**", expanded=(idx == 0)):
            if result["success"]:
                txt = result["answer"] or ""
                st.text_area(
                    f"Antwort von {prov.upper()}",
                    value=txt,
                    height=500,
                    disabled=False,
                    key=f"answer_{prov}"
                )
                # Längen-Check + Latenz
                latency = result.get("latency_ms", "—")
                st.caption(f"⏱️ Latenz: {latency} ms • Länge: {len(txt)} Zeichen")
                if len(txt) < 500:
                    st.warning("⚠️ Antwort < 500 Zeichen – eventuell kein echter Live-Call oder zu kurze Modellantwort.")
            else:
                st.error(f"❌ Fehler: {result['error']}")
                latency = result.get("latency_ms", "—")
                st.caption(f"⏱️ Latenz: {latency} ms (Fehlerfall)")

# ============ BEREICH 2: LIVE-CHAT ============
st.markdown("---")
st.header("2️⃣ Live-Chat (einzelner Provider)")

st.markdown("Hier kannst du direkt mit einem ausgewählten Provider chatten. **Der Verlauf wird automatisch in der Datenbank gespeichert** und überlebt Server-Neustarts.")

# Session-ID generieren (einmalig pro Browser-Session)
if "chat_session_id" not in st.session_state:
    st.session_state["chat_session_id"] = str(uuid.uuid4())

session_id = st.session_state["chat_session_id"]

# State für Chat-Historien pro Provider (wird aus DB geladen)
st.session_state.setdefault("chat_histories", {})
st.session_state.setdefault("chat_histories_loaded", [])

# Provider-Labels
prov_labels_chat = {
    "openai": "ChatGPT (OpenAI)",
    "anthropic": "Claude (Anthropic)",
    "mistral": "Mistral"
}

chat_prov = st.session_state.get("global_llm_provider")
if not chat_prov or chat_prov == "none":
    st.error("⚠️ Kein KI-Provider ausgewählt. Bitte in der Sidebar einen Provider wählen.")
    chat_prov = None

chat_model = st.session_state.get("global_llm_model")
chat_temperature = st.session_state.get("global_llm_temperature", 0.7)

if chat_prov:
    st.info(f"🤖 **KI-Einstellungen**: {chat_prov} → {chat_model or '—'} (T={chat_temperature})")

    # Auto-Load Historie aus DB (nur einmal pro Provider)
    if chat_prov not in st.session_state["chat_histories_loaded"]:
        try:
            db_hist = load_history(chat_prov, session_id)
            st.session_state["chat_histories"][chat_prov] = [
                {"role": m["role"], "content": m["content"], "timestamp": m["timestamp"]}
                for m in db_hist
            ]
            st.session_state["chat_histories_loaded"].append(chat_prov)
        except Exception as ex:
            st.warning(f"Konnte Verlauf nicht laden: {ex}")

    # Chat-Prompt
    chat_prompt = st.text_area(
        "Dein Prompt",
        placeholder="z. B. Erkläre mir SQLModel in 3 Sätzen...",
        height=120,
        key="chat_prompt_input",
        help="Stelle deine Frage oder gib einen Befehl ein. Der Verlauf wird berücksichtigt."
    )

    col_send, col_clear, col_clear_all = st.columns([1, 2, 2])
    with col_send:
        send_btn = st.button("📨 Senden", type="primary", width="stretch")
    with col_clear:
        clear_btn = st.button(f"🗑️ Verlauf {chat_prov.upper()}", width="stretch")
    with col_clear_all:
        clear_all_btn = st.button("🗑️ Alle Verläufe löschen", width="stretch")

    if send_btn:
        if not chat_prompt.strip():
            st.warning("Bitte einen Prompt eingeben.")
        else:
            with st.spinner(f"Warte auf Antwort von {prov_labels_chat.get(chat_prov, chat_prov.upper())}..."):
                try:
                    hist = st.session_state["chat_histories"].setdefault(chat_prov, [])
                    
                    user_msg = save_message(chat_prov, session_id, "user", chat_prompt, model_name=chat_model, model_temperature=chat_temperature)
                    hist.append({"role": "user", "content": chat_prompt, "timestamp": user_msg.timestamp.isoformat(), "model_name": chat_model, "model_temperature": chat_temperature})
                    
                    llm_hist = [{"role": m["role"], "content": m["content"]} for m in hist]
                    answer = chat_provider_messages(chat_prov, llm_hist)
                    
                    asst_msg = save_message(chat_prov, session_id, "assistant", answer, model_name=chat_model, model_temperature=chat_temperature)
                    hist.append({"role": "assistant", "content": answer, "timestamp": asst_msg.timestamp.isoformat(), "model_name": chat_model, "model_temperature": chat_temperature})
                    
                    st.session_state["chat_prompt_input"] = ""
                    st.success("✅ Nachricht gespeichert")
                    st.rerun()
                except Exception as ex:
                    st.error(f"❌ Fehler: {str(ex)}")
                    st.rerun()

    if clear_btn:
        try:
            count = delete_history(chat_prov, session_id)
            st.session_state["chat_histories"][chat_prov] = []
            st.success(f"✅ {count} Nachrichten gelöscht ({chat_prov.upper()})")
            st.rerun()
        except Exception as ex:
            st.error(f"Fehler beim Löschen: {ex}")

    if clear_all_btn:
        try:
            count = delete_all_history(session_id)
            st.session_state["chat_histories"] = {}
            st.session_state["chat_histories_loaded"] = []
            st.success(f"✅ {count} Nachrichten gelöscht (alle Provider)")
            st.rerun()
        except Exception as ex:
            st.error(f"Fehler beim Löschen: {ex}")

    # Chat-Historie anzeigen
    hist = st.session_state["chat_histories"].get(chat_prov, [])
    if hist:
        st.markdown("---")
        st.subheader(f"💬 Chat-Verlauf: {prov_labels_chat.get(chat_prov, chat_prov.upper())}")
        
        # Pagination-Einstellungen
        st.session_state.setdefault("chat_page_size", 20)
        page_size = st.selectbox("Nachrichten pro Ansicht", [10, 20, 50, 100, "Alle"], key="chat_page_size_select")
        if page_size != "Alle":
            page_size = int(page_size)
            st.session_state["chat_page_size"] = page_size
        else:
            page_size = len(hist)
        
        # Reverse für neueste oben
        hist_reversed = list(reversed(hist))
        
        # Pagination
        total_messages = len(hist_reversed)
        max_page = max(1, (total_messages + page_size - 1) // page_size)
        st.session_state.setdefault("chat_hist_page", 1)
        page = st.session_state["chat_hist_page"]
        
        col_p1, col_p2, col_p3 = st.columns([1, 2, 1])
        with col_p1:
            if st.button("◀︎ Vorherige", disabled=(page <= 1), key="chat_prev"):
                st.session_state["chat_hist_page"] = max(1, page - 1)
                st.rerun()
        with col_p2:
            st.caption(f"Seite {page}/{max_page} • Gesamt: {total_messages} Nachrichten")
        with col_p3:
            if st.button("Nächste ▶︎", disabled=(page >= max_page), key="chat_next"):
                st.session_state["chat_hist_page"] = min(max_page, page + 1)
                st.rerun()
        
        # Nachrichten anzeigen
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_messages = hist_reversed[start_idx:end_idx]
        
        for i, msg in enumerate(page_messages):
            role_icon = "👤" if msg["role"] == "user" else "🤖"
            role_name = "Du" if msg["role"] == "user" else prov_labels_chat.get(chat_prov, chat_prov.upper())
            
            # Zeitstempel formatieren (wenn vorhanden)
            timestamp_str = ""
            if "timestamp" in msg and msg["timestamp"]:
                try:
                    ts = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                    timestamp_str = ts.strftime('%d.%m.%Y %H:%M:%S')
                except Exception:
                    timestamp_str = msg['timestamp'][:19]
            
            with st.container():
                st.markdown(f"**{role_icon} {role_name}**")
                provider_label = prov_labels_chat.get(chat_prov, chat_prov.upper())
                meta_parts = [f"📡 {provider_label}"]
                if msg.get("model_name"):
                    meta_parts.append(f"🧠 {msg['model_name']}")
                if msg.get("model_temperature") is not None:
                    meta_parts.append(f"🌡️ {msg['model_temperature']}")
                if timestamp_str:
                    meta_parts.append(f"🕐 {timestamp_str}")
                st.caption(" • ".join(meta_parts))
                
                # Lange Nachrichten mit Expander
                content = msg["content"]
                if len(content) > 300:
                    preview = content[:300] + "..."
                    st.markdown(preview)
                    with st.expander("Vollständige Nachricht anzeigen"):
                        st.text_area(
                            f"Nachricht #{total_messages - start_idx - i}",
                            value=content,
                            height=300,
                            disabled=False,
                            key=f"chat_msg_{page}_{i}"
                        )
                else:
                    st.markdown(content)
                st.markdown("---")

# Session-Übersicht (erweitert mit Einzelansichten)
st.markdown("---")
st.subheader("🗂️ Alle Chat-Sessions")

try:
    sessions = list_sessions()
    if sessions:
        st.caption(f"Gefunden: {len(sessions)} Session(s)")
        
        # Gruppierung nach Provider
        sessions_by_provider = {}
        for sess in sessions:
            prov = sess["provider"]
            if prov not in sessions_by_provider:
                sessions_by_provider[prov] = []
            sessions_by_provider[prov].append(sess)
        
        # Pro Provider einen Expander
        for prov in ["openai", "anthropic", "mistral"]:
            if prov not in sessions_by_provider:
                continue
            
            prov_sessions = sessions_by_provider[prov]
            prov_name = prov_labels_chat.get(prov, prov.upper())
            total_msgs = sum(s["message_count"] for s in prov_sessions)
            
            with st.expander(f"**{prov_name}** • {len(prov_sessions)} Session(s) • {total_msgs} Nachrichten"):
                for sess in prov_sessions:
                    is_current = sess["session_id"] == session_id
                    session_label = "🟢 Aktuelle Session" if is_current else f"Session {sess['session_id'][:8]}..."
                    last_time = sess["last_timestamp"][:19]
                    
                    st.markdown(
                        f"**{session_label}** • "
                        f"{sess['message_count']} Nachrichten • "
                        f"Letzte Aktivität: {last_time}"
                    )
                    
                    # Button: Verlauf dieser Session anzeigen
                    if st.button(
                        f"📖 {sess['message_count']} Nachrichten anzeigen",
                        key=f"view_session_{prov}_{sess['session_id'][:8]}",
                        help="Zeigt den vollständigen Verlauf dieser Session"
                    ):
                        st.session_state["view_session_provider"] = prov
                        st.session_state["view_session_id"] = sess["session_id"]
                        st.rerun()
                    
                    st.markdown("---")
    else:
        st.info("Noch keine Chat-Sessions vorhanden.")
except Exception as ex:
    st.warning(f"Konnte Sessions nicht laden: {ex}")

# Einzelne Session-Ansicht (wenn ausgewählt)
if "view_session_provider" in st.session_state and "view_session_id" in st.session_state:
    view_prov = st.session_state["view_session_provider"]
    view_sid = st.session_state["view_session_id"]
    
    st.markdown("---")
    st.subheader(f"📜 Session-Verlauf: {prov_labels_chat.get(view_prov, view_prov.upper())}")
    st.caption(f"Session-ID: {view_sid}")
    
    col_back, col_delete = st.columns([3, 1])
    with col_back:
        if st.button("◀︎ Zurück zur Übersicht", type="secondary"):
            del st.session_state["view_session_provider"]
            del st.session_state["view_session_id"]
            st.rerun()
    with col_delete:
        if st.button("🗑️ Session löschen", type="primary"):
            try:
                from src.m10_chat import delete_history
                count = delete_history(view_prov, view_sid)
                st.success(f"✅ {count} Nachrichten gelöscht")
                del st.session_state["view_session_provider"]
                del st.session_state["view_session_id"]
                if view_sid == session_id and view_prov in st.session_state.get("chat_histories", {}):
                    st.session_state["chat_histories"][view_prov] = []
                st.rerun()
            except Exception as ex:
                st.error(f"Fehler beim Löschen: {ex}")
    
    # Historie laden und anzeigen
    try:
        view_hist = load_history(view_prov, view_sid)
        
        if view_hist:
            st.caption(f"Gesamt: {len(view_hist)} Nachrichten")
            
            # Pagination für Session-Ansicht
            page_size_session = st.selectbox(
                "Nachrichten pro Seite",
                [10, 20, 50, 100, "Alle"],
                key="session_view_page_size"
            )
            if page_size_session != "Alle":
                page_size_session = int(page_size_session)
            else:
                page_size_session = len(view_hist)
            
            # Reverse für neueste oben
            view_hist_reversed = list(reversed(view_hist))
            total = len(view_hist_reversed)
            max_page_session = max(1, (total + page_size_session - 1) // page_size_session)
            
            st.session_state.setdefault("session_view_page", 1)
            page_session = st.session_state["session_view_page"]
            
            col_p1, col_p2, col_p3 = st.columns([1, 2, 1])
            with col_p1:
                if st.button("◀︎ Vorherige", disabled=(page_session <= 1), key="session_prev"):
                    st.session_state["session_view_page"] = max(1, page_session - 1)
                    st.rerun()
            with col_p2:
                st.caption(f"Seite {page_session}/{max_page_session}")
            with col_p3:
                if st.button("Nächste ▶︎", disabled=(page_session >= max_page_session), key="session_next"):
                    st.session_state["session_view_page"] = min(max_page_session, page_session + 1)
                    st.rerun()
            
            # Nachrichten anzeigen
            start = (page_session - 1) * page_size_session
            end = start + page_size_session
            page_msgs = view_hist_reversed[start:end]
            
            for i, msg in enumerate(page_msgs):
                role_icon = "👤" if msg["role"] == "user" else "🤖"
                role_name = "User" if msg["role"] == "user" else prov_labels_chat.get(view_prov, view_prov.upper())
                
                # Timestamp formatieren
                ts_str = ""
                if "timestamp" in msg and msg["timestamp"]:
                    try:
                        ts = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                        ts_str = f" • {ts.strftime('%d.%m.%Y %H:%M:%S')}"
                    except Exception:
                        ts_str = f" • {msg['timestamp'][:19]}"
                
                with st.container():
                    st.markdown(f"**{role_icon} {role_name}**{ts_str}")
                    
                    content = msg["content"]
                    if len(content) > 300:
                        preview = content[:300] + "..."
                        st.markdown(preview)
                        with st.expander("Vollständige Nachricht"):
                            st.text_area(
                                f"Nachricht #{total - start - i}",
                                value=content,
                                height=300,
                                disabled=False,
                                key=f"session_msg_{page_session}_{i}"
                            )
                    else:
                        st.markdown(content)
                    st.markdown("---")
        else:
            st.info("Keine Nachrichten in dieser Session.")
    except Exception as ex:
        st.error(f"Fehler beim Laden: {ex}")
