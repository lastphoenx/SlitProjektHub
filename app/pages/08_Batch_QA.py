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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m01_config import get_settings
from src.m03_db import init_db, get_session, Document, DocumentChunk
from src.m06_ui import render_global_llm_settings
from src.m07_projects import list_projects_df, load_project, get_project_roles
from src.m08_llm import try_models_with_messages
from src.m09_rag import retrieve_relevant_chunks_hybrid, build_rag_context_from_search, deduplicate_results, get_all_documents_with_best_scores, format_chunk_preview
def render_retrieval_debug(debug_payload: dict | None, title: str = "🔎 Retrieval-Debug (BM25 / Semantic / Final)"):
    if not debug_payload:
        st.caption("Keine Retriever-Debugdaten verfügbar")
        return

    with st.expander(title, expanded=False):
        query_text = debug_payload.get("query", "")
        if query_text:
            st.caption(f"Query: {query_text[:220]}{'...' if len(query_text) > 220 else ''}")

        sections = [
            ("keyword_candidates", "🟡 Keyword/BM25"),
            ("semantic_candidates", "🔵 Semantic"),
            ("final_candidates", "🟢 Final verwendet"),
        ]

        for key, label in sections:
            rows = debug_payload.get(key, [])
            st.markdown(f"**{label}:**")
            if not rows:
                st.caption("Keine Einträge")
                continue
            for idx, row in enumerate(rows, 1):
                st.text(
                    f"{idx}. {row.get('filename','?')[:34]} | cls={row.get('classification','?')[:18]} | "
                    f"comb={row.get('combined_score',0):.3f} | sem={row.get('similarity',0):.3f} | "
                    f"kw={row.get('normalized_match_score',0):.3f} | raw={row.get('raw_bm25_score',0):.3f} | "
                    f"idf={row.get('keyword_idf_score',0):.3f} | cov={row.get('keyword_coverage',0):.3f} | "
                    f"hits={row.get('priority_hits',0)}"
                )
                if row.get("matched_terms"):
                    st.caption("terms: " + ", ".join(row.get("matched_terms", [])))
                st.caption(row.get("text_preview", ""))

from src.m09_docs import get_project_documents
from src.m13_ki_detector import analyze_all_vendors, analyze_vendor_with_ai
from sqlmodel import select

S = get_settings()


def _strip_contextual_prefix(chunk_text: str) -> str:
    """
    Entfernt Contextual Prefix wenn vorhanden.
    Format: [CSV | filename | Frage X]\n{JSON}
    Returns: Nur den JSON-Teil (oder original text wenn kein Prefix)
    """
    if chunk_text.startswith("["):
        newline_pos = chunk_text.find("\n")
        if newline_pos > 0:
            return chunk_text[newline_pos + 1:]
    return chunk_text


def _get_csv_field(data: dict, field_name: str, fallback="") -> str:
    """
    Robuste Feldextraktion aus CSV-JSON.
    Probiert alle Case-Varianten mit/ohne Punkt, plus semantische Aliase.
    
    Beispiel: 
    - field_name="Nr" findet: "Nr", "nr", "NR", "Nr.", "Nummer", "Number", "ID", "id"
    """
    # Standard-Varianten
    variants = [
        field_name,                      # "Nr"
        field_name.lower(),              # "nr"
        field_name.upper(),              # "NR"
        field_name + ".",                # "Nr."
        field_name.lower() + ".",        # "nr."
        field_name.upper() + ".",        # "NR."
    ]
    
    # Semantische Aliase für häufige Felder
    aliases = {
        "Nr": ["Nummer", "nummer", "NUMMER", "Number", "number", "NUMBER", "No", "no", "NO", "ID", "id", "Id"],
        "Frage": ["Question", "question", "QUESTION", "Text", "text", "TEXT"],
        "Lieferant": ["Anbieter", "anbieter", "ANBIETER", "Vendor", "vendor", "VENDOR", "Supplier", "supplier"],
    }
    
    # Füge Aliase hinzu wenn Feld bekannt
    if field_name in aliases:
        variants.extend(aliases[field_name])
    
    for v in variants:
        if v in data:
            val = data[v]
            # String-Konvertierung wenn nötig (für numerische IDs)
            return str(val) if val is not None else fallback
    
    return fallback
init_db()

st.set_page_config(page_title="Fragen-Batch", page_icon="🔄", layout="wide")

with st.sidebar:
    render_global_llm_settings()
    st.markdown("---")

st.title("🔄 Fragen-Batch-Beantworter")
st.markdown("**Beantworte große Mengen strukturierter Fragen (aus CSV) automatisch mit KI + RAG**")

st.info(
    "ℹ️ **Wie funktioniert RAG hier?** Für jede Frage werden relevante Abschnitte aus den **Projekt-zugeordneten Dokumenten** "
    "gesucht (via Embedding-Suche) und als Kontext in den Prompt eingefügt. Die KI kann so spezifische Antworten "
    "basierend auf Ihrem Pflichtenheft generieren.\n\n"
    "**🔍 Prompt-Vorschau:** Zeigt den exakten Prompt wie ihn das LLM sieht. Die **RAG-Diagnostics** listen ALLE Projekt-Dokumente "
    "mit ihren Relevanz-Scores auf — auch jene die NICHT in den Top-K kamen. So sehen Sie transparent welche Dokumente geprüft wurden "
    "und warum sie ein-/ausgeschlossen wurden (z.B. 'Score 38% < Threshold 45%')."
)

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

