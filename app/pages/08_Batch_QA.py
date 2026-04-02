# app/pages/08_Batch_QA.py
import streamlit as st
import pandas as pd
import json
import sys
from pathlib import Path
from io import BytesIO

# Check for optional dependencies
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m01_config import get_settings
from src.m03_db import init_db, get_session, Document, DocumentChunk
from src.m06_ui import render_global_llm_settings
from src.m07_projects import list_projects_df, load_project, get_project_roles
from src.m08_llm import try_models_with_messages
from src.m09_rag import retrieve_relevant_chunks_hybrid, build_rag_context_from_search, deduplicate_results
from src.m09_docs import get_project_documents
from sqlmodel import select

S = get_settings()
init_db()

st.set_page_config(page_title="Fragen-Batch", page_icon="🔄", layout="wide")

with st.sidebar:
    render_global_llm_settings()
    st.markdown("---")

st.title("🔄 Fragen-Batch-Beantworter")
st.markdown("**Beantworte große Mengen strukturierter Fragen (aus CSV) automatisch mit KI + RAG**")

# Session State initialisierung
st.session_state.setdefault("batch_project_key", None)
st.session_state.setdefault("batch_csv_doc_id", None)
st.session_state.setdefault("batch_results", None)
st.session_state.setdefault("batch_role_mode", "all_merged")
st.session_state.setdefault("batch_selected_roles", [])

# ============ SCHRITT 1: PROJEKT WÄHLEN ============
st.markdown("### 1️⃣ Projekt wählen")

try:
    projects_df = list_projects_df(include_deleted=False)
    has_projects = projects_df is not None and not projects_df.empty
except:
    has_projects = False

if not has_projects:
    st.warning("⚠️ **Keine Projekte vorhanden**")
    st.info("Bitte erstellen Sie zuerst ein Projekt in **Stammdaten → Projekte**.")
    st.stop()

project_options = {}
for _, row in projects_df.iterrows():
    key = row['Key']
    title = row['Titel']
    project_options[key] = f"{title}"

selected_project = st.selectbox(
    "Projekt",
    options=list(project_options.keys()),
    format_func=lambda x: project_options.get(x, x),
    key="batch_project_select"
)

if selected_project:
    st.session_state["batch_project_key"] = selected_project
    proj_obj, message = load_project(selected_project)
    if proj_obj:
        st.success(f"✅ Projekt geladen: **{proj_obj.title}**")
    else:
        st.error(f"❌ Projekt konnte nicht geladen werden: {message}")
        st.stop()
else:
    st.stop()

# ============ SCHRITT 2: CSV-DOKUMENT WÄHLEN ============
st.markdown("---")
st.markdown("### 2️⃣ CSV-Dokument wählen")
st.caption("Wählen Sie eine hochgeladene CSV-Datei mit Ihren Fragen (muss Spalten: Nr, Lieferant, Frage enthalten)")

# Hole alle Dokumente des Projekts
project_docs = get_project_documents(selected_project)
csv_docs = [doc for doc in project_docs if doc.filename.lower().endswith(".csv")]

if not csv_docs:
    st.warning("⚠️ **Keine CSV-Dateien in diesem Projekt gefunden**")
    st.info("Bitte laden Sie zuerst eine CSV-Datei im **Stammdaten → Dokumente** Tab hoch.")
    st.stop()

csv_doc_options = {doc.id: f"{doc.filename} ({doc.chunk_count} Zeilen)" for doc in csv_docs}
selected_csv_id = st.selectbox(
    "CSV-Dokument",
    options=list(csv_doc_options.keys()),
    format_func=lambda x: csv_doc_options.get(x, x),
    key="batch_csv_select"
)

if selected_csv_id:
    st.session_state["batch_csv_doc_id"] = selected_csv_id
    selected_csv = next((d for d in csv_docs if d.id == selected_csv_id), None)
    if selected_csv:
        st.success(f"✅ CSV geladen: **{selected_csv.filename}** ({selected_csv.chunk_count} Fragen)")
    else:
        st.error("❌ CSV konnte nicht geladen werden")
        st.stop()
else:
    st.stop()

# ============ SCHRITT 3: ROLLEN WÄHLEN ============
st.markdown("---")
st.markdown("### 3️⃣ Rollen wählen")
st.caption("Wählen Sie aus welchen Rollen-Perspektiven die Fragen beantwortet werden sollen")

