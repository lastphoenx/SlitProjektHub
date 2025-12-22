# app/pages/21_Tasks_NativePro.py
from __future__ import annotations
import sys, math, re, time, textwrap
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Aufgaben – Native Pro", page_icon="✅", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Backends --------------------------------------------------------------
# Aufgaben (gleiches Schema wie Rollen: key, title, group_name, body_text)
try:
    from src.m07_tasks import list_tasks_df, load_task, upsert_task, soft_delete_task, function_suggestions
    BACKEND_OK = True
except Exception:
    BACKEND_OK = False

# Rollen (für die Zuweisung „Standard-Rolle“)
try:
    from src.m07_roles import list_roles_df
    ROLES_OK = True
except Exception:
    ROLES_OK = False

# (Optional) Doku-Suche/Listing – wenn nicht vorhanden, wird nur Upload angeboten
def _empty_docs_df():
    import pandas as pd
    return pd.DataFrame(columns=["id","name","desc"])
DOCS_OK = False
try:
    from src.m05_ingest import list_documents_df as _list_docs
    def list_documents_df():
        try:
            df = _list_docs()
            return df if df is not None else _empty_docs_df()
        except Exception:
            return _empty_docs_df()
    DOCS_OK = True
except Exception:
    def list_documents_df():
        return _empty_docs_df()

# LLM für Vorschläge/Zusammenfassung
try:
    from src.m08_llm import providers_available, generate_role_text as _gen  # wir missbrauchen es für generischen Text
except Exception:
    providers_available = lambda: ["openai"]  # type: ignore
    def _gen(provider: str, title: str, group: str) -> str:  # type: ignore
        return f"# Aufgabe: {title}\n\n**Kategorie:** {group or '—'}\n\n_Beschreibung …_"

# --- UI Konstanten / Layout ------------------------------------------------
SECTION_H = 1060
LIST_H    = 792
PAD_TARGET = 15
GAP_AFTER_PIPE = 5
MAX_GROUP_LEN = PAD_TARGET

st.title("Aufgaben – Native (ohne AgGrid)")

if not BACKEND_OK:
    st.error("Backend **src.m07_tasks** fehlt. Erwartet: list_tasks_df, load_task, upsert_task, soft_delete_task, function_suggestions.")
    st.stop()

# --- CSS (ident zu 13, leicht getuned) ------------------------------------
st.markdown("""
<style>
:root{ --btn-blue:#60a5fa; --btn-blue-border:#60a5fa; --btn-grey:#e5e7eb; --btn-grey-border:#d1d5db; }
.stButton>button{ padding:.35rem .7rem; font-size:.9rem; border-radius:10px; text-align:left; justify-content:flex-start;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; transition:.15s; }
.stButton>button:hover{ filter:brightness(0.98); } .stButton>button:active{ transform:scale(0.98); }
.stButton>button[kind="primary"]:not(:disabled){ background:var(--btn-blue)!important; color:#fff!important; border:1px solid var(--btn-blue-border)!important; }
.stButton>button[kind="primary"]:disabled{ background:var(--btn-grey)!important; color:#374151!important; border:1px solid var(--btn-grey-border)!important; }
div[data-baseweb="select"]>div{ background:#dbeafe!important; border-color:#93c5fd!important; min-height:40px!important; height:40px!important; padding:0 .6rem!important; }
#tnp_preview{ overflow-x:hidden; } #tnp_preview p, #tnp_preview li, #tnp_preview pre, #tnp_preview code{ white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; }
#tnp_list [data-testid="stVerticalBlock"], #tnp_list .element-container{ gap:0!important; margin:0!important; padding:0!important; }
#tnp_list .stButton>button{ padding:.08rem .45rem!important; line-height:1.0!important; width:100%!important; display:block!important; font-size:.86rem!important; white-space:pre!important; }
#tnp_pager .stButton>button{ white-space:nowrap!important; min-width:120px!important; }
</style>
""", unsafe_allow_html=True)

