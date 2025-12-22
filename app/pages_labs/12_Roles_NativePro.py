# app/pages/12_Roles_NativePro.py
from __future__ import annotations
import sys, math, re, time
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Rollen – Native Pro", page_icon="👥", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m07_roles import list_roles_df, load_role, upsert_role, function_suggestions
from src.m06_ui import chips, md_editor_with_preview, page_header
from src.m08_llm import providers_available, generate_role_text, get_available_models

# ---------- Layout ----------
SECTION_H = 920
LIST_H    = 640
EDITOR_H  = 320
PAGE_SIZES = [8, 12, 20, 30, 50]

# ---------- Styles ----------
st.markdown("""
<style>
.block-container { padding-bottom: 2rem; }

/* kompakter */
:root { --gap: .16rem; }
div[data-testid="stVerticalBlock"] > div:has(>.row-wrap) { gap: var(--gap) !important; }

/* Buttons primär */
.stButton > button[kind="primary"], .stButton > button[data-testid="primary"] {
  background: #2563eb; color:#fff; border-radius: 10px; padding: .48rem 1rem;
  box-shadow: 0 1px 0 rgba(37,99,235,.25);
}
.stButton > button:disabled { opacity: .45; }

/* Listenzeilen */
.row-wrap {
  margin: 0; padding: 0;              /* Container kontrolliert nur Hintergrund */
  border-radius: 10px; overflow: clip;
}
.row-inner {
  width:100%;
  border:1px solid #EFF3FF;
  background:#fff;
  padding:.20rem .40rem;              /* sehr kompakt */
}
.row-selected .row-inner {
  background:#FFF9DB;                 /* Pastellgelb über die volle Zeile */
  border-color:#FFE58F;
}

/* Monospace 15 | 35 */
.row-inner button {
  width:100%;
  text-align:left;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace !important;
  background:transparent;
  border:none;
  padding:0; margin:0;
}

/* etwas schmalere linke Spalte */
</style>
""", unsafe_allow_html=True)

# ---------- Header ----------
page_header("Rollen – Native Pro", "Stabile Liste (Klick/Doppelklick), Vorschau & Bearbeiten – ohne Add-ons.")

# ---------- Session ----------
st.session_state.setdefault("rnp_query", "")
st.session_state.setdefault("rnp_sort_by", "Kürzel")
st.session_state.setdefault("rnp_sort_dir", "aufsteigend")
st.session_state.setdefault("rnp_page", 1)
st.session_state.setdefault("rnp_page_size", 12)

st.session_state.setdefault("rnp_selected_key", "")
st.session_state.setdefault("rnp_view_key", "")
st.session_state.setdefault("rnp_edit_key", "")
st.session_state.setdefault("rnp_last_loaded_key", None)

# Doppelklick-Detektor
st.session_state.setdefault("rnp_last_click_key", "")
st.session_state.setdefault("rnp_last_click_ts", 0.0)
DBLCLICK_MS = 400

# Formular
st.session_state.setdefault("rnp_title", "")
st.session_state.setdefault("rnp_group", "")
st.session_state.setdefault("rnp_body", "")

# Reset-Flag nach Speichern
st.session_state.setdefault("rnp_after_save_reset", False)

# KI Provider
try:
    PROVS = providers_available()
except Exception:
    PROVS = ["openai"]
st.session_state.setdefault("rnp_llm_provider", PROVS[0] if PROVS else "openai")

# ---------- Data ----------
df = list_roles_df(include_deleted=False, include_body=True)
if df is None:
    df = list_roles_df(include_deleted=True, include_body=True)

# ---------- Helpers ----------
def _format_cell(short: str, title: str) -> str:
    left  = (short or "")[:15].ljust(15)
    right = (title or "")[:35].ljust(35)
    return f"{left} | {right}"

def _on_change_reset_page():
    # Kein st.rerun() in Callbacks – widget change triggert rerun automatisch
    st.session_state["rnp_page"] = 1