# Lade Rollen des Projekts
project_roles = get_project_roles(selected_project)
selected_roles = []
role_mode = "all_merged"  # Default

if not project_roles:
    st.warning("⚠️ Diesem Projekt sind keine Rollen zugeordnet")
    st.info("Sie können trotzdem fortfahren (ohne Rollen-Kontext) oder Rollen in **Stammdaten → Projekte** zuordnen.")
    role_mode = "none"
else:
    role_options = {role.key: f"{role.title}" for role in project_roles}
    
    role_mode = st.radio(
        "Rollen-Modus",
        options=["all_merged", "individual", "single"],
        format_func=lambda x: {
            "all_merged": "🎭 Alle Rollen als Sammelrolle (eine fusionierte Antwort pro Frage)",
            "individual": "👥 Mehrere einzelne Rollen (separate Antwort pro Rolle pro Frage)",
            "single": "👤 Eine einzelne Rolle (eine Antwort pro Frage aus dieser Perspektive)"
        }[x],
        index=0,
        key="role_mode_select"
    )
    
    if role_mode == "all_merged":
        st.info("✨ **Alle Rollen vereint**: Die KI berücksichtigt alle Rollen-Perspektiven, gibt aber eine einzige fusionierte Antwort.")
        selected_roles = [role.key for role in project_roles]  # Alle
        
    elif role_mode == "individual":
        st.info("📊 **Mehrere Spalten**: Pro Frage werden mehrere Antworten generiert (eine pro Rolle)")
        selected_role_keys = st.multiselect(
            "Rollen auswählen",
            options=list(role_options.keys()),
            format_func=lambda x: role_options.get(x, x),
            default=list(role_options.keys())[:3] if len(role_options) > 3 else list(role_options.keys()),
            key="roles_multiselect"
        )
        selected_roles = selected_role_keys
        
        if not selected_roles:
            st.warning("⚠️ Bitte mindestens eine Rolle auswählen")
            
    else:  # single
        st.info("🎯 **Eine Perspektive**: Alle Fragen werden aus Sicht dieser einen Rolle beantwortet")
        selected_role_key = st.selectbox(
            "Rolle auswählen",
            options=list(role_options.keys()),
            format_func=lambda x: role_options.get(x, x),
            key="role_single_select"
        )
        selected_roles = [selected_role_key]

# ============ SCHRITT 4: OPTIONALE EINSTELLUNGEN ============
st.markdown("---")
st.markdown("### 4️⃣ Einstellungen")

col1, col2 = st.columns(2)

with col1:
    use_project_context = st.checkbox(
        "Projekt-Kontext einbeziehen",
        value=True,
        help="Fügt Projektbeschreibung, Rollen und Tasks in den Prompt ein"
    )

with col2:
    answer_style = st.selectbox(
        "Antwort-Stil",
        options=["Sachlich & präzise", "Ausführlich & erkärend", "Kurz & bündig"],
        index=0
    )

# ============ SCHRITT 5: BATCH STARTEN ============
st.markdown("---")
st.markdown("### 5️⃣ Batch starten")

if role_mode in ["individual", "single"] and not selected_roles:
    st.warning("⚠️ Bitte wählen Sie mindestens eine Rolle aus")
    st.stop()

# Sanity check für "none" Modus
if role_mode == "none":
    st.info("ℹ️ **Ohne Rollen-Kontext**: Fragen werden generisch beantwortet (keine rollenspezifische Perspektive)")