# --- Helpers ---------------------------------------------------------------
NBSP = "\u00A0"
def _normalize_spaces(s: str) -> str:
    if not s: return ""
    return re.sub(r"\s+"," ", s.replace("\u00A0"," "))

try:
    from wcwidth import wcswidth as _wcswidth
except Exception:
    _wcswidth = None  # type: ignore

def _vis_len(s: str) -> int:
    if _wcswidth is None: return len(s or "")
    try: return max(0, _wcswidth(s or ""))
    except Exception: return len(s or "")

def record_label(short: str, title: str, short_width: int = PAD_TARGET, gap_after_pipe: int = GAP_AFTER_PIPE) -> str:
    s_disp = _normalize_spaces((short or "").strip()) or "—"
    t_disp = (title or "").strip()
    pad = max(0, short_width - _vis_len(s_disp))
    return f"{s_disp}{NBSP*pad}|{NBSP*gap_after_pipe}{t_disp}"

def _strip_md_fence(text: str) -> str:
    if not text: return ""
    lines = text.splitlines()
    if not lines: return text
    first = lines[0].strip().lower()
    if first.startswith("```") and ("markdown" in first or first in ("```md","```markdown")):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text

def wrap_md(text: str, width: int = 100) -> str:
    text = _strip_md_fence(text)
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
        elif re.match(r"^\s*#{1,6}\s", line):
            out.append(line)
        else:
            out.append(textwrap.fill(s, width=width, break_long_words=False, break_on_hyphens=False))
    return "\n".join(out)

def _slug(s:str)->str:
    s=s.strip().lower(); s=re.sub(r"[^a-z0-9_-]+","-", s)
    return re.sub(r"-+","-", s).strip("-") or "task"

# --- State -----------------------------------------------------------------
st.session_state.setdefault("tnp_query","")
st.session_state.setdefault("tnp_sort_by","Rollenbezeichnung")
st.session_state.setdefault("tnp_sort_dir","aufsteigend")
st.session_state.setdefault("tnp_page",1)
st.session_state.setdefault("tnp_page_size",8)
st.session_state.setdefault("tnp_selected_key","")

# Formularfelder
st.session_state.setdefault("tnp_edit_key","")
st.session_state.setdefault("tnp_last_loaded",None)

st.session_state.setdefault("tnp_title","")          # Name/Beschreibung der Aufgabe
st.session_state.setdefault("tnp_group","")          # Kategorie/Typ (Kürzel)
st.session_state.setdefault("tnp_role_search","")    # Suchhilfe für Standard-Rolle
st.session_state.setdefault("tnp_role_key","")       # Key der zugewiesenen Rolle
st.session_state.setdefault("tnp_skills","")         # Fähigkeiten/Expertise
st.session_state.setdefault("tnp_duties","")         # Aufgaben / Pflichten
st.session_state.setdefault("tnp_links","")          # Links (CSV/Markdown-Liste)
st.session_state.setdefault("tnp_summary","")        # Zusammenfassung (manuell/LLM)
st.session_state.setdefault("tnp_body","")           # Freier Markdown-Teil (optional)

# Doku-Suche/Upload
st.session_state.setdefault("tnp_doc_q","")
st.session_state.setdefault("tnp_doc_selected_ids",[])  # verknüpfte IDs (Liste)
st.session_state.setdefault("tnp_wrap_now",False)
st.session_state.setdefault("tnp_prev_query","")
st.session_state.setdefault("tnp_prev_sort_by","Rollenbezeichnung")
st.session_state.setdefault("tnp_prev_sort_dir","aufsteigend")
st.session_state.setdefault("tnp_prev_page_size",8)
st.session_state.setdefault("tnp_last_click_key","")
st.session_state.setdefault("tnp_last_click_ts",0.0)
st.session_state.setdefault("tnp_form_clear",False)
st.session_state.setdefault("tnp_group_autotrim",False)

# --- Data ------------------------------------------------------------------
df = list_tasks_df(include_deleted=False)
if df is None:
    df = list_tasks_df(include_deleted=True)

col_list, col_preview, col_form = st.columns([0.8, 1.35, 0.95])

