# app/pages/12_Roles_MUI.py
from __future__ import annotations
import sys, re
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Roles – MUI DataGrid", page_icon="👥", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from streamlit_elements import elements, mui, sync  # type: ignore
except Exception:
    st.error(
        "Fehlendes Paket: streamlit-elements\n\n"
        "Im Projektordner ausführen:\n"
        "  .\\.venv\\Scripts\\Activate.ps1\n"
        "  pip install streamlit-elements==0.1.*"
    )
    st.stop()

from src.m06_ui import page_header, chips, md_editor_with_preview
from src.m07_roles import list_roles_df, load_role, upsert_role, function_suggestions

SECTION_H = 760
TABLE_H   = 560
EDITOR_H  = 300

st.markdown(f"""
<style>
.block-container {{ padding-top: 1rem; }}
.role-frame {{ min-height:{SECTION_H}px; display:flex; flex-direction:column; gap:.6rem; }}
</style>
""", unsafe_allow_html=True)

page_header("Rollen (MUI Grid)", "Single-Select per Klick, Doppelklick startet Bearbeiten.")

# ---------- Session-State ----------
st.session_state.setdefault("mui_sel", [])              # MUI selectionModel -> Liste[str]
st.session_state.setdefault("mui_view_key", "")         # Vorschau
st.session_state.setdefault("mui_edit_key", "")         # Edit
st.session_state.setdefault("mui_title", "")
st.session_state.setdefault("mui_group", "")
st.session_state.setdefault("mui_body", "")
st.session_state.setdefault("mui_last_loaded", None)

# ---------- Daten ----------
df = list_roles_df(include_deleted=False)
col_grid, col_preview, col_form = st.columns([1.1, 1.2, 1.1])

# =========================== GRID ===========================
with col_grid:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Rollen")

        if df is None or df.empty:
            st.info("Noch keine Rollen vorhanden.")
            st.session_state["mui_view_key"] = ""
        else:
            rows = [
                {"id": str(row["Key"]), "titel": str(row["Titel"]),
                 "kuerzel": "" if str(row.get("Funktion","")) == "nan" else str(row.get("Funktion",""))}
                for _, row in df.iterrows()
            ]
            cols = [
                {"field": "titel", "headerName": "Rollenbezeichnung", "flex": 1, "minWidth": 280},
                {"field": "kuerzel", "headerName": "Kürzel", "width": 140},
            ]

            # Initial: erste Zeile wählen, falls noch nichts gesetzt
            if rows and not st.session_state["mui_sel"] and not st.session_state["mui_view_key"]:
                first_id = rows[0]["id"]
                st.session_state["mui_sel"] = [first_id]
                st.session_state["mui_view_key"] = first_id

            # DataGrid – v2: Unterstützt v5/v6 Selection-APIs und konsolidierte Events
            with elements("roles_mui_grid_v2"):
                # IDs als Strings absichern
                rows = [{**r, "id": str(r["id"])} for r in rows]
                mui.DataGrid(
                    rows=rows,
                    columns=cols,
                    checkboxSelection=False,
                    disableMultipleRowSelection=True,
                    disableRowSelectionOnClick=False,      # Klick selektiert Zeile
                    hideFooterSelectedRowCount=True,

                    # beide APIs mitgeben (einige Builds hören nur auf eine davon)
                    selectionModel=st.session_state["mui_sel"],
                    onSelectionModelChange=sync("mui_sel"),
                    rowSelectionModel=st.session_state["mui_sel"],
                    onRowSelectionModelChange=sync("mui_sel"),

                    # Fallback: optional Events (nutzen wir nur, falls nötig)
                    onRowClick=sync("mui_row_click"),
                    onRowDoubleClick=sync("mui_row_dbl"),

                    density="compact",
                    autoHeight=True,
                    pageSizeOptions=[50],
                    initialState={"pagination": {"paginationModel": {"pageSize": 50}}},
                )

            # --- Events/State konsolidieren ---
            # 1) Klick -> Auswahl setzen + Vorschau updaten + RERUN
            ev = st.session_state.pop("mui_row_click", None)
            if isinstance(ev, dict) and ev.get("id"):
                rid = str(ev["id"])
                if st.session_state["mui_sel"] != [rid] or st.session_state.get("mui_view_key") != rid:
                    st.session_state["mui_sel"] = [rid]
                    st.session_state["mui_view_key"] = rid
                    # Formular leeren, falls nur Vorschau
                    st.session_state["mui_edit_key"] = ""
                    st.session_state["mui_last_loaded"] = None
                    st.session_state["mui_title"] = ""
                    st.session_state["mui_group"] = ""
                    st.session_state["mui_body"]  = ""
                    st.rerun()

            # 2) Pure Selection-Änderung (falls Click nicht kam)
            sel = st.session_state.get("mui_sel") or []
            if isinstance(sel, (str, int)):
                sel = [str(sel)]
                st.session_state["mui_sel"] = sel
            if sel:
                sel_id = str(sel[0])
                if sel_id != st.session_state.get("mui_view_key"):
                    st.session_state["mui_view_key"] = sel_id

            # 3) Doppelklick -> Edit
            ev2 = st.session_state.pop("mui_row_dbl", None)
            if isinstance(ev2, dict) and ev2.get("id"):
                rid = str(ev2["id"])
                st.session_state["mui_edit_key"] = rid
                st.session_state["mui_view_key"] = rid
                st.session_state["mui_last_loaded"] = None
                st.rerun()

