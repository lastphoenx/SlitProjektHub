import streamlit as st
import json
import re
import textwrap
import math
import time
import pandas as pd
from typing import Optional
from src.m03_db import get_session, Role, Document, DOCUMENT_CLASSIFICATIONS
from sqlmodel import select
from src.m06_ui import render_global_llm_settings
from src.m07_roles import list_roles_df, load_role, upsert_role, soft_delete_role, function_suggestions
from src.m07_contexts import list_contexts_df, load_context, upsert_context, soft_delete_context
from src.m07_tasks import list_tasks_df, load_task, upsert_task, delete_task
from src.m07_projects import list_projects_df, load_project, upsert_project, soft_delete_project
from src.m08_llm import providers_available, generate_role_details, generate_summary, generate_context_details, generate_project_details, generate_role_text
from src.m09_docs import ingest_document, list_documents, delete_document, get_project_documents, link_document_to_project, unlink_document_from_project

st.set_page_config(page_title="Lab Forms", page_icon="🧪", layout="wide")

# CSS für linksbündige Buttons (aus 03_Roles.py)
st.markdown(
    """
    <style>
    :root{ --btn-blue:#60a5fa; --btn-blue-border:#60a5fa; --btn-grey:#e5e7eb; --btn-grey-border:#d1d5db; }
    .stButton>button{ padding:.35rem .7rem; font-size:.9rem; border-radius:10px; text-align:left; justify-content:flex-start; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; transition: background-color .15s ease, border-color .15s ease, transform .03s ease, box-shadow .15s ease; }
    .stButton>button *{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace !important; }
    .stButton>button:hover{ filter: brightness(0.98); }
    .stButton>button:active{ transform: scale(0.98); }
    .stButton>button[kind="primary"]:not(:disabled), .stButton>button[data-testid="baseButton-primary"]:not(:disabled){ background:var(--btn-blue) !important; color:#fff !important; border:1px solid var(--btn-blue-border) !important; }
    .stButton>button[kind="primary"]:not(:disabled):hover, .stButton>button[data-testid="baseButton-primary"]:not(:disabled):hover{ background:#3b82f6 !important; border-color:#3b82f6 !important; box-shadow:0 0 0 3px rgba(59,130,246,.25) !important; }
    .stButton>button[kind="primary"]:not(:disabled):active, .stButton>button[data-testid="baseButton-primary"]:not(:disabled):active{ background:#2563eb !important; border-color:#2563eb !important; }
    .stButton>button[kind="primary"]:disabled, .stButton>button[data-testid="baseButton-primary"]:disabled{ background:var(--btn-grey) !important; color:#374151 !important; border:1px solid var(--btn-grey-border) !important; opacity:1 !important; }
    .stButton>button[kind="secondary"], .stButton>button[data-testid="baseButton-secondary"], .stButton>button:not([kind]){ background:#fff !important; color:#111827 !important; border:1px solid #e5e7eb !important; }
    .stButton>button[kind="secondary"]:hover, .stButton>button[data-testid="baseButton-secondary"]:hover, .stButton>button:not([kind]):hover{ background:#f9fafb !important; }
    .stButton>button[kind="secondary"]:active, .stButton>button[data-testid="baseButton-secondary"]:active, .stButton>button:not([kind]):active{ background:#f3f4f6 !important; }
    
    /* Spezielle Styles für Rollenliste */
    #exp_role_list [data-testid="stVerticalBlock"], #exp_role_list .element-container{ gap:0 !important; margin:0 !important; padding:0 !important; }
    #exp_role_list .stButton{ margin:0 !important; padding:0 !important; }
    #exp_role_list .stButton>button{ padding:.08rem .45rem !important; line-height:1.0 !important; width:100% !important; display:block !important; font-size:.86rem !important; white-space:pre !important; letter-spacing:0 !important; font-variant-ligatures:none !important; text-align:left !important; justify-content:flex-start !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    render_global_llm_settings()
    st.markdown("---")

# ============ SESSION STATE INIT ============
def init_session_state():
    """Initialize all session state variables"""
    defaults = {
        # Roles form state
        "lab_role_title": "",
        "lab_role_title_short": "",
        "lab_role_short": "",
        "lab_role_type": "leadership",
        "lab_role_description": "",
        "lab_role_responsibilities": "",
        "lab_role_qualifications": "",
        "lab_role_expertise": "",
        "lab_llm_provider": "openai",
        "lab_selected_role": None,
        "lab_search_query": "",
    }
    
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value

init_session_state()

# ============ UTILITY FUNCTIONS ============
def _strip_leading_markdown_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) > 1:
            text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text

def wrap_markdown_preserve(text: str, width: int = 100) -> str:
    text = _strip_leading_markdown_fence(text)
    out, in_code = [], False
    for line in (text or "").splitlines():
        s = line.rstrip("\n")
        if s.strip().startswith("```"):
            in_code = not in_code; out.append(line); continue
        if in_code or not s:
            out.append(line); continue
        m = re.match(r"^(\s*)([-*+]|[0-9]+\.)\s+(.*)$", line)
        if m:
            indent, bullet, rest = m.groups()
            out.append(textwrap.fill(rest, width=width, break_long_words=False, break_on_hyphens=False,
                                     initial_indent=indent+bullet+" ", subsequent_indent=indent+"  "))
        else:
            out.append(textwrap.fill(s, width=width, break_long_words=False, break_on_hyphens=False))
    return "\n".join(out)

def list_roles():
    """Get list of all roles from database"""
    try:
        with get_session() as ses:
            rows = ses.exec(select(Role).where(Role.is_deleted == False)).all()
        return rows
    except Exception:
        return []

def clear_role_form():
    """Clear all role form fields"""
    fields_to_clear = [
        "lab_role_title", "lab_role_title_short", "lab_role_short",
        "lab_role_type", "lab_role_description", "lab_role_responsibilities",
        "lab_role_qualifications"
    ]
    for field in fields_to_clear:
        st.session_state[field] = ""
    st.session_state["lab_selected_role"] = None

def load_role_into_form(role_key: str):
    """Load selected role into form fields"""
    try:
        role, body = load_role(role_key)
        if role:
            st.session_state["lab_role_description_input"] = getattr(role, 'description', '') or ""
            st.session_state["lab_role_title"] = role.title or ""
            st.session_state["lab_role_short"] = getattr(role, 'short_code', '') or ""
            st.session_state["lab_role_description"] = body or ""
            st.session_state["lab_role_responsibilities"] = getattr(role, 'responsibilities', '') or ""
            st.session_state["lab_role_qualifications"] = getattr(role, 'qualifications', '') or ""
            st.session_state["lab_role_expertise"] = getattr(role, 'expertise', '') or ""
            st.session_state["lab_selected_role"] = role_key
    except Exception as e:
        st.error(f"Fehler beim Laden der Rolle: {e}")

def get_filtered_roles():
    """Get roles filtered by search query"""
    try:
        roles = list_roles()
        query = st.session_state.get("lab_search_query", "").lower().strip()
        
        if not query:
            return roles
        
        filtered = []
        for role in roles:
            if (query in (role.title or "").lower() or 
                query in (role.key or "").lower() or
                query in (role.short_code or "").lower()):
                filtered.append(role)
        
        return filtered
    except Exception:
        return []

# ============ MAIN PAGE ============
st.title("🧪 Lab Forms - Modern UI Experiments")
st.markdown("**Experimentelle Formulare mit moderner Streamlit-Technologie**")

# URL-Parameter für Tab-Auswahl
try:
    query_params = dict(st.query_params)
    tab_param = query_params.get("tab", "rollen")
except:
    tab_param = "rollen"

# Tab-Mapping
tab_mapping = {
    "rollen": 0,
    "tasks": 1,
    "taskgen": 2,
    "kontexte": 3,
    "dokumente": 4,
    "projekte": 5
}

# Session State für aktiven Tab (URL-Parameter berücksichtigen)
if "lab_active_tab" not in st.session_state:
    st.session_state["lab_active_tab"] = tab_mapping.get(tab_param, 0)
else:
    # URL-Parameter beim Reload berücksichtigen
    url_tab = tab_mapping.get(tab_param, 0)
    if url_tab != st.session_state["lab_active_tab"]:
        st.session_state["lab_active_tab"] = url_tab

# ============ TABS ============
# URL-Parameter-basierte Tab-Auswahl mit Button-Navigation
tab_names = ["🎯 Rollen", "📋 Tasks", "🤖 Task-Gen", "📚 Kontexte", "📄 Dokumente", "🗂️ Projekte"]
tab_keys = ["rollen", "tasks", "taskgen", "kontexte", "dokumente", "projekte"]

# Tab-Navigation mit Buttons für bessere URL-Kontrolle
st.markdown("### 📑 Navigation")
st.markdown("---")  # Trennstrich für bessere Abgrenzung

nav_cols = st.columns(len(tab_names))

for i, (tab_name, tab_key) in enumerate(zip(tab_names, tab_keys)):
    with nav_cols[i]:
        # Aktiver Tab wird hervorgehoben
        button_type = "primary" if i == st.session_state["lab_active_tab"] else "secondary"
        # Emojis wieder aufnehmen - kompletter Tab-Name verwenden
        display_name = tab_name  # Behält Emojis: 🎯 Rollen, 📋 Tasks, 🏢 Contexts
        
        if st.button(display_name, key=f"nav_tab_{i}", type=button_type, width="stretch"):
            if i != st.session_state["lab_active_tab"]:
                st.session_state["lab_active_tab"] = i
                # URL-Parameter aktualisieren
                st.query_params.update({"tab": tab_key})
                st.rerun()

# Zusätzlicher Trennstrich nach der Navigation
st.markdown("---")

# Aktuelle Tab-Info
current_tab_name = tab_names[st.session_state["lab_active_tab"]]
current_tab_key = tab_keys[st.session_state["lab_active_tab"]]
st.markdown(f"**Aktiver Tab:** {current_tab_name} • URL: `?tab={current_tab_key}`")

# ============ TAB CONTENT ============
current_tab = st.session_state["lab_active_tab"]

# ============ PENDING UPDATES (vor allen Widgets!) ============
if st.session_state.get("lab_role_ai_update"):
    for k, v in st.session_state["lab_role_ai_update"].items():
        st.session_state[k] = v
    st.session_state["lab_role_ai_update"] = None

# ============ TAB CONTENT BASED ON CURRENT_TAB ============

# ============ TAB 0: ROLLEN (Mit Expander) ============
if current_tab == 0:
    st.subheader("🎯 Rollen - Expander Layout")
    st.markdown("**Experimenteller Layout mit zusammenklappbaren Bereichen**")
    
    # ========== HAUPTEXPANDER 1: ROLLENLISTE ==========
    with st.expander("📋 Rollenliste bestehender Rollen", expanded=True):
        
        # Session State für Rollenliste (analog zu 03_Roles.py)
        st.session_state.setdefault("exp_rn_query", "")
        st.session_state.setdefault("exp_rn_sort_by", "Rollenbezeichnung")
        st.session_state.setdefault("exp_rn_sort_dir", "aufsteigend")
        st.session_state.setdefault("exp_rn_page", 1)
        st.session_state.setdefault("exp_rn_page_size", 8)
        st.session_state.setdefault("exp_rn_selected_key", "")
        st.session_state.setdefault("exp_rn_prev_query", "")
        st.session_state.setdefault("exp_rn_prev_sort_by", st.session_state["exp_rn_sort_by"])
        st.session_state.setdefault("exp_rn_prev_sort_dir", st.session_state["exp_rn_sort_dir"])
        st.session_state.setdefault("exp_rn_prev_page_size", st.session_state["exp_rn_page_size"])
        st.session_state.setdefault("exp_rn_last_click_key", "")
        st.session_state.setdefault("exp_rn_last_click_ts", 0.0)
        
        # Utility functions (aus 03_Roles.py)
        def _normalize_spaces(s: str) -> str:
            if not s: return ""
            s = s.replace("\u00A0", " ")
            s = re.sub(r"\s+", " ", s)
            return s

        def _vis_len(s: str) -> int:
            return len(s or "")

        def record_label(short: str, title: str, short_width: int = 15, gap_after_pipe: int = 5) -> str:
            NBSP = "\u00A0"
            s_disp = _normalize_spaces((short or "").strip()) or "—"
            t_disp = (title or "").strip()
            cur_w  = _vis_len(s_disp)
            pad_len = max(0, short_width - cur_w)
            filler_after_short = NBSP * pad_len
            gap = NBSP * max(0, gap_after_pipe)
            return f"{s_disp}{filler_after_short}|{gap}{t_disp}"
        
        # Datenbank-Abfrage
        try:
            df = list_roles_df(include_deleted=False)
            if df is None:
                df = list_roles_df(include_deleted=True)
        except Exception as e:
            st.error(f"❌ Fehler beim Laden der Rollen: {str(e)}")
            df = None
        
        if df is not None and not df.empty:
            # Sub-Expander für Suchoptionen 
            with st.expander("🔍 Suche & Filter", expanded=False):
                col_search1, col_search2 = st.columns(2)
                with col_search1:
                    q = st.text_input("🔍 Suchen:", key="exp_rn_query", 
                                    placeholder="z.B. CISO oder Chief Information...")
                with col_search2:
                    # Erweiterte Suchoptionen (placeholder für künftige Features)
                    st.selectbox("🏷️ Gruppe filtern:", ["Alle", "leadership", "technical", "operational"], 
                               disabled=True, help="Coming Soon")
            
            # Sub-Expander für Anzeige-Optionen
            with st.expander("📄 Anzeige & Sortierung", expanded=False):
                col_page1, col_page2, col_page3 = st.columns(3)
                with col_page1:
                    st.selectbox("Einträge pro Seite:", [8, 12, 20, 30, 50], key="exp_rn_page_size")
                    # Reset page when page_size changes
                    if st.session_state["exp_rn_page_size"] != st.session_state["exp_rn_prev_page_size"]:
                        st.session_state["exp_rn_prev_page_size"] = st.session_state["exp_rn_page_size"]
                        st.session_state["exp_rn_page"] = 1
                        
                with col_page2:
                    sort_options = ["Rollenbezeichnung", "Kürzel"]
                    if st.session_state.get("exp_rn_sort_by") not in sort_options:
                        st.session_state["exp_rn_sort_by"] = "Rollenbezeichnung"
                    sort_by = st.selectbox("Sortieren nach:", sort_options, key="exp_rn_sort_by")
                    
                with col_page3:
                    sort_dir = st.selectbox("Richtung:", ["aufsteigend", "absteigend"], key="exp_rn_sort_dir")
            
            # Change detection für automatisches Page-Reset
            changed_q    = (q != st.session_state["exp_rn_prev_query"])
            changed_sort = (sort_by != st.session_state["exp_rn_prev_sort_by"])
            changed_dir  = (sort_dir != st.session_state["exp_rn_prev_sort_dir"])
            if changed_q or changed_sort or changed_dir:
                st.session_state["exp_rn_page"] = 1
                st.session_state["exp_rn_prev_query"] = q
                st.session_state["exp_rn_prev_sort_by"] = sort_by
                st.session_state["exp_rn_prev_sort_dir"] = sort_dir
                if changed_sort:
                    st.rerun()
            
            # Datenverarbeitung (1:1 aus 03_Roles.py)
            data = df.copy()
            data["Key"]               = data["Key"].astype(str).fillna("")
            data["Rollenbezeichnung"] = data["Rollenbezeichnung"].astype(str).fillna("")
            data["Rollenkürzel"]      = data["Rollenkürzel"].astype(str).fillna("")
            
            # Body-Mapping für Volltextsuche
            @st.cache_data(show_spinner=False)
            def _body_map(keys: list[str]):
                out = {}
                for k in keys:
                    try:
                        _, b = load_role(str(k))
                    except Exception:
                        b = ""
                    out[str(k)] = (b or "")
                return out

            bodies = _body_map(list(data["Key"]))
            beschreibung = data["Key"].map(lambda k: bodies.get(str(k), ""))
            
            # Suchfilter anwenden
            qraw  = q or ""
            qnorm = qraw.strip().lower()

            if qnorm and qnorm != "*":
                if ("*" in qnorm) or ("?" in qnorm):
                    pattern = re.escape(qnorm).replace("\\*", ".*").replace("\\?", ".")
                    key_m   = data["Key"].str.lower().str.contains(pattern, regex=True)
                    title_m = data["Rollenbezeichnung"].str.lower().str.contains(pattern, regex=True)
                    short_m = data["Rollenkürzel"].str.lower().str.contains(pattern, regex=True)
                    body_m  = beschreibung.str.lower().str.contains(pattern, regex=True)
                else:
                    key_m   = data["Key"].str.lower().str.contains(qnorm, regex=False)
                    title_m = data["Rollenbezeichnung"].str.lower().str.contains(qnorm, regex=False)
                    short_m = data["Rollenkürzel"].str.lower().str.contains(qnorm, regex=False)
                    body_m  = beschreibung.str.lower().str.contains(qnorm, regex=False)
                data = data[key_m | title_m | short_m | body_m]

            # Sortierung anwenden
            by = st.session_state["exp_rn_sort_by"]
            ascend = st.session_state["exp_rn_sort_dir"] == "aufsteigend"
            if by in data.columns:
                data = data.sort_values(by=by, ascending=ascend, kind="mergesort")

            # Paginierung
            total = len(data)
            page_size = st.session_state["exp_rn_page_size"]
            max_page = max(1, math.ceil(total / page_size))
            st.session_state["exp_rn_page"] = min(max_page, max(1, st.session_state["exp_rn_page"]))
            page = st.session_state["exp_rn_page"]

            start = (page - 1) * page_size
            end   = start + page_size
            page_df = data.iloc[start:end].reset_index(drop=True)
            
            # Pager Controls - linksbündig
            col_controls = st.columns([3, 1])  # Mehr Platz für Buttons links
            with col_controls[0]:
                col_prev, col_next, col_info = st.columns([1, 1, 2])
                with col_prev:
                    if st.button("◀ Zurück", disabled=(page <= 1), key="exp_rn_prev", type="primary", width="stretch"):
                        st.session_state["exp_rn_page"] = max(1, page - 1)
                        st.rerun()
                with col_next:
                    if st.button("Weiter ▶", disabled=(page >= max_page), key="exp_rn_next", type="primary", width="stretch"):
                        st.session_state["exp_rn_page"] = min(max_page, page + 1)
                        st.rerun()
                with col_info:
                    st.markdown(f"📄 Seite {page}/{max_page} • {total} Treffer")
            
            # Rollenliste als DataFrame
            st.markdown("---")
            st.markdown("### 📋 Rollen")
            
            if page_df.empty:
                st.info("🔍 Keine Rollen auf dieser Seite gefunden")
            else:
                # DataFrame vorbereiten - nur Key, Kürzel, Titel
                display_df = page_df[["Key", "Rollenkürzel", "Rollenbezeichnung", "Beschreibung"]].copy()
                
                st.caption(f"📊 Zeige {len(display_df)} von {total} Rollen")
                
                # DataFrame mit Auswahl
                event = st.dataframe(
                    display_df,
                    column_config={
                        "Key": None,  # Ausblenden
                        "Rollenkürzel": st.column_config.TextColumn(
                            "Kürzel",
                            width="small",
                            help="Rollen-Kürzel (z.B. CEO, CTO)"
                        ),
                        "Rollenbezeichnung": st.column_config.TextColumn(
                            "Rolle",
                            width="large",
                            help="Vollständige Rollenbezeichnung"
                        ),
                        "Beschreibung": st.column_config.TextColumn(
                            "Beschreibung",
                            width="large",
                            help="Kurzbeschreibung"
                        )
                    },
                    hide_index=True,
                    width="stretch",
                    height=350,
                    on_select="rerun",
                    selection_mode="single-row",
                    key=f"roles_df_{page}"
                )
                
                # Auswahl verarbeiten
                if event and "selection" in event and event["selection"]["rows"]:
                    selected_idx = event["selection"]["rows"][0]
                    key = display_df.iloc[selected_idx]["Key"]
                    title = display_df.iloc[selected_idx]["Rollenbezeichnung"]
                    
                    old_selected = st.session_state.get("exp_rn_selected_key", "")
                    
                    # Neue Auswahl erkannt
                    if key != old_selected:
                        st.session_state["exp_rn_selected_key"] = key
                        st.session_state["exp_preview_expanded"] = True
                        
                        # Button-Bereich für Aktionen
                        col_act1, col_act2 = st.columns(2)
                        with col_act1:
                            if st.button("👁 Vorschau", key="action_preview", type="primary", width="stretch"):
                                st.session_state["exp_preview_expanded"] = True
                                st.toast(f"✅ Rolle ausgewählt: {title}")
                                st.rerun()
                        with col_act2:
                            if st.button("✏️ Bearbeiten", key="action_edit", width="stretch"):
                                st.session_state["lab_selected_role"] = key
                                st.session_state["exp_last_loaded_role_key"] = ""
                                st.session_state["exp_form_expanded"] = True
                                st.toast(f"✏️ Rolle wird bearbeitet: {title}")
                                st.rerun()
                            
        else:
            st.info("📭 Noch keine Rollen vorhanden")
            st.session_state["exp_rn_selected_key"] = ""
    
    # ========== HAUPTEXPANDER 2: VORSCHAU ==========  
    # 🎯 MAGIC: Automatische Steuerung basierend auf Auswahl
    preview_expanded = st.session_state.get("exp_preview_expanded", False)
    selected_role_key = st.session_state.get("exp_rn_selected_key", "")
    
    # Automatisches Schließen wenn keine Auswahl
    if not selected_role_key and preview_expanded:
        st.session_state["exp_preview_expanded"] = False
        preview_expanded = False
    
    with st.expander("👁 Vorschau", expanded=preview_expanded):
        
        if selected_role_key:
            try:
                role_obj, role_body = load_role(selected_role_key)
                if role_obj:
                    # Info-Box mit Grunddaten (kompakt)
                    with st.expander("ℹ️ Rollen-Information", expanded=True):
                        col_info1, col_info2 = st.columns(2)
                        with col_info1:
                            st.markdown(f"**🎭 Titel:** {role_obj.title}")
                            # Zeige short_code als Kürzel
                            kuerzel = role_obj.short_code or 'Nicht definiert'
                            st.markdown(f"**🔖 Kürzel:** {kuerzel}")
                        with col_info2:
                            st.markdown(f"**🔑 Key:** `{role_obj.key}`")
                            # Fix: Role hat kein created_at Attribut
                            if hasattr(role_obj, 'created_at') and role_obj.created_at:
                                created_str = role_obj.created_at.strftime('%d.%m.%Y %H:%M')
                            else:
                                created_str = 'Unbekannt'
                            st.markdown(f"**📅 Erstellt:** {created_str}")
                    
                    # Volltext-Vorschau mit erweiterten Feldern
                    with st.expander("📄 Volltext-Vorschau", expanded=True):
                        # Titel und Grunddaten
                        st.markdown(f"**📝 {role_obj.title}**")
                        
                        if hasattr(role_obj, 'short_code') and role_obj.short_code:
                            st.markdown(f"**🔖 Kürzel:** {role_obj.short_code}")
                        
                        # Markdown-Beschreibung ZUERST (Prosa-Einleitung)
                        # Extrahiere nur die Prosa (vor den strukturierten Abschnitten)
                        if role_body:
                            from src.m11_role_markdown import extract_prose_intro
                            prose = extract_prose_intro(role_body)
                            if prose:
                                st.markdown("**📄 Beschreibung / Profil:**")
                                st.markdown(prose)
                                st.markdown("")  # Leerzeile
                        
                        # Strukturierte Felder (aus DB, nicht aus Markdown)
                        if hasattr(role_obj, 'responsibilities') and role_obj.responsibilities:
                            st.markdown("**� Hauptverantwortlichkeiten:**")
                            st.markdown(role_obj.responsibilities)
                            
                        if hasattr(role_obj, 'qualifications') and role_obj.qualifications:
                            st.markdown("**🎓 Qualifikationen & Anforderungen:**")
                            st.markdown(role_obj.qualifications)
                            
                        if hasattr(role_obj, 'expertise') and role_obj.expertise:
                            st.markdown("**🧠 Expertise / Spezialwissen:**")
                            st.markdown(role_obj.expertise)
                    
                    # ========== ZUGEORDNETE AUFGABEN IN VORSCHAU ==========
                    from src.m07_tasks import list_tasks_df as get_tasks
                    try:
                        all_tasks_df = get_tasks(include_metadata=True)
                        
                        if all_tasks_df is not None and not all_tasks_df.empty:
                            role_tasks = all_tasks_df[all_tasks_df["Quell-Rolle"] == selected_role_key]
                            task_count = len(role_tasks)
                            
                            with st.expander(f"📋 Zugeordnete Aufgaben ({task_count})", expanded=True):
                                if task_count > 0:
                                    st.markdown(f"**{task_count} Aufgabe(n) sind dieser Rolle zugeordnet:**")
                                    
                                    for idx, task_row in role_tasks.iterrows():
                                        col1, col2 = st.columns([3, 1])
                                        with col1:
                                            task_title = task_row['Titel']
                                            task_short = task_row.get('Kürzel', '')
                                            label = f"{task_short} - {task_title}" if task_short else task_title
                                            st.markdown(f"• **{label}**")
                                            if task_row.get('Beschreibung'):
                                                st.caption(task_row['Beschreibung'])
                                        
                                        with col2:
                                            if st.button("Bearb.", key=f"preview_edit_task_{task_row['Key']}", width="stretch"):
                                                st.session_state["lab_active_tab"] = 1
                                                st.session_state["task_mgmt_selected_key"] = task_row['Key']
                                                st.session_state["task_mgmt_edit_mode"] = True
                                                st.query_params.update({"tab": "tasks"})
                                                st.rerun()
                                    
                                    st.markdown("---")
                                    if st.button("Neue Aufgabe erstellen", key="preview_create_task", width="stretch"):
                                        st.session_state["lab_active_tab"] = 1
                                        st.session_state["task_mgmt_selected_key"] = "_NEW_"
                                        st.session_state["task_mgmt_edit_mode"] = True
                                        st.session_state["task_mgmt_preselect_role"] = selected_role_key
                                        st.query_params.update({"tab": "tasks"})
                                        st.rerun()
                                else:
                                    st.info("Noch keine Aufgaben zugeordnet")
                                    if st.button("Erste Aufgabe erstellen", key="preview_create_first_task", width="stretch"):
                                        st.session_state["lab_active_tab"] = 1
                                        st.session_state["task_mgmt_selected_key"] = "_NEW_"
                                        st.session_state["task_mgmt_edit_mode"] = True
                                        st.session_state["task_mgmt_preselect_role"] = selected_role_key
                                        st.query_params.update({"tab": "tasks"})
                                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler beim Laden der Aufgaben: {str(e)}")
                    
                    # Aktionen (1:1 aus 03_Roles.py)
                    with st.expander("⚡ Aktionen", expanded=False):
                        col_act1, col_act2, col_act3 = st.columns(3)
                        with col_act1:
                            if st.button("✏️ Bearbeiten", key="exp_preview_edit", type="primary", width="stretch"):
                                # 1:1 wie Original: Rolle zum Bearbeiten laden
                                st.session_state["lab_selected_role"] = selected_role_key
                                st.toast("✏️ Rolle zum Bearbeiten geladen")
                                st.rerun()
                        with col_act2:
                            # Download wie Original
                            try:
                                st.download_button("📄 Markdown ⬇️", 
                                                 data=role_body or "", 
                                                 file_name=f"{selected_role_key}.md", 
                                                 mime="text/markdown",
                                                 key="exp_download_md",
                                                 width="stretch")
                            except Exception:
                                st.button("📄 Markdown ⬇️", disabled=True, width="stretch", key="exp_download_disabled")
                        with col_act3:
                            if st.button("🗑️ Löschen", key="exp_preview_delete", type="primary", width="stretch"):
                                # 1:1 Löschlogik aus Original
                                if soft_delete_role(selected_role_key):
                                    st.toast("🗑️ Eintrag gelöscht")
                                    st.session_state["exp_rn_selected_key"] = ""
                                    st.session_state["lab_selected_role"] = None  # Reset Edit-Modus
                                    st.session_state["exp_preview_expanded"] = False  # Vorschau schließen
                                    st.rerun()
                else:
                    st.warning("⚠️ Rolle nicht gefunden")
            except Exception as e:
                st.error(f"❌ Fehler beim Laden der Rolle: {str(e)}")
        else:
            # Wie Original: Info wenn nichts ausgewählt
            st.info("👆 Bitte wähle eine Rolle aus der Liste aus")
            
        # Manueller Close-Button für bessere UX
        if selected_role_key:
            st.markdown("---")
            if st.button("❌ Vorschau schließen", key="exp_close_preview", width="stretch"):
                st.session_state["exp_rn_selected_key"] = ""
                st.session_state["exp_preview_expanded"] = False
                st.rerun()
    
    # ========== HAUPTEXPANDER 3: NEUE ROLLE / BEARBEITEN ==========
    # 🎯 MAGIC: Automatische Steuerung des Form-Expanders
    edit_role_key = st.session_state.get("lab_selected_role", "")
    form_expanded = st.session_state.get("exp_form_expanded", False)
    
    # Automatisches Öffnen wenn Rolle zur Bearbeitung ausgewählt
    if edit_role_key and not form_expanded:
        st.session_state["exp_form_expanded"] = True
        form_expanded = True
    
    # Automatisches Schließen wenn keine Rolle ausgewählt
    if not edit_role_key and form_expanded:
        st.session_state["exp_form_expanded"] = False
        form_expanded = False
    
    with st.expander("➕ Neue Rolle / Bearbeiten", expanded=form_expanded):
        edit_role_key = st.session_state.get("lab_selected_role", "")
        
        # PENDING-UPDATE: Form-Reset nach Speichern (schließt Formular und Bearbeitungsmodus)
        if st.session_state.get("lab_role_form_reset"):
            st.session_state["lab_role_description_input"] = ""
            st.session_state["lab_role_title"] = ""
            st.session_state["lab_role_short"] = ""
            st.session_state["lab_role_responsibilities"] = ""
            st.session_state["lab_role_qualifications"] = ""
            st.session_state["lab_role_expertise"] = ""
            st.session_state["lab_role_description"] = ""
            st.session_state["lab_selected_role"] = None
            st.session_state["exp_last_loaded_role_key"] = ""
            st.session_state["exp_form_expanded"] = False
            st.session_state["lab_role_form_reset"] = False
        
        # PENDING-UPDATE: Formular leeren (OHNE Bearbeitungsmodus zu beenden)
        if st.session_state.get("lab_role_form_clear"):
            st.session_state["lab_role_description_input"] = ""
            st.session_state["lab_role_title"] = ""
            st.session_state["lab_role_short"] = ""
            st.session_state["lab_role_responsibilities"] = ""
            st.session_state["lab_role_qualifications"] = ""
            st.session_state["lab_role_expertise"] = ""
            st.session_state["lab_role_description"] = ""
            # WICHTIG: lab_selected_role und exp_form_expanded NICHT ändern!
            st.session_state["lab_role_form_clear"] = False
        
        # PENDING-UPDATE: Rolle laden falls bearbeitet wird
        if edit_role_key and st.session_state.get("exp_last_loaded_role_key") != edit_role_key:
            try:
                from src.m11_role_markdown import extract_prose_intro
                
                role_obj, role_body = load_role(edit_role_key)
                if role_obj:
                    st.session_state["lab_role_description_input"] = getattr(role_obj, 'description', '') or ""
                    st.session_state["lab_role_title"] = role_obj.title
                    st.session_state["lab_role_short"] = getattr(role_obj, 'short_code', '') or ""
                    st.session_state["lab_role_responsibilities"] = getattr(role_obj, 'responsibilities', '') or ""
                    st.session_state["lab_role_qualifications"] = getattr(role_obj, 'qualifications', '') or ""
                    st.session_state["lab_role_expertise"] = getattr(role_obj, 'expertise', '') or ""
                    
                    # Extract prose from markdown for editing
                    prose = extract_prose_intro(role_body or "")
                    st.session_state["lab_role_description"] = prose
                    
                    st.session_state["exp_last_loaded_role_key"] = edit_role_key
            except Exception as e:
                st.error(f"❌ Fehler beim Laden: {str(e)}")
        
        # Rolle laden falls bearbeitet wird
        if edit_role_key:
            with st.expander("ℹ️ Bearbeitungs-Modus", expanded=True):
                st.info(f"✏️ **Bearbeite Rolle:** `{edit_role_key}`")
                if st.button("❌ Bearbeitung abbrechen", key="exp_cancel_edit"):
                    st.session_state["lab_selected_role"] = None
                    st.session_state["exp_form_expanded"] = False  # Expander schließen
                    st.rerun()
        
        # Formular-Bereiche
        with st.expander("📝 Gewünschte Rolle", expanded=True):
            st.text_input("📋 Gewünschte Rolle beschreiben:", key="lab_role_description_input", 
                         placeholder="z.B. Verantwortlich für IT-Strategie und digitale Transformation")
            
            col_short1, col_short2 = st.columns(2)
            with col_short1:
                st.text_input("🎭 Rollen-Titel:", key="lab_role_title", 
                             placeholder="z.B. Chief Information Officer")
            with col_short2:
                st.text_input("🔖 Kürzel (max 14):", key="lab_role_short", 
                             max_chars=14, placeholder="z.B. CIO")
        
        with st.expander("🤖 KI-Unterstützung", expanded=True):
            
            try:
                providers = providers_available()
                prov = st.session_state.get("global_llm_provider", "openai")
                model = st.session_state.get("global_llm_model")
                temp = st.session_state.get("global_llm_temperature", 0.7)
                
                col_ai1, col_ai2 = st.columns(2)
                with col_ai1:
                    st.markdown("**🤖 KI-Einstellungen**")
                    st.caption(f"{prov} → {model or '—'} (T={temp:.1f})")
                with col_ai2:
                    st.markdown("**&nbsp;**")  # Spacer für Alignment
                    if st.button("✨ KI-Vorschlag", key="exp_ai_suggest", type="primary", width="stretch"):
                        description = st.session_state.get("lab_role_description_input", "").strip()
                        if description:
                            try:
                                title, short_code, responsibilities, qualifications, expertise, prose = generate_role_details(
                                    prov if prov != "none" else "openai", description, model=model, temperature=temp, role_key=edit_role_key if edit_role_key else None
                                )
                                # Pending-Update setzen statt direkt zu ändern
                                st.session_state["lab_role_ai_update"] = {
                                    "lab_role_title": title,
                                    "lab_role_short": short_code,
                                    "lab_role_responsibilities": responsibilities,
                                    "lab_role_qualifications": qualifications,
                                    "lab_role_expertise": expertise,
                                    "lab_role_description": prose
                                }
                                st.toast("✨ KI-Vorschlag wird eingefügt...")
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ KI-Fehler: {str(e)}")
                        else:
                            st.warning("⚠️ Bitte Rollenbeschreibung eingeben")
            except Exception:
                st.warning("⚠️ KI-Provider nicht verfügbar")
        
        with st.expander("📄 Beschreibung", expanded=True):
            st.text_area("🎯 Rollenbeschreibung:", 
                        key="lab_role_description", 
                        height=200,
                        placeholder="Beschreibe die Rolle detailliert...")
        
        with st.expander("🎯 Verantwortlichkeiten", expanded=False):
            st.text_area("🎯 Hauptverantwortlichkeiten:", 
                        key="lab_role_responsibilities", 
                        height=120,
                        placeholder="• Strategische Führung\n• Entscheidungsfindung\n• Teamleitung...")
        
        with st.expander("🎓 Qualifikationen", expanded=False):
            st.text_area("🎓 Qualifikationen & Anforderungen:", 
                        key="lab_role_qualifications", 
                        height=120,
                        placeholder="• Studium in BWL/VWL\n• 5+ Jahre Führungserfahrung\n• Strategisches Denken...")
        
        with st.expander("🧠 Expertise", expanded=False):
            st.text_area("🧠 Expertise / Spezialwissen:", 
                        key="lab_role_expertise", 
                        height=120,
                        placeholder="• Digitale Transformation\n• Change Management\n• Unternehmensführung...")
        
        # ========== ZUGEORDNETE AUFGABEN ==========
        # Debug: Prüfe ob wir im Bearbeitungsmodus sind
        current_role_key = st.session_state.get("lab_selected_role", "")
        st.caption(f"Debug: lab_selected_role='{current_role_key}', edit_role_key='{edit_role_key}'")
        
        if current_role_key:  # Verwende current_role_key statt edit_role_key
            from src.m07_tasks import list_tasks_df as get_tasks
            try:
                all_tasks_df = get_tasks(include_metadata=True)
                st.caption(f"Debug: {len(all_tasks_df) if all_tasks_df is not None else 0} Aufgaben geladen")
                
                if all_tasks_df is not None and not all_tasks_df.empty:
                    st.caption(f"Debug: Spalten={list(all_tasks_df.columns)[:5]}...")  # Nur erste 5
                    role_tasks = all_tasks_df[all_tasks_df["Quell-Rolle"] == current_role_key]
                    task_count = len(role_tasks)
                    st.caption(f"Debug: {task_count} Aufgaben für '{current_role_key}'")
                    
                    with st.expander(f"📋 Zugeordnete Aufgaben ({task_count})", expanded=True):  # Erstmal expanded=True zum Testen
                        if task_count > 0:
                            st.markdown(f"**{task_count} Aufgabe(n) sind dieser Rolle zugeordnet:**")
                            
                            for idx, task_row in role_tasks.iterrows():
                                col1, col2 = st.columns([3, 1])
                                with col1:
                                    task_title = task_row['Titel']
                                    task_short = task_row.get('Kürzel', '')
                                    label = f"{task_short} - {task_title}" if task_short else task_title
                                    st.markdown(f"• **{label}**")
                                    if task_row.get('Beschreibung'):
                                        st.caption(task_row['Beschreibung'])
                                
                                with col2:
                                    if st.button("Bearb.", key=f"edit_task_{task_row['Key']}", width="stretch"):
                                        # Navigate to Tasks tab with pre-selected task
                                        st.session_state["lab_active_tab"] = 1
                                        st.session_state["task_mgmt_selected_key"] = task_row['Key']
                                        st.session_state["task_mgmt_edit_mode"] = True
                                        st.query_params.update({"tab": "tasks"})
                                        st.rerun()
                            
                            # Schnellzugriff: Neue Aufgabe für diese Rolle erstellen
                            st.markdown("---")
                            if st.button("Neue Aufgabe erstellen", key="create_task_for_role", width="stretch"):
                                st.session_state["lab_active_tab"] = 1
                                st.session_state["task_mgmt_selected_key"] = "_NEW_"
                                st.session_state["task_mgmt_edit_mode"] = True
                                st.session_state["task_mgmt_preselect_role"] = current_role_key
                                st.query_params.update({"tab": "tasks"})
                                st.rerun()
                        else:
                            st.info("Noch keine Aufgaben zugeordnet")
                            if st.button("Erste Aufgabe erstellen", key="create_first_task", width="stretch"):
                                st.session_state["lab_active_tab"] = 1
                                st.session_state["task_mgmt_selected_key"] = "_NEW_"
                                st.session_state["task_mgmt_edit_mode"] = True
                                st.session_state["task_mgmt_preselect_role"] = current_role_key
                                st.query_params.update({"tab": "tasks"})
                                st.rerun()
                else:
                    st.warning("Keine Aufgaben in der Datenbank")
            except Exception as e:
                st.error(f"Fehler: {str(e)}")
                import traceback
                st.code(traceback.format_exc())
        else:
            st.caption("Debug: Keine Rolle ausgewählt (current_role_key ist leer)")
        
        with st.expander("💾 Speichern & Aktionen", expanded=True):
            col_save1, col_save2, col_save3, col_save4 = st.columns(4)
            with col_save1:
                if st.button("💾 Speichern", key="exp_save_role", type="primary", width="stretch"):
                    from src.m11_role_markdown import compose_role_markdown
                    
                    title = st.session_state.get("lab_role_title", "").strip()
                    prose = st.session_state.get("lab_role_description", "").strip()
                    
                    if not title:
                        st.error("❌ Titel ist erforderlich")
                    else:
                        try:
                            # Alle Formular-Werte sammeln
                            description_input = st.session_state.get("lab_role_description_input", "").strip()
                            short_code = st.session_state.get("lab_role_short", "").strip()
                            responsibilities = st.session_state.get("lab_role_responsibilities", "").strip()
                            qualifications = st.session_state.get("lab_role_qualifications", "").strip()
                            expertise = st.session_state.get("lab_role_expertise", "").strip()
                            
                            # Compose complete markdown: prose + structured sections
                            markdown_body = compose_role_markdown(
                                prose or "",
                                responsibilities or "",
                                qualifications or "",
                                expertise or ""
                            )
                            
                            role_obj, created = upsert_role(
                                title=title,
                                body_text=markdown_body,
                                short_code=short_code if short_code else None,
                                description=description_input if description_input else None,
                                responsibilities=responsibilities if responsibilities else None,
                                qualifications=qualifications if qualifications else None,
                                expertise=expertise if expertise else None,
                                attached_docs=None,  # Dokument-Upload kommt später
                                key=edit_role_key if edit_role_key else None
                            )
                            action = "erstellt" if created else "aktualisiert"
                            st.success(f"✅ Rolle {action}: {role_obj.key}")
                            
                            # Pending-Update für Form-Reset
                            st.session_state["lab_role_form_reset"] = True
                            st.session_state["exp_rn_selected_key"] = role_obj.key
                            st.session_state["exp_preview_expanded"] = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Speichern fehlgeschlagen: {str(e)}")
            
            with col_save2:
                # Button nur aktiv im Bearbeitungsmodus
                disabled = not bool(edit_role_key)
                if st.button("↩️ Verwerfen", key="exp_revert_form", disabled=disabled, width="stretch"):
                    # Änderungen verwerfen = Original-Daten neu laden
                    st.session_state["exp_last_loaded_role_key"] = ""  # Triggert neues Laden
                    st.toast("↩️ Änderungen verworfen, Daten neu geladen")
                    st.rerun()
            
            with col_save3:
                if st.button("🗑️ Formular leeren", key="exp_clear_form", width="stretch"):
                    # Pending-Update für komplettes Leeren (OHNE Bearbeitungsmodus zu beenden)
                    st.session_state["lab_role_form_clear"] = True
                    st.toast("🗑️ Formular wird geleert...")
                    st.rerun()
            
            with col_save4:
                disabled = not bool(edit_role_key)
                if st.button("❌ Rolle löschen", key="exp_delete_form", type="secondary", 
                           disabled=disabled, width="stretch"):
                    if edit_role_key:
                        try:
                            soft_delete_role(edit_role_key)
                            st.session_state["lab_selected_role"] = None
                            st.session_state["exp_last_loaded_role_key"] = ""
                            st.success("🗑️ Rolle gelöscht")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Löschen fehlgeschlagen: {str(e)}")

# ============ TAB 1: TASKS MANAGEMENT ============
elif current_tab == 1:
    st.subheader("📋 Aufgaben Management")
    
    from src.m07_tasks import list_tasks_df, load_task, upsert_task, delete_task
    from src.m07_roles import list_roles_df as roles_list
    
    # Session State für Task-Management
    st.session_state.setdefault("task_mgmt_filter_role", None)
    st.session_state.setdefault("task_mgmt_search", "")
    st.session_state.setdefault("task_mgmt_selected_key", None)
    st.session_state.setdefault("task_mgmt_edit_mode", False)
    
    # Check if we came from role editing with a preselected role
    if "task_mgmt_preselect_role" in st.session_state and st.session_state["task_mgmt_preselect_role"]:
        preselect_role = st.session_state["task_mgmt_preselect_role"]
        st.session_state["task_mgmt_filter_role"] = preselect_role
        st.session_state["task_mgmt_preselect_role"] = None  # Clear after use
        st.info(f"📌 Gefiltert nach Rolle: {preselect_role}")
    
    # ========== FILTER SECTION ==========
    with st.container(border=True):
        st.markdown("### 🔍 Filter & Suche")
        
        col1, col2 = st.columns([1, 1])
        
        with col1:
            # Role filter with search
            roles_df = roles_list(include_deleted=False)
            role_filter_options = {"": "Alle Aufgaben"}
            
            if roles_df is not None and not roles_df.empty:
                for _, row in roles_df.iterrows():
                    key = row['Key']
                    title = row['Rollenbezeichnung']
                    short = row.get('Rollenkürzel', '')
                    label = f"{short} - {title}" if short else title
                    role_filter_options[key] = label
            
            selected_filter_role = st.selectbox(
                "📌 Nach Rolle filtern",
                options=list(role_filter_options.keys()),
                format_func=lambda x: role_filter_options[x],
                key="task_filter_role_select"
            )
            st.session_state["task_mgmt_filter_role"] = selected_filter_role if selected_filter_role else None
        
        with col2:
            # Search across all fields
            search_query = st.text_input(
                "🔍 Textsuche",
                placeholder="Titel, Beschreibung, Kürzel...",
                key="task_search_input"
            )
            st.session_state["task_mgmt_search"] = search_query
    
    # ========== TASK LIST ==========
    with st.container(border=True):
        st.markdown("### 📋 Aufgabenliste")
        
        try:
            tasks_df = list_tasks_df(include_metadata=True)
            
            if tasks_df is None or tasks_df.empty:
                st.info("� Noch keine Aufgaben vorhanden")
            else:
                # Apply filters
                filtered_df = tasks_df.copy()
                
                # Filter by role
                if st.session_state["task_mgmt_filter_role"]:
                    filtered_df = filtered_df[
                        filtered_df["Quell-Rolle"] == st.session_state["task_mgmt_filter_role"]
                    ]
                
                # Search filter
                if st.session_state["task_mgmt_search"]:
                    search_lower = st.session_state["task_mgmt_search"].lower()
                    filtered_df = filtered_df[
                        filtered_df.apply(
                            lambda row: any(
                                search_lower in str(row[col]).lower()
                                for col in filtered_df.columns
                                if pd.notna(row[col])
                            ),
                            axis=1
                        )
                    ]
                
                if filtered_df.empty:
                    st.warning("⚠️ Keine Aufgaben gefunden mit den aktuellen Filtern")
                else:
                    st.info(f"✓ {len(filtered_df)} von {len(tasks_df)} Aufgaben")
                    
                    # DataFrame für Aufgaben-Liste
                    display_df = filtered_df[["Key", "Kürzel", "Titel", "Quell-Rolle", "Beschreibung"]].copy()
                    
                    # DataFrame mit Auswahl
                    event = st.dataframe(
                        display_df,
                        column_config={
                            "Key": None,
                            "Kürzel": st.column_config.TextColumn(
                                "Kürzel",
                                width="small"
                            ),
                            "Titel": st.column_config.TextColumn(
                                "Aufgabe",
                                width="large"
                            ),
                            "Quell-Rolle": st.column_config.TextColumn(
                                "Rolle",
                                width="medium"
                            ),
                            "Beschreibung": st.column_config.TextColumn(
                                "Beschreibung",
                                width="large"
                            )
                        },
                        hide_index=True,
                        width="stretch",
                        height=350,
                        on_select="rerun",
                        selection_mode="single-row",
                        key="tasks_df_view"
                    )
                    
                    # Auswahl verarbeiten
                    if event and "selection" in event and event["selection"]["rows"]:
                        selected_idx = event["selection"]["rows"][0]
                        key = display_df.iloc[selected_idx]["Key"]
                        
                        # Button zum Bearbeiten
                        if st.button("✏️ Bearbeiten", key="edit_selected_task", type="primary"):
                            st.session_state["task_mgmt_selected_key"] = key
                            st.session_state["task_mgmt_edit_mode"] = True
                            st.rerun()
        
        except Exception as e:
            st.error(f"❌ Fehler beim Laden der Aufgaben: {str(e)}")
    
    # ========== EDIT/CREATE FORM ==========
    # Debug info
    st.caption(f"Debug: edit_mode={st.session_state.get('task_mgmt_edit_mode')}, selected_key={st.session_state.get('task_mgmt_selected_key')}")
    
    if st.session_state.get("task_mgmt_edit_mode") and st.session_state.get("task_mgmt_selected_key"):
        with st.container(border=True):
            is_new = st.session_state["task_mgmt_selected_key"] == "_NEW_"
            current_key = st.session_state["task_mgmt_selected_key"]
            st.markdown(f"### {'🆕 Neue Aufgabe erstellen' if is_new else '✏️ Aufgabe bearbeiten'}")
            
            try:
                if is_new:
                    task_obj = None
                    task_body = ""
                else:
                    task_obj, task_body = load_task(current_key)
                
                if task_obj or is_new:
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        if not is_new:
                            st.markdown(f"**Aufgabe:** `{task_obj.key}`")
                    
                    with col2:
                        if st.button("❌ Abbrechen", key="task_cancel_edit"):
                            st.session_state["task_mgmt_edit_mode"] = False
                            st.session_state["task_mgmt_selected_key"] = None
                            st.rerun()
                    
                    # Form fields - WICHTIG: Keys sind dynamisch basierend auf current_key!
                    edit_title = st.text_input("Titel", value=task_obj.title if task_obj else "", key=f"task_edit_title_{current_key}")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        edit_short_title = st.text_input("Kurz-Titel", value=task_obj.short_title if task_obj else "", key=f"task_edit_short_title_{current_key}")
                    with col2:
                        edit_short_code = st.text_input("Kürzel", value=task_obj.short_code if task_obj else "", key=f"task_edit_short_code_{current_key}")
                    
                    edit_description = st.text_area("Beschreibung", value=task_obj.description if task_obj else "", height=100, key=f"task_edit_description_{current_key}")
                    
                    # Role assignment
                    roles_df = list_roles_df(include_deleted=False)
                    role_assign_options = {"": "Keine Rolle"}
                    
                    if roles_df is not None and not roles_df.empty:
                        for _, row in roles_df.iterrows():
                            key = row['Key']
                            title = row['Rollenbezeichnung']
                            short = row.get('Rollenkürzel', '')
                            label = f"{short} - {title}" if short else title
                            role_assign_options[key] = label
                    
                    # Determine current role: from task object, or from filter if new
                    if task_obj:
                        current_role = task_obj.source_role_key or ""
                    elif is_new and st.session_state.get("task_mgmt_filter_role"):
                        current_role = st.session_state["task_mgmt_filter_role"]
                    else:
                        current_role = ""
                    
                    edit_role = st.selectbox(
                        "Zugeordnete Rolle",
                        options=list(role_assign_options.keys()),
                        format_func=lambda x: role_assign_options[x],
                        index=list(role_assign_options.keys()).index(current_role) if current_role in role_assign_options else 0,
                        key=f"task_edit_role_{current_key}"
                    )
                    
                    edit_body = st.text_area("Aufgabeninhalt (Markdown)", value=task_body if task_body else "", height=300, key=f"task_edit_body_{current_key}")
                    
                    # Action buttons
                    col1, col2, col3 = st.columns([1, 1, 2])
                    
                    with col1:
                        if st.button("� Speichern", key="task_save", type="primary"):
                            try:
                                upsert_task(
                                    title=edit_title,
                                    body_text=edit_body,
                                    key=task_obj.key,
                                    short_title=edit_short_title,
                                    short_code=edit_short_code,
                                    description=edit_description,
                                    source_role_key=edit_role if edit_role else None,
                                    source_responsibility=task_obj.source_responsibility,
                                    generation_batch_id=task_obj.generation_batch_id,
                                    generated_at=task_obj.generated_at
                                )
                                st.success("✅ Aufgabe gespeichert")
                                st.session_state["task_mgmt_edit_mode"] = False
                                st.session_state["task_mgmt_selected_key"] = None
                                time.sleep(0.5)
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Fehler beim Speichern: {str(e)}")
                    
                    with col2:
                        if st.button("�️ Löschen", key="task_delete"):
                            if delete_task(task_obj.key):
                                st.success("✅ Aufgabe gelöscht")
                                st.session_state["task_mgmt_edit_mode"] = False
                                st.session_state["task_mgmt_selected_key"] = None
                                time.sleep(0.5)
                                st.rerun()
                            else:
                                st.error("❌ Löschen fehlgeschlagen")
                
                else:
                    st.error("❌ Aufgabe nicht gefunden")
                    st.session_state["task_mgmt_edit_mode"] = False
                    st.session_state["task_mgmt_selected_key"] = None
            
            except Exception as e:
                st.error(f"❌ Fehler beim Laden: {str(e)}")
                st.session_state["task_mgmt_edit_mode"] = False
    
    else:
        # New task button
        with st.container(border=True):
            st.markdown("### ➕ Neue Aufgabe erstellen")
            
            if st.button("🆕 Neue Aufgabe", key="task_create_new"):
                st.session_state["task_mgmt_edit_mode"] = True
                st.session_state["task_mgmt_selected_key"] = "_NEW_"
                st.rerun()

# ============ TAB 2: TASK-GENERIERUNG ============
elif current_tab == 2:
    st.subheader("🤖 Aufgaben aus Rolle generieren")
    
    st.info(
        "ℹ️ **RAG-Nutzung**: Task-Gen durchsucht **NUR** Dokumente mit Klassifizierung 'Pflichtenheft (Rolle)', "
        "die dieser Rolle zugeordnet sind (via 'Zugehörige Rollen' beim Upload). "
        "Allgemeine Projekt-Dokumente werden NICHT verwendet."
    )
    
    from src.m07_roles import list_roles_df
    from src.m12_task_generation import generate_tasks_from_role
    from src.m08_llm import providers_available
    
    # Load available roles
    try:
        roles_df = list_roles_df(include_deleted=False)
        if roles_df is None or roles_df.empty:
            st.warning("⚠️ Keine Rollen gefunden. Bitte erst eine Rolle erstellen.")
        else:
            # Role selection with search
            with st.container(border=True):
                st.markdown("### 1️⃣ Rolle auswählen")
                
                # Search/filter input
                st.session_state.setdefault("taskgen_search", "")
                search_query = st.text_input(
                    "🔍 Suche (durchsucht alle Felder)",
                    value=st.session_state["taskgen_search"],
                    placeholder="Titel, Kürzel, Beschreibung, Verantwortlichkeiten...",
                    key="taskgen_search_input"
                )
                st.session_state["taskgen_search"] = search_query
                
                # Filter roles based on search
                if search_query:
                    search_lower = search_query.lower()
                    filtered_df = roles_df[
                        roles_df.apply(
                            lambda row: any(
                                search_lower in str(row[col]).lower() 
                                for col in roles_df.columns 
                                if pd.notna(row[col])
                            ), 
                            axis=1
                        )
                    ]
                    if filtered_df.empty:
                        st.warning(f"⚠️ Keine Rollen gefunden für: '{search_query}'")
                    else:
                        st.info(f"✓ {len(filtered_df)} von {len(roles_df)} Rollen gefunden")
                else:
                    filtered_df = roles_df
                
                # Create role options (key: label) from filtered results
                role_options = {}
                for _, row in filtered_df.iterrows():
                    key = row['Key']
                    title = row['Rollenbezeichnung']
                    short = row.get('Rollenkürzel', '')
                    label = f"{short} - {title}" if short else title
                    role_options[key] = label
                
                # Show dropdown only if there are options
                if role_options:
                    selected_role_key = st.selectbox(
                        "Rolle",
                        options=list(role_options.keys()),
                        format_func=lambda x: role_options[x],
                        key="taskgen_role"
                    )
                else:
                    selected_role_key = None
                
                # Show role details
                if selected_role_key:
                    from src.m07_roles import load_role
                    role_obj, _ = load_role(selected_role_key)
                    
                    if role_obj and role_obj.responsibilities:
                        # Parse responsibilities count
                        resp_lines = [l for l in role_obj.responsibilities.split('\n') if l.strip().startswith('- ')]
                        resp_count = len(resp_lines)
                        
                        st.info(f"📋 **{resp_count} Verantwortlichkeiten** gefunden")
                        
                        with st.expander("Verantwortlichkeiten anzeigen", expanded=False):
                            st.markdown(role_obj.responsibilities)
            
            # Generation settings
            with st.container(border=True):
                st.markdown("### 2️⃣ Generierungs-Einstellungen")
                
                prov = st.session_state.get("global_llm_provider", "openai")
                model = st.session_state.get("global_llm_model")
                temp = st.session_state.get("global_llm_temperature", 0.7)
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.markdown("**🤖 KI-Einstellungen**")
                    st.caption(f"{prov} → {model or '—'} (T={temp:.1f})")
                    provider = prov if prov != "none" else "openai"
                
                with col2:
                    min_tasks = st.number_input(
                        "Min. Aufgaben pro Verantwortlichkeit",
                        min_value=1,
                        max_value=10,
                        value=3,
                        key="taskgen_min"
                    )
                
                with col3:
                    max_tasks = st.number_input(
                        "Max. Aufgaben pro Verantwortlichkeit",
                        min_value=1,
                        max_value=15,
                        value=7,
                        key="taskgen_max"
                    )
                
                # Validate
                if max_tasks < min_tasks:
                    st.error("⚠️ Maximum muss größer oder gleich Minimum sein")
                
                # Generate button
                st.markdown("")
                generate_disabled = not provider or not selected_role_key or max_tasks < min_tasks
                
                if st.button("✨ Aufgaben generieren", type="primary", disabled=generate_disabled, width="stretch"):
                    role_obj, _ = load_role(selected_role_key)
                    
                    if not role_obj or not role_obj.responsibilities:
                        st.error("❌ Rolle hat keine Verantwortlichkeiten")
                    else:
                        with st.spinner(f"🤖 Generiere Aufgaben mit {provider}..."):
                            try:
                                tasks = generate_tasks_from_role(
                                    provider=provider,
                                    role_title=role_obj.title,
                                    role_key=role_obj.key,
                                    responsibilities=role_obj.responsibilities,
                                    min_per_resp=min_tasks,
                                    max_per_resp=max_tasks,
                                    model_name=st.session_state.get("global_llm_model"),
                                    temperature=st.session_state.get("global_llm_temperature", 0.7),
                                    rag_enabled=st.session_state.get("global_rag_enabled", True),
                                    rag_top_k=st.session_state.get("global_llm_rag_top_k", 5),
                                    rag_similarity_threshold=st.session_state.get("global_rag_similarity_threshold", 0.5),
                                    rag_chunk_size=st.session_state.get("global_rag_chunk_size", 1000)
                                )
                                
                                if tasks:
                                    st.session_state["generated_tasks"] = tasks
                                    st.session_state["taskgen_batch_id"] = tasks[0].get("generation_batch_id", "")
                                    st.success(f"✅ {len(tasks)} Aufgaben generiert!")
                                    st.rerun()
                                else:
                                    st.error("❌ Keine Aufgaben generiert")
                                    
                            except Exception as e:
                                st.error(f"❌ Fehler bei Generierung: {str(e)}")
                                import traceback
                                with st.expander("Fehler-Details"):
                                    st.code(traceback.format_exc())
            
            # Review generated tasks
            if "generated_tasks" in st.session_state:
                tasks = st.session_state["generated_tasks"]
                
                with st.container(border=True):
                    st.markdown(f"### 3️⃣ Review ({len(tasks)} Aufgaben)")
                    
                    # Group by responsibility
                    from collections import defaultdict
                    by_resp = defaultdict(list)
                    for task in tasks:
                        resp = task.get("responsibility", "Unbekannt")
                        by_resp[resp].append(task)
                    
                    # Initialize selection state
                    if "taskgen_selected" not in st.session_state:
                        st.session_state["taskgen_selected"] = {i: True for i in range(len(tasks))}
                    
                    # Display tasks grouped by responsibility
                    task_index = 0
                    for resp_text, resp_tasks in by_resp.items():
                        st.markdown(f"**📋 {resp_text[:80]}...** ({len(resp_tasks)} Aufgaben)")
                        
                        for task in resp_tasks:
                            col1, col2 = st.columns([0.1, 0.9])
                            
                            with col1:
                                selected = st.checkbox(
                                    "✓",
                                    value=st.session_state["taskgen_selected"].get(task_index, True),
                                    key=f"taskgen_check_{task_index}",
                                    label_visibility="collapsed"
                                )
                                st.session_state["taskgen_selected"][task_index] = selected
                            
                            with col2:
                                with st.expander(f"{task['title']}", expanded=False):
                                    st.text_input("Titel", value=task['title'], key=f"taskgen_title_{task_index}")
                                    st.text_input("Kürzel", value=task['short_code'], key=f"taskgen_code_{task_index}", max_chars=14)
                                    st.text_area("Beschreibung", value=task['description'], key=f"taskgen_desc_{task_index}", height=100)
                            
                            task_index += 1
                        
                        st.markdown("")
                    
                    # Save buttons
                    st.markdown("---")
                    col_save1, col_save2 = st.columns(2)
                    
                    with col_save1:
                        selected_count = sum(1 for v in st.session_state["taskgen_selected"].values() if v)
                        if st.button(f"💾 {selected_count} Aufgaben speichern", type="primary", width="stretch"):
                            from src.m07_tasks import upsert_task
                            from datetime import datetime
                            
                            saved_count = 0
                            batch_id = st.session_state.get("taskgen_batch_id", "")
                            
                            with st.spinner("Speichere Aufgaben..."):
                                for i, task in enumerate(tasks):
                                    if not st.session_state["taskgen_selected"].get(i, False):
                                        continue
                                    
                                    # Get edited values from form
                                    title = st.session_state.get(f"taskgen_title_{i}", task['title'])
                                    short_code = st.session_state.get(f"taskgen_code_{i}", task['short_code'])
                                    description = st.session_state.get(f"taskgen_desc_{i}", task['description'])
                                    
                                    try:
                                        upsert_task(
                                            title=title,
                                            body_text=description,
                                            short_code=short_code,
                                            description=description,
                                            source_role_key=task['source_role_key'],
                                            source_responsibility=task['source_responsibility'],
                                            generation_batch_id=batch_id,
                                            generated_at=datetime.now()
                                        )
                                        saved_count += 1
                                    except Exception as e:
                                        st.error(f"❌ Fehler beim Speichern von '{title}': {str(e)}")
                            
                            if saved_count > 0:
                                st.success(f"✅ {saved_count} Aufgaben gespeichert!")
                                # Clear state
                                del st.session_state["generated_tasks"]
                                del st.session_state["taskgen_selected"]
                                if "taskgen_batch_id" in st.session_state:
                                    del st.session_state["taskgen_batch_id"]
                                st.rerun()
                    
                    with col_save2:
                        if st.button("❌ Alle verwerfen", width="stretch"):
                            del st.session_state["generated_tasks"]
                            del st.session_state["taskgen_selected"]
                            if "taskgen_batch_id" in st.session_state:
                                del st.session_state["taskgen_batch_id"]
                            st.rerun()
                
    except Exception as e:
        st.error(f"❌ Fehler: {str(e)}")
        import traceback
        with st.expander("Fehler-Details"):
            st.code(traceback.format_exc())

# ============ TAB 3: KONTEXTE ============
elif current_tab == 3:
    st.subheader("📚 Kontexte - Management")
    st.markdown("**Verwaltung von Kontext-Dokumenten für Projekte**")
    
    # Info-Box: Was sind Kontexte?
    with st.expander("ℹ️ Was sind Kontexte und wie werden sie verwendet?", expanded=False):
        st.markdown("""
        **Kontexte** sind wiederverwendbare Wissens-Bausteine für Ihre Projekte:
        
        **📝 Was sind Kontexte?**
        - Markdown-Dokumente mit strukturiertem Wissen
        - Z.B. Anforderungen, Einschränkungen, Business-Regeln, Rahmenbedingungen
        - Können mehreren Projekten zugeordnet werden
        
        **🔧 Wie funktionieren Kontexte?**
        1. **Erstellen**: Kontext mit Titel, Kürzel und Inhalt (Markdown) anlegen
        2. **Zuordnen**: In **Projekte-Tab** → Kontexte dem Projekt zuweisen
        3. **Verwendung**: KI erhält automatisch Kontext-Inhalte beim Chat
        
        **🎯 Anwendungsbeispiele:**
        - **"DSGVO-Anforderungen"**: Datenschutz-Richtlinien für alle Projekte
        - **"Barrierefreiheit"**: WCAG 2.1 AA Standards
        - **"Corporate Design"**: Marken-Guidelines
        - **"Security-Policy"**: Sicherheits-Anforderungen
        
        **💡 Tipp**: Kontexte sind projekt-übergreifend → einmal erstellen, mehrfach verwenden!
        """)
    
    st.session_state.setdefault("context_mgmt_edit_mode", False)
    st.session_state.setdefault("context_mgmt_selected_key", None)
    st.session_state.setdefault("context_mgmt_search_query", "")
    
    col_header1, col_header2 = st.columns([2, 1])
    with col_header1:
        st.markdown("### 📋 Kontexte verwalten")
    with col_header2:
        if st.button("🆕 Neuer Kontext", key="context_create_new", width="stretch"):
            st.session_state["context_mgmt_edit_mode"] = True
            st.session_state["context_mgmt_selected_key"] = "_NEW_"
            st.rerun()
    
    with st.container(border=True):
        st.markdown("### 📋 Bestehende Kontexte")
        
        try:
            contexts_df = list_contexts_df(include_body=False)
            if contexts_df is None or contexts_df.empty:
                st.info("⚠️ Keine Kontexte vorhanden. Erstellen Sie einen neuen Kontext mit dem Button oben.")
            else:
                col_search1, col_search2 = st.columns([2, 1])
                with col_search1:
                    search_query = st.text_input(
                        "🔍 Suchen:",
                        value=st.session_state["context_mgmt_search_query"],
                        placeholder="Titel, Kürzel...",
                        key="context_search_input"
                    )
                    st.session_state["context_mgmt_search_query"] = search_query
                
                with col_search2:
                    st.markdown("")
                
                if search_query:
                    search_lower = search_query.lower()
                    filtered_df = contexts_df[
                        contexts_df.apply(
                            lambda row: any(
                                search_lower in str(row[col]).lower()
                                for col in contexts_df.columns
                                if pd.notna(row[col])
                            ),
                            axis=1
                        )
                    ]
                    if filtered_df.empty:
                        st.warning(f"⚠️ Keine Kontexte gefunden für: '{search_query}'")
                    else:
                        st.info(f"✓ {len(filtered_df)} von {len(contexts_df)} Kontexte gefunden")
                else:
                    filtered_df = contexts_df
                
                # DataFrame für Kontexte-Liste
                display_df = filtered_df[['Key', 'Kürzel', 'Titel', 'Beschreibung']].copy()
                
                st.caption(f"📊 {len(display_df)} Kontexte")
                
                event = st.dataframe(
                    display_df,
                    column_config={
                        "Key": None,
                        "Kürzel": st.column_config.TextColumn(
                            "Kürzel",
                            width="small"
                        ),
                        "Titel": st.column_config.TextColumn(
                            "Kontext",
                            width="large"
                        ),
                        "Beschreibung": st.column_config.TextColumn(
                            "Beschreibung",
                            width="large"
                        )
                    },
                    hide_index=True,
                    width="stretch",
                    height=350,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="contexts_df_view"
                )
                
                if event and "selection" in event and event["selection"]["rows"]:
                    selected_idx = event["selection"]["rows"][0]
                    key = display_df.iloc[selected_idx]["Key"]
                    
                    if st.button("✏️ Bearbeiten", key="edit_selected_context", type="primary"):
                        st.session_state["context_mgmt_selected_key"] = key
                        st.session_state["context_mgmt_edit_mode"] = True
                        st.rerun()
        except Exception as e:
            st.error(f"❌ Fehler beim Laden der Kontexte: {str(e)}")
    
    if st.session_state.get("context_mgmt_edit_mode") and st.session_state.get("context_mgmt_selected_key"):
        with st.container(border=True):
            is_new = st.session_state["context_mgmt_selected_key"] == "_NEW_"
            current_key = st.session_state["context_mgmt_selected_key"]
            st.markdown(f"### {'🆕 Neuen Kontext erstellen' if is_new else '✏️ Kontext bearbeiten'}")
            
            try:
                if is_new:
                    ctx_obj = None
                    ctx_body = ""
                else:
                    ctx_obj, ctx_body = load_context(current_key)
                
                if ctx_obj or is_new:
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        if not is_new:
                            st.markdown(f"**Kontext:** `{ctx_obj.key}`")
                    
                    with col2:
                        if st.button("❌ Abbrechen", key="context_cancel_edit"):
                            st.session_state["context_mgmt_edit_mode"] = False
                            st.session_state["context_mgmt_selected_key"] = None
                            st.rerun()
                    
                    st.session_state.setdefault("context_description_input", "")
                    
                    with st.expander("🤖 KI-Unterstützung", expanded=True):
                        try:
                            providers = providers_available()
                            prov = st.session_state.get("global_llm_provider", "openai")
                            model = st.session_state.get("global_llm_model")
                            temp = st.session_state.get("global_llm_temperature", 0.7)
                            
                            col_ai1, col_ai2 = st.columns(2)
                            with col_ai1:
                                st.markdown("**🤖 KI-Einstellungen**")
                                st.caption(f"{prov} → {model or '—'} (T={temp:.1f})")
                            with col_ai2:
                                st.markdown("**&nbsp;**")
                                if st.button("✨ KI-Vorschlag", key="context_ai_suggest", type="primary", width="stretch"):
                                    description_input = st.session_state.get("context_description_input", "").strip()
                                    if description_input:
                                        try:
                                            title, short_code, short_title, desc, body = generate_context_details(
                                                prov if prov != "none" else "openai", description_input, model=model, temperature=temp, context_key=current_key if current_key else None
                                            )
                                            st.session_state["context_ai_update"] = {
                                                "context_edit_title": title,
                                                "context_edit_short_title": short_title,
                                                "context_edit_short_code": short_code,
                                                "context_edit_description": desc,
                                                "context_edit_body": body
                                            }
                                            st.toast("✨ KI-Vorschlag wird eingefügt...")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"❌ KI-Fehler: {str(e)}")
                                    else:
                                        st.warning("⚠️ Bitte Kontextbeschreibung eingeben")
                        except Exception:
                            st.warning("⚠️ KI-Provider nicht verfügbar")
                        
                        context_desc_input = st.text_area(
                            "📝 Kontextbeschreibung (für KI-Vorschlag)",
                            value=st.session_state.get("context_description_input", ""),
                            key="context_description_input_field",
                            height=100,
                            placeholder="Beschreiben Sie den Kontext... z.B. Unternehmensrichtlinien, Prozessbeschreibungen, etc."
                        )
                        st.session_state["context_description_input"] = context_desc_input
                    
                    if st.session_state.get("context_ai_update"):
                        for k, v in st.session_state["context_ai_update"].items():
                            if k == "context_edit_title":
                                st.session_state[f"context_edit_title_{current_key}"] = v
                            elif k == "context_edit_short_title":
                                st.session_state[f"context_edit_short_title_{current_key}"] = v
                            elif k == "context_edit_short_code":
                                st.session_state[f"context_edit_short_code_{current_key}"] = v
                            elif k == "context_edit_description":
                                st.session_state[f"context_edit_description_{current_key}"] = v
                            elif k == "context_edit_body":
                                st.session_state[f"context_edit_body_{current_key}"] = v
                        st.session_state["context_ai_update"] = None
                    
                    st.session_state.setdefault(f"context_edit_title_{current_key}", ctx_obj.title if ctx_obj else "")
                    st.session_state.setdefault(f"context_edit_short_title_{current_key}", ctx_obj.short_title if ctx_obj else "")
                    st.session_state.setdefault(f"context_edit_short_code_{current_key}", ctx_obj.short_code if ctx_obj else "")
                    st.session_state.setdefault(f"context_edit_description_{current_key}", ctx_obj.description if ctx_obj else "")
                    st.session_state.setdefault(f"context_edit_body_{current_key}", ctx_body if ctx_body else "")
                    
                    edit_title = st.text_input("Titel", key=f"context_edit_title_{current_key}")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        edit_short_title = st.text_input("Kurz-Titel", key=f"context_edit_short_title_{current_key}")
                    with col2:
                        edit_short_code = st.text_input("Kürzel", key=f"context_edit_short_code_{current_key}")
                    
                    edit_description = st.text_area("Beschreibung", height=100, key=f"context_edit_description_{current_key}")
                    
                    edit_body = st.text_area("Kontext-Inhalt (Markdown)", height=300, key=f"context_edit_body_{current_key}")
                    
                    col1, col2 = st.columns([1, 2])
                    
                    with col1:
                        if st.button("💾 Speichern", key="context_save", type="primary"):
                            try:
                                upsert_context(
                                    title=edit_title,
                                    body_text=edit_body,
                                    key=ctx_obj.key if ctx_obj else None,
                                    short_title=edit_short_title,
                                    short_code=edit_short_code,
                                    description=edit_description
                                )
                                st.success("✅ Kontext gespeichert")
                                st.session_state["context_mgmt_edit_mode"] = False
                                st.session_state["context_mgmt_selected_key"] = None
                                time.sleep(0.5)
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Fehler beim Speichern: {str(e)}")
                    
                    with col2:
                        if not is_new:
                            if st.button("🗑️ Löschen", key="context_delete"):
                                if soft_delete_context(ctx_obj.key):
                                    st.success("✅ Kontext gelöscht")
                                    st.session_state["context_mgmt_edit_mode"] = False
                                    st.session_state["context_mgmt_selected_key"] = None
                                    time.sleep(0.5)
                                    st.rerun()
                                else:
                                    st.error("❌ Löschen fehlgeschlagen")
                
                else:
                    st.error("❌ Kontext nicht gefunden")
                    st.session_state["context_mgmt_edit_mode"] = False
                    st.session_state["context_mgmt_selected_key"] = None
            
            except Exception as e:
                st.error(f"❌ Fehler beim Laden: {str(e)}")
                st.session_state["context_mgmt_edit_mode"] = False

# ============ TAB 5: PROJEKTE ============
elif current_tab == 5:
    st.subheader("🗂️ Projekte - Management")
    st.markdown("**Verwaltung von Projekten mit Rollen, Aufgaben und Kontexten**")
    
    st.info(
        "ℹ️ **Dokument-Zuordnung**: Nur Dokumente, die einem Projekt zugeordnet sind, werden in Chat und Batch-QA verwendet. "
        "Ordnen Sie Dokumente im Tab 'Dokumente' zu → 'Zu Projekt zuordnen'."
    )
    
    st.session_state.setdefault("project_mgmt_edit_mode", False)
    st.session_state.setdefault("project_mgmt_selected_key", None)
    st.session_state.setdefault("project_mgmt_search_query", "")
    
    col_header1, col_header2 = st.columns([2, 1])
    with col_header1:
        st.markdown("### 📋 Projekte verwalten")
    with col_header2:
        if st.button("🆕 Neues Projekt", key="project_create_new", width="stretch"):
            st.session_state["project_mgmt_edit_mode"] = True
            st.session_state["project_mgmt_selected_key"] = "_NEW_"
            st.rerun()
    
    with st.container(border=True):
        st.markdown("### 📋 Bestehende Projekte")
        
        try:
            projects_df = list_projects_df(include_body=False, include_deleted=False)
            if projects_df is None or projects_df.empty:
                st.info("⚠️ Keine Projekte vorhanden. Erstellen Sie ein neues Projekt mit dem Button oben.")
            else:
                col_search1, col_search2 = st.columns([2, 1])
                with col_search1:
                    search_query = st.text_input(
                        "🔍 Suchen:",
                        value=st.session_state["project_mgmt_search_query"],
                        placeholder="Titel, Typ, Kürzel...",
                        key="project_search_input"
                    )
                    st.session_state["project_mgmt_search_query"] = search_query
                
                with col_search2:
                    st.markdown("")
                
                if search_query:
                    search_lower = search_query.lower()
                    filtered_df = projects_df[
                        projects_df.apply(
                            lambda row: any(
                                search_lower in str(row[col]).lower()
                                for col in projects_df.columns
                                if pd.notna(row[col])
                            ),
                            axis=1
                        )
                    ]
                    if filtered_df.empty:
                        st.warning(f"⚠️ Keine Projekte gefunden für: '{search_query}'")
                    else:
                        st.info(f"✓ {len(filtered_df)} von {len(projects_df)} Projekte gefunden")
                else:
                    filtered_df = projects_df
                
                # DataFrame für Projekte-Liste
                display_df = filtered_df[['Key', 'Kürzel', 'Titel', 'Typ', 'Beschreibung']].copy()
                
                st.caption(f"📊 {len(display_df)} Projekte")
                
                event = st.dataframe(
                    display_df,
                    column_config={
                        "Key": None,
                        "Kürzel": st.column_config.TextColumn(
                            "Kürzel",
                            width="small"
                        ),
                        "Titel": st.column_config.TextColumn(
                            "Projekt",
                            width="large"
                        ),
                        "Typ": st.column_config.TextColumn(
                            "Typ",
                            width="small"
                        ),
                        "Beschreibung": st.column_config.TextColumn(
                            "Beschreibung",
                            width="large"
                        )
                    },
                    hide_index=True,
                    width="stretch",
                    height=350,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="projects_df_view"
                )
                
                if event and "selection" in event and event["selection"]["rows"]:
                    selected_idx = event["selection"]["rows"][0]
                    key = display_df.iloc[selected_idx]["Key"]
                    
                    if st.button("✏️ Bearbeiten", key="edit_selected_project", type="primary"):
                        st.session_state["project_mgmt_selected_key"] = key
                        st.session_state["project_mgmt_edit_mode"] = True
                        st.rerun()
        except Exception as e:
            st.error(f"❌ Fehler beim Laden der Projekte: {str(e)}")
    
    if st.session_state.get("project_mgmt_edit_mode") and st.session_state.get("project_mgmt_selected_key"):
        with st.container(border=True):
            is_new = st.session_state["project_mgmt_selected_key"] == "_NEW_"
            current_key = st.session_state["project_mgmt_selected_key"]
            st.markdown(f"### {'🆕 Neues Projekt erstellen' if is_new else '✏️ Projekt bearbeiten'}")
            
            try:
                if is_new:
                    proj_obj = None
                    proj_body = ""
                else:
                    proj_obj, proj_body = load_project(current_key)
                
                if proj_obj or is_new:
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        if not is_new:
                            st.markdown(f"**Projekt:** `{proj_obj.key}`")
                    
                    with col2:
                        if st.button("❌ Abbrechen", key="project_cancel_edit"):
                            st.session_state["project_mgmt_edit_mode"] = False
                            st.session_state["project_mgmt_selected_key"] = None
                            st.rerun()
                    
                    st.session_state.setdefault("project_description_input", "")
                    
                    # Load existing selections
                    current_role_keys = []
                    current_context_keys = []
                    if proj_obj:
                        if getattr(proj_obj, "role_keys", None):
                            try:
                                current_role_keys = json.loads(proj_obj.role_keys)
                            except:
                                pass
                        if getattr(proj_obj, "context_keys", None):
                            try:
                                current_context_keys = json.loads(proj_obj.context_keys)
                            except:
                                pass
                    
                    st.session_state.setdefault("project_selected_roles", current_role_keys)
                    st.session_state.setdefault("project_selected_contexts", current_context_keys)
                    
                    with st.expander("📌 Rollen & Kontexte", expanded=True):
                        col_r, col_c = st.columns(2)
                        
                        with col_r:
                            st.markdown("**📌 Zuordnete Rollen:**")
                            try:
                                roles_df = list_roles_df(include_deleted=False)
                                if roles_df is not None and not roles_df.empty:
                                    role_options = {}
                                    for _, row in roles_df.iterrows():
                                        key_r = row['Key']
                                        title_r = row['Rollenbezeichnung']
                                        short_r = row.get('Rollenkürzel', '')
                                        label_r = f"{short_r} - {title_r}" if short_r else title_r
                                        role_options[key_r] = label_r
                                    
                                    # Filter out invalid keys
                                    valid_role_keys = [k for k in current_role_keys if k in role_options]
                                    
                                    selected_role_keys = st.multiselect(
                                        "Rollen wählen",
                                        options=list(role_options.keys()),
                                        format_func=lambda x: role_options.get(x, x),
                                        default=valid_role_keys,
                                        key=f"project_roles_{current_key}",
                                        label_visibility="collapsed"
                                    )
                                    st.session_state["project_selected_roles"] = selected_role_keys
                                else:
                                    st.info("Keine Rollen vorhanden")
                                    selected_role_keys = []
                            except Exception as e:
                                st.error(f"Fehler beim Laden der Rollen: {str(e)}")
                                selected_role_keys = []
                        
                        with col_c:
                            st.markdown("**📚 Zugeordnete Kontexte:**")
                            try:
                                contexts_df = list_contexts_df(include_body=False)
                                if contexts_df is not None and not contexts_df.empty:
                                    context_options = {}
                                    for _, row in contexts_df.iterrows():
                                        key_c = row['Key']
                                        title_c = row['Titel']
                                        short_c = row.get('Kürzel', '')
                                        label_c = f"{short_c} - {title_c}" if short_c else title_c
                                        context_options[key_c] = label_c
                                    
                                    # Filter out invalid keys
                                    valid_context_keys = [k for k in current_context_keys if k in context_options]
                                    
                                    selected_context_keys = st.multiselect(
                                        "Kontexte wählen",
                                        options=list(context_options.keys()),
                                        format_func=lambda x: context_options.get(x, x),
                                        default=valid_context_keys,
                                        key=f"project_contexts_{current_key}",
                                        label_visibility="collapsed"
                                    )
                                    st.session_state["project_selected_contexts"] = selected_context_keys
                                else:
                                    st.info("Keine Kontexte vorhanden")
                                    selected_context_keys = []
                            except Exception as e:
                                st.error(f"Fehler beim Laden der Kontexte: {str(e)}")
                                selected_context_keys = []
                    
                    with st.expander("🤖 KI-Unterstützung", expanded=True):
                        try:
                            providers = providers_available()
                            prov = st.session_state.get("global_llm_provider", "openai")
                            model = st.session_state.get("global_llm_model")
                            temp = st.session_state.get("global_llm_temperature", 0.7)
                            
                            col_ai1, col_ai2 = st.columns(2)
                            with col_ai1:
                                st.markdown("**🤖 KI-Einstellungen**")
                                st.caption(f"{prov} → {model or '—'} (T={temp:.1f})")
                            with col_ai2:
                                st.markdown("**&nbsp;**")
                                if st.button("✨ KI-Vorschlag", key="project_ai_suggest", type="primary", width="stretch"):
                                    proj_desc = st.session_state.get("project_description_input", "").strip()
                                    if proj_desc:
                                        try:
                                            role_descs = ""
                                            if selected_role_keys:
                                                for rk in selected_role_keys:
                                                    try:
                                                        role_obj, role_body = load_role(rk)
                                                        if role_obj:
                                                            role_descs += f"- {role_obj.title}\n"
                                                    except:
                                                        pass
                                            
                                            context_descs = ""
                                            if selected_context_keys:
                                                for ck in selected_context_keys:
                                                    try:
                                                        ctx_obj, ctx_body = load_context(ck)
                                                        if ctx_obj:
                                                            context_descs += f"- {ctx_obj.title}\n"
                                                    except:
                                                        pass
                                            
                                            title, short_code, short_title, desc, briefing = generate_project_details(
                                                prov if prov != "none" else "openai", proj_desc, role_descs, context_descs, model=model, temperature=temp, project_key=current_key if current_key else None
                                            )
                                            st.session_state["project_ai_update"] = {
                                                "project_edit_title": title,
                                                "project_edit_short_title": short_title,
                                                "project_edit_short_code": short_code,
                                                "project_edit_description": desc,
                                                "project_edit_body": briefing
                                            }
                                            st.toast("✨ KI-Vorschlag wird eingefügt...")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"❌ KI-Fehler: {str(e)}")
                                    else:
                                        st.warning("⚠️ Bitte Projektbeschreibung eingeben")
                        except Exception:
                            st.warning("⚠️ KI-Provider nicht verfügbar")
                        
                        project_desc_input = st.text_area(
                            "📝 Projektbeschreibung (für KI-Vorschlag)",
                            value=st.session_state.get("project_description_input", ""),
                            key="project_description_input_field",
                            height=100,
                            placeholder="Beschreiben Sie das Projekt... z.B. Ziele, Scope, Meilensteine, etc."
                        )
                        st.session_state["project_description_input"] = project_desc_input
                    
                    if st.session_state.get("project_ai_update"):
                        for k, v in st.session_state["project_ai_update"].items():
                            if k == "project_edit_title":
                                st.session_state[f"project_edit_title_{current_key}"] = v
                            elif k == "project_edit_short_title":
                                st.session_state[f"project_edit_short_title_{current_key}"] = v
                            elif k == "project_edit_short_code":
                                st.session_state[f"project_edit_short_code_{current_key}"] = v
                            elif k == "project_edit_description":
                                st.session_state[f"project_edit_description_{current_key}"] = v
                            elif k == "project_edit_body":
                                st.session_state[f"project_edit_body_{current_key}"] = v
                        st.session_state["project_ai_update"] = None
                    
                    st.session_state.setdefault(f"project_edit_title_{current_key}", proj_obj.title if proj_obj else "")
                    st.session_state.setdefault(f"project_edit_short_title_{current_key}", proj_obj.short_title if proj_obj else "")
                    st.session_state.setdefault(f"project_edit_short_code_{current_key}", proj_obj.short_code if proj_obj else "")
                    st.session_state.setdefault(f"project_edit_type_{current_key}", proj_obj.type if proj_obj else "")
                    st.session_state.setdefault(f"project_edit_description_{current_key}", proj_obj.description if proj_obj else "")
                    st.session_state.setdefault(f"project_edit_body_{current_key}", proj_body if proj_body else "")
                    
                    edit_title = st.text_input("Titel", key=f"project_edit_title_{current_key}")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        edit_short_title = st.text_input("Kurz-Titel", key=f"project_edit_short_title_{current_key}")
                    with col2:
                        edit_short_code = st.text_input("Kürzel", key=f"project_edit_short_code_{current_key}")
                    with col3:
                        project_types = ["", "IT", "Business", "Finance", "HR", "Operations", "Custom"]
                        edit_type = st.selectbox(
                            "Projekt-Typ",
                            options=project_types,
                            key=f"project_edit_type_{current_key}"
                        )
                    
                    edit_description = st.text_area("Beschreibung", height=100, key=f"project_edit_description_{current_key}")
                    
                    edit_body = st.text_area("Projekt-Brief / Auftrag (Markdown)", height=300, key=f"project_edit_body_{current_key}")
                    
                    st.markdown("---")
                    st.markdown("#### 📄 Zugeordnete Dokumente")
                    
                    all_docs = list_documents(include_deleted=False)
                    if all_docs:
                        assigned_docs = get_project_documents(proj_obj.key) if proj_obj else []
                        assigned_ids = [d.id for d in assigned_docs]
                        doc_options = {d.id: f"{d.filename} ({d.classification})" for d in all_docs}
                        
                        selected_doc_ids = st.multiselect(
                            "Dokumente für RAG-Kontext wählen",
                            options=list(doc_options.keys()),
                            format_func=lambda x: doc_options[x],
                            default=assigned_ids,
                            key=f"project_edit_docs_{current_key}"
                        )
                    else:
                        st.info("📭 Keine Dokumente verfügbar. Laden Sie zunächst Dokumente im 'Dokumente' Tab hoch.")
                        selected_doc_ids = []
                    
                    col1, col2 = st.columns([1, 2])
                    
                    with col1:
                        if st.button("💾 Speichern", key="project_save", type="primary"):
                            try:
                                # 1. Projekt speichern
                                saved_project, _ = upsert_project(
                                    title=edit_title,
                                    body_text=edit_body,
                                    type_name=edit_type if edit_type else None,
                                    key=proj_obj.key if proj_obj else None,
                                    short_title=edit_short_title,
                                    short_code=edit_short_code,
                                    description=edit_description,
                                    role_keys=st.session_state.get(f"project_roles_{current_key}", []),
                                    context_keys=st.session_state.get(f"project_contexts_{current_key}", [])
                                )
                                
                                # 2. Dokument-Links aktualisieren
                                if saved_project:
                                    current_assigned = [d.id for d in get_project_documents(saved_project.key)]
                                    
                                    # Unlink removed
                                    for doc_id in current_assigned:
                                        if doc_id not in selected_doc_ids:
                                            unlink_document_from_project(saved_project.key, doc_id)
                                    
                                    # Link new
                                    for doc_id in selected_doc_ids:
                                        if doc_id not in current_assigned:
                                            link_document_to_project(saved_project.key, doc_id)
                                
                                st.success("✅ Projekt gespeichert")
                                st.session_state["project_mgmt_edit_mode"] = False
                                st.session_state["project_mgmt_selected_key"] = None
                                time.sleep(0.5)
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Fehler beim Speichern: {str(e)}")
                    
                    with col2:
                        if not is_new:
                            if st.button("🗑️ Löschen", key="project_delete"):
                                if soft_delete_project(proj_obj.key):
                                    st.success("✅ Projekt gelöscht")
                                    st.session_state["project_mgmt_edit_mode"] = False
                                    st.session_state["project_mgmt_selected_key"] = None
                                    time.sleep(0.5)
                                    st.rerun()
                                else:
                                    st.error("❌ Löschen fehlgeschlagen")
                
                else:
                    st.error("❌ Projekt nicht gefunden")
                    st.session_state["project_mgmt_edit_mode"] = False
                    st.session_state["project_mgmt_selected_key"] = None
            
            except Exception as e:
                st.error(f"❌ Fehler beim Laden: {str(e)}")
                st.session_state["project_mgmt_edit_mode"] = False

# ============ TAB 4: DOKUMENTE (RAG Document Management) ============
elif current_tab == 4:
    st.subheader("📄 Dokumente - RAG Management")
    st.markdown("**Verwalte Dokumente für RAG-Kontext in Projekten**")
    
    with st.expander("ℹ️ Wie funktioniert die Dokument-Nutzung?", expanded=False):
        st.markdown("""
        **Klassifizierung bestimmt Nutzung:**
        
        - **🎭 Pflichtenheft (Rolle)**: Wird bei **Task-Generierung** verwendet → Rollen zuordnen beim Upload
        - **📁 Pflichtenheft (Projekt)**: Wird in **Chat & Batch-QA** verwendet → Projekt zuordnen nach Upload
        - **📋 Andere**: Verfügbar in **Chat & Batch-QA** → Projekt zuordnen nach Upload
        
        **Chunk-Größe**: Bestimmt wie Text zerlegt wird (Standard: 1000 Zeichen). Größere Chunks = mehr Kontext, kleinere = präziser.
        
        **Beispiele für Chunk-Größen:**
        ```
        ├─ Pflichtenheft.pdf    → 1500 (Zusammenhänge wichtig)
        ├─ Fragen.csv           → 250  (1 Zeile = 1 Chunk)
        ├─ ISO-Standard.pdf     → 1000 (strukturiert)
        └─ Meeting-Notes.txt    → 800  (Absätze)
        ```
        
        **RAG-Suche**: Findet automatisch relevante Abschnitte via Embedding (Ähnlichkeitssuche) und fügt sie als Kontext in Prompts ein.
        """)
    
    st.session_state.setdefault("doc_upload_mode", False)
    st.session_state.setdefault("doc_filter_classification", "")
    
    doc_col1, doc_col2 = st.columns([3, 1])
    
    with doc_col1:
        st.markdown("#### 📂 Dokumenten-Übersicht")
    
    with doc_col2:
        if st.button("➕ Neues Dokument", key="doc_toggle_upload", type="primary"):
            st.session_state["doc_upload_mode"] = not st.session_state["doc_upload_mode"]
    
    if st.session_state["doc_upload_mode"]:
        st.markdown("---")
        st.markdown("#### 📤 Dokument hochladen")
        
        uploaded_file = st.file_uploader(
            "Datei hochladen (PDF, Word, TXT, MD, CSV, etc.)",
            type=["pdf", "docx", "txt", "md", "csv", "json", "yaml", "yml"],
            key="doc_file_uploader"
        )
        
        classification = st.selectbox(
            "Klassifizierung",
            options=DOCUMENT_CLASSIFICATIONS,
            key="doc_classification_select"
        )
        
        # Info-Hinweis zu Klassifizierungen
        if classification == "Pflichtenheft (Rolle)":
            st.info(
                "📋 **Pflichtenheft (Rolle)**: Wird bei Task-Generierung verwendet. "
                "Wählen Sie unten die zugehörigen Rollen aus."
            )
        elif classification == "Pflichtenheft (Projekt)":
            st.info(
                "📁 **Pflichtenheft (Projekt)**: Verfügbar in Chat und Batch-QA für alle Projekt-Mitglieder. "
                "Wird NICHT bei Task-Generierung verwendet."
            )
        elif classification in ["Anforderung/Feature", "Standard/Richtlinie", "FAQ/Fragen-Katalog"]:
            st.caption("💡 Dieses Dokument wird in Chat und Batch-QA verwendet (wenn dem Projekt zugeordnet).")
        
        # Chunk-Größe (editierbar)
        default_chunk_size = st.session_state.get("global_rag_chunk_size", 1000)
        chunk_size = st.number_input(
            "Chunk-Größe (Zeichen)",
            min_value=200,
            max_value=5000,
            value=default_chunk_size,
            step=100,
            key="doc_chunk_size_input",
            help="Größere Chunks = mehr Kontext, kleinere = präziser. Standard: 1000"
        )
        
        # Rollen-Zuordnung bei "Pflichtenheft (Rolle)"
        linked_role_keys = None
        if classification == "Pflichtenheft (Rolle)":
            st.info("💡 **Rollen-Dokument**: Wählen Sie die zugehörigen Rollen aus")
            all_roles = list_roles()
            if all_roles:
                role_options = {r.key: f"{r.title} ({r.short_code})" for r in all_roles}
                selected_roles = st.multiselect(
                    "Zugehörige Rollen",
                    options=list(role_options.keys()),
                    format_func=lambda x: role_options.get(x, x),
                    key="doc_linked_roles_select",
                    help="Dieses Dokument wird NUR bei Task-Gen für diese Rollen verwendet"
                )
                if selected_roles:
                    linked_role_keys = selected_roles
            else:
                st.warning("⚠️ Keine Rollen vorhanden. Bitte erstellen Sie zuerst Rollen.")
        
        # CSV-spezifische Optionen
        csv_delimiter = ";"
        if uploaded_file and uploaded_file.name.endswith(".csv"):
            st.info("📊 **CSV-Datei erkannt**: Jede Zeile wird als separater Chunk behandelt (ideal für Listen/Fragen).")
            csv_delimiter = st.selectbox(
                "CSV-Trennzeichen (Delimiter)",
                options=[";", ",", "\t", "|"],
                index=0,
                key="csv_delimiter_select",
                help="Wählen Sie das Trennzeichen Ihrer CSV-Datei. Standard für deutsche Excel-Dateien: ';'"
            )
            st.caption("**Erforderliche Spalten für Batch-QA:** Nr, Lieferant, Frage (Antwort ist optional)")
        
        if uploaded_file is not None:
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("✅ Hochladen & Einbetten", key="doc_submit_btn", type="primary"):
                    with st.spinner("Datei wird verarbeitet..."):
                        file_data = uploaded_file.read()
                        success, message = ingest_document(
                            file_name=uploaded_file.name,
                            file_bytes=file_data,
                            classification=classification,
                            chunk_size=chunk_size,
                            csv_delimiter=csv_delimiter,
                            linked_role_keys=linked_role_keys
                        )
                        
                        if success:
                            st.success(message)
                            st.session_state["doc_upload_mode"] = False
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error(message)
            
            with col2:
                if st.button("❌ Abbrechen", key="doc_cancel_btn"):
                    st.session_state["doc_upload_mode"] = False
                    st.rerun()
        
        st.markdown("---")
    
    docs = list_documents()
    
    if docs:
        st.markdown(f"**Insgesamt: {len(docs)} Dokumente**")
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            search_text = st.text_input(
                "🔍 Volltext-Suche (Dateiname)",
                placeholder="z.B. 'Strategie', 'PDF'...",
                key="doc_search_text"
            )
        
        with col2:
            doc_filter = st.selectbox(
                "Klassifizierung",
                options=["Alle"] + DOCUMENT_CLASSIFICATIONS,
                key="doc_filter_select"
            )
        
        filtered_docs = docs
        
        if doc_filter != "Alle":
            filtered_docs = [d for d in filtered_docs if d.classification == doc_filter]
        
        if search_text.strip():
            search_lower = search_text.lower()
            filtered_docs = [d for d in filtered_docs if search_lower in d.filename.lower()]
        
        if filtered_docs:
            doc_df = pd.DataFrame([
                {
                    "ID": d.id,
                    "Dateiname": d.filename,
                    "Klassifizierung": d.classification,
                    "Chunks": d.chunk_count,
                    "Ø Chunk-Size": d.chunk_size_used if d.chunk_size_used else (
                        f"~{d.file_size // d.chunk_count:,}" if d.chunk_count > 0 and d.file_size else "—"
                    ),
                    "Größe (KB)": d.file_size // 1024 if d.file_size else 0,
                    "Hochgeladen": d.uploaded_at.strftime("%Y-%m-%d %H:%M") if d.uploaded_at else "",
                    "SHA256": d.sha256_hash[:16] + "..."
                }
                for d in filtered_docs
            ])
            
            st.dataframe(doc_df, width="stretch", key="doc_table")
            
            st.markdown("---")
            st.markdown("#### 🗑️ Dokument löschen")
            
            doc_to_delete = st.selectbox(
                "Zu löschendes Dokument wählen",
                options=[d.filename for d in filtered_docs],
                key="doc_delete_select"
            )
            
            if doc_to_delete:
                selected_doc = next((d for d in filtered_docs if d.filename == doc_to_delete), None)
                if selected_doc:
                    if st.button("🗑️ Endgültig löschen", key="doc_delete_btn"):
                        if delete_document(selected_doc.id):
                            st.success("Dokument gelöscht.")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error("Fehler beim Löschen.")
        else:
            st.info("📭 Keine Dokumente gefunden. Versuchen Sie, die Filter anzupassen.")
    else:
        st.info("📭 Noch keine Dokumente vorhanden. Laden Sie ein Dokument hoch!")
    
    st.markdown("---")
    st.markdown("#### 🔄 Admin - ChromaDB Sync")
    st.markdown("Synchronisiert alle Dokumente und Chunks aus SQLite zu ChromaDB für Semantic Search.")
    
    if st.button("🔄 ChromaDB Sync starten", key="chroma_sync_btn"):
        with st.spinner("Synchronisiere Dokumente zu ChromaDB..."):
            from src.m09_docs import sync_documents_to_chromadb
            chunks_synced, projects_count, status = sync_documents_to_chromadb()
            st.success(status)

# ============ FOOTER ============
st.markdown("---")
st.markdown("🧪 **Lab Forms** - Experimentelle UI-Patterns für moderne Streamlit-Apps")
