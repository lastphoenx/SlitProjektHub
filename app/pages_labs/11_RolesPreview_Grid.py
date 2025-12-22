from __future__ import annotations
import sys
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Roles Preview (AgGrid)", page_icon="📋", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m07_roles import list_roles_df, load_role

from st_aggrid import AgGrid, GridOptionsBuilder
from st_aggrid.shared import JsCode

st.session_state.setdefault("rp_grid_view_key", "")

SECTION_H = 700
TABLE_H   = 560
st.markdown(f"""
<style>
.block-container {{ padding-top:1rem; }}
.col-frame {{ min-height:{SECTION_H}px; }}
.stDataFrame thead th {{ font-weight:600; }}
</style>
""", unsafe_allow_html=True)

st.title("Roles Preview (AgGrid)")
st.caption("Einfachklick: Vorschau rechts. Kein Checkbox-Select. Stabil bei Sort/Filter dank getRowId=Key.")

left, right = st.columns([1.2, 1.0])

with left:
    st.subheader("Rollen (AgGrid)")
    df = list_roles_df(include_deleted=False)
    if df is None or df.empty:
        st.info("Noch keine Rollen vorhanden.")
        st.session_state["rp_grid_view_key"] = ""
    else:
        show = (
            df[["Key", "Titel", "Funktion"]]
            .rename(columns={"Titel": "Rollenbezeichnung", "Funktion": "Kürzel"})
            .reset_index(drop=True)
        )

        gb = GridOptionsBuilder.from_dataframe(show)
        gb.configure_default_column(resizable=True, sortable=True, filter=True)

        # Key als stabile RowId nutzen (und in UI verstecken)
        gb.configure_column("Key", hide=True)
        gb.configure_column("Rollenbezeichnung", width=360)
        gb.configure_column("Kürzel", width=160)

        # Single-Selection ohne Checkbox – Klick auf Zeile reicht
        gb.configure_selection(
            selection_mode="single",
            use_checkbox=False,
            rowMultiSelectWithClick=False
        )

        # WICHTIG: stabile Zeilen-IDs -> korrekte Selektion auch bei Sort/Filter
        gb.configure_grid_options(
            getRowId=JsCode("function(params){ return params.data.Key; }"),
            suppressRowClickSelection=False,
        )

        grid_opts = gb.build()

        # Neuer Parameter 'update_on' für moderne st-aggrid Versionen,
        # Fallback auf 'update_mode' wenn 'update_on' nicht unterstützt wird.
        grid = None
        try:
            grid = AgGrid(
                show,
                gridOptions=grid_opts,
                update_on=["SELECTION_CHANGED"],   # moderne API
                height=TABLE_H,
                fit_columns_on_grid_load=True,
                allow_unsafe_jscode=True,
                key="roles_preview_grid",
            )
        except TypeError:
            # Fallback (ältere st-aggrid Version)
            from st_aggrid import GridUpdateMode
            grid = AgGrid(
                show,
                gridOptions=grid_opts,
                update_mode=GridUpdateMode.SELECTION_CHANGED,
                height=TABLE_H,
                fit_columns_on_grid_load=True,
                allow_unsafe_jscode=True,
                key="roles_preview_grid",
            )

        # Ausgewählte Zeile verarbeiten
        rows = grid.get("selected_rows", []) if isinstance(grid, dict) else []
        sel_key = rows[0].get("Key") if rows else None

        # Optional: Beim ersten Laden erste Zeile vorwählen
        if not st.session_state.get("rp_grid_view_key") and not sel_key and not show.empty:
            st.session_state["rp_grid_view_key"] = str(show.loc[0, "Key"])

        if sel_key and sel_key != st.session_state.get("rp_grid_view_key"):
            st.session_state["rp_grid_view_key"] = str(sel_key)
            # kein rerun nötig

with right:
    st.subheader("Vorschau")
    k = st.session_state.get("rp_grid_view_key", "")
    if k:
        obj, body = load_role(k)
        st.caption(f"Key: {k} — {obj.title if obj else ''}")
        st.markdown(body or "_(leer)_")
    else:
        st.info("Bitte eine Zeile anklicken.")
