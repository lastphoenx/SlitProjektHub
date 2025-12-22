import streamlit as st
import pandas as pd
from src.m09_docs import ingest_document, list_documents, delete_document
from src.m03_db import DOCUMENT_CLASSIFICATIONS

def render_documents_tab():
    st.header("📂 Dokumenten-Verwaltung (RAG)")
    st.markdown("Hier können Dokumente hochgeladen werden, die dann automatisch in Chunks zerlegt und vektorisiert werden. Diese Dokumente können später Projekten zugewiesen werden.")

    # --- Upload Bereich ---
    with st.expander("📤 Neues Dokument hochladen", expanded=True):
        uploaded_file = st.file_uploader("Datei auswählen (PDF, MD, TXT, JSON)", type=["pdf", "md", "txt", "json", "yaml", "yml"])
        classification = st.selectbox("Klassifizierung", options=DOCUMENT_CLASSIFICATIONS)
        
        if uploaded_file and st.button("Hochladen & Verarbeiten", type="primary"):
            with st.spinner("Verarbeite Dokument... (Text-Extraktion, Chunking, Embedding)"):
                success, msg = ingest_document(
                    file_name=uploaded_file.name,
                    file_bytes=uploaded_file.getvalue(),
                    classification=classification
                )
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.markdown("---")

    # --- Liste der Dokumente ---
    st.subheader("Verfügbare Dokumente")
    
    docs = list_documents()
    if not docs:
        st.info("Keine Dokumente vorhanden.")
    else:
        # Als Tabelle anzeigen
        data = []
        for d in docs:
            data.append({
                "ID": d.id,
                "Dateiname": d.filename,
                "Kategorie": d.classification,
                "Chunks": d.chunk_count,
                "Größe (Bytes)": d.file_size,
                "Hochgeladen am": d.uploaded_at.strftime("%d.%m.%Y %H:%M")
            })
        
        df = pd.DataFrame(data)
        st.dataframe(df, width="stretch", hide_index=True)
        
        # Löschen-Dialog
        st.markdown("### Dokument löschen")
        col1, col2 = st.columns([3, 1])
        with col1:
            doc_to_delete = st.selectbox(
                "Dokument auswählen", 
                options=docs, 
                format_func=lambda x: f"{x.filename} ({x.classification})",
                key="doc_delete_select"
            )
        with col2:
            if st.button("🗑️ Löschen", type="primary"):
                if doc_to_delete:
                    if delete_document(doc_to_delete.id):
                        st.success(f"Dokument '{doc_to_delete.filename}' gelöscht.")
                        st.rerun()
                    else:
                        st.error("Fehler beim Löschen.")
