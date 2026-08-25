from __future__ import annotations
import sys, math, re, time
from pathlib import Path
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Rollen – Native", page_icon="👥", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m06_ui import chips, md_editor_with_preview
from src.m07_roles import list_roles_df, load_role, upsert_role, function_suggestions
from src.m08_llm import providers_available, generate_role_details
import textwrap

SECTION_H = 1060
LIST_H    = 792

PAD_TARGET = 15
GAP_AFTER_PIPE = 5
MAX_GROUP_LEN = PAD_TARGET

st.title("Rollen – Native (ohne AgGrid)")

st.markdown(
        """
        <style>
        :root{ --btn-blue:#60a5fa; --btn-blue-border:#60a5fa; --btn-grey:#e5e7eb; --btn-grey-border:#d1d5db; }
        .stButton>button{ padding:.35rem .7rem; font-size:.9rem; border-radius:10px; text-align:left; justify-content:flex-start; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \"Liberation Mono\", monospace; transition: background-color .15s ease, border-color .15s ease, transform .03s ease, box-shadow .15s ease; }
        .stButton>button *{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \"Liberation Mono\", monospace !important; }
        .stButton>button:hover{ filter: brightness(0.98); }
        .stButton>button:active{ transform: scale(0.98); }
        .stButton>button[kind="primary"]:not(:disabled), .stButton>button[data-testid="baseButton-primary"]:not(:disabled){ background:var(--btn-blue) !important; color:#fff !important; border:1px solid var(--btn-blue-border) !important; }
        .stButton>button[kind="primary"]:not(:disabled):hover, .stButton>button[data-testid="baseButton-primary"]:not(:disabled):hover{ background:#3b82f6 !important; border-color:#3b82f6 !important; box-shadow:0 0 0 3px rgba(59,130,246,.25) !important; }
        .stButton>button[kind="primary"]:not(:disabled):active, .stButton>button[data-testid="baseButton-primary"]:not(:disabled):active{ background:#2563eb !important; border-color:#2563eb !important; }
        .stButton>button[kind="primary"]:disabled, .stButton>button[data-testid="baseButton-primary"]:disabled{ background:var(--btn-grey) !important; color:#374151 !important; border:1px solid var(--btn-grey-border) !important; opacity:1 !important; }
        .stButton>button[kind="secondary"], .stButton>button[data-testid="baseButton-secondary"], .stButton>button:not([kind]){ background:#fff !important; color:#111827 !important; border:1px solid #e5e7eb !important; }
        .stButton>button[kind="secondary"]:hover, .stButton>button[data-testid="baseButton-secondary"]:hover, .stButton>button:not([kind]):hover{ background:#f9fafb !important; }
        .stButton>button[kind="secondary"]:active, .stButton>button[data-testid="baseButton-secondary"]:active, .stButton>button:not([kind]):active{ background:#f3f4f6 !important; }
        div[data-baseweb="select"]>div{ background:#dbeafe !important; border-color:#93c5fd !important; min-height:40px !important; height:40px !important; padding:0 .6rem !important; }
        #rn_preview{ overflow-x:hidden; }
        #rn_preview p, #rn_preview li, #rn_preview pre, #rn_preview code{ white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; }
        .stMarkdown p, .stMarkdown li, .stMarkdown pre, .stMarkdown code{ white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; }
        #rn_list [data-testid="stVerticalBlock"], #rn_list .element-container{ gap:0 !important; margin:0 !important; padding:0 !important; }
        #rn_list .stButton{ margin:0 !important; padding:0 !important; }
        #rn_list .stMarkdown{ margin:0 !important; padding:0 !important; }
        #rn_list p{ margin:0 !important; }
        #rn_list .stButton>button{ padding:.08rem .45rem !important; line-height:1.0 !important; width:100% !important; display:block !important; font-size:.86rem !important; white-space:pre !important; letter-spacing:0 !important; font-variant-ligatures:none !important; }
        #rn_pager .stButton>button{ white-space:nowrap !important; min-width:120px !important; }
        </style>
        """,
        unsafe_allow_html=True,
)