if st.button("🚀 Batch-Verarbeitung starten", type="primary", width="stretch"):
    
    # Lade CSV-Chunks aus DB
    with get_session() as session:
        chunks = session.exec(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == selected_csv_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
    
    if not chunks:
        st.error("❌ Keine Daten in der CSV gefunden")
        st.stop()
    
    # Parse JSON aus chunks
    questions = []
    for chunk in chunks:
        try:
            row_data = json.loads(chunk.chunk_text)
            questions.append(row_data)
        except:
            st.warning(f"⚠️ Zeile {chunk.chunk_index} konnte nicht gelesen werden")
    
    if not questions:
        st.error("❌ CSV enthält keine gültigen Daten")
        st.stop()
    
    st.info(f"📊 Verarbeite **{len(questions)} Fragen**...")
    
    # Progress Bar
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    results = []
    
    # System Prompt vorbereiten
    style_instructions = {
        "Sachlich & präzise": "Antworte sachlich, präzise und auf den Punkt.",
        "Ausführlich & erkärend": "Antworte ausführlich und erkläre alle relevanten Details.",
        "Kurz & bündig": "Antworte so kurz wie möglich, max. 2-3 Sätze."
    }
    
    project_context_text = ""
    if use_project_context:
        project_context_text = f"\n\nPROJEKT: {proj_obj.title}\nBESCHREIBUNG: {proj_obj.body or 'Keine Beschreibung'}"
    
    # Batch-Loop
    for idx, question_data in enumerate(questions):
        question_text = question_data.get("Frage", "")
        nr = question_data.get("Nr", idx+1)
        lieferant = question_data.get("Lieferant", "")
        
        result_row = {
            "Nr": nr,
            "Lieferant": lieferant,
            "Frage": question_text
        }
        
        # RAG: Hole relevante Dokument-Chunks (einmal pro Frage, für alle Rollen gleich)
        rag_context = ""
        if st.session_state.get("global_rag_enabled", True):
            rag_top_k = st.session_state.get("global_llm_rag_top_k", 5)
            rag_threshold = st.session_state.get("global_rag_similarity_threshold", 0.5)
            
            rag_results = retrieve_relevant_chunks_hybrid(
                question_text,
                project_key=selected_project,
                limit=rag_top_k,
                threshold=rag_threshold
            )
            rag_results = deduplicate_results(rag_results)
            rag_context = build_rag_context_from_search(rag_results)
        
        # Je nach Rollen-Modus: 1 oder N Antworten generieren
        if role_mode == "none":
            # Keine Rollen: generische Antwort
            status_text.text(f"Verarbeite Frage {idx+1}/{len(questions)}: Nr. {nr}")
            
            system_prompt = f"""Du bist ein technischer Berater der Fragen zu einem Pflichtenheft beantwortet.

{style_instructions[answer_style]}{project_context_text}

RELEVANTE DOKUMENTE (aus Pflichtenheft):
{rag_context}

AUFGABE: Beantworte die folgende Frage präzise und vollständig basierend auf den bereitgestellten Dokumenten."""
            
            user_prompt = f"Frage von {lieferant} (Nr. {nr}):\n{question_text}"
            
            try:
                response = try_models_with_messages(
                    provider=st.session_state.get("global_llm_provider"),
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    max_tokens=2000,
                    temperature=st.session_state.get("global_llm_temperature", 0.7),
                    model=st.session_state.get("global_llm_model")
                )
                answer = response if response else "[Keine Antwort generiert]"
            except Exception as e:
                answer = f"[Fehler: {str(e)}]"
            
            result_row["Antwort"] = answer
            
        elif role_mode == "all_merged":
            # Alle Rollen als Sammelrolle
            status_text.text(f"Verarbeite Frage {idx+1}/{len(questions)}: Nr. {nr} (Sammelrolle)")
            
            # Baue Rollen-Kontext auf
            roles_context = "\n".join([
                f"- {role.title}: {role.description or '(keine Beschreibung)'}"
                for role in project_roles if role.key in selected_roles
            ])
            
            system_prompt = f"""Du bist ein technischer Berater-Team das Fragen zu einem Pflichtenheft beantwortet.

{style_instructions[answer_style]}{project_context_text}

TEAM-PERSPEKTIVEN (berücksichtige alle):
{roles_context}

RELEVANTE DOKUMENTE (aus Pflichtenheft):
{rag_context}

AUFGABE: Beantworte die folgende Frage vollständig, indem du die Perspektiven ALLER Team-Rollen berücksichtigst und in EINE fusionierte Antwort einbaust. Die Antwort soll ausgewogen sein und keine Rolle offensichtlich einzeln nennen."""
            
            user_prompt = f"Frage von {lieferant} (Nr. {nr}):\n{question_text}"
            
            try:
                response = try_models_with_messages(
                    provider=st.session_state.get("global_llm_provider"),
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    max_tokens=2000,
                    temperature=st.session_state.get("global_llm_temperature", 0.7),
                    model=st.session_state.get("global_llm_model")
                )
                answer = response if response else "[Keine Antwort generiert]"
            except Exception as e:
                answer = f"[Fehler: {str(e)}]"
            
            result_row["Antwort"] = answer
            
        else:
            # Individual oder Single: Pro Rolle eine Antwort
            for role_key in selected_roles:
                role_obj = next((r for r in project_roles if r.key == role_key), None)
                if not role_obj:
                    continue
                
                status_text.text(f"Verarbeite Frage {idx+1}/{len(questions)}: Nr. {nr} ({role_obj.title})")
                
                role_description = role_obj.description or f"Rolle: {role_obj.title}"
                
                system_prompt = f"""Du bist ein technischer Berater in der Rolle "{role_obj.title}".

DEINE ROLLE:
{role_description}

{style_instructions[answer_style]}{project_context_text}

RELEVANTE DOKUMENTE (aus Pflichtenheft):
{rag_context}

AUFGABE: Beantworte die folgende Frage AUS DER PERSPEKTIVE deiner Rolle. Fokussiere dich auf die Aspekte die zu deinem Verantwortungsbereich gehören."""
                
                user_prompt = f"Frage von {lieferant} (Nr. {nr}):\n{question_text}"
                
                try:
                    response = try_models_with_messages(
                        provider=st.session_state.get("global_llm_provider"),
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_prompt}],
                        max_tokens=2000,
                        temperature=st.session_state.get("global_llm_temperature", 0.7),
                        model=st.session_state.get("global_llm_model")
                    )
                    answer = response if response else "[Keine Antwort generiert]"
                except Exception as e:
                    answer = f"[Fehler: {str(e)}]"
                
                # Spaltenname dynamisch
                if role_mode == "single":
                    result_row["Antwort"] = answer
                else:  # individual
                    result_row[f"Antwort_{role_obj.title}"] = answer
        
        results.append(result_row)
        progress_bar.progress((idx + 1) / len(questions))
    
    status_text.text("✅ Batch abgeschlossen!")
    st.session_state["batch_results"] = results
    st.success(f"🎉 **{len(results)} Fragen erfolgreich beantwortet!**")