def _handle_click(row_key: str):
    now = time.time() * 1000.0
    last_k = st.session_state.get("rnp_last_click_key","")
    last_t = st.session_state.get("rnp_last_click_ts", 0.0)

    st.session_state["rnp_selected_key"] = row_key
    st.session_state["rnp_view_key"] = row_key

    if last_k == row_key and (now - last_t) <= DBLCLICK_MS:
        st.session_state["rnp_edit_key"] = row_key
        st.session_state["rnp_last_loaded_key"] = None
        st.rerun()
    else:
        st.session_state["rnp_edit_key"] = ""
    st.session_state["rnp_last_click_key"] = row_key
    st.session_state["rnp_last_click_ts"]  = now

# ---------- 3-Spalten ----------
col_list, col_preview, col_form = st.columns([0.9, 1.35, 1.05])

# ================= LISTE =================
with col_list:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Rollen")

        # nach Speichern: Reset vor Widget-Aufbau
        if st.session_state.get("rnp_after_save_reset"):
            st.session_state["rnp_after_save_reset"] = False
            st.session_state["rnp_edit_key"] = ""
            st.session_state["rnp_last_loaded_key"] = None
            st.session_state["rnp_title"] = ""
            st.session_state["rnp_group"] = ""
            st.session_state["rnp_body"]  = ""

        c1, c2, c3 = st.columns([1.0, 0.85, 0.7])
        with c1:
            st.text_input("Wildcards: * und ?", key="rnp_query",
                          placeholder="z. B. CISO oder Chief Information ...",
                          on_change=_on_change_reset_page)
        with c2:
            st.selectbox("Sortieren nach", ["Kürzel","Rollenbezeichnung"],
                         key="rnp_sort_by", on_change=_on_change_reset_page,
                         index=(0 if st.session_state["rnp_sort_by"]=="Kürzel" else 1))
        with c3:
            st.selectbox("Richtung", ["aufsteigend","absteigend"],
                         key="rnp_sort_dir", on_change=_on_change_reset_page,
                         index=(0 if st.session_state["rnp_sort_dir"]=="aufsteigend" else 1))

        with st.container():
            st.selectbox("Einträge pro Seite", PAGE_SIZES,
                         key="rnp_page_size", on_change=_on_change_reset_page,
                         index=PAGE_SIZES.index(st.session_state["rnp_page_size"]))

        if df is not None and not df.empty:
            data = df.rename(columns={"Titel":"Rollenbezeichnung","Funktion":"Kürzel"}).copy()
            for col in ("Key","Rollenbezeichnung","Kürzel","Inhalt"):
                if col not in data.columns:
                    data[col] = ""
                data[col] = data[col].astype(str).fillna("")

            # Suche
            qnorm = (st.session_state.get("rnp_query","") or "").strip().lower()
            if qnorm and qnorm != "*":
                if ("*" in qnorm) or ("?" in qnorm):
                    patt = re.escape(qnorm).replace("\\*",".*").replace("\\?",".")
                    key_m   = data["Key"].str.lower().str.contains(patt, regex=True)
                    title_m = data["Rollenbezeichnung"].str.lower().str.contains(patt, regex=True)
                    short_m = data["Kürzel"].str.lower().str.contains(patt, regex=True)
                    body_m  = data["Inhalt"].str.lower().str.contains(patt, regex=True)
                else:
                    key_m   = data["Key"].str.lower().str.contains(qnorm, regex=False)
                    title_m = data["Rollenbezeichnung"].str.lower().str.contains(qnorm, regex=False)
                    short_m = data["Kürzel"].str.lower().str.contains(qnorm, regex=False)
                    body_m  = data["Inhalt"].str.lower().str.contains(qnorm, regex=False)
                data = data[key_m | title_m | short_m | body_m]

            # Sortierung (sofort – weil Callback page=1 setzt)
            by = st.session_state["rnp_sort_by"]
            ascend = st.session_state["rnp_sort_dir"] == "aufsteigend"
            if by in data.columns:
                data = data.sort_values(by=by, ascending=ascend, kind="mergesort")

            # Pagination
            total = len(data)
            page_size = st.session_state["rnp_page_size"]
            max_page = max(1, math.ceil(total / page_size))
            page = max(1, min(st.session_state["rnp_page"], max_page))
            st.session_state["rnp_page"] = page
            st.session_state["rnp_page_num"] = page

            st.caption(f"Treffer: {total} • Seite {page}/{max_page}")

            pcol1, pcol2, pcol3 = st.columns([0.6, 0.6, 1.0])
            with pcol1:
                if st.button("◀︎ Zurück", disabled=(page<=1), key="rnp_prev", type="primary"):
                    st.session_state["rnp_page"] = page - 1
                    st.rerun()
            with pcol2:
                if st.button("Weiter ▶︎", disabled=(page>=max_page), key="rnp_next", type="primary"):
                    st.session_state["rnp_page"] = page + 1
                    st.rerun()
            with pcol3:
                new_page = st.number_input("Seite", min_value=1, max_value=max_page,
                                           value=st.session_state["rnp_page_num"], step=1, key="rnp_page_num_input")
                if int(new_page) != page:
                    st.session_state["rnp_page"] = int(new_page)
                    st.rerun()

            start = (page - 1) * page_size
            end   = start + page_size
            page_df = data.iloc[start:end].reset_index(drop=True)

            # Liste (kompakt, volle Breite, Pastellgelb bei Auswahl)
            with st.container(height=LIST_H):
                if page_df.empty:
                    st.info("Keine Einträge auf dieser Seite.")
                else:
                    cur = st.session_state.get("rnp_selected_key","")
                    for i, row in page_df.iterrows():
                        key   = str(row["Key"])
                        title = str(row["Rollenbezeichnung"])
                        short = "" if str(row.get("Kürzel","")) == "nan" else str(row.get("Kürzel",""))
                        label = _format_cell(short, title)

                        selected = (key == cur)
                        css = "row-selected" if selected else "row-normal"
                        st.markdown(f'<div class="row-wrap {css}"><div class="row-inner">', unsafe_allow_html=True)
                        if st.button(label, key=f"rnp_row_{page}_{i}_{key}"):
                            _handle_click(key)
                        st.markdown("</div></div>", unsafe_allow_html=True)
        else:
            st.info("Noch keine Rollen vorhanden.")
            st.session_state["rnp_selected_key"] = ""
            st.session_state["rnp_view_key"] = ""
            st.session_state["rnp_edit_key"] = ""