# ========== Liste (links) ==================================================
with col_list:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Aufgaben (Liste)")
        c1, _, _ = st.columns([0.9, 0.55, 0.45])
        with c1:
            q = st.text_input("Suchen", key="tnp_query", placeholder="z. B. Review, Research …")

        c4, c5, c6 = st.columns([0.45, 0.8, 0.75])
        with c4:
            st.selectbox("Einträge pro Seite", [8,12,20,30,50], key="tnp_page_size")
            if st.session_state["tnp_page_size"] != st.session_state["tnp_prev_page_size"]:
                st.session_state["tnp_prev_page_size"] = st.session_state["tnp_page_size"]
                st.session_state["tnp_page"] = 1
        with c5:
            opts = ["Rollenbezeichnung","Kürzel"]
            if st.session_state.get("tnp_sort_by") not in opts:
                st.session_state["tnp_sort_by"] = "Rollenbezeichnung"
            sort_by = st.selectbox("Sortieren nach", opts, key="tnp_sort_by")
        with c6:
            sort_dir = st.selectbox("Richtung", ["aufsteigend","absteigend"], key="tnp_sort_dir")

        changed = (
            (q != st.session_state["tnp_prev_query"]) or
            (sort_by != st.session_state["tnp_prev_sort_by"]) or
            (sort_dir != st.session_state["tnp_prev_sort_dir"])
        )
        if changed:
            st.session_state["tnp_page"] = 1
            st.session_state["tnp_prev_query"] = q
            st.session_state["tnp_prev_sort_by"] = sort_by
            st.session_state["tnp_prev_sort_dir"] = sort_dir
            if sort_by != st.session_state["tnp_prev_sort_by"]:
                st.rerun()

        if df is not None and not df.empty:
            data = df.rename(columns={"Titel":"Rollenbezeichnung","Funktion":"Kürzel"}).copy()
            for c in ("Key","Rollenbezeichnung","Kürzel"):
                data[c] = data[c].astype(str).fillna("")

            # Body-Volltext optional
            @st.cache_data(show_spinner=False)
            def _body_map(keys:list[str]):
                out={}
                for k in keys:
                    try: _,b = load_task(str(k))
                    except Exception: b=""
                    out[str(k)] = b or ""
                return out
            bodies = _body_map(list(data["Key"]))
            beschr = data["Key"].map(lambda k: bodies.get(str(k),""))

            qraw = q or ""; qn = qraw.strip().lower()
            if qn and qn != "*":
                if ("*" in qn) or ("?" in qn):
                    pat = re.escape(qn).replace("\\*",".*").replace("\\?"," .")
                    mask = (
                        data["Key"].str.lower().str.contains(pat, regex=True) |
                        data["Rollenbezeichnung"].str.lower().str.contains(pat, regex=True) |
                        data["Kürzel"].str.lower().str.contains(pat, regex=True) |
                        beschr.str.lower().str.contains(pat, regex=True)
                    )
                else:
                    mask = (
                        data["Key"].str.lower().str.contains(qn, regex=False) |
                        data["Rollenbezeichnung"].str.lower().str.contains(qn, regex=False) |
                        data["Kürzel"].str.lower().str.contains(qn, regex=False) |
                        beschr.str.lower().str.contains(qn, regex=False)
                    )
                data = data[mask]

            by = st.session_state["tnp_sort_by"]; asc = (st.session_state["tnp_sort_dir"]=="aufsteigend")
            if by in data.columns:
                data = data.sort_values(by=by, ascending=asc, kind="mergesort")

            total=len(data); pz=st.session_state["tnp_page_size"]; maxp=max(1, math.ceil(total/pz))
            st.session_state["tnp_page"]=min(maxp, max(1, st.session_state["tnp_page"]))
            page=st.session_state["tnp_page"]

            start=(page-1)*pz; end=start+pz
            page_df=data.iloc[start:end].reset_index(drop=True)

            with st.container():
                st.markdown('<div id="tnp_pager">', unsafe_allow_html=True)
                left,spacer,right=st.columns([1.2,6.0,1.2])
                with left:
                    if st.button("◀︎ Zurück", disabled=(page<=1), type="primary", use_container_width=True):
                        st.session_state["tnp_page"]=max(1,page-1); st.rerun()
                with right:
                    if st.button("Weiter ▶︎", disabled=(page>=maxp), type="primary", use_container_width=True):
                        st.session_state["tnp_page"]=min(maxp,page+1); st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

            st.caption(f"Treffer: {total} • Seite {page}/{maxp}")

            per_row_px=28
            content_h=12+len(page_df)*per_row_px
            container = st.container() if content_h<=LIST_H else st.container(height=LIST_H)
            with container:
                st.markdown('<div id="tnp_list">', unsafe_allow_html=True)
                if page_df.empty:
                    st.info("Keine Einträge.")
                else:
                    for i,row in page_df.iterrows():
                        key=str(row["Key"]); title=str(row["Rollenbezeichnung"]); short=str(row["Kürzel"])
                        label=record_label(short,title)
                        if st.button(label, key=f"tnp_row_{page}_{i}_{key}", type="secondary", use_container_width=True):
                            now=time.time()
                            last_k=st.session_state.get("tnp_last_click_key","")
                            last_t=st.session_state.get("tnp_last_click_ts",0.0)
                            if last_k==key and (now-last_t)<0.5:
                                st.session_state["tnp_edit_key"]=key
                                st.session_state["tnp_last_loaded"]=None
                                st.session_state["tnp_last_click_key"]=""
                                st.session_state["tnp_last_click_ts"]=0.0
                                st.rerun()
                            else:
                                st.session_state["tnp_selected_key"]=key
                                st.session_state["tnp_last_click_key"]=key
                                st.session_state["tnp_last_click_ts"]=now
                                st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.info("Keine Aufgaben vorhanden.")
            st.session_state["tnp_selected_key"] = ""