# ============ SCHRITT 6: ERGEBNISSE ANZEIGEN ============
if st.session_state.get("batch_results"):
    st.markdown("---")
    st.markdown("### 6️⃣ Ergebnisse")
    
    results_df = pd.DataFrame(st.session_state["batch_results"])
    
    # Dynamische Spalten-Konfiguration je nach Rollen-Modus
    column_config = {
        "Nr": st.column_config.NumberColumn("Nr", width="small"),
        "Lieferant": st.column_config.TextColumn("Lieferant", width="medium"),
        "Frage": st.column_config.TextColumn("Frage", width="large"),
    }
    
    # Füge Antwort-Spalten hinzu (dynamisch)
    answer_columns = [col for col in results_df.columns if col.startswith("Antwort")]
    for col in answer_columns:
        column_config[col] = st.column_config.TextColumn(col, width="large")
    
    # Editierbare Tabelle
    edited_df = st.data_editor(
        results_df,
        width="stretch",
        num_rows="dynamic",
        column_config=column_config,
        key="batch_results_editor"
    )
    
    # Download-Button
    st.markdown("---")
    
    if HAS_OPENPYXL:
        col1, col2 = st.columns([1, 1])
    else:
        col1 = st.columns(1)[0]
        st.caption("ℹ️ Excel-Export nicht verfügbar (openpyxl fehlt). Installieren: `pip install openpyxl`")
    
    with col1:
        # CSV Download
        csv_buffer = BytesIO()
        edited_df.to_csv(csv_buffer, index=False, sep=";", encoding="utf-8-sig")
        csv_buffer.seek(0)
        
        st.download_button(
            label="📥 Download als CSV",
            data=csv_buffer.getvalue(),
            file_name=f"batch_antworten_{selected_project}.csv",
            mime="text/csv",
            width="stretch"
        )
    
    if HAS_OPENPYXL:
        with col2:
            # Excel Download
            excel_buffer = BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                edited_df.to_excel(writer, index=False, sheet_name="Antworten")
            excel_buffer.seek(0)
            
            st.download_button(
                label="📥 Download als Excel",
                data=excel_buffer.getvalue(),
                file_name=f"batch_antworten_{selected_project}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch"
            )