# ================= VORSCHAU =================
with col_preview:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Vorschau")
        vkey = st.session_state.get("rnp_view_key") or st.session_state.get("rnp_edit_key")
        if vkey:
            obj, body = load_role(vkey)
            st.caption(f"Key: {vkey} — {obj.title if obj else ''}")
            st.markdown((body or "").lstrip())
            c1, c2, c3 = st.columns([1,1,1])
            with c1:
                if st.button("Bearbeiten", key="rnp_preview_edit", type="primary"):
                    st.session_state["rnp_edit_key"] = vkey
                    st.session_state["rnp_last_loaded_key"] = None
                    st.rerun()
            with c2:
                try:
                    _, b = load_role(vkey)
                    st.download_button("Markdown ⬇️", data=b or "", file_name=f"{vkey}.md", mime="text/markdown", key="rnp_dl")
                except Exception:
                    st.button("Markdown ⬇️", disabled=True)
            with c3:
                st.empty()
        else:
            st.info("Bitte links einen Eintrag anklicken.")

# ================= FORMULAR =================
with col_form:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Neue Rolle / Bearbeiten")

        if st.session_state.get("rnp_after_save_reset"):
            st.session_state["rnp_after_save_reset"] = False
            st.session_state["rnp_edit_key"] = ""
            st.session_state["rnp_last_loaded_key"] = None
            st.session_state["rnp_title"] = ""
            st.session_state["rnp_group"] = ""
            st.session_state["rnp_body"]  = ""

        ekey = st.session_state.get("rnp_edit_key","")
        obj_e, body_e = load_role(ekey) if ekey else (None, "")

        if ekey and st.session_state.get("rnp_last_loaded_key") != ekey and obj_e:
            st.session_state["rnp_title"] = obj_e.title
            st.session_state["rnp_group"] = obj_e.short_code or ""
            st.session_state["rnp_body"]  = body_e or ""
            st.session_state["rnp_last_loaded_key"] = ekey

        chips(
            function_suggestions()[:8],
            target_title_key="rnp_title",
            target_function_key="rnp_group",
            state_key="rnp_quickpick",
            label="Schnellauswahl",
            title_map={
                "CEO":"Chief Executive Officer","CFO":"Chief Financial Officer",
                "CIO":"Chief Information Officer","CTO":"Chief Technology Officer",
                "COO":"Chief Operating Officer","CPO":"Chief Product Officer",
                "CISO":"Chief Information Security Officer","DPO":"Data Protection Officer",
            },
        )

        # KI-Assistent
        with st.container(border=True):
            prov = st.session_state.get("global_llm_provider", "openai")
            model = st.session_state.get("global_llm_model")
            temp = st.session_state.get("global_llm_temperature", 0.7)
            
            st.caption("🤖 **Globale KI-Einstellungen:**")
            ci1, ci2, ci3 = st.columns(3)
            with ci1:
                st.metric("Provider", prov)
            with ci2:
                st.metric("Modell", model or "—")
            with ci3:
                st.metric("Temp.", f"{temp:.1f}")
            
            st.caption("Erzeugt einen Rollen-Textvorschlag. Überschreibt nichts automatisch.")
            if st.button("KI-Vorschlag einfügen", key="rnp_ai", type="primary"):
                try:
                    suggestion = generate_role_text(
                        prov if prov != "none" else "openai",
                        st.session_state.get("rnp_title",""),
                        st.session_state.get("rnp_group","")
                    )
                    st.session_state["rnp_body"] = suggestion or st.session_state.get("rnp_body","")
                    st.toast("KI-Vorschlag eingefügt.")
                    st.rerun()
                except Exception as ex:
                    st.error(f"KI-Generierung fehlgeschlagen: {ex}")

        st.markdown("**Rollenbezeichnung**")
        st.text_input(" ", key="rnp_title", label_visibility="collapsed",
                      placeholder="z. B. Chief Information Officer")

        st.markdown("**Kürzel (übergeordnete Rolle)**")
        st.text_input(" ", key="rnp_group", label_visibility="collapsed",
                      placeholder="CIO / CFO / CEO / CISO / DPO …")

        # Editor
        try:
            md_editor_with_preview("Beschreibung / Profil (Markdown)",
                                   st.session_state.get("rnp_body",""),
                                   key="rnp_body",
                                   height=EDITOR_H)
        except TypeError:
            md_editor_with_preview("Beschreibung / Profil (Markdown)",
                                   st.session_state.get("rnp_body",""),
                                   key="rnp_body")

        # Speichern: IMMER überschreiben, wenn wir in Edit sind
        if st.button("Speichern", type="primary", key="rnp_save"):
            title_val = (st.session_state.get("rnp_title","") or "").strip()
            if not title_val:
                st.error("Bitte die **Rollenbezeichnung** ausfüllen.")
            else:
                group_val = st.session_state.get("rnp_group","") or None
                body_val  = st.session_state.get("rnp_body","") or ""

                key_to_use = st.session_state.get("rnp_edit_key") or None  # update bei Edit
                r, created = upsert_role(title=title_val, group_name=group_val, body_text=body_val, key=key_to_use)

                st.toast(("Angelegt" if created else "Aktualisiert") + f": {r.key}")
                st.session_state["rnp_selected_key"] = r.key
                st.session_state["rnp_view_key"] = r.key

                st.session_state["rnp_after_save_reset"] = True
                st.rerun()