NBSP = "\u00A0"

def _normalize_spaces(s: str) -> str:
    if not s:
        return ""
    s = (s.replace("\u00A0", " "))
    s = re.sub(r"\s+", " ", s)
    return s

try:
    import importlib.util as _ilus  # type: ignore
    _spec = _ilus.find_spec("wcwidth")
    if _spec is not None:
        from wcwidth import wcswidth as _wcswidth  # type: ignore
    else:
        _wcswidth = None  # type: ignore
except Exception:  # pragma: no cover
    _wcswidth = None  # type: ignore

def _vis_len(s: str) -> int:
    if _wcswidth is None:
        return len(s or "")
    try:
        w = _wcswidth(s or "")
        return max(0, w)
    except Exception:
        return len(s or "")

def record_label(short: str, title: str, short_width: int = PAD_TARGET, gap_after_pipe: int = GAP_AFTER_PIPE) -> str:
    s_disp = _normalize_spaces((short or "").strip()) or "—"
    t_disp = (title or "").strip()
    cur_w  = _vis_len(s_disp)
    pad_len = max(0, short_width - cur_w)
    filler_after_short = NBSP * pad_len
    gap = NBSP * max(0, gap_after_pipe)
    return f"{s_disp}{filler_after_short}|{gap}{t_disp}"

