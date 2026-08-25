# app/pages/06_Kontexte.py
from __future__ import annotations
import sys, math, re, time
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Kontexte – Native", page_icon="📚", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m06_ui import md_editor_with_preview, inject_form_css
from src.m07_contexts import list_contexts_df, load_context, upsert_context, delete_context
from src.m07_roles import list_roles_df
from src.m09_docs import search_docs, save_upload, list_docs
from src.m08_llm import providers_available, generate_role_text, generate_summary
import textwrap

SECTION_H = 1060
LIST_H    = 792

# Textarea-Höhen (Start/Min/Max) – analog Aufgaben-Seite
TA_START_BODY  = 200
TA_START_SMALL = 80
TA_MIN         = 60
TA_MAX         = 520

PAD_TARGET = 15
GAP_AFTER_PIPE = 5

st.title("Kontexte – Native (ohne AgGrid)")

st.markdown(
        """
        <style>
        :root{ --btn-blue:#60a5fa; --btn-blue-border:#60a5fa; --btn-grey:#e5e7eb; --btn-grey-border:#d1d5db; }
        .stButton>button{ padding:.35rem .7rem; font-size:.9rem; border-radius:10px; text-align:left; justify-content:flex-start; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \"Liberation Mono\", monospace; transition: background-color .15s ease, border-color .15s ease, transform .03s ease, box-shadow .15s ease; }
        .stButton>button *{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \"Liberation Mono\", monospace !important; }
        .stButton>button:hover{ filter: brightness(0.98); }
        .stButton>button:active{ transform: scale(0.98); }
        .stButton>button[kind=\"primary\"]:not(:disabled), .stButton>button[data-testid=\"baseButton-primary\"]:not(:disabled){ background:var(--btn-blue) !important; color:#fff !important; border:1px solid var(--btn-blue-border) !important; }
        .stButton>button[kind=\"primary\"]:not(:disabled):hover, .stButton>button[data-testid=\"baseButton-primary\"]:not(:disabled):hover{ background:#3b82f6 !important; border-color:#3b82f6 !important; box-shadow:0 0 0 3px rgba(59,130,246,.25) !important; }
        .stButton>button[kind=\"primary\"]:not(:disabled):active, .stButton>button[data-testid=\"baseButton-primary\"]:not(:disabled):active{ background:#2563eb !important; border-color:#2563eb !important; }
        .stButton>button[kind=\"primary\"]:disabled, .stButton>button[data-testid=\"baseButton-primary\"]:disabled{ background:var(--btn-grey) !important; color:#374151 !important; border:1px solid var(--btn-grey-border) !important; opacity:1 !important; }
        .stButton>button[kind=\"secondary\"], .stButton>button[data-testid=\"baseButton-secondary\"], .stButton>button:not([kind]){ background:#fff !important; color:#111827 !important; border:1px solid #e5e7eb !important; }
        .stButton>button[kind=\"secondary\"]:hover, .stButton>button[data-testid=\"baseButton-secondary\"]:hover, .stButton>button:not([kind]):hover{ background:#f9fafb !important; }
        .stButton>button[kind=\"secondary\"]:active, .stButton>button[data-testid=\"baseButton-secondary\"]:active, .stButton>button:not([kind]):active{ background:#f3f4f6 !important; }
        div[data-baseweb=\"select\"]>div{ background:#dbeafe !important; border-color:#93c5fd !important; min-height:40px !important; height:40px !important; padding:0 .6rem !important; }
        #cx_preview{ overflow-x:hidden; }
        #cx_preview p, #cx_preview li, #cx_preview pre, #cx_preview code{ white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; }
        .stMarkdown p, .stMarkdown li, .stMarkdown pre, .stMarkdown code{ white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; }
        #cx_list [data-testid=\"stVerticalBlock\"], #cx_list .element-container{ gap:0 !important; margin:0 !important; padding:0 !important; }
        #cx_list .stButton{ margin:0 !important; padding:0 !important; }
        #cx_list .stMarkdown{ margin:0 !important; padding:0 !important; }
        #cx_list p{ margin:0 !important; }
        #cx_list .stButton>button{ padding:.08rem .45rem !important; line-height:1.0 !important; width:100% !important; display:block !important; font-size:.86rem !important; white-space:pre !important; letter-spacing:0 !important; font-variant-ligatures:none !important; }
    #cx_pager .stButton>button{ white-space:nowrap !important; min-width:120px !important; }
    hr.tight-hr{ border:0; border-top:1px solid #e5e7eb; margin:6px 0 8px !important; }
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

def record_label(short: str, title: str, short_width: int = PAD_TARGET, gap_after_pipe: int = GAP_AFTER_PIPE) -> str:
    s_disp = _normalize_spaces((short or "").strip()) or "—"
    t_disp = (title or "").strip()
    pad_len = max(0, short_width - len(s_disp))
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

# -------- State --------
st.session_state.setdefault("cx_query", "")
st.session_state.setdefault("cx_sort_by", "Kontext")
st.session_state.setdefault("cx_sort_dir", "aufsteigend")
st.session_state.setdefault("cx_page", 1)
st.session_state.setdefault("cx_page_size", 8)
st.session_state.setdefault("cx_selected_key", "")

st.session_state.setdefault("cx_edit_key", "")
st.session_state.setdefault("cx_last_loaded", None)
st.session_state.setdefault("cx_title", "")
st.session_state.setdefault("cx_title_short", "")
st.session_state.setdefault("cx_short", "")
st.session_state.setdefault("cx_body", "")
st.session_state.setdefault("cx_role_key", "")
st.session_state.setdefault("cx_role_search", "")
st.session_state.setdefault("cx_policies", "")
st.session_state.setdefault("cx_outline", "")
st.session_state.setdefault("cx_links_text", "")
st.session_state.setdefault("cx_doc_query", "")
st.session_state.setdefault("cx_docs_selected", [])
st.session_state.setdefault("cx_summary", "")

st.session_state.setdefault("cx_prev_query", "")
st.session_state.setdefault("cx_prev_sort_by", st.session_state["cx_sort_by"])
st.session_state.setdefault("cx_prev_sort_dir", st.session_state["cx_sort_dir"])
st.session_state.setdefault("cx_prev_page_size", st.session_state["cx_page_size"])
st.session_state.setdefault("cx_last_click_key", "")
st.session_state.setdefault("cx_last_click_ts", 0.0)
st.session_state.setdefault("cx_form_clear", False)
st.session_state.setdefault("cx_wrap_now", False)

from src.m07_contexts import list_contexts_df

df = list_contexts_df()

# Vorschau etwas schmaler, Formular breiter – analog Aufgaben-Seite
col_list, col_preview, col_form = st.columns([0.8, 1.08, 1.14])

with col_list:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Kontexte (Native Liste)")

        c1, _s1, _s2 = st.columns([0.9, 0.55, 0.45])
        with c1:
            q = st.text_input("Suchen", key="cx_query", placeholder="z. B. Beschaffungsrichtlinie …")

        c4, c5, c6 = st.columns([0.45, 0.8, 0.75])
        with c4:
            st.selectbox("Einträge pro Seite", [8, 12, 20, 30, 50], key="cx_page_size")
            if st.session_state["cx_page_size"] != st.session_state["cx_prev_page_size"]:
                st.session_state["cx_prev_page_size"] = st.session_state["cx_page_size"]
                st.session_state["cx_page"] = 1
        with c5:
            sort_options = ["Kontext"]
            if st.session_state.get("cx_sort_by") not in sort_options:
                st.session_state["cx_sort_by"] = "Kontext"
            sort_by = st.selectbox("Sortieren nach", sort_options, key="cx_sort_by")
        with c6:
            sort_dir = st.selectbox("Richtung", ["aufsteigend", "absteigend"], key="cx_sort_dir")

        changed_q    = (q != st.session_state["cx_prev_query"])
        changed_sort = (sort_by != st.session_state["cx_prev_sort_by"])
        changed_dir  = (sort_dir != st.session_state["cx_prev_sort_dir"])
        if changed_q or changed_sort or changed_dir:
            st.session_state["cx_page"] = 1
            st.session_state["cx_prev_query"] = q
            st.session_state["cx_prev_sort_by"] = sort_by
            st.session_state["cx_prev_sort_dir"] = sort_dir
            if changed_sort:
                st.rerun()

        if df is not None and not df.empty:
            data = df.rename(columns={"Titel":"Kontext"}).copy()
            data["Key"]      = data["Key"].astype(str).fillna("")
            data["Kontext"]  = data["Kontext"].astype(str).fillna("")

            qraw  = q or ""
            qnorm = qraw.strip().lower()
            if qnorm and qnorm != "*":
                if ("*" in qnorm) or ("?" in qnorm):
                    pattern = re.escape(qnorm).replace("\\*", ".*").replace("\\?", ".")
                    key_m   = data["Key"].str.lower().str.contains(pattern, regex=True)
                    title_m = data["Kontext"].str.lower().str.contains(pattern, regex=True)
                else:
                    key_m   = data["Key"].str.lower().str.contains(qnorm, regex=False)
                    title_m = data["Kontext"].str.lower().str.contains(qnorm, regex=False)
                data = data[key_m | title_m]

            by = st.session_state["cx_sort_by"]
            ascend = st.session_state["cx_sort_dir"] == "aufsteigend"
            if by in data.columns:
                data = data.sort_values(by=by, ascending=ascend, kind="mergesort")

            total = len(data)
            page_size = st.session_state["cx_page_size"]
            max_page = max(1, math.ceil(total / page_size))
            st.session_state["cx_page"] = min(max_page, max(1, st.session_state["cx_page"]))
            page = st.session_state["cx_page"]

            start = (page - 1) * page_size
            end   = start + page_size
            page_df = data.iloc[start:end].reset_index(drop=True)

            with st.container():
                st.markdown('<div id="cx_pager">', unsafe_allow_html=True)
                left, spacer, right = st.columns([1.2, 6.0, 1.2])
                with left:
                    if st.button("◀︎ Zurück", disabled=(page <= 1), type="primary", use_container_width=True):
                        st.session_state["cx_page"] = max(1, page - 1)
                        st.rerun()
                with right:
                    if st.button("Weiter ▶︎", disabled=(page >= max_page), type="primary", use_container_width=True):
                        st.session_state["cx_page"] = min(max_page, page + 1)
                        st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

            st.caption(f"Treffer: {total} • Seite {page}/{max_page}")

            per_row_px = 28
            content_h = 12 + len(page_df) * per_row_px
            list_container = st.container() if content_h <= LIST_H else st.container(height=LIST_H)
            with list_container:
                st.markdown('<div id="cx_list">', unsafe_allow_html=True)
                if page_df.empty:
                    st.info("Keine Einträge auf dieser Seite.")
                else:
                    for i, row in page_df.iterrows():
                        key   = str(row["Key"])
                        title = str(row["Kontext"])
                        label = record_label(short="", title=title)
                        if st.button(label, key=f"cx_row_{page}_{i}_{key}", type="secondary", use_container_width=True):
                            now = time.time()
                            last_k = st.session_state.get("cx_last_click_key", "")
                            last_t = st.session_state.get("cx_last_click_ts", 0.0)
                            if last_k == key and (now - last_t) < 0.5:
                                st.session_state["cx_edit_key"] = key
                                st.session_state["cx_last_loaded"] = None
                                st.session_state["cx_last_click_key"] = ""
                                st.session_state["cx_last_click_ts"] = 0.0
                                st.rerun()
                            else:
                                st.session_state["cx_selected_key"] = key
                                st.session_state["cx_last_click_key"] = key
                                st.session_state["cx_last_click_ts"] = now
                                st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.info("Keine Kontexte gefunden.")
            st.session_state["cx_selected_key"] = ""

with col_preview:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Vorschau")
        st.markdown('<div id="cx_preview">', unsafe_allow_html=True)
        sel_key = st.session_state.get("cx_selected_key", "")
        if sel_key:
            obj, body = load_context(sel_key)
            st.caption(f"— | {obj.title if obj else ''}")
            st.markdown(body or "_(leer)_")
            c1, c2, c3 = st.columns([1,1,1])
            with c1:
                if st.button("Bearbeiten", key="cx_preview_edit", type="primary"):
                    st.session_state["cx_edit_key"] = sel_key
                    st.session_state["cx_last_loaded"] = None
                    st.rerun()
            with c2:
                try:
                    _, body_dl = load_context(sel_key)
                    st.download_button("Markdown ⬇️", data=body_dl or "", file_name=f"{sel_key}.md", mime="text/markdown")
                except Exception:
                    st.button("Markdown ⬇️", disabled=True, use_container_width=True)
            with c3:
                if st.button("Record löschen", key="cx_preview_delete", type="primary"):
                    if delete_context(sel_key):
                        st.toast("Eintrag gelöscht")
                        st.session_state["cx_selected_key"] = ""
                        st.session_state["cx_edit_key"] = ""
                        st.session_state["cx_form_clear"] = True
                        st.rerun()
        else:
            st.info("Bitte links einen Eintrag anklicken.")
        st.markdown('</div>', unsafe_allow_html=True)

with col_form:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Neuer Kontext / Bearbeiten")
        st.markdown('<div id="cx_form">', unsafe_allow_html=True)

        # Sichtbare Scrollbar umschaltbar machen (deutlich vs. dezent)
        show_sb = st.checkbox("Deutliche Scrollbar im Formular", key="cx_strong_sb", value=True)
        inject_form_css(ta_min=TA_MIN, ta_max=TA_MAX, scope_ids=("cx_form",), strong_scrollbar=show_sb)

        ekey = st.session_state.get("cx_edit_key", "")
        obj_e, body_e = load_context(ekey) if ekey else (None, "")

        if st.session_state.get("cx_form_clear"):
            st.session_state["cx_edit_key"] = ""
            st.session_state["cx_last_loaded"] = None
            st.session_state["cx_title"] = ""
            st.session_state["cx_title_short"] = ""
            st.session_state["cx_short"] = ""
            st.session_state["cx_body"]  = ""
            st.session_state["cx_role_key"] = ""
            st.session_state["cx_role_search"] = ""
            st.session_state["cx_policies"] = ""
            st.session_state["cx_outline"] = ""
            st.session_state["cx_links_text"] = ""
            st.session_state["cx_doc_query"] = ""
            st.session_state["cx_docs_selected"] = []
            st.session_state["cx_summary"] = ""
            st.session_state["cx_form_clear"] = False

        if ekey and st.session_state.get("cx_last_loaded") != ekey and obj_e:
            st.session_state["cx_title"] = obj_e.title
            st.session_state["cx_title_short"] = getattr(obj_e, "short_title", "") or ""
            st.session_state["cx_short"] = getattr(obj_e, "short_code", "") or ""
            st.session_state["cx_body"]  = body_e or ""
            st.session_state["cx_last_loaded"] = ekey

        st.markdown("**Kontext**")
        st.text_input(" ", key="cx_title", label_visibility="collapsed", placeholder="z. B. Beschaffungsrichtlinie")
        
        # Kurz-Bezeichnung und Kürzel (kompakt in einer Zeile)
        cst1, cst2 = st.columns([1.3, 0.7])
        with cst1:
            st.text_input("Kontextbezeichnung (max 50)", key="cx_title_short", max_chars=50, placeholder="Kurze, prägnante Bezeichnung…")
        with cst2:
            st.text_input("Kürzel Kontextbezeichnung (max 14)", key="cx_short", max_chars=14, placeholder="z. B. CTX-123")

        try:
            PROVS = providers_available()
        except Exception:
            PROVS = ["openai"]
        if PROVS and "llm_provider" not in st.session_state:
            st.session_state["llm_provider"] = PROVS[0]

        with st.container(border=True):
            kc1, kc2, kc3 = st.columns([1, 1, 1])
            with kc1:
                sel_idx = 0
                try:
                    sel_idx = PROVS.index(st.session_state.get("llm_provider", PROVS[0]))
                except Exception:
                    pass
                st.selectbox("KI-Provider", options=PROVS, index=sel_idx, key="llm_provider", label_visibility="collapsed")
            with kc2:
                if st.button("KI-Vorschlag", key="cx_ai_suggest", type="primary", use_container_width=True):
                    try:
                        prov = st.session_state.get("llm_provider", "openai")
                        suggestion = generate_role_text(
                            prov if prov != "none" else "openai",
                            st.session_state.get("cx_title",""),
                            "",
                        )
                        st.session_state["cx_body"] = wrap_markdown_preserve(suggestion, width=100)
                        st.toast("Vorschlag eingefügt.")
                        st.rerun()
                    except Exception as ex:
                        st.error(f"KI-Generierung fehlgeschlagen: {ex}")
            with kc3:
                if st.button("KI-Zusammenfassung", key="cx_ai_summary", type="primary", use_container_width=True):
                    try:
                        prov = st.session_state.get("llm_provider", "openai")
                        title_text = st.session_state.get("cx_title", "")
                        body_text = st.session_state.get("cx_body", "")
                        facts_txt = f"{title_text}\n\n{body_text}"
                        summary = generate_summary(prov, title_text, facts_txt)
                        st.session_state["cx_title_short"] = summary[:50] if summary else ""
                        st.toast("Zusammenfassung generiert!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler: {e}")

        if st.session_state.get("cx_wrap_now"):
            st.session_state["cx_body"] = wrap_markdown_preserve(st.session_state.get("cx_body",""), width=100)
            st.session_state["cx_wrap_now"] = False

        try:
            md_editor_with_preview("Beschreibung / Inhalt (Markdown)", st.session_state.get("cx_body",""), key="cx_body", height=TA_START_BODY)
        except TypeError:
            md_editor_with_preview("Beschreibung / Inhalt (Markdown)", st.session_state.get("cx_body",""), key="cx_body")

        st.markdown('<hr class="tight-hr" />', unsafe_allow_html=True)
        st.markdown("#### Stammdaten & Zuordnung")

        # Standardrolle zuordnen – kompakte Suche + Auswahl
        with st.container(border=True):
            r1, r2 = st.columns([1.2, 2])
            with r1:
                st.text_input("Rolle suchen", key="cx_role_search", placeholder="Funktion oder Titel…")
            roles_df = list_roles_df(include_deleted=False)
            options = []
            if roles_df is not None and not roles_df.empty:
                df_roles = roles_df.copy().fillna("")
                qrole = (st.session_state.get("cx_role_search","") or "").strip().lower()
                if qrole:
                    if ("*" in qrole) or ("?" in qrole):
                        patt = re.escape(qrole).replace("\\*", ".*").replace("\\?", ".")
                        m = df_roles["Rollenbezeichnung"].str.lower().str.contains(patt, regex=True)
                    else:
                        m = df_roles["Rollenbezeichnung"].str.lower().str.contains(qrole, regex=False)
                    df_roles = df_roles[m]
                for _, rr in df_roles.head(25).iterrows():
                    label = f"{(rr['Rollenbezeichnung'] or '').strip()}"
                    options.append((label, rr["Key"]))
            with r2:
                labels = [o[0] for o in options]
                values = [o[1] for o in options]
                if labels:
                    try:
                        cur_idx = values.index(st.session_state.get("cx_role_key","")) if st.session_state.get("cx_role_key","") in values else 0
                    except Exception:
                        cur_idx = 0
                    sel = st.selectbox("Standardrolle zuordnen", labels, index=cur_idx, key="cx_role_label")
                    st.session_state["cx_role_key"] = values[labels.index(sel)] if sel in labels else ""
                else:
                    st.selectbox("Standardrolle zuordnen", ["(keine Treffer)"])
        c_po, c_gl = st.columns(2)
        with c_po:
            st.text_area("Policies / Rahmenbedingungen", key="cx_policies", height=TA_START_SMALL, placeholder="Stichpunkte, Vorgaben, Einschränkungen…")
        with c_gl:
            st.text_area("Gliederung / Outline", key="cx_outline", height=TA_START_SMALL, placeholder="Struktur, Abschnitte…")

        # Dokumente: Suche und Upload auf einer Zeile, Upload-Label versteckt
        c_d1, c_d2 = st.columns([1.4, 1])
        with c_d1:
            st.text_input("Dokumente suchen", key="cx_doc_query", placeholder="Dateiname oder Inhalt…")
            qd = st.session_state.get("cx_doc_query","")
            docs = search_docs(qd) if qd else list_docs()
            sel_docs: list[str] = list(st.session_state.get("cx_docs_selected", []))
            for info in docs[:8]:
                ck = st.checkbox(f"{info.name} ({info.modified})", key=f"cx_doc_{info.name}", value=(info.name in sel_docs))
                if ck and info.name not in sel_docs:
                    sel_docs.append(info.name)
                if (not ck) and info.name in sel_docs:
                    sel_docs.remove(info.name)
            st.session_state["cx_docs_selected"] = sel_docs
        with c_d2:
            up = st.file_uploader("Dateien hochladen", key="cx_uploader", accept_multiple_files=True, label_visibility="collapsed")
            if up:
                for f in up:
                    try:
                        p = save_upload(f.name, f.getvalue())
                        if p.name not in st.session_state["cx_docs_selected"]:
                            st.session_state["cx_docs_selected"].append(p.name)
                    except Exception as ex:
                        st.warning(f"Upload fehlgeschlagen: {f.name} ({ex})")
                st.rerun()

        st.markdown('<hr class="tight-hr" />', unsafe_allow_html=True)
        st.markdown("#### Zusammenfassung")
        st.text_area("Kurzfassung (optional)", key="cx_summary", height=TA_START_SMALL, placeholder="3–5 Sätze…")
        csz1, csz2 = st.columns([1,2])
        with csz1:
            if st.button("KI-Zusammenfassung", key="cx_ai_summary_full", type="primary", use_container_width=True):
                try:
                    prov = st.session_state.get("llm_provider", "openai")
                    facts = []
                    if st.session_state.get("cx_policies"): facts.append(f"Policies: {st.session_state['cx_policies']}")
                    if st.session_state.get("cx_outline"): facts.append(f"Outline: {st.session_state['cx_outline']}")
                    if st.session_state.get("cx_links_text"): facts.append(f"Links: {st.session_state['cx_links_text'].replace('\n', ', ')}")
                    if st.session_state.get("cx_docs_selected"): facts.append(f"Dokumente: {', '.join(st.session_state['cx_docs_selected'])}")
                    facts_txt = "\n".join(facts)
                    st.session_state["cx_summary"] = generate_summary(prov if prov != "none" else "openai", st.session_state.get("cx_title",""), facts_txt)
                    st.rerun()
                except Exception as ex:
                    st.error(f"KI-Zusammenfassung fehlgeschlagen: {ex}")
        with csz2:
            st.caption("Live-Vorschau (zusammengesetzt)")
            def compose_ctx_markdown() -> str:
                parts: list[str] = []
                title_val = (st.session_state.get("cx_title","") or "").strip()
                if title_val:
                    parts.append(f"# Kontext: {title_val}")
                if (st.session_state.get("cx_summary") or "").strip():
                    parts.append("## Zusammenfassung\n" + st.session_state["cx_summary"].strip())
                if st.session_state.get("cx_role_key"):
                    parts.append(f"## Zugeordnete Standardrolle\nKey: {st.session_state['cx_role_key']}")
                if (st.session_state.get("cx_policies") or "").strip():
                    parts.append("## Policies / Rahmenbedingungen\n" + st.session_state["cx_policies"].strip())
                if (st.session_state.get("cx_outline") or "").strip():
                    parts.append("## Gliederung / Outline\n" + st.session_state["cx_outline"].strip())
                links_txt = (st.session_state.get("cx_links_text") or "").strip()
                if links_txt:
                    lines = [ln.strip() for ln in links_txt.splitlines() if ln.strip()]
                    if lines:
                        parts.append("## Links\n" + "\n".join(f"- {ln}" for ln in lines))
                sel_docs = list(st.session_state.get("cx_docs_selected", []))
                if sel_docs:
                    parts.append("## Dokumente\n" + "\n".join(f"- {nm}" for nm in sel_docs))
                body_val = (st.session_state.get("cx_body","") or "").strip()
                if body_val:
                    parts.append("## Beschreibung / Inhalt\n" + body_val)
                return "\n\n".join(parts)
            st.markdown(compose_ctx_markdown())

        b1, b2, b3 = st.columns([1, 1, 1])
        with b1:
            if st.button("Zeilen umbrechen", key="cx_wrap_text", type="primary", use_container_width=True):
                st.session_state["cx_wrap_now"] = True
                st.rerun()
        with b2:
            if st.button("Speichern", key="cx_save", type="primary", use_container_width=True):
                title_val = (st.session_state.get("cx_title","") or "").strip()
                if not title_val:
                    st.error("Bitte den **Kontext** ausfüllen.")
                else:
                    links_txt = (st.session_state.get("cx_links_text") or "").strip()
                    links_lines = [ln.strip() for ln in links_txt.splitlines() if ln.strip()]
                    body_raw  = st.session_state.get("cx_body","") or ""
                    body_wrapped = wrap_markdown_preserve(body_raw, width=100)
                    parts = []
                    parts.append(f"# Kontext: {title_val}")
                    if (st.session_state.get("cx_summary") or "").strip():
                        parts.append("## Zusammenfassung\n" + st.session_state["cx_summary"].strip())
                    if st.session_state.get("cx_role_key"):
                        parts.append(f"## Zugeordnete Standardrolle\nKey: {st.session_state['cx_role_key']}")
                    if (st.session_state.get("cx_policies") or "").strip():
                        parts.append("## Policies / Rahmenbedingungen\n" + st.session_state["cx_policies"].strip())
                    if (st.session_state.get("cx_outline") or "").strip():
                        parts.append("## Gliederung / Outline\n" + st.session_state["cx_outline"].strip())
                    if links_lines:
                        parts.append("## Links\n" + "\n".join(f"- {ln}" for ln in links_lines))
                    sel_docs = list(st.session_state.get("cx_docs_selected", []))
                    if sel_docs:
                        parts.append("## Dokumente\n" + "\n".join(f"- {nm}" for nm in sel_docs))
                    if body_wrapped.strip():
                        parts.append("## Beschreibung / Inhalt\n" + body_wrapped)
                    body_val = "\n\n".join(parts)
                    def _slug(s:str)->str:
                        s = s.strip().lower(); s = re.sub(r"[^a-z0-9_-]+","-", s); return re.sub(r"-+","-", s).strip("-") or "context"
                    key_to_use = ekey if (ekey and _slug(title_val)==_slug(ekey)) else None
                    r, created = upsert_context(title=title_val, body_text=body_val, key=key_to_use)
                    st.toast(("Angelegt" if created else "Aktualisiert") + f": {r.key}")
                    st.session_state["cx_selected_key"] = r.key
                    st.session_state["cx_form_clear"] = True
                    st.rerun()
        with b3:
            delete_disabled = not bool(ekey)
            if st.button("Record löschen", key="cx_form_delete", type="primary", use_container_width=True, disabled=delete_disabled):
                if delete_context(ekey):
                    st.toast("Eintrag gelöscht")
                    st.session_state["cx_selected_key"] = ""
                    st.session_state["cx_edit_key"] = ""
                    st.session_state["cx_form_clear"] = True
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