# ============ KI-ERKENNUNG (optional, nach CSV-Auswahl) ============
st.markdown("---")
st.markdown("### 🤖 KI-Erkennung (optional)")
st.caption("Analysiert die Fragen auf typische Merkmale KI-generierter Texte – pro Anbieter und gesamt")

# Session State für KI-Analyse (persistiert über Reruns)
st.session_state.setdefault("ki_analysis_result", None)
st.session_state.setdefault("ki_analysis_csv_id", None)
st.session_state.setdefault("ki_raw_questions", [])
st.session_state.setdefault("ki_ai_results", {})

# Analyse-Ergebnis zurücksetzen wenn andere CSV gewählt
if st.session_state["ki_analysis_csv_id"] != selected_csv_id:
    st.session_state["ki_analysis_result"] = None
    st.session_state["ki_raw_questions"] = []
    st.session_state["ki_ai_results"] = {}

if st.button("🔍 Fragen analysieren", help="Analysiert alle Fragen aus der CSV auf KI-Muster (ohne API-Aufrufe)"):
    with get_session() as session:
        ki_chunks_raw = session.exec(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == selected_csv_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()

    ki_questions = []
    for chunk in ki_chunks_raw:
        try:
            clean_text = _strip_contextual_prefix(chunk.chunk_text)
            ki_questions.append(json.loads(clean_text))
        except Exception:
            pass

    if not ki_questions:
        st.warning("⚠️ Keine Daten in der CSV gefunden")
    else:
        with st.spinner("Analysiere…"):
            ki_result = analyze_all_vendors(ki_questions)
        st.session_state["ki_analysis_result"] = ki_result
        st.session_state["ki_raw_questions"] = ki_questions
        st.session_state["ki_analysis_csv_id"] = selected_csv_id
        st.session_state["ki_ai_results"] = {}  # AI-Ergebnisse zurücksetzen bei neuer Analyse

# Ergebnis anzeigen (auch nach Rerun durch zweiten Button)
if st.session_state["ki_analysis_result"] is not None:
    ki_result = st.session_state["ki_analysis_result"]
    ki_questions = st.session_state["ki_raw_questions"]

    total_q = ki_result["total_questions"]
    total_v = ki_result["total_vendors"]
    overall = ki_result["overall_ki_score"]
    ki_v_count = ki_result["ki_vendors_count"]

    # Gesamtfazit
    if overall >= 0.70:
        overall_verdict = "🤖 Sehr hoher KI-Index – wahrscheinlich grossflächiger ChatGPT-Einsatz"
        overall_color = "error"
    elif overall >= 0.45:
        overall_verdict = "⚠️ Erhöhter KI-Index – teilweiser KI-Einsatz wahrscheinlich"
        overall_color = "warning"
    elif overall >= 0.25:
        overall_verdict = "🔍 Moderater KI-Index – vereinzelte Hinweise"
        overall_color = "info"
    else:
        overall_verdict = "✅ Niedriger KI-Index – Fragen wirken überwiegend manuell verfasst"
        overall_color = "success"

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Fragen total", total_q)
    col_b.metric("Anbieter", total_v)
    col_c.metric("Gesamt-KI-Index", f"{overall:.0%}")
    col_d.metric("Anbieter mit KI-Verdacht", f"{ki_v_count} / {total_v}")

    getattr(st, overall_color)(overall_verdict)

    # Ranking-Tabelle
    rows = []
    for vendor, res in ki_result["ranking"]:
        rows.append({
            "Lieferant": vendor,
            "Fragen": res["count"],
            "KI-Score": f"{res['ki_score']:.0%}",
            "Strukturrefs": f"{res['structural_refs_ratio']:.0%}",
            "KI-Floskeln": f"{res['ki_phrases_ratio']:.0%}",
            "Übergänge": f"{res['transition_phrases_ratio']:.0%}",
            "Einstiege": f"{res['uniform_openers_ratio']:.0%}",
            "Bullets": f"{res['bullet_sublists_ratio']:.0%}",
            "Aufzähl.": f"{res['exhaustive_enum_ratio']:.0%}",
            "Informal ↓": f"{res['informal_markers_ratio']:.0%}",
            "Burstiness": f"{res['sentence_burstiness_score']:.0%}",
            "Volumen": f"{res['volume_signal']:.0%}",
            "Ø Länge": f"{res['avg_length']:.0f} Z.",
            "Urteil": f"{res['verdict_emoji']} {res['verdict']}",
        })

    ranking_df = pd.DataFrame(rows)
    st.dataframe(ranking_df, width="stretch", hide_index=True)

    with st.expander("ℹ️ Wie wird der KI-Score berechnet?"):
        st.markdown(
            """
Der KI-Score ist ein gewichteter Heuristik-Index (0–100%) basierend auf zehn textbasierten Merkmalen:

| Merkmal | Gewicht | Erklärung |
|---|---|---|
| **Strukturrefs** | 20% | Kapitel X.Y / Abschnitt X / Anforderung X |
| **KI-Floskeln** | 15% | "Bitte beschreiben Sie…", "Wie stellen Sie sicher…" |
| **Bullets** | 12% | Bullet-/Unterlisten innerhalb einer Frage |
| **Aufzähl.** | 10% | Erschöpfende Aufzählungen: "A, B, C und D" |
| **Übergänge** | 10% | "Darüber hinaus", "Des Weiteren", "Im Hinblick auf…" |
| **Einstiege** | 10% | Gleichartige Satzeinstiege über alle Fragen |
| **Volumen** | 10% | Überdurchschnittlich viele Fragen |
| **Burstiness** | 8% | Gleichmässige Satzlängen (ab 5 Fragen / 8 Sätzen) |
| **Länge** | 5% | Uniforme Fragelänge (niedriger CV) |
| **Informal ↓** | −20% | Abzug bei informellen Markern ("eigentlich", "bei uns", ...) |

Ein Score ≥ 70% gilt als **sehr wahrscheinlich KI-generiert**, ≥ 45% als **verdächtig**.
Kein Werkzeug ist perfekt – der Score ist ein Hinweis, kein Beweis.
            """
        )

    # --- Optionale KI-Tiefenanalyse ---
    st.markdown("#### 🧠 KI-gestützte Tiefenanalyse (optional)")
    st.caption("Schickt eine Stichprobe der Fragen an OpenAI/Anthropic für eine zweite Meinung. OpenAI verwendet immer gpt-4o-mini.")

    vendor_list = [v for v, _ in ki_result["ranking"]]

    ai_mode = st.radio(
        "Analyse-Modus",
        options=["single", "all"],
        format_func=lambda x: "👤 Einzelner Anbieter" if x == "single" else "👥 Alle Anbieter",
        horizontal=True,
        key="ki_ai_mode"
    )

    if ai_mode == "single":
        selected_ai_vendor = st.selectbox(
            "Anbieter für Tiefenanalyse wählen",
            options=vendor_list,
            key="ki_ai_vendor_select"
        )

        if st.button("🔬 KI-Tiefenanalyse starten", key="ki_ai_analyze_btn"):
            all_vendor_qs = [
                str(chunk.get("Frage", "")).strip()
                for chunk in ki_questions
                if str(chunk.get("Lieferant", "")).strip() == selected_ai_vendor
                and str(chunk.get("Frage", "")).strip()
            ]
            with st.spinner(f"Analysiere {selected_ai_vendor} mit {st.session_state.get('global_llm_provider', 'LLM')}…"):
                ai_result = analyze_vendor_with_ai(
                    questions=all_vendor_qs,
                    vendor=selected_ai_vendor,
                    provider=st.session_state.get("global_llm_provider"),
                    model=st.session_state.get("global_llm_model"),
                    temperature=0.2,
                )
            st.session_state["ki_ai_results"][selected_ai_vendor] = ai_result

        if selected_ai_vendor in st.session_state["ki_ai_results"]:
            ai_res = st.session_state["ki_ai_results"][selected_ai_vendor]
            if "error" in ai_res:
                st.error(f"Fehler bei AI-Analyse: {ai_res['error']}")
            else:
                ai_score = ai_res.get("ki_score", "?")
                confidence = ai_res.get("confidence", "?")
                fazit = ai_res.get("fazit", "")
                merkmale = ai_res.get("hauptmerkmale", [])
                heur_score = ki_result["vendors"][selected_ai_vendor]["ki_score"]

                col_x, col_y = st.columns(2)
                col_x.metric("KI-Score (Heuristik)", f"{heur_score:.0%}")
                col_y.metric("KI-Score (AI-Urteil)", f"{ai_score}%", help=f"Konfidenz: {confidence}")
                if fazit:
                    st.info(f"**Fazit (AI):** {fazit}")
                if merkmale:
                    st.markdown("**Erkannte Hauptmerkmale:**")
                    for m in merkmale:
                        st.markdown(f"- {m}")

    else:  # all vendors
        already_done = [v for v in vendor_list if v in st.session_state["ki_ai_results"]]
        if already_done:
            st.caption(f"✅ Bereits analysiert: {len(already_done)}/{len(vendor_list)} Anbieter")

        if st.button("🔬 Alle Anbieter analysieren", key="ki_ai_all_btn"):
            all_progress = st.progress(0)
            all_status = st.empty()
            provider = st.session_state.get("global_llm_provider")
            model = st.session_state.get("global_llm_model")
            for i, vendor in enumerate(vendor_list):
                all_status.text(f"Analysiere {vendor} ({i+1}/{len(vendor_list)})…")
                vendor_qs = [
                    str(chunk.get("Frage", "")).strip()
                    for chunk in ki_questions
                    if str(chunk.get("Lieferant", "")).strip() == vendor
                    and str(chunk.get("Frage", "")).strip()
                ]
                ai_result = analyze_vendor_with_ai(
                    questions=vendor_qs,
                    vendor=vendor,
                    provider=provider,
                    model=model,
                    temperature=0.2,
                )
                st.session_state["ki_ai_results"][vendor] = ai_result
                all_progress.progress((i + 1) / len(vendor_list))
            all_status.text("✅ Alle Anbieter analysiert!")

        # Kombinierte Tabelle: Heuristik vs AI
        if st.session_state["ki_ai_results"]:
            _MERKMAL_THRESHOLDS = [
                ("structural_refs_ratio",    0.15, "Struct.Refs"),
                ("ki_phrases_ratio",         0.10, "KI-Floskeln"),
                ("bullet_sublists_ratio",    0.15, "Bullets"),
                ("exhaustive_enum_ratio",    0.20, "Aufzähl."),
                ("transition_phrases_ratio", 0.15, "Übergänge"),
                ("uniform_openers_ratio",    0.30, "Einstiege"),
                ("sentence_burstiness_score",0.40, "Burstiness"),
                ("volume_signal",            0.50, "Volumen"),
            ]
            combined_rows = []
            for vendor in vendor_list:
                heur = ki_result["vendors"].get(vendor, {})
                ai_res = st.session_state["ki_ai_results"].get(vendor)
                triggered = [label for key, thresh, label in _MERKMAL_THRESHOLDS if heur.get(key, 0) >= thresh]
                row = {
                    "Lieferant": vendor,
                    "Heuristik-%": f"{heur.get('ki_score', 0):.0%}",
                    "Heuristik-Urteil": f"{heur.get('verdict_emoji', '')} {heur.get('verdict', '')}",
                    "Auffällige Merkmale": ", ".join(triggered) if triggered else "—",
                }
                if ai_res and "error" not in ai_res:
                    ai_score_val = ai_res.get("ki_score", 0) or 0
                    if ai_score_val >= 70:
                        ai_urteil = "🤖 KI-generiert"
                    elif ai_score_val >= 45:
                        ai_urteil = "⚠️ Verdächtig"
                    elif ai_score_val >= 25:
                        ai_urteil = "🔍 Teilweise KI"
                    else:
                        ai_urteil = "✅ Manuell"
                    row["AI-%"] = f"{ai_score_val}%"
                    row["AI-Urteil"] = ai_urteil
                    row["Konfidenz"] = ai_res.get("confidence", "?")
                    row["Fazit (AI)"] = ai_res.get("fazit", "")
                else:
                    row["AI-%"] = "—"
                    row["AI-Urteil"] = "—"
                    row["Konfidenz"] = "—"
                    row["Fazit (AI)"] = ai_res.get("error", "") if ai_res else "nicht analysiert"
                combined_rows.append(row)
            st.dataframe(pd.DataFrame(combined_rows), width="stretch", hide_index=True)

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
        help="Fügt Projekttitel und -beschreibung in den Prompt ein (Tasks werden nicht verwendet)"
    )