# ========== Vorschau (Mitte) ==============================================
with col_preview:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Vorschau")
        st.markdown('<div id="tnp_preview">', unsafe_allow_html=True)
        vkey = st.session_state.get("tnp_selected_key","")
        if vkey:
            obj, body = load_task(vkey)
            cap_left = (getattr(obj, "short_code", None) or "—") if obj else "—"
            st.caption(f"{cap_left} | {obj.title if obj else ''}")
            st.markdown(body or "_(leer)_")
            c1,c2,c3 = st.columns([1,1,1])
            with c1:
                if st.button("Bearbeiten", key="tnp_prev_edit", type="primary"):
                    st.session_state["tnp_edit_key"]=vkey
                    st.session_state["tnp_last_loaded"]=None
                    st.rerun()
            with c2:
                try:
                    _, body_dl = load_task(vkey)
                    st.download_button("Markdown ⬇️", data=body_dl or "", file_name=f"{vkey}.md", mime="text/markdown")
                except Exception:
                    st.button("Markdown ⬇️", disabled=True)
            with c3:
                if st.button("Record löschen", key="tnp_prev_del", type="primary"):
                    if soft_delete_task(vkey):
                        st.toast("Eintrag gelöscht")
                        st.session_state["tnp_selected_key"]=""
                        st.session_state["tnp_edit_key"]=""
                        st.session_state["tnp_form_clear"]=True
                        st.rerun()
        else:
            st.info("Bitte links einen Eintrag anklicken.")
        st.markdown('</div>', unsafe_allow_html=True)

