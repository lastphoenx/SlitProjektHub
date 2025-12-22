# app/pages/05_Aufgaben.py
from __future__ import annotations
import sys, math, re, time
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Aufgaben – Native", page_icon="📝", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m06_ui import md_editor_with_preview
from src.m07_tasks import list_tasks_df, load_task, upsert_task, delete_task
from src.m07_roles import list_roles_df, load_role as load_role_by_key
from src.m09_docs import search_docs, save_upload, list_docs, delete_doc
from src.m08_llm import providers_available, generate_summary, generate_task_bullets, generate_short_title
import textwrap

SECTION_H = 1060
LIST_H    = 792
# Textarea-Höhen (Start/Min/Max)
TA_START_BODY  = 160
TA_START_SMALL = 80
TA_MIN         = 60
TA_MAX         = 520

# Unterer Formularblock (Stammdaten & Zuordnung): Mindesthöhe innerhalb des rechten Rahmens
FORM_LOWER_MIN = 420

PAD_TARGET = 15
GAP_AFTER_PIPE = 5

# Schnellschalter: Lösch-Button in der linken Liste ein-/ausblenden
# Auf True setzen, um den Button "Record löschen (aus Liste)" sichtbar zu machen.
SHOW_LIST_DELETE_BUTTON = False

st.title("Aufgaben – Native (ohne AgGrid)")