# =========================== VORSCHAU ===========================
with col_preview:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Vorschau")
        vkey = st.session_state.get("mui_view_key") or st.session_state.get("mui_edit_key")
        if vkey:
            obj, body = load_role(vkey)
            st.caption(f"Key: {vkey} — {obj.title if obj else ''}")
            # kein extra Scroll-Wrapper -> keine „Luft“ oben
            st.markdown((body or "").lstrip())
            c1, c2, c3 = st.columns([1,1,1])
            with c1:
                if st.button("Bearbeiten", key="mui_preview_edit", width="content"):
                    st.session_state["mui_edit_key"] = vkey
                    st.session_state["mui_last_loaded"] = None
                    st.rerun()
            with c2:
                _, b = load_role(vkey)
                st.download_button("Markdown ⬇️", data=b or "", file_name=f"{vkey}.md", mime="text/markdown")
            with c3:
                st.empty()
        else:
            st.info("Bitte links einen Eintrag anklicken.")

# =========================== FORMULAR ===========================
with col_form:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Neue Rolle / Bearbeiten")

        ekey = st.session_state.get("mui_edit_key", "")
        obj_e, body_e = load_role(ekey) if ekey else (None, "")

        if ekey and st.session_state.get("mui_last_loaded") != ekey and obj_e:
            st.session_state["mui_title"] = obj_e.title
            st.session_state["mui_group"] = obj_e.short_code or ""
            st.session_state["mui_body"]  = body_e or ""
            st.session_state["mui_last_loaded"] = ekey

        chips(
            function_suggestions()[:8],
            target_title_key="mui_title",
            target_function_key="mui_group",
            state_key="mui_role_quickpick",
            label="Schnellauswahl",
            title_map={
                "CEO":"Chief Executive Officer","CFO":"Chief Financial Officer",
                "CIO":"Chief Information Officer","CTO":"Chief Technology Officer",
                "COO":"Chief Operating Officer","CPO":"Chief Product Officer",
                "CISO":"Chief Information Security Officer","DPO":"Data Protection Officer",
            },
        )

        st.markdown("**Rollenbezeichnung**")
        st.text_input(" ", key="mui_title", label_visibility="collapsed",
                      placeholder="z. B. Chief Information Officer")

        st.markdown("**Kürzel (übergeordnete Rolle)**")
        st.text_input(" ", key="mui_group", label_visibility="collapsed",
                      placeholder="CIO / CFO / CEO / CISO / DPO …")

        try:
            md_editor_with_preview("Beschreibung / Profil (Markdown)",
                                   st.session_state.get("mui_body",""),
                                   key="mui_body",
                                   height=EDITOR_H)
        except TypeError:
            md_editor_with_preview("Beschreibung / Profil (Markdown)",
                                   st.session_state.get("mui_body",""),
                                   key="mui_body")

        b1, b2 = st.columns([1,1])
        with b1:
            if st.button("Speichern", type="primary", key="mui_save", width="content"):
                title_val = (st.session_state.get("mui_title","") or "").strip()
                if not title_val:
                    st.error("Bitte die **Rollenbezeichnung** ausfüllen.")
                else:
                    group_val = st.session_state.get("mui_group","") or None
                    body_val  = st.session_state.get("mui_body","") or ""
                    def _slug(s:str)->str:
                        import re
                        s = s.strip().lower()
                        s = re.sub(r"[^a-z0-9_-]+","-", s)
                        return re.sub(r"-+","-", s).strip("-") or "role"
                    key_to_use = ekey if (ekey and _slug(title_val)==_slug(ekey)) else None
                    r, created = upsert_role(title=title_val, group_name=group_val, body_text=body_val, key=key_to_use)
                    st.toast(("Angelegt" if created else "Aktualisiert") + f": {r.key}")
                    st.session_state["mui_sel"] = [r.key]
                    st.session_state["mui_view_key"] = r.key
                    st.session_state["mui_edit_key"] = ""
                    st.session_state["mui_last_loaded"] = None
                    st.session_state["mui_title"] = ""
                    st.session_state["mui_group"] = ""
                    st.session_state["mui_body"]  = ""
                    st.rerun()
        with b2:
            if st.button("Neu", key="mui_new", width="content"):
                st.session_state["mui_edit_key"] = ""
                st.session_state["mui_last_loaded"] = None
                st.session_state["mui_title"] = ""
                st.session_state["mui_group"] = ""
                st.session_state["mui_body"]  = ""
                st.toast("Neu…")
                st.rerun()