with col2:
    answer_style = st.selectbox(
        "Antwort-Stil",
        options=["Sachlich & präzise", "Ausführlich & erkärend", "Kurz & bündig"],
        index=0
    )

# style_instructions hier definiert, damit Prompt-Vorschau und Batch-Loop darauf zugreifen können
style_instructions = {
    "Sachlich & präzise": "Antworte sachlich, präzise und auf den Punkt.",
    "Ausführlich & erkärend": "Antworte ausführlich und erkläre alle relevanten Details.",
    "Kurz & bündig": "Antworte so kurz wie möglich, max. 2-3 Sätze."
}

# ============ PROMPT-VORSCHAU ============
st.markdown("---")
st.markdown("### 🔍 Prompt-Vorschau")
st.caption("Zeigt den exakten Prompt, den das Modell für eine Frage erhält – inkl. RAG-Chunks und Rollen-Kontext.")

_prev_col1, _prev_col2, _prev_col3 = st.columns([1, 1, 1])
with _prev_col1:
    _preview_style = st.selectbox(
        "Tonalität für Vorschau",
        options=list(style_instructions.keys()),
        index=list(style_instructions.keys()).index(answer_style),
        key="pp_style_sel"
    )
with _prev_col2:
    _preview_role_options = ["(wie Batch-Einstellung)"] + [r.title for r in project_roles]
    _preview_role_sel = st.selectbox(
        "Rolle für Vorschau",
        options=_preview_role_options,
        key="pp_role_sel"
    )