st.markdown(
    f"""
    <style>
    :root{{ --btn-blue:#60a5fa; --btn-blue-border:#60a5fa; --btn-grey:#e5e7eb; --btn-grey-border:#d1d5db; }}
    .stButton>button{{ padding:.35rem .7rem; font-size:.9rem; border-radius:10px; text-align:left; justify-content:flex-start; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \"Liberation Mono\", monospace; transition: background-color .15s ease, border-color .15s ease, transform .03s ease, box-shadow .15s ease; }}
    .stButton>button *{{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \"Liberation Mono\", monospace !important; }}
    .stButton>button:hover{{ filter: brightness(0.98); }}
    .stButton>button:active{{ transform: scale(0.98); }}
    .stButton>button[kind=\"primary\"]:not(:disabled), .stButton>button[data-testid=\"baseButton-primary\"]:not(:disabled){{ background:var(--btn-blue) !important; color:#fff !important; border:1px solid var(--btn-blue-border) !important; }}
    .stButton>button[kind=\"primary\"]:not(:disabled):hover, .stButton>button[data-testid=\"baseButton-primary\"]:not(:disabled):hover{{ background:#3b82f6 !important; border-color:#3b82f6 !important; box-shadow:0 0 0 3px rgba(59,130,246,.25) !important; }}
    .stButton>button[kind=\"primary\"]:not(:disabled):active, .stButton>button[data-testid=\"baseButton-primary\"]:not(:disabled):active{{ background:#2563eb !important; border-color:#2563eb !important; }}
    .stButton>button[kind=\"primary\"]:disabled, .stButton>button[data-testid=\"baseButton-primary\"]:disabled{{ background:var(--btn-grey) !important; color:#374151 !important; border:1px solid var(--btn-grey-border) !important; opacity:1 !important; }}
    .stButton>button[kind=\"secondary\"], .stButton>button[data-testid=\"baseButton-secondary\"], .stButton>button:not([kind]){{ background:#fff !important; color:#111827 !important; border:1px solid #e5e7eb !important; }}
    .stButton>button[kind=\"secondary\"]:hover, .stButton>button[data-testid=\"baseButton-secondary\"]:hover, .stButton>button:not([kind]):hover{{ background:#f9fafb !important; }}
    .stButton>button[kind=\"secondary\"]:active, .stButton>button[data-testid=\"baseButton-secondary\"]:active, .stButton>button:not([kind]):active{{ background:#f3f4f6 !important; }}
    div[data-baseweb=\"select\"]>div{{ background:#dbeafe !important; border-color:#93c5fd !important; min-height:40px !important; height:40px !important; padding:0 .6rem !important; }}
    #tk_preview{{ overflow-x:hidden; }}
    #tk_preview p, #tk_preview li, #tk_preview pre, #tk_preview code{{ white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; }}
    .stMarkdown p, .stMarkdown li, .stMarkdown pre, .stMarkdown code{{ white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; }}
    #tk_list [data-testid=\"stVerticalBlock\"], #tk_list .element-container{{ gap:0 !important; margin:0 !important; padding:0 !important; }}
    #tk_list .stButton{{ margin:0 !important; padding:0 !important; }}
    #tk_list .stMarkdown{{ margin:0 !important; padding:0 !important; }}
    #tk_list p{{ margin:0 !important; }}
    #tk_list .stButton>button{{ padding:.08rem .45rem !important; line-height:1.0 !important; width:100% !important; display:block !important; font-size:.86rem !important; white-space:pre !important; letter-spacing:0 !important; font-variant-ligatures:none !important; }}
    #tk_pager .stButton>button{{ white-space:nowrap !important; min-width:240px !important; }}
    hr.tight-hr{{ border:0; border-top:1px solid #e5e7eb; margin:6px 0 8px !important; }}
    /* Standard: alle Textareas vertikal vergrößer-/verkleinerbar, mit Min/Max-Höhe; Overflow aktivieren, damit der Browser das Resize-Handle zeigt */
    .stTextArea textarea,
    div[data-testid=\"stTextArea\"] textarea,
    div[data-baseweb=\"textarea\"] textarea,
    #tk_form textarea{{ resize: vertical !important; overflow: auto !important; min-height: {TA_MIN}px !important; max-height: {TA_MAX}px !important; }}
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

def _strip_headings(text: str) -> str:
    """Entfernt Überschriften und Feld-Labels aus KI-Texten.
    - Entfernt Markdown-Headings (beginnend mit '#')
    - Entfernt Zeilen, die nur aus den Label-Bezeichnern bestehen (mit/ohne '#', ':' etc.)
    """
    if not text:
        return ""
    LABELS = [
        r"expertise\s*/\s*spezialwissen",
        r"aufgaben\s*/\s*pflichten",
    ]
    label_re = re.compile(rf"^\s*#*\s*(?:{'|'.join(LABELS)})\s*:?\s*$", re.IGNORECASE)
    lines = []
    for ln in text.splitlines():
        if re.match(r"^\s*#{1,6}\s", ln):
            continue
        if label_re.match(ln or ""):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()

def _make_shortcode(text: str, max_len: int = 12) -> str:
    """Leitet ein Kürzel aus einem Titel/Kurz-Titel ab: nimmt die Anfangsbuchstaben
    der Wörter (Acronym), fällt zurück auf eine gekürzte, bereinigte Variante.
    """
    t = (text or "").strip()
    if not t:
        return ""
    words = re.findall(r"[A-Za-z0-9ÄÖÜäöü]+", t)
    if words:
        acronym = "".join(w[0] for w in words).upper()
    else:
        acronym = re.sub(r"[^A-Za-z0-9]+", "", t).upper()
    if not acronym:
        acronym = "TK"
    return acronym[:max_len]

def _strip_field_label(text: str, keywords: list[str]) -> str:
    """Entfernt führende Label-Zeilen wie '## Expertise / Spezialwissen' oder
    'Aufgaben / Pflichten' – auch ohne Markdown-Hash, optional mit Doppelpunkten.
    Prüft die ersten ca. 3 nicht-leeren Zeilen.
    """
    if not text:
        return ""
    patt = re.compile(r"^\s*#*\s*(" + "|".join(re.escape(k) for k in keywords) + r")(\s*/\s*(" + "|".join(re.escape(k) for k in keywords) + r"))?\s*:?\s*$",
                      flags=re.IGNORECASE)
    lines = text.splitlines()
    out = []
    removed = 0
    for ln in lines:
        if removed < 3 and ln.strip() and patt.match(ln.strip()):
            removed += 1
            continue
        out.append(ln)
    return "\n".join(out).strip()

def _normalize_bullets(text: str) -> str:
    """Säubert Bulletlisten:
    - entfernt leere Zeilen und reine Bindestrich-Zeilen ('-' oder '--')
    - stellt sicher, dass jede Zeile mit '- ' beginnt
    """
    if not text:
        return ""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s in {"-", "--", "—"}:
            continue
        if not s.startswith("- ") and not s.startswith("-"):
            s = "- " + s
        elif s == "-":
            continue
        elif s.startswith("-") and not s.startswith("- "):
            # '-foo' -> '- foo'
            s = "- " + s[1:].lstrip()
        out.append(s)
    return "\n".join(out)

def compose_task_body_from_state() -> str:
    """Baut den Body so zusammen, wie er beim Speichern persistiert wird
    (ohne '# Aufgabe: Titel')."""
    links_txt = (st.session_state.get("tk_links_text") or "").strip()
    links_lines = [ln.strip() for ln in links_txt.splitlines() if ln.strip()]
    body_raw  = st.session_state.get("tk_body","") or ""
    body_wrapped = wrap_markdown_preserve(body_raw, width=100)
    parts = []
    # Kürzel und Kurz-Titel werden in der DB gespeichert und als Caption angezeigt,
    # nicht im Body selbst.
    if (st.session_state.get("tk_expertise") or "").strip():
        parts.append("## Expertise / Spezialwissen\n" + st.session_state["tk_expertise"].strip())
    if (st.session_state.get("tk_duties") or "").strip():
        parts.append("## Aufgaben / Pflichten\n" + st.session_state["tk_duties"].strip())
    if links_lines:
        parts.append("## Links\n" + "\n".join(f"- {ln}" for ln in links_lines))
    sel_docs = list(st.session_state.get("tk_docs_selected", []))
    if sel_docs:
        parts.append("## Dokumente\n" + "\n".join(f"- {nm}" for nm in sel_docs))
    if body_wrapped.strip():
        parts.append("## Beschreibung / Inhalt\n" + body_wrapped)
    return "\n\n".join(parts)

# -------- State --------
st.session_state.setdefault("tk_query", "")
st.session_state.setdefault("tk_sort_by", "Aufgabenbezeichnung")
st.session_state.setdefault("tk_sort_dir", "aufsteigend")
st.session_state.setdefault("tk_page", 1)
st.session_state.setdefault("tk_page_size", 8)
st.session_state.setdefault("tk_selected_key", "")

st.session_state.setdefault("tk_edit_key", "")
st.session_state.setdefault("tk_last_loaded", None)
st.session_state.setdefault("tk_title", "")
st.session_state.setdefault("tk_title_short", "")
st.session_state.setdefault("tk_body", "")
st.session_state.setdefault("tk_role_key", "")
st.session_state.setdefault("tk_role_search", "")
st.session_state.setdefault("tk_expertise", "")
st.session_state.setdefault("tk_duties", "")
st.session_state.setdefault("tk_links_text", "")
st.session_state.setdefault("tk_short", "")
st.session_state.setdefault("tk_doc_query", "")
st.session_state.setdefault("tk_docs_selected", [])
# legacy summary field no longer used
if "tk_summary" in st.session_state:
    del st.session_state["tk_summary"]

st.session_state.setdefault("tk_prev_query", "")
st.session_state.setdefault("tk_prev_sort_by", st.session_state["tk_sort_by"])
st.session_state.setdefault("tk_prev_sort_dir", st.session_state["tk_sort_dir"])
st.session_state.setdefault("tk_prev_page_size", st.session_state["tk_page_size"])
st.session_state.setdefault("tk_last_click_key", "")
st.session_state.setdefault("tk_last_click_ts", 0.0)
st.session_state.setdefault("tk_form_clear", False)
st.session_state.setdefault("tk_wrap_now", False)

# Für umfassende Suche auch den Markdown-Inhalt laden
df = list_tasks_df(include_body=True)

# c) Vorschau etwas schmaler, Formular breiter
col_list, col_preview, col_form = st.columns([0.8, 1.08, 1.14])

with col_list:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Aufgaben (Liste bestehender Aufgaben)")

        c1, _s1, _s2 = st.columns([0.9, 0.55, 0.45])
        with c1:
            q = st.text_input("Suchen (elastische Suchfilter)", key="tk_query", placeholder="z. B. Datenqualität prüfen …")

        # Breiten anpassen: "Sortieren nach" und "Richtung" jeweils um ~25% schmaler
        c4, c5, c6 = st.columns([0.45, 0.6, 0.56])
        with c4:
            st.selectbox("Einträge pro Seite", [8, 12, 20, 30, 50], key="tk_page_size")
            if st.session_state["tk_page_size"] != st.session_state["tk_prev_page_size"]:
                st.session_state["tk_prev_page_size"] = st.session_state["tk_page_size"]
                st.session_state["tk_page"] = 1
        with c5:
            # Nur noch nach Aufgabenbezeichnung (KurzTitel) oder Kürzel sortieren
            sort_display_to_col = {"Aufgabenbezeichnung": "KurzTitel", "Kürzel": "Kuerzel"}
            sort_options = list(sort_display_to_col.keys())
            if st.session_state.get("tk_sort_by") not in sort_options:
                st.session_state["tk_sort_by"] = "Aufgabenbezeichnung"
            sort_by = st.selectbox("Sortieren nach", sort_options, key="tk_sort_by")
        with c6:
            sort_dir = st.selectbox("Richtung", ["aufsteigend", "absteigend"], key="tk_sort_dir")

        changed_q    = (q != st.session_state["tk_prev_query"])
        changed_sort = (sort_by != st.session_state["tk_prev_sort_by"])
        changed_dir  = (sort_dir != st.session_state["tk_prev_sort_dir"])
        if changed_q or changed_sort or changed_dir:
            st.session_state["tk_page"] = 1
            st.session_state["tk_prev_query"] = q
            st.session_state["tk_prev_sort_by"] = sort_by
            st.session_state["tk_prev_sort_dir"] = sort_dir
            if changed_sort:
                st.rerun()

        if df is not None and not df.empty:
            data = df.rename(columns={"Titel":"Aufgabe"}).copy()
            data["Key"]      = data["Key"].astype(str).fillna("")
            data["Aufgabe"]  = data["Aufgabe"].astype(str).fillna("")
            # Sicherstellen, dass Inhalts-Spalte vorhanden ist
            if "Inhalt" not in data.columns:
                data["Inhalt"] = ""
            # Sicherstellen, dass Kurzspalten vorhanden sind
            if "KurzTitel" not in data.columns:
                data["KurzTitel"] = ""
            if "Kuerzel" not in data.columns:
                data["Kuerzel"] = ""

            qraw  = q or ""
            qnorm = qraw.strip().lower()
            if qnorm and qnorm != "*":
                if ("*" in qnorm) or ("?" in qnorm):
                    pattern = re.escape(qnorm).replace("\\*", ".*").replace("\\?", ".")
                    key_m   = data["Key"].str.lower().str.contains(pattern, regex=True)
                    title_m = data["Aufgabe"].str.lower().str.contains(pattern, regex=True)
                    body_m  = data["Inhalt"].astype(str).str.lower().str.contains(pattern, regex=True)
                    short_m = data["KurzTitel"].astype(str).str.lower().str.contains(pattern, regex=True)
                    code_m  = data["Kuerzel"].astype(str).str.lower().str.contains(pattern, regex=True)
                else:
                    key_m   = data["Key"].str.lower().str.contains(qnorm, regex=False)
                    title_m = data["Aufgabe"].str.lower().str.contains(qnorm, regex=False)
                    body_m  = data["Inhalt"].astype(str).str.lower().str.contains(qnorm, regex=False)
                    short_m = data["KurzTitel"].astype(str).str.lower().str.contains(qnorm, regex=False)
                    code_m  = data["Kuerzel"].astype(str).str.lower().str.contains(qnorm, regex=False)
                data = data[key_m | title_m | body_m | short_m | code_m]

            by_display = st.session_state["tk_sort_by"]
            by = sort_display_to_col.get(by_display, by_display)
            ascend = st.session_state["tk_sort_dir"] == "aufsteigend"
            if by in data.columns:
                data = data.sort_values(by=by, ascending=ascend, kind="mergesort")

            total = len(data)
            page_size = st.session_state["tk_page_size"]
            max_page = max(1, math.ceil(total / page_size))
            st.session_state["tk_page"] = min(max_page, max(1, st.session_state["tk_page"]))
            page = st.session_state["tk_page"]

            start = (page - 1) * page_size
            end   = start + page_size
            page_df = data.iloc[start:end].reset_index(drop=True)

            with st.container():
                st.markdown('<div id="tk_pager">', unsafe_allow_html=True)
                # Pager-Buttons breiter (links/rechts jeweils ca. verdoppeln)
                left, spacer, right = st.columns([2.4, 4.8, 2.4])
                with left:
                    if st.button("◀︎ Zurück", disabled=(page <= 1), type="primary", use_container_width=True):
                        st.session_state["tk_page"] = max(1, page - 1)
                        st.rerun()
                with right:
                    if st.button("Weiter ▶︎", disabled=(page >= max_page), type="primary", use_container_width=True):
                        st.session_state["tk_page"] = min(max_page, page + 1)
                        st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

            st.caption(f"Treffer: {total} • Seite {page}/{max_page}")

            per_row_px = 28
            content_h = 12 + len(page_df) * per_row_px
            list_container = st.container() if content_h <= LIST_H else st.container(height=LIST_H)
            with list_container:
                st.markdown('<div id="tk_list">', unsafe_allow_html=True)
                if page_df.empty:
                    st.info("Keine Einträge auf dieser Seite.")
                else:
                    for i, row in page_df.iterrows():
                        key   = str(row["Key"])
                        title = str(row["Aufgabe"])
                        short_disp = str(row.get("Kuerzel", "") or "")
                        title_disp = str(row.get("KurzTitel", "") or title)
                        label = record_label(short=short_disp, title=title_disp)
                        if st.button(label, key=f"tk_row_{page}_{i}_{key}", type="secondary", use_container_width=True):
                            now = time.time()
                            last_k = st.session_state.get("tk_last_click_key", "")
                            last_t = st.session_state.get("tk_last_click_ts", 0.0)
                            if last_k == key and (now - last_t) < 0.5:
                                st.session_state["tk_edit_key"] = key
                                st.session_state["tk_last_loaded"] = None
                                st.session_state["tk_last_click_key"] = ""
                                st.session_state["tk_last_click_ts"] = 0.0
                                st.rerun()
                            else:
                                st.session_state["tk_selected_key"] = key
                                st.session_state["tk_last_click_key"] = key
                                st.session_state["tk_last_click_ts"] = now
                                st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
                # Optionale Lösch-Aktion direkt in der Liste, per Schalter deaktivierbar
                if SHOW_LIST_DELETE_BUTTON:
                    sel_key_for_delete = st.session_state.get("tk_selected_key", "")
                    if sel_key_for_delete:
                        if st.button("Record löschen (aus Liste)", key="tk_list_delete", type="primary"):
                            if delete_task(sel_key_for_delete):
                                st.toast("Eintrag gelöscht")
                                st.session_state["tk_selected_key"] = ""
                                st.session_state["tk_edit_key"] = ""
                                st.session_state["tk_form_clear"] = True
                                st.rerun()
        else:
            st.info("Keine Aufgaben gefunden.")
            st.session_state["tk_selected_key"] = ""

## Vorschau-Block wird nach dem Formular gerendert (aktualisierte State-Werte)
def _render_preview():
    with col_preview:
        with st.container(border=True, height=SECTION_H):
            st.markdown("### Vorschau")
            st.markdown('<div id="tk_preview">', unsafe_allow_html=True)
            sel_key = st.session_state.get("tk_selected_key", "")
            edit_mode = bool(st.session_state.get("tk_edit_key"))
            
            if edit_mode:
                # Live preview from form
                cap_left = (st.session_state.get('tk_short','') or '—').strip()
                cap_right = (st.session_state.get('tk_title_short','') or st.session_state.get('tk_title','') or '').strip()
                st.caption(f"{cap_left} | {cap_right}")
                st.markdown(compose_task_body_from_state() or "_(leer)_")
            elif sel_key:
                # Selected task preview
                obj, body = load_task(sel_key)
                cap_left = (getattr(obj, 'short_code', None) or '—').strip() if obj else '—'
                cap_right = (getattr(obj, 'short_title', None) or (obj.title if obj else '') or '').strip()
                st.caption(f"{cap_left} | {cap_right}")
                st.markdown(body or "_(leer)_")
                c1, c2, c3 = st.columns([1,1,1])
                with c1:
                    if st.button("Bearbeiten", key="tk_preview_edit", type="primary"):
                        st.session_state["tk_edit_key"] = sel_key
                        st.session_state["tk_last_loaded"] = None
                        st.rerun()
                with c2:
                    try:
                        _, body_dl = load_task(sel_key)
                        st.download_button("Markdown ⬇️", data=body_dl or "", file_name=f"{sel_key}.md", mime="text/markdown")
                    except Exception:
                        st.button("Markdown ⬇️", disabled=True, use_container_width=True)
                with c3:
                    if st.button("Record löschen", key="tk_preview_delete", type="primary"):
                        if delete_task(sel_key):
                            st.toast("Eintrag gelöscht")
                            st.session_state["tk_selected_key"] = ""
                            st.session_state["tk_edit_key"] = ""
                            st.session_state["tk_form_clear"] = True
                            st.rerun()
            else:
                st.info("Bitte links einen Eintrag anklicken.")
            st.markdown('</div>', unsafe_allow_html=True)

with col_form:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Neue Aufgabe / Bearbeiten")
        st.markdown('<div id="tk_form">', unsafe_allow_html=True)

        # WICHTIG: Pending-Updates VOR ALLEN WIDGETS anwenden, damit Streamlit-Widgets
        # ihre initialen Werte aus dem State beziehen können, ohne den Laufzeitfehler
        # "cannot be modified after the widget ... is instantiated" zu triggern.
        pending = st.session_state.pop("tk_ai_update", None)
        if pending:
            for k, v in pending.items():
                st.session_state[k] = v

        ekey = st.session_state.get("tk_edit_key", "")
        obj_e, body_e = load_task(ekey) if ekey else (None, "")

        if st.session_state.get("tk_form_clear"):
            st.session_state["tk_edit_key"] = ""
            st.session_state["tk_last_loaded"] = None
            st.session_state["tk_title"] = ""
            st.session_state["tk_title_short"] = ""
            st.session_state["tk_body"]  = ""
            st.session_state["tk_role_key"] = ""
            st.session_state["tk_role_search"] = ""
            st.session_state["tk_expertise"] = ""
            st.session_state["tk_duties"] = ""
            st.session_state["tk_links_text"] = ""
            st.session_state["tk_short"] = ""
            st.session_state["tk_doc_query"] = ""
            st.session_state["tk_docs_selected"] = []
            st.session_state["tk_form_clear"] = False

        if ekey and st.session_state.get("tk_last_loaded") != ekey and obj_e:
            st.session_state["tk_title"] = obj_e.title
            # vorhandene Kurz-Titel/Kürzel aus DB laden (wenn vorhanden)
            st.session_state["tk_title_short"] = getattr(obj_e, "short_title", "") or ""
            st.session_state["tk_short"] = getattr(obj_e, "short_code", "") or ""
            st.session_state["tk_body"]  = body_e or ""
            st.session_state["tk_last_loaded"] = ekey

        st.markdown("**Gewünschter Aufgabeninhalt beschreiben**")
        st.text_input(" ", key="tk_title", label_visibility="collapsed", placeholder="z. B. Datenqualität prüfen")

        # Kurz-Titel und Kürzel (kompakt in einer Zeile)
        cst1, cst2 = st.columns([1.3, 0.7])
        with cst1:
            st.text_input("Aufgabenbezeichnung (max 50)", key="tk_title_short", max_chars=50, placeholder="Kurzer, prägnanter Titel…")
        with cst2:
            st.text_input("Kürzel der Aufgabenbezeichnung (max 14)", key="tk_short", max_chars=14, placeholder="z. B. TK-123")

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
                if st.button("KI-Vorschlag", key="tk_ai_suggest", type="primary", use_container_width=True):
                    # e) Fill Expertise & Aufgaben with 3–5 bullets derived from the title
                    try:
                        # Provider robust bestimmen
                        prov = (st.session_state.get("global_llm_provider") or "openai").strip().lower()
                        if prov in ("", "none", "disabled"):
                            prov = "none"

                        exp_md, dut_md = generate_task_bullets(
                            prov,
                            st.session_state.get("tk_title",""),
                            model=st.session_state.get("global_llm_model"),
                            temperature=st.session_state.get("global_llm_temperature", 0.7)
                        )
                        # b/c) Headings & Feld-Labels entfernen, damit keine doppelten Überschriften entstehen
                        exp_clean = _normalize_bullets(_strip_field_label(_strip_headings(exp_md), ["Expertise", "Spezialwissen"]))
                        dut_clean = _normalize_bullets(_strip_field_label(_strip_headings(dut_md), ["Aufgaben", "Pflichten"]))
                        # Bei leeren Aufgaben (z. B. von Mistral) fallback auf Expertise-Liste
                        if not dut_clean.strip():
                            dut_clean = exp_clean
                        # Minimal-Fallbacks auf Platzhalter, falls leer
                        exp_out = (exp_clean or "").strip() or "- (leer)"
                        dut_out = (dut_clean or "").strip() or exp_out
                        # b) Kurz-Titel (max 50) automatisch vorschlagen –
                        #    falls Titel leer/ungeeignet: Hint aus ersten Bullets bauen
                        hint_title = (st.session_state.get("tk_title","") or "").strip()
                        if not hint_title:
                            def first_lines(s: str, n:int=2) -> str:
                                lines = [ln.lstrip("- ").strip() for ln in (s or "").splitlines() if ln.strip()]
                                return ", ".join(lines[:n])
                            hint_title = first_lines(exp_clean, 2) or first_lines(dut_clean, 2) or "(Ohne Titel)"

                        short = ""
                        try:
                            short = (generate_short_title(
                                prov,
                                hint_title,
                                max_len=50,
                                model=st.session_state.get("global_llm_model"),
                                temperature=st.session_state.get("global_llm_temperature", 0.7)
                            ) or "").strip()
                        except Exception as ex:
                            st.warning(f"Short-Title Fallback (Vorschlag): {ex}")

                        # Harte Fallbacks, falls short leer bleibt
                        if not short:
                            short = hint_title[:50].rstrip(" -–—,:;")

                        sc = _make_shortcode(short or st.session_state.get("tk_title",""), max_len=14)

                        # Pending-Update statt direkter Zuweisungen (für den nächsten Run vor Widget-Instanzierung)
                        st.session_state["tk_ai_update"] = {
                            "tk_expertise": exp_out,
                            "tk_duties": dut_out,
                            "tk_title_short": short[:50],  # a1: 50
                            "tk_short": (sc or "TK")[:14],  # a1: 14
                        }
                        st.toast("Bulletpoints eingefügt.")
                        # Kurz-Titel/Kürzel-Widgets stehen oberhalb – erneutes Rendern für sofortige Anzeige
                        st.rerun()
                    except Exception as ex:
                        st.error(f"KI-Generierung fehlgeschlagen: {ex}")
            with kc3:
                if st.button("KI-Zusammenfassung", key="tk_ai_summary_inline", type="primary", use_container_width=True):
                    # f) Create summary from body, bullets, links, docs, and linked role and put into body
                    try:
                        prov = (st.session_state.get("llm_provider") or "openai").strip().lower()
                        if prov in ("", "none", "disabled"):
                            prov = "none"
                        facts = []
                        body_src = st.session_state.get("tk_body", "")
                        if body_src.strip(): facts.append(f"Beschreibung: {body_src.strip()}")
                        if st.session_state.get("tk_expertise"): facts.append(f"Expertise: {st.session_state['tk_expertise']}")
                        if st.session_state.get("tk_duties"): facts.append(f"Aufgaben: {st.session_state['tk_duties']}")
                        if st.session_state.get("tk_links_text"): facts.append(f"Links: {st.session_state['tk_links_text'].replace('\n', ', ')}")
                        if st.session_state.get("tk_docs_selected"): facts.append(f"Dokumente: {', '.join(st.session_state['tk_docs_selected'])}")
                        rk = st.session_state.get("tk_role_key", "")
                        if rk:
                            try:
                                robj, _rb = load_role_by_key(rk)
                                if robj:
                                    facts.append(f"Rolle: {robj.short_code or '—'} | {robj.title}")
                            except Exception:
                                pass
                        facts_txt = "\n".join(facts)
                        summary = generate_summary(prov, st.session_state.get("tk_title",""), facts_txt)
                        # Body darf direkt gesetzt werden, da das Body-Widget erst weiter unten instanziiert wird
                        st.session_state["tk_body"] = wrap_markdown_preserve(summary, width=100)
                        # c) Kurz-Titel (max 30) aus der Zusammenfassung ableiten – mit robusten Fallbacks
                        short30 = ""
                        try:
                            hint = f"{st.session_state.get('tk_title','')}: {summary}"
                            short30 = (generate_short_title(prov, hint, max_len=30) or "").strip()
                        except Exception as ex:
                            st.warning(f"Short-Title Fallback (Zusammenfassung): {ex}")

                        if not short30:
                            base = (st.session_state.get('tk_title','') or summary.split("\n",1)[0] or "(Ohne Titel)")
                            short30 = base[:30].rstrip(" -–—,:;")

                        sc = _make_shortcode(short30 or st.session_state.get("tk_title",""), max_len=7)

                        # Pending-Update für Kurz-Titel/Kürzel; Expertise/Aufgaben EXPLIZIT beibehalten
                        st.session_state["tk_ai_update"] = {
                            "tk_title_short": short30[:30],  # a2: 30
                            "tk_short": (sc or "TK")[:7],    # a2: 7
                            # Bewusst nicht verändern – gegen versehentliches Leeren absichern
                            "tk_expertise": st.session_state.get("tk_expertise", ""),
                            "tk_duties": st.session_state.get("tk_duties", ""),
                        }
                        st.toast("Zusammenfassung erstellt.")
                        st.rerun()
                    except Exception as ex:
                        st.error(f"KI-Zusammenfassung fehlgeschlagen: {ex}")
        if st.session_state.get("tk_wrap_now"):
            st.session_state["tk_body"] = wrap_markdown_preserve(st.session_state.get("tk_body",""), width=100)
            st.session_state["tk_wrap_now"] = False

        try:
            md_editor_with_preview("Beschreibung / Inhalt (Markdown)", st.session_state.get("tk_body",""), key="tk_body", height=TA_START_BODY)
        except TypeError:
            md_editor_with_preview("Beschreibung / Inhalt (Markdown)", st.session_state.get("tk_body",""), key="tk_body")

        # a) Trennlinie dichter an das Eingabefeld rücken
        st.markdown('<hr class="tight-hr" />', unsafe_allow_html=True)

        # Unterer Abschnitt mit eigenem Rahmen; ohne feste Höhe, damit er sich
        # natürlich mit dem Layout mitverhält. Mindestgrößen sichern wir über die
        # Textarea-Min-Höhen (TA_MIN) – dadurch bleibt er nutzbar, schrumpft aber
        # nicht unkontrolliert beim Zoomen.
        with st.container(border=True):
            st.markdown("#### Zuweisung einer Rolle")

            # Standardrolle zuordnen – kompakte Suche + Auswahl
            with st.container(border=True):
                r1, r2 = st.columns([1.2, 2])
                with r1:
                    st.text_input("Rolle suchen", key="tk_role_search", placeholder="Funktion oder Titel…")
                roles_df = list_roles_df(include_deleted=False)
                options = []
                if roles_df is not None and not roles_df.empty:
                    df_roles = roles_df.copy().fillna("")
                    qrole = (st.session_state.get("tk_role_search","") or "").strip().lower()
                    if qrole:
                        if ("*" in qrole) or ("?" in qrole):
                            patt = re.escape(qrole).replace("\\*", ".*").replace("\\?", ".")
                            m = df_roles["Rollenbezeichnung"].str.lower().str.contains(patt, regex=True) | df_roles["Rollenkürzel"].str.lower().str.contains(patt, regex=True)
                        else:
                            m = df_roles["Rollenbezeichnung"].str.lower().str.contains(qrole, regex=False) | df_roles["Rollenkürzel"].str.lower().str.contains(qrole, regex=False)
                        df_roles = df_roles[m]
                    # prepare label list
                    for _, rr in df_roles.head(25).iterrows():
                        label = f"{(rr['Rollenkürzel'] or '—').strip()} | {(rr['Rollenbezeichnung'] or '').strip()}"
                        options.append((label, rr["Key"]))
                with r2:
                    labels = [o[0] for o in options]
                    values = [o[1] for o in options]
                    if labels:
                        try:
                            cur_idx = values.index(st.session_state.get("tk_role_key","")) if st.session_state.get("tk_role_key","") in values else 0
                        except Exception:
                            cur_idx = 0
                        sel = st.selectbox("Standardrolle zuordnen", labels, index=cur_idx, key="tk_role_label")
                        st.session_state["tk_role_key"] = values[labels.index(sel)] if sel in labels else ""
                    else:
                        st.selectbox("Standardrolle zuordnen", ["(keine Treffer)"])

            # Trennlinie zwischen Rolle und den drei Feldern
            st.markdown('<hr class="tight-hr" />', unsafe_allow_html=True)

            # d) Felder direkt unter Rollen suchen, jeweils volle Breite
            st.text_area("Expertise / Spezialwissen (für die Ausübung der Aufgabe benötigt)", key="tk_expertise", height=TA_START_SMALL, placeholder="z. B. SQL, Datenschutzrecht, ETL-Erfahrung…")

            st.text_area("To‑Dos / Pflichten (zur Ausübung der Aufgabe gehörend)", key="tk_duties", height=TA_START_SMALL, placeholder="Stichpunkte, eine pro Zeile…")

            st.text_area("Links, einer pro Zeile (weiterführende Informationen zur Aufgabenerfüllung)", key="tk_links_text", height=TA_START_SMALL, placeholder="https://…")

            # Trennlinie vor den Dokumenten
            st.markdown('<hr class="tight-hr" />', unsafe_allow_html=True)

            # a) search and upload on the same row (innerhalb des Containers)
            c_d1, c_d2 = st.columns([1.4, 1])
            with c_d1:
                st.text_input("Dokumente suchen", key="tk_doc_query", placeholder="Dateiname oder Inhalt…")
                st.caption("Hier können weitere für die Ausübung der Aufgabe zwingend relevante Dokumente verknüpft werden.")
                qd = st.session_state.get("tk_doc_query","")
                docs = search_docs(qd) if qd else list_docs()
                sel_docs: list[str] = list(st.session_state.get("tk_docs_selected", []))
                # Pendente Deselektionen (von "Entfernen") vor dem Render anwenden
                pend_uncheck = list(st.session_state.get("tk_uncheck_docs", []))
                if pend_uncheck:
                    sel_docs = [nm for nm in sel_docs if nm not in set(pend_uncheck)]
                    st.session_state["tk_docs_selected"] = sel_docs
                    st.session_state["tk_uncheck_docs"] = []
                for info in docs[:8]:
                    ck = st.checkbox(f"{info.name} ({info.modified})", key=f"tk_doc_{info.name}", value=(info.name in sel_docs))
                    if ck and info.name not in sel_docs:
                        sel_docs.append(info.name)
                    if (not ck) and info.name in sel_docs:
                        sel_docs.remove(info.name)
                st.session_state["tk_docs_selected"] = sel_docs
                # Ausgewählte Dokumente mit Entfernen/Löschen-Aktionen
                if sel_docs:
                    st.markdown("**Ausgewählt**")
                    for nm in list(sel_docs):
                        # Nur noch 'Löschen' – links ausgerichtet und volle Breite
                        if st.button("🗑 Löschen", key=f"tk_doc_delete_{nm}", type="primary", use_container_width=True):
                            if delete_doc(nm):
                                if nm in st.session_state["tk_docs_selected"]:
                                    st.session_state["tk_docs_selected"].remove(nm)
                                lst = list(st.session_state.get("tk_uncheck_docs", []))
                                if nm not in lst:
                                    lst.append(nm)
                                st.session_state["tk_uncheck_docs"] = lst
                                st.toast(f"Datei gelöscht: {nm}")
                                st.rerun()
                        st.caption(nm)
            with c_d2:
                # b) Label ausblenden (nicht leer lassen für Accessibility, stattdessen verstecken)
                up = st.file_uploader("Dateien hochladen", key="tk_uploader", accept_multiple_files=True, label_visibility="collapsed")
                if up:
                    processed = set(st.session_state.get("tk_uploaded", []))
                    for f in up:
                        try:
                            ident = f"{f.name}:{getattr(f, 'size', None) or len(f.getvalue() or b'')}"
                            if ident in processed:
                                continue
                            p = save_upload(f.name, f.getvalue())
                            if p.name not in st.session_state["tk_docs_selected"]:
                                st.session_state["tk_docs_selected"].append(p.name)
                            processed.add(ident)
                        except Exception as ex:
                            st.warning(f"Upload fehlgeschlagen: {f.name} ({ex})")
                    st.session_state["tk_uploaded"] = list(processed)
                    # kein st.rerun(), damit kein Upload-Loop entsteht

            # removed old Zusammenfassung & composed live preview per requirements (b, f)

            b1, b2, b3 = st.columns([1, 1, 1])
            with b1:
                if st.button("Zeilen umbrechen", key="tk_wrap_text", type="primary", use_container_width=True):
                    st.session_state["tk_wrap_now"] = True
                    st.rerun()
            with b2:
                if st.button("Speichern", key="tk_save", type="primary", use_container_width=True):
                    aufgabe_val = (st.session_state.get("tk_title","") or "").strip()
                    short_title_val = (st.session_state.get("tk_title_short") or "").strip()
                    short_code_val = (st.session_state.get("tk_short") or "").strip()

                    # Regel: Wenn nur die Aufgabe gefüllt ist, NICHT speichern.
                    has_other_content = any([
                        bool(short_title_val),
                        bool(short_code_val),
                        bool((st.session_state.get("tk_expertise") or "").strip()),
                        bool((st.session_state.get("tk_duties") or "").strip()),
                        bool((st.session_state.get("tk_links_text") or "").strip()),
                        bool(st.session_state.get("tk_docs_selected")),
                        bool((st.session_state.get("tk_body") or "").strip()),
                    ])
                    if aufgabe_val and not has_other_content:
                        st.error("Nicht gespeichert: Bitte Kurz‑Titel/Kürzel oder Inhalte ergänzen. Das Feld 'Aufgabe' wird nicht persistiert.")
                        st.stop()

                    # Wir persistieren NIE den langen 'Aufgabe'-Text als DB-Titel, sondern den Kurz‑Titel.
                    if not short_title_val:
                        st.error("Bitte einen Kurz‑Titel angeben (der DB‑Titel wird aus dem Kurz‑Titel gesetzt).")
                        st.stop()

                    # Compose full markdown identisch zur Live-Vorschau
                    body_val = compose_task_body_from_state()
                    def _slug(s:str)->str:
                        s = s.strip().lower(); s = re.sub(r"[^a-z0-9_-]+","-", s); return re.sub(r"-+","-", s).strip("-") or "task"
                    key_to_use = ekey if (ekey and _slug(short_title_val)==_slug(ekey)) else None
                    r, created = upsert_task(
                        title=short_title_val,  # DB-Titel = Kurz‑Titel
                        body_text=body_val,
                        key=key_to_use,
                        short_title=short_title_val,
                        short_code=short_code_val,
                    )
                    st.toast(("Angelegt" if created else "Aktualisiert") + f": {r.key}")
                    st.session_state["tk_selected_key"] = r.key
                    st.session_state["tk_form_clear"] = True
                    st.rerun()
            with b3:
                delete_disabled = not bool(ekey)
                if st.button("Record löschen", key="tk_form_delete", type="primary", use_container_width=True, disabled=delete_disabled):
                    if delete_task(ekey):
                        st.toast("Eintrag gelöscht")
                        st.session_state["tk_selected_key"] = ""
                        st.session_state["tk_edit_key"] = ""
                        st.session_state["tk_form_clear"] = True
                        st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

# Vorschau zuletzt rendern, damit alle Formular-Änderungen bereits im State stehen
_render_preview()