# ========== Formular (rechts) =============================================
with col_form:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Neue Aufgabe / Bearbeiten")

        ekey = st.session_state.get("tnp_edit_key","")
        obj_e, body_e = load_task(ekey) if ekey else (None, "")

        if st.session_state.get("tnp_form_clear"):
            for k in ["tnp_edit_key","tnp_last_loaded","tnp_title","tnp_group","tnp_role_search","tnp_role_key",
                      "tnp_skills","tnp_duties","tnp_links","tnp_summary","tnp_body","tnp_doc_q"]:
                st.session_state[k] = "" if not k.endswith("_ids") else []
            st.session_state["tnp_form_clear"]=False

        # Beim Wechsel Datensatz in Felder „parsen“ (wir legen alles in body als Markdown ab)
        if ekey and st.session_state.get("tnp_last_loaded") != ekey and obj_e:
            st.session_state["tnp_title"] = obj_e.title
            st.session_state["tnp_group"] = (getattr(obj_e, "short_code", None) or "")
            raw = body_e or ""
            # Versuch: Abschnitte rückwärts aus Markdown extrahieren (best-effort)
            def _extract(sec: str) -> str:
                m = re.search(rf"^## {re.escape(sec)}\s*\n(.*?)(?:\n## |\Z)", raw, flags=re.S|re.M)
                return (m.group(1).strip() if m else "")
            st.session_state["tnp_summary"] = _extract("Zusammenfassung")
            st.session_state["tnp_skills"]   = _extract("Fähigkeiten / Expertise")
            st.session_state["tnp_duties"]   = _extract("Aufgaben / Pflichten")
            st.session_state["tnp_links"]    = _extract("Links")
            # Role Key aus Kopfzeile?
            rk = re.search(r"^**Standard-Rolle:**\s*\[(.*?)\]", raw, flags=re.M)
            st.session_state["tnp_role_key"] = rk.group(1).strip() if rk else ""
            st.session_state["tnp_body"] = _strip_md_fence(raw)  # für Editor sichtbar lassen
            st.session_state["tnp_last_loaded"] = ekey

        # --- Schnellauswahl für Titel/Kategorie
        try:
            sugg = function_suggestions()
        except Exception:
            sugg = ["Bug","Feature","Research","Review","ETL","Audit","Migration","Meeting Notes"]
        try:
            from src.m06_ui import chips, md_editor_with_preview
        except Exception:
            def chips(*args, **kwargs): pass
            def md_editor_with_preview(label, value, key, height=300): st.text_area(label, value=value, key=key, height=height)
        chips(
            sugg[:8],
            target_title_key="tnp_title",
            target_function_key="tnp_group",
            state_key="tnp_quickpick",
            label="Schnellauswahl",
            title_map={s:s for s in sugg},
        )

        # --- Titel & Kategorie (Kürzel)
        st.markdown("**Name / Beschreibung der Aufgabe**")
        st.text_input(" ", key="tnp_title", label_visibility="collapsed", placeholder="z. B. Review Security Policies …")

        st.markdown("**Kategorie (Kürzel)**")
        st.text_input(" ", key="tnp_group", label_visibility="collapsed", placeholder="Bug / Feature / Research …")
        _g = st.session_state.get("tnp_group","") or ""; _glen=len(_g)
        h1,h2=st.columns([1,1])
        with h1: st.caption(f"Länge: {_glen}/{MAX_GROUP_LEN}")
        with h2: st.checkbox(f"Automatisch kürzen (max {MAX_GROUP_LEN})", key="tnp_group_autotrim")
        if _glen > MAX_GROUP_LEN and not st.session_state.get("tnp_group_autotrim", False):
            st.warning(f"Kategorie ist länger als {MAX_GROUP_LEN} Zeichen.")

        # --- Standard-Rolle zuweisen (kompakter Such-Picker)
        st.markdown("**Standard-Rolle (Zuweisung)**")
        r1, r2 = st.columns([0.6, 0.4])
        with r2:
            st.text_input("Suche", key="tnp_role_search", placeholder="Tippen zum Filtern …")
        role_options = []
        if ROLES_OK:
            try:
                rdf = list_roles_df(include_deleted=False)
                if rdf is not None and not rdf.empty:
                    q = (st.session_state.get("tnp_role_search","") or "").strip().lower()
                    rdf = rdf[["Key","Titel","Funktion"]].rename(columns={"Titel":"Rollenbezeichnung","Funktion":"Kürzel"})
                    if q:
                        m = (
                            rdf["Key"].astype(str).str.lower().str.contains(q, regex=False) |
                            rdf["Rollenbezeichnung"].astype(str).str.lower().str.contains(q, regex=False) |
                            rdf["Kürzel"].astype(str).fillna("").str.lower().str.contains(q, regex=False)
                        )
                        rdf = rdf[m]
                    role_options = [f"{row['Kürzel'] or '—'} | {row['Rollenbezeichnung']}  ·  [{row['Key']}]" for _,row in rdf.iterrows()]
            except Exception:
                role_options = []
        with r1:
            sel = st.selectbox(" ", options=["(keine)"] + role_options, label_visibility="collapsed")
        # Speichere den Key aus der Klammer
        if sel and sel != "(keine)":
            mk = re.search(r"\[(.*?)\]\s*$", sel)
            st.session_state["tnp_role_key"] = mk.group(1) if mk else ""
        elif sel == "(keine)":
            st.session_state["tnp_role_key"] = ""

        # --- Fähigkeiten / Pflichten
        st.markdown("**Fähigkeiten / Expertise / Spezialwissen**")
        st.text_area(" ", key="tnp_skills", label_visibility="collapsed", placeholder="z. B. IAM, SIEM, CloudSec …", height=120)

        st.markdown("**Aufgaben / Pflichten**")
        st.text_area(" ", key="tnp_duties", label_visibility="collapsed", placeholder="Stichpunkte oder Markdown …", height=150)

        # --- Links
        st.markdown("**Links (eine pro Zeile, optional Markdown-Format)**")
        st.text_area(" ", key="tnp_links", label_visibility="collapsed", placeholder="https://…  oder  [Name](URL)", height=90)

        # --- Dokumente (Suche + Upload)
        with st.expander("Dokumente verknüpfen / hochladen", expanded=False):
            cdl, cdr = st.columns([0.55, 0.45])
            with cdr:
                st.text_input("Suchen (Dateiname/Inhalt)", key="tnp_doc_q", placeholder="Wildcard * und ? möglich")
            with cdl:
                if DOCS_OK:
                    try:
                        ddf = list_documents_df()
                    except Exception:
                        ddf = _empty_docs_df()
                else:
                    ddf = _empty_docs_df()

                if ddf is not None and not ddf.empty:
                    q = (st.session_state.get("tnp_doc_q","") or "").strip().lower()
                    if q and q != "*":
                        if ("*" in q) or ("?" in q):
                            pat = re.escape(q).replace("\\*",".*").replace("\\?"," .")
                            mask = ddf["name"].astype(str).str.lower().str.contains(pat, regex=True) | ddf.get("desc","").astype(str).str.lower().str.contains(pat, regex=True)
                        else:
                            mask = ddf["name"].astype(str).str.lower().str.contains(q, regex=False) | ddf.get("desc","").astype(str).str.lower().str.contains(q, regex=False)
                        ddf = ddf[mask]
                    st.caption(f"Dokumente: {len(ddf)} Treffer")
                    opts = [f"{row['name']}  ·  ({row['id']})" for _,row in ddf.iterrows()]
                    sel_docs = st.multiselect("Vorhandene Dokumente verknüpfen", options=opts, key="tnp_doc_multi")
                    # Halte nur die IDs
                    ids=[]
                    for s in sel_docs:
                        mm=re.search(r"\((.*?)\)\s*$", s); 
                        if mm: ids.append(mm.group(1))
                    st.session_state["tnp_doc_selected_ids"]=ids
                else:
                    st.info("Kein Dokumenten-Index verfügbar.")

            st.file_uploader("Dateien hochladen (optional)", key="tnp_doc_upload", accept_multiple_files=True)

        # --- Zusammenfassung & freier Body
        st.markdown("**Zusammenfassung (Kurzfassung aller Inhalte)**")
        st.text_area(" ", key="tnp_summary", label_visibility="collapsed", placeholder="Eine knappe Executive Summary …", height=100)

        # LLM-Schnellknöpfe
        prov = st.session_state.get("global_llm_provider", "openai")
        model = st.session_state.get("global_llm_model")
        temp = st.session_state.get("global_llm_temperature", 0.7)
        
        l1,l2 = st.columns([1.5,1.5])
        with l1:
            st.caption("🤖 **KI:** " + f"{prov} → {model or '—'} (T={temp:.1f})")
        with l2:
            if st.button("KI-Vorschlag einfügen (Basisbeschreibung)", type="primary", use_container_width=True):
                try:
                    s=_gen(prov if prov != "none" else "openai",
                           st.session_state.get("tnp_title",""),
                           st.session_state.get("tnp_group",""))
                    st.session_state["tnp_body"]=wrap_md(s, width=100); st.rerun()
                except Exception as ex:
                    st.error(f"KI-Generierung fehlgeschlagen: {ex}")

        st.markdown("**Freier Markdown-Teil (optional)**")
        try:
            from src.m06_ui import md_editor_with_preview
            md_editor_with_preview(" ", st.session_state.get("tnp_body",""), key="tnp_body", height=220)
        except Exception:
            st.text_area(" ", key="tnp_body", height=220, label_visibility="collapsed")

        # --- Aktionen (Umbrechen / Speichern / Löschen)
        b1,b2,b3 = st.columns([1,1,1])
        with b1:
            if st.button("Zeilen umbrechen", type="primary", use_container_width=True):
                st.session_state["tnp_body"]=wrap_md(st.session_state.get("tnp_body",""), width=100); st.rerun()
        with b2:
            if st.button("Speichern", type="primary", use_container_width=True):
                title=(st.session_state.get("tnp_title","") or "").strip()
                if not title:
                    st.error("Bitte **Name/Beschreibung** ausfüllen."); st.stop()
                group_raw=st.session_state.get("tnp_group","") or ""
                if len(group_raw)>MAX_GROUP_LEN and not st.session_state.get("tnp_group_autotrim", False):
                    st.error(f"Kategorie zu lang (> {MAX_GROUP_LEN})."); st.stop()
                group=(group_raw[:MAX_GROUP_LEN] if st.session_state.get("tnp_group_autotrim", False) else group_raw) or None

                # Konsolidiertes Markdown erzeugen
                role_info = st.session_state.get("tnp_role_key","") or "—"
                skills = st.session_state.get("tnp_skills","")
                duties = st.session_state.get("tnp_duties","")
                links  = st.session_state.get("tnp_links","")
                summ   = st.session_state.get("tnp_summary","")
                body_free = st.session_state.get("tnp_body","")

                docs_ids = st.session_state.get("tnp_doc_selected_ids",[])
                docs_block = ""
                if docs_ids:
                    docs_block = "\n".join([f"- (Doc-ID) `{i}`" for i in docs_ids])

                full_md = f"""# Aufgabe: {title}

**Kategorie:** {group or '—'}  
**Standard-Rolle:** [{role_info}]

## Zusammenfassung
{summ or ''}

## Fähigkeiten / Expertise
{skills or ''}

## Aufgaben / Pflichten
{duties or ''}

## Links
{links or ''}

## Verknüpfte Dokumente
{docs_block or '_(keine)_'}

## Freitext
{body_free or ''}
"""
                ekey = st.session_state.get("tnp_edit_key","")
                key_to_use = ekey if (ekey and _slug(title)==_slug(ekey)) else None
                r, created = upsert_task(title=title, short_title=title, short_code=group or None, body_text=full_md, key=key_to_use)
                st.toast(("Angelegt" if created else "Aktualisiert")+f": {r.key}")
                st.session_state["tnp_selected_key"]=r.key
                st.session_state["tnp_form_clear"]=True
                st.rerun()
        with b3:
            dis = not bool(st.session_state.get("tnp_edit_key",""))
            if st.button("Record löschen", disabled=dis, type="primary", use_container_width=True):
                ekey = st.session_state.get("tnp_edit_key","")
                if ekey and soft_delete_task(ekey):
                    st.toast("Eintrag gelöscht")
                    st.session_state["tnp_selected_key"]=""
                    st.session_state["tnp_edit_key"]=""
                    st.session_state["tnp_form_clear"]=True
                    st.rerun()