def _strip_leading_markdown_fence(text: str) -> str:
    if not text:
        return text or ""
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0].strip().lower()
    if first.startswith("```") and ("markdown" in first or first == "```md" or first == "```markdown"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
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
        elif re.match(r"^\s*#{1,6}\s", line):
            out.append(line)
        else:
            out.append(textwrap.fill(s, width=width, break_long_words=False, break_on_hyphens=False))
    return "\n".join(out)

st.session_state.setdefault("rn_query", "")
st.session_state.setdefault("rn_sort_by", "Rollenbezeichnung")
st.session_state.setdefault("rn_sort_dir", "aufsteigend")
st.session_state.setdefault("rn_page", 1)
st.session_state.setdefault("rn_page_size", 8)
st.session_state.setdefault("rn_selected_key", "")

st.session_state.setdefault("rn_edit_key", "")
st.session_state.setdefault("rn_last_loaded", None)
st.session_state.setdefault("rn_title", "")
st.session_state.setdefault("rn_group", "")
st.session_state.setdefault("rn_body", "")

st.session_state.setdefault("rn_prev_query", "")
st.session_state.setdefault("rn_prev_sort_by", st.session_state["rn_sort_by"])
st.session_state.setdefault("rn_prev_sort_dir", st.session_state["rn_sort_dir"])
st.session_state.setdefault("rn_prev_page_size", st.session_state["rn_page_size"])
st.session_state.setdefault("rn_last_click_key", "")
st.session_state.setdefault("rn_last_click_ts", 0.0)
st.session_state.setdefault("rn_form_clear", False)
st.session_state.setdefault("rn_wrap_now", False)
st.session_state.setdefault("rn_group_autotrim", False)

df = list_roles_df(include_deleted=False)
if df is None:
    df = list_roles_df(include_deleted=True)

col_list, col_preview, col_form = st.columns([0.8, 1.35, 0.95])

with col_list:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Rollen (Native Liste)")

        c1, _s1, _s2 = st.columns([0.9, 0.55, 0.45])
        with c1:
            q = st.text_input("Suchen", key="rn_query", placeholder="z. B. CISO oder Chief Information …")

        c4, c5, c6 = st.columns([0.45, 0.8, 0.75])
        with c4:
            st.selectbox("Einträge pro Seite", [8, 12, 20, 30, 50], key="rn_page_size")
            if st.session_state["rn_page_size"] != st.session_state["rn_prev_page_size"]:
                st.session_state["rn_prev_page_size"] = st.session_state["rn_page_size"]
                st.session_state["rn_page"] = 1
        with c5:
            sort_options = ["Rollenbezeichnung", "Rollenkürzel", "Übergeordnete Rolle", "Hauptverantwortlichkeiten", "Qualifikationen", "Expertise"]
            if st.session_state.get("rn_sort_by") not in sort_options:
                st.session_state["rn_sort_by"] = "Rollenbezeichnung"
            sort_by = st.selectbox("Sortieren nach", sort_options, key="rn_sort_by")
        with c6:
            sort_dir = st.selectbox("Richtung", ["aufsteigend", "absteigend"], key="rn_sort_dir")

        changed_q    = (q != st.session_state["rn_prev_query"])
        changed_sort = (sort_by != st.session_state["rn_prev_sort_by"])
        changed_dir  = (sort_dir != st.session_state["rn_prev_sort_dir"])
        if changed_q or changed_sort or changed_dir:
            st.session_state["rn_page"] = 1
            st.session_state["rn_prev_query"] = q
            st.session_state["rn_prev_sort_by"] = sort_by
            st.session_state["rn_prev_sort_dir"] = sort_dir
            if changed_sort:
                st.rerun()

        if df is not None and not df.empty:
            data = df.copy()

            data["Key"]               = data["Key"].astype(str).fillna("")
            data["Rollenbezeichnung"] = data["Rollenbezeichnung"].astype(str).fillna("")
            data["Rollenkürzel"]      = data["Rollenkürzel"].astype(str).fillna("")

            @st.cache_data(show_spinner=False)
            def _body_map(keys: list[str]):
                from src.m07_roles import load_role
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

            qraw  = q or ""
            qnorm = qraw.strip().lower()

            if qnorm and qnorm != "*":
                if ("*" in qnorm) or ("?" in qnorm):
                    pattern = re.escape(qnorm).replace("\\*", ".*").replace("\\?", ".")
                    key_m   = data["Key"].str.lower().str.contains(pattern, regex=True)
                    title_m = data["Rollenbezeichnung"].str.lower().str.contains(pattern, regex=True)
                    short_m = data["Rollenkürzel"].str.lower().str.contains(pattern, regex=True) if "Rollenkürzel" in data.columns else pd.Series(False, index=data.index)
                    func_m  = data["Übergeordnete Rolle"].str.lower().str.contains(pattern, regex=True) if "Übergeordnete Rolle" in data.columns else pd.Series(False, index=data.index)
                    resp_m  = data["Hauptverantwortlichkeiten"].str.lower().str.contains(pattern, regex=True) if "Hauptverantwortlichkeiten" in data.columns else pd.Series(False, index=data.index)
                    qual_m  = data["Qualifikationen"].str.lower().str.contains(pattern, regex=True) if "Qualifikationen" in data.columns else pd.Series(False, index=data.index)
                    exp_m   = data["Expertise"].str.lower().str.contains(pattern, regex=True) if "Expertise" in data.columns else pd.Series(False, index=data.index)
                    body_m  = beschreibung.str.lower().str.contains(pattern, regex=True)
                else:
                    key_m   = data["Key"].str.lower().str.contains(qnorm, regex=False)
                    title_m = data["Rollenbezeichnung"].str.lower().str.contains(qnorm, regex=False)
                    short_m = data["Rollenkürzel"].str.lower().str.contains(qnorm, regex=False) if "Rollenkürzel" in data.columns else pd.Series(False, index=data.index)
                    func_m  = data["Übergeordnete Rolle"].str.lower().str.contains(qnorm, regex=False) if "Übergeordnete Rolle" in data.columns else pd.Series(False, index=data.index)
                    resp_m  = data["Hauptverantwortlichkeiten"].str.lower().str.contains(qnorm, regex=False) if "Hauptverantwortlichkeiten" in data.columns else pd.Series(False, index=data.index)
                    qual_m  = data["Qualifikationen"].str.lower().str.contains(qnorm, regex=False) if "Qualifikationen" in data.columns else pd.Series(False, index=data.index)
                    exp_m   = data["Expertise"].str.lower().str.contains(qnorm, regex=False) if "Expertise" in data.columns else pd.Series(False, index=data.index)
                    body_m  = beschreibung.str.lower().str.contains(qnorm, regex=False)
                data = data[key_m | title_m | short_m | func_m | resp_m | qual_m | exp_m | body_m]

            # Sortier-Mapping für neue Spalten
            sort_mapping = {
                "Rollenbezeichnung": "Rollenbezeichnung",
                "Rollenkürzel": "Rollenkürzel", 
                "Übergeordnete Rolle": "Übergeordnete Rolle",
                "Hauptverantwortlichkeiten": "Hauptverantwortlichkeiten",
                "Qualifikationen": "Qualifikationen", 
                "Expertise": "Expertise"
            }
            by = sort_mapping.get(st.session_state["rn_sort_by"], "Rollenbezeichnung")
            ascend = st.session_state["rn_sort_dir"] == "aufsteigend"
            if by in data.columns:
                data = data.sort_values(by=by, ascending=ascend, kind="mergesort")

            total = len(data)
            page_size = st.session_state["rn_page_size"]
            max_page = max(1, math.ceil(total / page_size))
            st.session_state["rn_page"] = min(max_page, max(1, st.session_state["rn_page"]))
            page = st.session_state["rn_page"]

            start = (page - 1) * page_size
            end   = start + page_size
            page_df = data.iloc[start:end].reset_index(drop=True)

            with st.container():
                st.markdown('<div id="rn_pager">', unsafe_allow_html=True)
                left, spacer, right = st.columns([1.2, 6.0, 1.2])
                with left:
                    if st.button("◀︎ Zurück", disabled=(page <= 1), type="primary", use_container_width=True):
                        st.session_state["rn_page"] = max(1, page - 1)
                        st.rerun()
                with right:
                    if st.button("Weiter ▶︎", disabled=(page >= max_page), type="primary", use_container_width=True):
                        st.session_state["rn_page"] = min(max_page, page + 1)
                        st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

            st.caption(f"Treffer: {total} • Seite {page}/{max_page}")

            per_row_px = 28
            content_h = 12 + len(page_df) * per_row_px
            list_container = st.container() if content_h <= LIST_H else st.container(height=LIST_H)
            with list_container:
                st.markdown('<div id="rn_list">', unsafe_allow_html=True)
                if page_df.empty:
                    st.info("Keine Einträge auf dieser Seite.")
                else:
                    for i, row in page_df.iterrows():
                        key   = str(row["Key"])
                        title = str(row["Rollenbezeichnung"])
                        short = "" if str(row.get("Kürzel","")) == "nan" else str(row.get("Kürzel",""))
                        label = record_label(short=short, title=title)

                        if st.button(label, key=f"rn_row_{page}_{i}_{key}", type="secondary", use_container_width=True):
                            now = time.time()
                            last_k = st.session_state.get("rn_last_click_key", "")
                            last_t = st.session_state.get("rn_last_click_ts", 0.0)
                            if last_k == key and (now - last_t) < 0.5:
                                st.session_state["rn_edit_key"] = key
                                st.session_state["rn_last_loaded"] = None
                                st.session_state["rn_last_click_key"] = ""
                                st.session_state["rn_last_click_ts"] = 0.0
                                st.rerun()
                            else:
                                st.session_state["rn_selected_key"] = key
                                st.session_state["rn_last_click_key"] = key
                                st.session_state["rn_last_click_ts"] = now
                                st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

        else:
            st.info("Keine Rollen gefunden.")
            st.session_state["rn_selected_key"] = ""

with col_preview:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Vorschau")
        st.markdown('<div id="rn_preview">', unsafe_allow_html=True)
        sel_key = st.session_state.get("rn_selected_key", "")
        if sel_key:
            obj, body = load_role(sel_key)
            
            # Erweiterte Vorschau mit allen Feldern
            st.markdown(f"**📝 {obj.title if obj else 'Unbekannt'}**")
            
            if hasattr(obj, 'short_code') and obj.short_code:
                st.markdown(f"**🔖 Kürzel:** {obj.short_code}")
            
            # Strukturierte Felder anzeigen
            if hasattr(obj, 'responsibilities') and obj.responsibilities:
                st.markdown("**🎯 Hauptverantwortlichkeiten:**")
                st.markdown(obj.responsibilities)
                
            if hasattr(obj, 'qualifications') and obj.qualifications:
                st.markdown("**🎓 Qualifikationen & Anforderungen:**")
                st.markdown(obj.qualifications)
                
            if hasattr(obj, 'expertise') and obj.expertise:
                st.markdown("**🧠 Expertise / Spezialwissen:**")
                st.markdown(obj.expertise)
            
            # Markdown-Beschreibung
            if body:
                st.markdown("**📄 Beschreibung / Profil:**")
                st.markdown(body)
            else:
                st.markdown("_(Keine Beschreibung)_")
            c1, c2, c3 = st.columns([1,1,1])
            with c1:
                if st.button("Bearbeiten", key="rn_preview_edit", type="primary"):
                    st.session_state["rn_edit_key"] = sel_key
                    st.session_state["rn_last_loaded"] = None
                    st.rerun()
            with c2:
                try:
                    _, body_dl = load_role(sel_key)
                    st.download_button("Markdown ⬇️", data=body_dl or "", file_name=f"{sel_key}.md", mime="text/markdown")
                except Exception:
                    st.button("Markdown ⬇️", disabled=True, use_container_width=True)
            with c3:
                if st.button("Record löschen", key="rn_preview_delete", type="primary"):
                    from src.m07_roles import soft_delete_role
                    if soft_delete_role(sel_key):
                        st.toast("Eintrag gelöscht")
                        st.session_state["rn_selected_key"] = ""
                        st.session_state["rn_edit_key"] = ""
                        st.session_state["rn_form_clear"] = True
                        st.rerun()
        else:
            st.info("Bitte links einen Eintrag anklicken.")
        st.markdown('</div>', unsafe_allow_html=True)

with col_form:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Neue Rolle / Bearbeiten")
        st.markdown('<div id="rn_form">', unsafe_allow_html=True)

        # Pending-Update anwenden (bevor Widgets instanziiert werden)
        if st.session_state.get("rn_ai_update"):
            for k, v in st.session_state["rn_ai_update"].items():
                st.session_state[k] = v
            st.session_state["rn_ai_update"] = None

        ekey = st.session_state.get("rn_edit_key", "")
        obj_e, body_e = load_role(ekey) if ekey else (None, "")

        if st.session_state.get("rn_form_clear"):
            st.session_state["rn_edit_key"] = ""
            st.session_state["rn_last_loaded"] = None
            st.session_state["rn_description"] = ""
            st.session_state["rn_title"] = ""
            st.session_state["rn_short_code"] = ""
            st.session_state["rn_body"]  = ""
            st.session_state["rn_responsibilities"] = ""
            st.session_state["rn_qualifications"] = ""
            st.session_state["rn_expertise"] = ""
            st.session_state["rn_form_clear"] = False

        if ekey and st.session_state.get("rn_last_loaded") != ekey and obj_e:
            from src.m11_role_markdown import extract_prose_intro
            
            st.session_state["rn_description"] = getattr(obj_e, 'description', '') or ""
            st.session_state["rn_title"] = obj_e.title
            st.session_state["rn_short_code"] = getattr(obj_e, 'short_code', '') or ""
            
            # Extract prose from markdown file for editing
            prose = extract_prose_intro(body_e or "")
            st.session_state["rn_body"]  = prose
            
            st.session_state["rn_responsibilities"] = getattr(obj_e, 'responsibilities', '') or ""
            st.session_state["rn_qualifications"] = getattr(obj_e, 'qualifications', '') or ""
            st.session_state["rn_expertise"] = getattr(obj_e, 'expertise', '') or ""
            st.session_state["rn_last_loaded"] = ekey

        st.markdown("**Gewünschte Rolle beschreiben**")
        st.text_input(" ", key="rn_description", label_visibility="collapsed", placeholder="z. B. Verantwortlich für IT-Strategie und digitale Transformation")

        # Kurz-Titel und Kürzel (kompakt in einer Zeile)
        cst1, cst2 = st.columns([1.3, 0.7])
        with cst1:
            st.text_input("Rollenbezeichnung (max 50)", key="rn_title", placeholder="z. B. Chief Information Officer", max_chars=50)
        with cst2:
            st.text_input("Kürzel (max 14)", key="rn_short_code", placeholder="z. B. CIO", max_chars=14)

        st.markdown("**Hauptverantwortlichkeiten**")
        st.text_area(" ", key="rn_responsibilities", label_visibility="collapsed", height=100,
                    placeholder="• Strategische Führung\n• Entscheidungsfindung\n• Teamleitung...")

        st.markdown("**Qualifikationen & Anforderungen**")
        st.text_area(" ", key="rn_qualifications", label_visibility="collapsed", height=100,
                    placeholder="• Studium in BWL/VWL\n• 5+ Jahre Führungserfahrung\n• Strategisches Denken...")

        st.markdown("**Expertise / Spezialwissen**")
        st.text_area(" ", key="rn_expertise", label_visibility="collapsed", height=100,
                    placeholder="• Digitale Transformation\n• Change Management\n• Unternehmensführung...")
        try:
            _g = st.session_state.get("rn_group", "") or ""
            _glen = len(_g)
        except Exception:
            _g, _glen = "", 0
        hint_cols = st.columns([1,1])
        with hint_cols[0]:
            st.caption(f"Kürzel-Länge: {_glen}/{MAX_GROUP_LEN}")
        with hint_cols[1]:
            st.checkbox(f"Automatisch kürzen (max {MAX_GROUP_LEN})", key="rn_group_autotrim")
        if _glen > MAX_GROUP_LEN:
            st.warning(f"Kürzel ist länger als {MAX_GROUP_LEN} Zeichen. Bitte kürzen oder 'Automatisch kürzen' aktivieren.")

        try:
            PROVS = providers_available()
        except Exception:
            PROVS = ["openai"]
        if PROVS and "llm_provider" not in st.session_state:
            st.session_state["llm_provider"] = PROVS[0]

        with st.container(border=True):
            kc1, kc2 = st.columns([1, 2])
            with kc1:
                st.markdown("**KI-Provider**")
                sel_idx = 0
                try:
                    sel_idx = PROVS.index(st.session_state.get("llm_provider", PROVS[0]))
                except Exception:
                    pass
                st.selectbox("KI-Provider", options=PROVS, index=sel_idx, key="llm_provider", label_visibility="collapsed")
            with kc2:
                st.markdown("**&nbsp;**")  # Spacer für Alignment
                if st.button("KI-Vorschlag", key="rn_ai_suggest", type="primary"):
                    try:
                        prov = st.session_state.get("global_llm_provider", "openai")
                        description = st.session_state.get("rn_description", "").strip()
                        if not description:
                            st.warning("⚠️ Bitte Rollenbeschreibung eingeben")
                        else:
                            title, short_code, responsibilities, qualifications, expertise, prose = generate_role_details(
                                prov if prov != "none" else "openai",
                                description,
                                model=st.session_state.get("global_llm_model"),
                                temperature=st.session_state.get("global_llm_temperature", 0.7)
                            )
                            st.session_state["rn_ai_update"] = {
                                "rn_title": title,
                                "rn_short_code": short_code,
                                "rn_responsibilities": responsibilities,
                                "rn_qualifications": qualifications,
                                "rn_expertise": expertise,
                                "rn_body": prose
                            }
                            st.toast("✨ KI-Vorschlag wird eingefügt...")
                            st.rerun()
                    except Exception as ex:
                        st.error(f"KI-Generierung fehlgeschlagen: {ex}")

        if st.session_state.get("rn_wrap_now"):
            st.session_state["rn_body"] = wrap_markdown_preserve(st.session_state.get("rn_body",""), width=100)
            st.session_state["rn_wrap_now"] = False

        try:
            md_editor_with_preview("Beschreibung / Profil (Markdown)", st.session_state.get("rn_body",""), key="rn_body", height=200)
        except TypeError:
            md_editor_with_preview("Beschreibung / Profil (Markdown)", st.session_state.get("rn_body",""), key="rn_body")

        b1, b2, b3 = st.columns([1, 1, 1])
        with b1:
            if st.button("Zeilen umbrechen", key="rn_wrap_text", type="primary", use_container_width=True):
                st.session_state["rn_wrap_now"] = True
                st.rerun()
        with b2:
            if st.button("Speichern", key="rn_save", type="primary", use_container_width=True):
                title_val = (st.session_state.get("rn_title","") or "").strip()
                if not title_val:
                    st.error("Bitte die **Rollenbezeichnung** ausfüllen.")
                else:
                    from src.m11_role_markdown import compose_role_markdown
                    
                    # Felder sammeln
                    description_val = st.session_state.get("rn_description","").strip() or None
                    short_code_val = st.session_state.get("rn_short_code","").strip() or None
                    responsibilities_val = st.session_state.get("rn_responsibilities","").strip() or None
                    qualifications_val = st.session_state.get("rn_qualifications","").strip() or None
                    expertise_val = st.session_state.get("rn_expertise","").strip() or None
                    
                    # Get prose from rn_body (user may have edited it)
                    body_raw  = st.session_state.get("rn_body","") or ""
                    body_val = wrap_markdown_preserve(body_raw, width=100)
                    
                    # Compose full markdown: prose + structured sections
                    # NOTE: If user edited body manually, we use that. 
                    # If body is empty or only contains bulletpoints, compose from fields
                    if not body_val.strip() or all(line.startswith(("- ", "## ", "#")) for line in body_val.split("\n") if line.strip()):
                        # No prose or only bulletpoints → compose from structured fields
                        body_val = compose_role_markdown(
                            "", 
                            responsibilities_val or "",
                            qualifications_val or "",
                            expertise_val or ""
                        )
                    # else: User has custom prose, keep it as-is
                    
                    def _slug(s:str)->str:
                        s = s.strip().lower(); s = re.sub(r"[^a-z0-9_-]+","-", s); return re.sub(r"-+","-", s).strip("-") or "role"
                    key_to_use = ekey if (ekey and _slug(title_val)==_slug(ekey)) else None
                    r, created = upsert_role(
                        title=title_val, 
                        body_text=body_val, 
                        short_code=short_code_val,
                        description=description_val,
                        responsibilities=responsibilities_val,
                        qualifications=qualifications_val,
                        expertise=expertise_val,
                        attached_docs=None,
                        key=key_to_use
                    )
                    st.toast(("Angelegt" if created else "Aktualisiert") + f": {r.key}")
                    st.session_state["rn_selected_key"] = r.key
                    st.session_state["rn_form_clear"] = True
                    st.rerun()
        with b3:
            delete_disabled = not bool(ekey)
            if st.button("Record löschen", key="rn_form_delete", type="primary", use_container_width=True, disabled=delete_disabled):
                from src.m07_roles import soft_delete_role
                if soft_delete_role(ekey):
                    st.toast("Eintrag gelöscht")
                    st.session_state["rn_selected_key"] = ""
                    st.session_state["rn_edit_key"] = ""
                    st.session_state["rn_form_clear"] = True
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