with _prev_col3:
    _preview_frage_nr = st.number_input("Frage Nr.", min_value=1, value=1, step=1, key="pp_frage_nr")

if st.button("📋 Prompt-Vorschau anzeigen", key="prompt_preview_btn"):
    st.session_state["_show_prompt_preview"] = True

if st.session_state.get("_show_prompt_preview") and selected_csv_id:
    try:
        with get_session() as _prev_session:
            _all_chunks = _prev_session.exec(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == selected_csv_id)
                .order_by(DocumentChunk.chunk_index)
            ).all()
        
        # Parse alle Chunks in ein Dict: {nr -> chunk}
        chunks_by_nr = {}
        parse_errors = []
        
        for _c in _all_chunks:
            try:
                clean_text = _strip_contextual_prefix(_c.chunk_text)
                _cd = json.loads(clean_text)
                
                # Extrahiere Nr (robust: alle Case-Varianten mit/ohne Punkt)
                chunk_nr_raw = _get_csv_field(_cd, "Nr")
                
                if not chunk_nr_raw or chunk_nr_raw == "":
                    continue  # Skip Chunks ohne Nr
                
                # Konvertiere zu Int (bevorzugt) oder String
                try:
                    chunk_nr = int(float(str(chunk_nr_raw).strip()))
                except (ValueError, TypeError):
                    chunk_nr = str(chunk_nr_raw).strip()
                
                chunks_by_nr[chunk_nr] = (_c, _cd)
                
            except json.JSONDecodeError as e:
                parse_errors.append(f"Chunk #{_c.id}: JSON-Parse-Fehler")
            except Exception as e:
                parse_errors.append(f"Chunk #{_c.id}: {type(e).__name__}")
        
        # Lookup: Finde gewünschte Frage
        target_nr = int(_preview_frage_nr)
        
        if target_nr in chunks_by_nr:
            _first_chunk, _q1 = chunks_by_nr[target_nr]
        else:
            # Nicht gefunden: Zeige verfügbare Nummern
            available_nrs = sorted(chunks_by_nr.keys())[:20]  # Erste 20
            st.error(f"❌ Frage Nr. {target_nr} existiert nicht im CSV!")
            st.info(f"Verfügbare Nummern (Auswahl): {', '.join(map(str, available_nrs))}{'...' if len(chunks_by_nr) > 20 else ''}")
            _first_chunk = None
        
        # Parse-Fehler anzeigen (wenn vorhanden)
        if parse_errors and len(parse_errors) <= 5:
            with st.expander(f"⚠️ {len(parse_errors)} Parse-Fehler", expanded=False):
                for err in parse_errors:
                    st.caption(err)
        
        if _first_chunk:
            _q1_text = _get_csv_field(_q1, "Frage", "")
            _q1_nr = _get_csv_field(_q1, "Nr", str(_preview_frage_nr))
            _q1_lief = _get_csv_field(_q1, "Lieferant", "")

            _proj_ctx = ""
            if use_project_context and proj_obj:
                _proj_ctx = f"\n\nPROJEKT: {proj_obj.title}\nBESCHREIBUNG: {proj_obj.description or 'Keine Beschreibung'}"

            _rag_prev = ""
            _rag_sources = "(RAG deaktiviert)"
            _rag_diagnostics = []
            _rag_debug = None
            if st.session_state.get("global_rag_enabled", True):
                _rr = retrieve_relevant_chunks_hybrid(
                    _q1_text,
                    project_key=selected_project,
                    limit=st.session_state.get("global_llm_rag_top_k", 5),
                    threshold=st.session_state.get("global_rag_similarity_threshold", 0.5),
                    exclude_classification="FAQ/Fragen-Katalog"  # CSV-Fragendatei aus RAG ausschliessen
                )
                _rr = deduplicate_results(_rr)
                _rag_debug = _rr.get("debug")
                _rag_prev = build_rag_context_from_search(_rr)
                
                # RAG-Quellen (gefundene Chunks)
                if _rr.get("documents"):
                    _rag_sources = "\n".join(
                        f"  • {d.get('filename','?')} ({min(max(d.get('similarity', 0), d.get('match_score', 0)), 1.0):.0%})\n"
                        f"    {format_chunk_preview((d.get('text','') or '').replace(chr(10),' '), max_length=150, query=_q1_text)}"
                        for d in _rr["documents"]
                    )
                else:
                    _rag_sources = "(keine passenden Chunks gefunden)"
                
                # DIAGNOSTICS: Alle Dokumente mit Scores
                _rag_diagnostics = get_all_documents_with_best_scores(
                    _q1_text,
                    project_key=selected_project,
                    threshold=st.session_state.get("global_rag_similarity_threshold", 0.5),
                    exclude_classification="FAQ/Fragen-Katalog"
                )

            _style = style_instructions[_preview_style]

            # Rolle für Preview bestimmen: eigene Auswahl oder Batch-Einstellung
            _prev_role_override = None
            if _preview_role_sel != "(wie Batch-Einstellung)":
                _prev_role_override = next((r for r in project_roles if r.title == _preview_role_sel), None)

            if role_mode == "none" and not _prev_role_override:
                _sys_prev = f"""Du bist ein technischer Berater der Fragen zu einem Pflichtenheft beantwortet.\n\n{_style}{_proj_ctx}\n\nRELEVANTE DOKUMENTE (aus Pflichtenheft):\n{_rag_prev or '(keine RAG-Ergebnisse)'}\n\nAUFGABE: Beantworte die folgende Frage präzise und vollständig basierend auf den bereitgestellten Dokumenten."""
            elif _prev_role_override:
                _sys_prev = f"""Du bist ein technischer Berater in der Rolle "{_prev_role_override.title}".\n\nDEINE ROLLE:\n{_prev_role_override.description or ''}\n\n{_style}{_proj_ctx}\n\nRELEVANTE DOKUMENTE (aus Pflichtenheft):\n{_rag_prev or '(keine RAG-Ergebnisse)'}\n\nAUFGABE: Beantworte die folgende Frage AUS DER PERSPEKTIVE deiner Rolle. Fokussiere dich auf die Aspekte die zu deinem Verantwortungsbereich gehören."""
            elif role_mode == "all_merged":
                _roles_ctx = "\n".join(
                    f"- {r.title}: {r.description or '(keine Beschreibung)'}"
                    for r in project_roles if r.key in selected_roles
                )
                _sys_prev = f"""Du bist ein technisches Berater-Team das Fragen zu einem Pflichtenheft beantwortet.\n\n{_style}{_proj_ctx}\n\nTEAM-PERSPEKTIVEN (berücksichtige alle):\n{_roles_ctx}\n\nRELEVANTE DOKUMENTE (aus Pflichtenheft):\n{_rag_prev or '(keine RAG-Ergebnisse)'}\n\nAUFGABE: Beantworte die folgende Frage vollständig, indem du die Perspektiven ALLER Team-Rollen berücksichtigst und in EINE fusionierte Antwort einbaust. Die Antwort soll ausgewogen sein und keine Rolle offensichtlich einzeln nennen."""
            else:
                _rp_key = selected_roles[0] if selected_roles else None
                _rp_obj = next((r for r in project_roles if r.key == _rp_key), None) if _rp_key else None
                if _rp_obj:
                    _sys_prev = f"""Du bist ein technischer Berater in der Rolle "{_rp_obj.title}".\n\nDEINE ROLLE:\n{_rp_obj.description or ''}\n\n{_style}{_proj_ctx}\n\nRELEVANTE DOKUMENTE (aus Pflichtenheft):\n{_rag_prev or '(keine RAG-Ergebnisse)'}\n\nAUFGABE: Beantworte die folgende Frage AUS DER PERSPEKTIVE deiner Rolle. Fokussiere dich auf die Aspekte die zu deinem Verantwortungsbereich gehören."""
                else:
                    _sys_prev = "(Keine Rolle ausgewählt)"

            _user_prev = f"Frage von {_q1_lief} (Nr. {_q1_nr}):\n{_q1_text}"

            with st.expander(f"📋 Prompt-Vorschau (Frage {_q1_nr}) — so sieht das Modell die Anfrage", expanded=True):
                _pc1, _pc2 = st.columns([3, 1])
                with _pc2:
                    st.caption("**Verwendete RAG-Quellen:**")
                    st.text(_rag_sources)
                    
                    # DIAGNOSTICS: Alle Dokumente mit Scores anzeigen
                    if _rag_diagnostics:
                        with st.expander("🔍 RAG-Diagnostics (alle Dokumente)", expanded=False):
                            st.caption("Zeigt **alle** Projekt-Dokumente mit ihrem besten Score:")
                            
                            # Gruppiere: Included vs. Excluded
                            _included = [d for d in _rag_diagnostics if d["included"]]
                            _excluded = [d for d in _rag_diagnostics if not d["included"]]
                            
                            if _included:
                                st.markdown("**✅ Eingeschlossen (>= Threshold):**")
                                for d in _included:
                                    st.text(f"  {min(d['best_score'], 1.0):.0%} | {d['filename'][:35]}")
                            
                            if _excluded:
                                st.markdown("**⚠️ Ausgeschlossen:**")
                                for d in _excluded:
                                    _reason_short = d['reason'][:30] if len(d['reason']) <= 30 else d['reason'][:27] + "..."
                                    st.text(f"  {min(d['best_score'], 1.0):>3.0%} | {d['filename'][:25]:25} | {_reason_short}")

                    render_retrieval_debug(_rag_debug)
                
                with _pc1:
                    st.caption("**SYSTEM-PROMPT:**")
                    st.text_area("sys_prev", _sys_prev, height=380, key=f"pp_sys_{_q1_nr}", disabled=True, label_visibility="collapsed")
                    st.caption("**USER:**")
                    st.text_area("usr_prev", _user_prev, height=80, key=f"pp_usr_{_q1_nr}", disabled=True, label_visibility="collapsed")

                st.markdown("---")
                # Button setzt nur Flag, damit er mehrfach funktioniert (Streamlit Buttons sind nur im Klick-Moment TRUE)
                if st.button("🤖 KI-Antwort abrufen", key="pp_run_llm_btn"):
                    st.session_state["_pp_trigger_llm"] = True
                    st.session_state["_pp_trigger_frage"] = _q1_nr
                    st.rerun()
                
                # LLM-Call außerhalb des Button-Blocks (sonst funktioniert nur 1x)
                if st.session_state.get("_pp_trigger_llm") and st.session_state.get("_pp_trigger_frage") == _q1_nr:
                    st.session_state["_pp_trigger_llm"] = False
                    st.session_state["_pp_answer"] = None
                    
                    with st.spinner(f"Warte auf {st.session_state.get('global_llm_model', 'LLM')}…"):
                        _pp_used_model: list = []
                        _pp_answer = try_models_with_messages(
                            provider=st.session_state.get("global_llm_provider", "openai"),
                            system=_sys_prev,
                            messages=[{"role": "user", "content": _user_prev}],
                            max_tokens=st.session_state.get("global_llm_max_tokens", 2000),
                            temperature=st.session_state.get("global_llm_temperature", 0.7),
                            model=st.session_state.get("global_llm_model"),
                            _used_model=_pp_used_model,
                        )
                        st.session_state["_pp_answer"] = _pp_answer
                        st.session_state["_pp_used_model"] = _pp_used_model[0] if _pp_used_model else "?"
                        st.session_state["_pp_answer_frage"] = _q1_nr

                if st.session_state.get("_pp_answer") and st.session_state.get("_pp_answer_frage") == _q1_nr:
                    st.caption(f"**KI-Antwort** (Modell: `{st.session_state.get('_pp_used_model', '?')}`):")
                    st.markdown(st.session_state["_pp_answer"])
        else:
            st.warning("Keine Fragen in der CSV gefunden.")
    except Exception as _prev_err:
        st.warning(f"Vorschau nicht möglich: {_prev_err}")

