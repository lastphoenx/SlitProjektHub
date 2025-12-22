from __future__ import annotations
import sys
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Roles Preview", page_icon="📋", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from src.m07_roles import list_roles_df, load_role
except Exception:
    st.error(
        "Umgebung prüfen:\n"
        "  .\\.venv\\Scripts\\Activate.ps1\n"
        "  pip install -r requirements.txt"
    )
    st.stop()

SECTION_H = 700
TABLE_H   = 560
st.markdown(f"""
<style>
.block-container {{ padding-top: 1rem; }}
.col-frame {{ min-height: {SECTION_H}px; }}
.stDataFrame thead th {{ font-weight:600; }}
</style>
""", unsafe_allow_html=True)

st.title("Roles Preview")
st.caption("Links: Tabelle (Key, Rollenbezeichnung, Kürzel). Rechts: Vorschau. Ein Klick reicht – strikt Single-Select.")

# Nur dieser State (kein Widget-State anfassen!)
st.session_state.setdefault("rp_selected_key", "")

left, right = st.columns([1.2, 1.0])

with left:
    st.subheader("Rollen")
    df = list_roles_df(include_deleted=False)
    if df is None or df.empty:
        st.info("Noch keine Rollen vorhanden.")
        st.session_state["rp_selected_key"] = ""
    else:
        show = (
            df[["Key", "Titel", "Funktion"]]
            .rename(columns={"Titel": "Rollenbezeichnung", "Funktion": "Kürzel"})
            .reset_index(drop=True)
            .copy()
        )

        # Single-Select-Spalte vorbereiten
        sel_key_prev = st.session_state.get("rp_selected_key", "")
        show.insert(0, "Auswählen", False)
        if sel_key_prev and sel_key_prev in show["Key"].astype(str).values:
            pre_idx = show.index[show["Key"].astype(str) == sel_key_prev].tolist()
            if pre_idx:
                show.at[pre_idx[0], "Auswählen"] = True

        # Tabelle rendern (editierbar nur die Auswahl)
        edited = st.data_editor(
            show,
            key="rp_table",                  # Widget verwaltet diesen Key – wir schreiben NICHT darauf
            width="stretch",                 # statt use_container_width
            height=TABLE_H,
            num_rows="fixed",
            disabled={"Key": True, "Rollenbezeichnung": True, "Kürzel": True},
            hide_index=True,
        )

        # Auswahl aus Rückgabe streng normalisieren
        true_idxs = edited.index[edited["Auswählen"] == True].tolist()  # noqa: E712
        if len(true_idxs) == 0:
            chosen_key = ""
        elif len(true_idxs) == 1:
            chosen_key = str(edited.loc[true_idxs[0], "Key"])
        else:
            # mehrere angehakt -> nimm die letzte sichtbare und normalisiere via rerun
            chosen_key = str(edited.loc[true_idxs[-1], "Key"])

        if chosen_key != sel_key_prev:
            st.session_state["rp_selected_key"] = chosen_key
            st.rerun()  # erzwingt beim nächsten Render genau EIN Häkchen

with right:
    st.subheader("Vorschau")
    view_key = st.session_state.get("rp_selected_key", "")
    if view_key:
        obj, body = load_role(view_key)
        st.caption(f"Key: {view_key} — {obj.title if obj else ''}")
        st.markdown(body or "_(leer)_")
    else:
        st.info("Bitte in der Tabelle genau einen Eintrag auswählen.")