# ============ SCHRITT 5: BATCH STARTEN ============
st.markdown("---")
st.markdown("### 5️⃣ Batch starten")

if role_mode in ["individual", "single"] and not selected_roles:
    st.warning("⚠️ Bitte wählen Sie mindestens eine Rolle aus")
    st.stop()

# Sanity check für "none" Modus
if role_mode == "none":
    st.info("ℹ️ **Ohne Rollen-Kontext**: Fragen werden generisch beantwortet (keine rollenspezifische Perspektive)")

# --- Checkpoint-Info & Löschen-Button (ausserhalb des Batch-Buttons) ---
_cp_path_preview = ROOT / "data" / f"batch_checkpoint_{selected_project}_{selected_csv_id}.json"
if _cp_path_preview.exists():
    try:
        _cp_data = json.loads(_cp_path_preview.read_text(encoding="utf-8"))
        _cp_meta = _cp_data.get("__meta__", {})
        _cp_results = _cp_data.get("results", _cp_data) if isinstance(_cp_data, dict) else _cp_data
        _cp_n = len(_cp_results) if isinstance(_cp_results, list) else 0
        _cp_model = _cp_meta.get("model", "?") if _cp_meta else "?"
        _cp_proj = _cp_meta.get("project", "?") if _cp_meta else "?"
        _cp_roles = _cp_meta.get("roles", []) if _cp_meta else []
        st.warning(
            f"♻️ **Checkpoint vorhanden**: {_cp_n} Fragen gespeichert "
            f"(Modell: `{_cp_model}`, Rollen: {', '.join(_cp_roles) or '—'})"
        )
        if st.button("🗑️ Checkpoint löschen", key="delete_checkpoint_btn"):
            _cp_path_preview.unlink(missing_ok=True)
            st.success("Checkpoint gelöscht.")
            st.rerun()
    except Exception:
        pass

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
            clean_text = _strip_contextual_prefix(chunk.chunk_text)
            row_data = json.loads(clean_text)
            questions.append(row_data)
        except:
            st.warning(f"⚠️ Zeile {chunk.chunk_index} konnte nicht gelesen werden")
    
    if not questions:
        st.error("❌ CSV enthält keine gültigen Daten")
        st.stop()
    
    # Checkpoint-Datei
    checkpoint_path = ROOT / "data" / f"batch_checkpoint_{selected_project}_{selected_csv_id}.json"
    # Metadaten der aktuellen Einstellungen für Validierung
    _current_provider = st.session_state.get("global_llm_provider", "")
    _current_model = st.session_state.get("global_llm_model", "")
    _current_roles = sorted(selected_roles or [])
    _current_meta = {
        "project": selected_project,
        "csv_id": str(selected_csv_id),
        "provider": _current_provider,
        "model": _current_model,
        "role_mode": role_mode,
        "roles": _current_roles,
    }
    results = []
    resume_from = 0
    if checkpoint_path.exists():
        try:
            _saved_raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            # Format: {"__meta__": {...}, "results": [...]}
            if isinstance(_saved_raw, dict) and "results" in _saved_raw:
                _saved_meta = _saved_raw.get("__meta__", {})
                _saved_results = _saved_raw["results"]
                # Validierung: Einstellungen müssen übereinstimmen
                _meta_ok = (
                    _saved_meta.get("project") == selected_project
                    and str(_saved_meta.get("csv_id")) == str(selected_csv_id)
                    and _saved_meta.get("provider") == _current_provider
                    and _saved_meta.get("model") == _current_model
                    and _saved_meta.get("role_mode") == role_mode
                    and sorted(_saved_meta.get("roles", [])) == _current_roles
                )
                if _meta_ok and isinstance(_saved_results, list) and len(_saved_results) > 0:
                    results = _saved_results
                    resume_from = len(results)
                    st.info(f"♻️ **Checkpoint passt** – setze bei Frage {resume_from + 1} von {len(questions)} fort.")
                elif not _meta_ok:
                    st.warning("⚠️ Checkpoint übersprungen – Einstellungen haben sich geändert (anderes Modell, Rollen o.ä.)")
            elif isinstance(_saved_raw, list) and len(_saved_raw) > 0:
                # Altes Format (ohne Metadaten) – ignorieren, da Validierung nicht möglich
                st.warning("⚠️ Alter Checkpoint ohne Metadaten ignoriert. Starte von vorne.")
        except Exception:
            pass

    st.info(f"📊 Verarbeite **{len(questions)} Fragen**...")
    
    # Progress Bar
    progress_bar = st.progress(resume_from / len(questions) if questions else 0)
    status_text = st.empty()
    
    # Live-Vorschau Container
    st.markdown("---")
    st.markdown("### 📊 Zwischenstand (Live-Vorschau)")
    live_preview_container = st.empty()
    
    # System Prompt vorbereiten (style_instructions ist oben global definiert)
    project_context_text = ""
    if use_project_context:
        project_context_text = f"\n\nPROJEKT: {proj_obj.title}\nBESCHREIBUNG: {proj_obj.description or 'Keine Beschreibung'}"
    
    # Batch-Loop
    _llm_model_buf: list = []        # [model_id, fallback_warning] – wird pro Aufruf befüllt
    _fallback_warned = False         # Warnung nur einmal anzeigen
    fallback_warning_container = st.empty()
    for idx, question_data in enumerate(questions):
        if idx < resume_from:
            continue
        question_text = _get_csv_field(question_data, "Frage", "")
        nr = _get_csv_field(question_data, "Nr", str(idx+1))
        lieferant = _get_csv_field(question_data, "Lieferant", "")
        
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
                threshold=rag_threshold,
                exclude_classification="FAQ/Fragen-Katalog"  # CSV-Fragendatei aus RAG ausschliessen
            )
            rag_results = deduplicate_results(rag_results)
            rag_context = build_rag_context_from_search(rag_results)
            # RAG-Debug: Quellen für Export-Spalte _RAG_Chunks
            result_row["_RAG_Chunks"] = " | ".join(
                f"{d.get('filename','?')} ({min(max(d.get('similarity', 0), d.get('match_score', 0)), 1.0):.0%}): {format_chunk_preview((d.get('text','') or '').replace(chr(10),' '), max_length=120, query=question_text)}"
                for d in rag_results.get("documents", [])
            )
            _bucket_labels = {"keyword_candidates": "kw", "semantic_candidates": "sem", "final_candidates": "final"}
            result_row["_RAG_Debug"] = " | ".join(
                (
                    lambda src, d: (
                        f"[{src}] {d.get('filename','?')} "
                        f"terms=[{','.join(d.get('matched_terms') or [])}] "
                        f"comb={d.get('combined_score',0):.3f} sem={d.get('similarity',0):.3f} "
                        f"kw={d.get('normalized_match_score',0):.3f} raw={d.get('raw_bm25_score',0):.3f} "
                        f"idf={d.get('keyword_idf_score',0):.3f} cov={d.get('keyword_coverage',0):.3f} hits={d.get('priority_hits',0)}"
                    )
                )(src=_bucket_labels.get(bucket, bucket), d=d)
                for bucket in ["keyword_candidates", "semantic_candidates", "final_candidates"]
                for d in rag_results.get("debug", {}).get(bucket, [])[:5]
            )
        else:
            result_row["_RAG_Chunks"] = "(RAG deaktiviert)"
            result_row["_RAG_Debug"] = "(RAG deaktiviert)"
        
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
                    model=st.session_state.get("global_llm_model"),
                    _used_model=_llm_model_buf,
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
                    model=st.session_state.get("global_llm_model"),
                    _used_model=_llm_model_buf,
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
                        model=st.session_state.get("global_llm_model"),
                        _used_model=_llm_model_buf,
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

        # Fallback-Warnung einmalig anzeigen
        if not _fallback_warned and len(_llm_model_buf) >= 2 and _llm_model_buf[1]:
            fallback_warning_container.warning(
                f"{_llm_model_buf[1]}  \n"
                f"Alle weiteren Fragen werden ebenfalls mit **gpt-4o-mini** beantwortet."
            )
            _fallback_warned = True
        
        # Checkpoint nach jeder Frage schreiben (Absturzsicherung) — inkl. Metadaten
        try:
            checkpoint_path.write_text(
                json.dumps({"__meta__": _current_meta, "results": results}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception:
            pass
        
        # Live-Vorschau: lese aus Checkpoint-JSON und zeige letzte Antworten im Klartext
        try:
            _cp_raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            preview_data = _cp_raw["results"] if isinstance(_cp_raw, dict) and "results" in _cp_raw else _cp_raw
        except Exception:
            preview_data = results
        with live_preview_container.container():
            st.caption(f"**{len(preview_data)} von {len(questions)} Fragen bearbeitet** – letzte 5 Antworten:")
            for entry in reversed(preview_data[-5:]):
                _nr = entry.get("Nr", "?")
                _lief = entry.get("Lieferant", "")
                _frage = entry.get("Frage", "")
                _frage_short = (_frage[:90] + "…") if len(_frage) > 90 else _frage
                _antwort = next(
                    (v for k, v in entry.items() if k.startswith("Antwort")),
                    "—"
                )
                with st.expander(f"Nr. {_nr} | {_lief} — {_frage_short}"):
                    st.write(_antwort)
    
    status_text.text("✅ Batch abgeschlossen!")
    live_preview_container.empty()  # Leere Live-Vorschau nach Abschluss
    st.session_state["batch_results"] = results
    # Checkpoint löschen nach erfolgreichem Abschluss
    try:
        checkpoint_path.unlink(missing_ok=True)
    except Exception:
        pass
    st.success(f"🎉 **{len(results)} Fragen erfolgreich beantwortet!**")

# ============ SCHRITT 6: ERGEBNISSE ANZEIGEN ============
if st.session_state.get("batch_results"):
    st.markdown("---")
    st.markdown("### 6️⃣ Ergebnisse")
    
    results_df = pd.DataFrame(st.session_state["batch_results"])
    
    # Interne RAG-Debugspalten in Anzeige versteckt, aber im CSV/Excel-Export enthalten
    display_df = results_df[[c for c in results_df.columns if c not in ["_RAG_Chunks", "_RAG_Debug"]]]

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
    
    # Editierbare Tabelle (ohne _RAG_Chunks)
    edited_df = st.data_editor(
        display_df,
        width="stretch",
        num_rows="dynamic",
        column_config=column_config,
        key="batch_results_editor"
    )
    # Export-DataFrame: interne RAG-Debugspalten wieder hinzufügen
    export_df = edited_df.copy()
    if "_RAG_Chunks" in results_df.columns:
        export_df["_RAG_Chunks"] = results_df["_RAG_Chunks"].values
    if "_RAG_Debug" in results_df.columns:
        export_df["_RAG_Debug"] = results_df["_RAG_Debug"].values
    
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
        export_df.to_csv(csv_buffer, index=False, sep=";", encoding="utf-8-sig")
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
                export_df.to_excel(writer, index=False, sheet_name="Antworten")
            excel_buffer.seek(0)
            
            st.download_button(
                label="📥 Download als Excel",
                data=excel_buffer.getvalue(),
                file_name=f"batch_antworten_{selected_project}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch"
            )
