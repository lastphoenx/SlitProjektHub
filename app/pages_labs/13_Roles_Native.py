# app/pages/13_Roles_Native.py
from __future__ import annotations
import sys, math, re, time
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Roles – Native", page_icon="📋", layout="wide")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m06_ui import chips, md_editor_with_preview
from src.m07_roles import list_roles_df, load_role, upsert_role, function_suggestions
from src.m08_llm import providers_available, generate_role_text
import textwrap

SECTION_H = 1060  # +~10%
LIST_H    = 792   # +~10%

# Anzeige-/Validierungs-Konstanten zentral
PAD_TARGET = 15         # Zielbreite für das Kürzel (Ausrichtung der '|' Spalte)
GAP_AFTER_PIPE = 5      # Anzahl NBSP nach dem '|'
MAX_GROUP_LEN = PAD_TARGET  # Maximal erlaubte Länge für das Kürzel (Konsistenz mit Ausrichtung)

st.title("Roles – Native (ohne AgGrid)")

# Styling: Buttons kleiner/links, selektbox hellblau, Aktionszonen blau, Liste neutral
st.markdown(
        """
        <style>
        :root{
            --btn-blue:#60a5fa;       /* hellblau aktiv */
            --btn-blue-border:#60a5fa;
            --btn-grey:#e5e7eb;       /* grau inaktiv */
            --btn-grey-border:#d1d5db;
        }
    /* Buttons generell kleiner & linksbündig */
    .stButton>button{ padding:.35rem .7rem; font-size:.9rem; border-radius:10px; text-align:left; justify-content:flex-start; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \"Liberation Mono\", monospace; transition: background-color .15s ease, border-color .15s ease, transform .03s ease, box-shadow .15s ease; }
    .stButton>button *{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \"Liberation Mono\", monospace !important; }
        .stButton>button:hover{ filter: brightness(0.98); }
        .stButton>button:active{ transform: scale(0.98); }
    /* Primär-Buttons (für Aktionen): hellblau aktiv, grau inaktiv */
    .stButton>button[kind="primary"]:not(:disabled),
    .stButton>button[data-testid="baseButton-primary"]:not(:disabled){ background:var(--btn-blue) !important; color:#fff !important; border:1px solid var(--btn-blue-border) !important; }
    .stButton>button[kind="primary"]:not(:disabled):hover,
    .stButton>button[data-testid="baseButton-primary"]:not(:disabled):hover{ background:#3b82f6 !important; border-color:#3b82f6 !important; box-shadow:0 0 0 3px rgba(59,130,246,.25) !important; }
    .stButton>button[kind="primary"]:not(:disabled):active,
    .stButton>button[data-testid="baseButton-primary"]:not(:disabled):active{ background:#2563eb !important; border-color:#2563eb !important; }
    .stButton>button[kind="primary"]:disabled,
    .stButton>button[data-testid="baseButton-primary"]:disabled{ background:var(--btn-grey) !important; color:#374151 !important; border:1px solid var(--btn-grey-border) !important; opacity:1 !important; }
    /* Sekundär-Buttons (Liste): neutral */
    .stButton>button[kind="secondary"],
    .stButton>button[data-testid="baseButton-secondary"],
    .stButton>button:not([kind]){ background:#fff !important; color:#111827 !important; border:1px solid #e5e7eb !important; }
        .stButton>button[kind="secondary"]:hover,
        .stButton>button[data-testid="baseButton-secondary"]:hover,
        .stButton>button:not([kind]):hover{ background:#f9fafb !important; }
        .stButton>button[kind="secondary"]:active,
        .stButton>button[data-testid="baseButton-secondary"]:active,
        .stButton>button:not([kind]):active{ background:#f3f4f6 !important; }
        /* Selects hellblau + gleiche Höhe wie Buttons */
        div[data-baseweb="select"]>div{
            background:#dbeafe !important; border-color:#93c5fd !important;
            min-height:40px !important; height:40px !important; padding:0 .6rem !important;
        }
        /* Inhalt in der Vorschau weich umbrechen, kein horizontales Scrollen */
        #rn_preview{ overflow-x:hidden; }
        #rn_preview p, #rn_preview li, #rn_preview pre, #rn_preview code{
            white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere;
        }
        /* Global: Markdown-Inhalte weich umbrechen (falls Preview-Wrapper nicht greift) */
        .stMarkdown p, .stMarkdown li, .stMarkdown pre, .stMarkdown code{
            white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere;
        }
    /* Liste: extrem kompakt & volle Breite */
    #rn_list [data-testid="stVerticalBlock"],
    #rn_list .element-container{ gap:0 !important; margin:0 !important; padding:0 !important; }
    #rn_list .stButton{ margin:0 !important; padding:0 !important; }
    #rn_list .stMarkdown{ margin:0 !important; padding:0 !important; }
    #rn_list p{ margin:0 !important; }
    #rn_list .stButton>button{ padding:.08rem .45rem !important; line-height:1.0 !important; width:100% !important; display:block !important; font-size:.86rem !important; white-space:pre !important; letter-spacing:0 !important; font-variant-ligatures:none !important; }
    /* Pager: Buttons nicht umbrechen, komfortbreit */
    #rn_pager .stButton>button{ white-space:nowrap !important; min-width:120px !important; }
        </style>
        """,
        unsafe_allow_html=True,
)

# --- Helpers --------------------------------------------------------------
NBSP = "\u00A0"

def _normalize_spaces(s: str) -> str:
    """Normalize all whitespace (including exotic Unicode spaces and NBSP) to single regular spaces.
    Display-only: does not mutate stored data, just ensures stable width for alignment.
    """
    if not s:
        return ""
    # Replace NBSP and all Unicode whitespace with normal spaces, collapse runs
    s = (s.replace("\u00A0", " "))
    s = re.sub(r"\s+", " ", s)
    return s

try:  # optional dependency; avoid static import errors by probing via importlib
    import importlib.util as _ilus  # type: ignore
    _spec = _ilus.find_spec("wcwidth")
    if _spec is not None:
        from wcwidth import wcswidth as _wcswidth  # type: ignore
    else:
        _wcswidth = None  # type: ignore
except Exception:  # pragma: no cover
    _wcswidth = None  # type: ignore

def _vis_len(s: str) -> int:
    """Return visual cell width of s if wcwidth is available, else unicode codepoint length."""
    if _wcswidth is None:
        return len(s or "")
    try:
        w = _wcswidth(s or "")
        return max(0, w)
    except Exception:
        return len(s or "")

def record_label(short: str, title: str, short_width: int = PAD_TARGET, gap_after_pipe: int = GAP_AFTER_PIPE) -> str:
    """Build "SHORT···|·····TITLE" using NBSP padding to align the pipe column.
    - Normalizes internal whitespace to avoid mixed-width unicode spaces affecting alignment.
    - Uses visual width via wcwidth if available; falls back to len().
    """
    s_disp = _normalize_spaces((short or "").strip()) or "—"
    t_disp = (title or "").strip()
    cur_w  = _vis_len(s_disp)
    pad_len = max(0, short_width - cur_w)
    filler_after_short = NBSP * pad_len
    gap = NBSP * max(0, gap_after_pipe)
    return f"{s_disp}{filler_after_short}|{gap}{t_disp}"

def _strip_leading_markdown_fence(text: str) -> str:
    """If the text is wrapped in a leading ```markdown (or ```md) fence, remove it (and a matching trailing fence if present)."""
    if not text:
        return text or ""
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0].strip().lower()
    if first.startswith("```") and ("markdown" in first or first == "```md" or first == "```markdown"):
        # drop first line
        lines = lines[1:]
        # drop final closing fence if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text

def wrap_markdown_preserve(text: str, width: int = 100) -> str:
    """Soft-wrap paragraphs, preserve code blocks and headings, respect lists; also remove leading ```markdown fences."""
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
st.session_state.setdefault("rn_query", "")
st.session_state.setdefault("rn_sort_by", "Rollenbezeichnung")
st.session_state.setdefault("rn_sort_dir", "aufsteigend")
st.session_state.setdefault("rn_page", 1)
st.session_state.setdefault("rn_page_size", 8)
st.session_state.setdefault("rn_selected_key", "")

# Formular-States (rechter Bereich)
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

        # Zeile 1: nur Suche
        c1, _s1, _s2 = st.columns([0.9, 0.55, 0.45])
        with c1:
            q = st.text_input(
                "Suchen",
                key="rn_query",
                placeholder="z. B. CISO oder Chief Information ..."
            )

        # Zeile 2: Einträge/Seite (schmal), Sortieren nach, Richtung
        c4, c5, c6 = st.columns([0.45, 0.8, 0.75])
        with c4:
            st.selectbox("Einträge pro Seite", [8, 12, 20, 30, 50], key="rn_page_size")
            if st.session_state["rn_page_size"] != st.session_state["rn_prev_page_size"]:
                st.session_state["rn_prev_page_size"] = st.session_state["rn_page_size"]
                st.session_state["rn_page"] = 1
        with c5:
            sort_options = ["Rollenbezeichnung", "Kürzel"]
            if st.session_state.get("rn_sort_by") not in sort_options:
                st.session_state["rn_sort_by"] = "Rollenbezeichnung"
            sort_by = st.selectbox("Sortieren nach", sort_options, key="rn_sort_by")
        with c6:
            sort_dir = st.selectbox("Richtung", ["aufsteigend", "absteigend"], key="rn_sort_dir")

        # Änderungen -> zurück zu Seite 1; Wechsel bei Sortieren nach löst sofort Neuaufbau aus
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
            data = df.rename(columns={"Titel":"Rollenbezeichnung","Funktion":"Kürzel"}).copy()

            # Alles als String, NaN -> ""
            data["Key"]               = data["Key"].astype(str).fillna("")
            data["Rollenbezeichnung"] = data["Rollenbezeichnung"].astype(str).fillna("")
            data["Kürzel"]            = data["Kürzel"].astype(str).fillna("")

            # Bodies für Volltextsuche (einfaches OR über alle Felder)
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

            # Suche: nutze direkte Eingabe 'q' (nicht den Session-Wert), um doppeltes Tippen zu vermeiden
            qraw  = q or ""
            qnorm = qraw.strip().lower()

            # Wildcards '*' und '?' unterstützen. Leere Query oder '*' => kein Filter.
            if qnorm and qnorm != "*":
                if ("*" in qnorm) or ("?" in qnorm):
                    pattern = re.escape(qnorm).replace("\\*", ".*").replace("\\?", ".")
                    key_m   = data["Key"].str.lower().str.contains(pattern, regex=True)
                    title_m = data["Rollenbezeichnung"].str.lower().str.contains(pattern, regex=True)
                    short_m = data["Kürzel"].str.lower().str.contains(pattern, regex=True)
                    body_m  = beschreibung.str.lower().str.contains(pattern, regex=True)
                else:
                    key_m   = data["Key"].str.lower().str.contains(qnorm, regex=False)
                    title_m = data["Rollenbezeichnung"].str.lower().str.contains(qnorm, regex=False)
                    short_m = data["Kürzel"].str.lower().str.contains(qnorm, regex=False)
                    body_m  = beschreibung.str.lower().str.contains(qnorm, regex=False)
                data = data[key_m | title_m | short_m | body_m]

            # Sortierung
            by = st.session_state["rn_sort_by"]
            ascend = st.session_state["rn_sort_dir"] == "aufsteigend"
            if by in data.columns:
                data = data.sort_values(by=by, ascending=ascend, kind="mergesort")

            total = len(data)
            page_size = st.session_state["rn_page_size"]
            max_page = max(1, math.ceil(total / page_size))
            # Seite innerhalb der Grenzen halten
            st.session_state["rn_page"] = min(max_page, max(1, st.session_state["rn_page"]))
            page = st.session_state["rn_page"]

            start = (page - 1) * page_size
            end   = start + page_size
            page_df = data.iloc[start:end].reset_index(drop=True)

            # Pager-Controls (nur aktiv, wenn es eine Seite davor/dahinter gibt)
            with st.container():
                st.markdown('<div id="rn_pager">', unsafe_allow_html=True)
                left, spacer, right = st.columns([1.2, 6.0, 1.2])
                with left:
                    if st.button("◀︎ Zurück", disabled=(page <= 1), type="primary", width="stretch"):
                        st.session_state["rn_page"] = max(1, page - 1)
                        st.rerun()
                with right:
                    if st.button("Weiter ▶︎", disabled=(page >= max_page), type="primary", width="stretch"):
                        st.session_state["rn_page"] = min(max_page, page + 1)
                        st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

            st.caption(f"Treffer: {total} • Seite {page}/{max_page}")

            # --- Liste (einfach & sicher): jede Zeile = ein Button ---
            current = st.session_state.get("rn_selected_key", "")

            # Nativer Scroll-Container – nur scrollen, wenn wirklich nötig
            per_row_px = 28  # kompaktere Zeilenhöhe inkl. Abstände
            content_h = 12 + len(page_df) * per_row_px
            if content_h <= LIST_H:
                list_container = st.container()  # keine fixe Höhe -> kein Scrollbalken
            else:
                list_container = st.container(height=LIST_H)
            with list_container:
                st.markdown('<div id="rn_list">', unsafe_allow_html=True)
                if page_df.empty:
                    st.info("Keine Einträge auf dieser Seite.")
                else:
                    for i, row in page_df.iterrows():
                        key   = str(row["Key"])
                        title = str(row["Rollenbezeichnung"])
                        short = "" if str(row.get("Kürzel","")) == "nan" else str(row.get("Kürzel",""))
                        # Robust formatiertes Label mit NBSP-Padding und Whitespace-Normalisierung
                        label = record_label(short=short, title=title)

                        if st.button(label, key=f"rn_row_{page}_{i}_{key}", type="secondary", width="stretch"):
                            now = time.time()
                            last_k = st.session_state.get("rn_last_click_key", "")
                            last_t = st.session_state.get("rn_last_click_ts", 0.0)
                            if last_k == key and (now - last_t) < 0.5:
                                # Doppel-Klick -> Edit
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
            # Anzeige nur: Kürzel | Rollenbezeichnung (ohne Key)
            st.caption(f"{obj.short_code or '—'} | {obj.title if obj else ''}")
            st.markdown(body or "_(leer)_")
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
                    st.button("Markdown ⬇️", disabled=True, width="stretch")
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

# =============== (3) Formular (rechts) ===============
with col_form:
    with st.container(border=True, height=SECTION_H):
        st.markdown("### Neue Rolle / Bearbeiten")
        st.markdown('<div id="rn_form">', unsafe_allow_html=True)

        ekey = st.session_state.get("rn_edit_key", "")
        obj_e, body_e = load_role(ekey) if ekey else (None, "")

        # Vor dem Rendern: Falls Clear-Flag gesetzt, Felder leeren (fix für Widget-Reset)
        if st.session_state.get("rn_form_clear"):
            st.session_state["rn_edit_key"] = ""
            st.session_state["rn_last_loaded"] = None
            st.session_state["rn_title"] = ""
            st.session_state["rn_group"] = ""
            st.session_state["rn_body"]  = ""
            st.session_state["rn_form_clear"] = False

        # Beim Wechsel laden
        if ekey and st.session_state.get("rn_last_loaded") != ekey and obj_e:
            st.session_state["rn_title"] = obj_e.title
            st.session_state["rn_group"] = obj_e.short_code or ""
            st.session_state["rn_body"]  = body_e or ""
            st.session_state["rn_last_loaded"] = ekey

        # Schnellauswahl-Chips
        chips(
            function_suggestions()[:8],
            target_title_key="rn_title",
            target_function_key="rn_group",
            state_key="rn_quickpick",
            label="Schnellauswahl",
            title_map={
                "CEO":"Chief Executive Officer","CFO":"Chief Financial Officer",
                "CIO":"Chief Information Officer","CTO":"Chief Technology Officer",
                "COO":"Chief Operating Officer","CPO":"Chief Product Officer",
                "CISO":"Chief Information Security Officer","DPO":"Data Protection Officer",
            },
        )

        st.markdown("**Rollenbezeichnung**")
        st.text_input(" ", key="rn_title", label_visibility="collapsed",
                      placeholder="z. B. Chief Information Officer")

        st.markdown("**Kürzel (übergeordnete Rolle)**")
        st.text_input(" ", key="rn_group", label_visibility="collapsed",
                      placeholder="CIO / CFO / CEO / CISO / DPO …")
        # Live-Längenhinweis für das Kürzel (Max gemäß PAD_TARGET, passend zur Listenspalte)
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

        # KI-Assistent
        try:
            PROVS = providers_available()
        except Exception:
            PROVS = ["openai"]
        if PROVS and "llm_provider" not in st.session_state:
            st.session_state["llm_provider"] = PROVS[0]

        with st.container(border=True):
            # Globale KI-Einstellungen anzeigen + KI-Button
            prov = st.session_state.get("global_llm_provider", "openai")
            model = st.session_state.get("global_llm_model")
            temp = st.session_state.get("global_llm_temperature", 0.7)
            
            kc_info, kc_btn = st.columns([1.5, 1.5])
            with kc_info:
                st.caption("🤖 **KI-Einstellungen:** " + f"{prov} → {model or '—'} (T={temp:.1f})")
            with kc_btn:
                if st.button("KI-Vorschlag", key="rn_ai_suggest", type="primary", width="stretch"):
                    try:
                        suggestion = generate_role_text(
                            prov if prov != "none" else "openai",
                            st.session_state.get("rn_title",""),
                            st.session_state.get("rn_group",""),
                        )
                        # Vorschlag ebenfalls weich umbrechen (Codeblöcke/Listen respektieren + führenden ```markdown-Fence entfernen)
                        st.session_state["rn_body"] = wrap_markdown_preserve(suggestion, width=100)
                        st.toast("Vorschlag eingefügt.")
                        st.rerun()
                    except Exception as ex:
                        st.error(f"KI-Generierung fehlgeschlagen: {ex}")

        # Falls 'Zeilen umbrechen' im letzten Run angefordert wurde, Text VOR dem Rendern aktualisieren
        if st.session_state.get("rn_wrap_now"):
            st.session_state["rn_body"] = wrap_markdown_preserve(st.session_state.get("rn_body",""), width=100)
            st.session_state["rn_wrap_now"] = False

        try:
            md_editor_with_preview("Beschreibung / Profil (Markdown)",
                                   st.session_state.get("rn_body",""),
                                   key="rn_body",
                                   height=300)
        except TypeError:
            md_editor_with_preview("Beschreibung / Profil (Markdown)",
                                   st.session_state.get("rn_body",""),
                                   key="rn_body")

        # Aktionen – alle Buttons auf eine Zeile: [Zeilen umbrechen] [Speichern] [Löschen]
        b1, b2, b3 = st.columns([1, 1, 1])
        with b1:
            if st.button("Zeilen umbrechen", key="rn_wrap_text", type="primary", width="stretch"):
                # Nur Flag setzen; tatsächliches Update erfolgt vor dem Rendern des Editors im nächsten Run
                st.session_state["rn_wrap_now"] = True
                st.rerun()
        with b2:
            if st.button("Speichern", key="rn_save", type="primary", width="stretch"):
                title_val = (st.session_state.get("rn_title","") or "").strip()
                if not title_val:
                    st.error("Bitte die **Rollenbezeichnung** ausfüllen.")
                else:
                    group_raw = st.session_state.get("rn_group","") or ""
                    # Validierung Kürzel-Länge (max gemäß MAX_GROUP_LEN)
                    if len(group_raw) > MAX_GROUP_LEN and not st.session_state.get("rn_group_autotrim", False):
                        st.error(f"Kürzel ist länger als {MAX_GROUP_LEN} Zeichen. Kürzen oder 'Automatisch kürzen' aktivieren.")
                        st.stop()
                    group_val = (group_raw[:MAX_GROUP_LEN] if st.session_state.get("rn_group_autotrim", False) else group_raw) or None
                    body_raw  = st.session_state.get("rn_body","") or ""
                    # Zeilen vorsichtig umbrechen (Codeblöcke/Listen respektieren + führenden ```markdown-Fence entfernen)
                    body_val = wrap_markdown_preserve(body_raw, width=100)
                    # Nur dann Key beibehalten, wenn Titel-Slug == Key-Slug
                    def _slug(s:str)->str:
                        s = s.strip().lower()
                        s = re.sub(r"[^a-z0-9_-]+","-", s)
                        return re.sub(r"-+","-", s).strip("-") or "role"
                    key_to_use = ekey if (ekey and _slug(title_val)==_slug(ekey)) else None
                    r, created = upsert_role(title=title_val, group_name=group_val, body_text=body_val, key=key_to_use)
                    st.toast(("Angelegt" if created else "Aktualisiert") + f": {r.key}")
                    st.session_state["rn_selected_key"] = r.key
                    # Formular im nächsten Run leeren (Widget-Reset-konform)
                    st.session_state["rn_form_clear"] = True
                    st.rerun()
        with b3:
            delete_disabled = not bool(ekey)
            if st.button("Record löschen", key="rn_form_delete", type="primary", width="stretch", disabled=delete_disabled):
                from src.m07_roles import soft_delete_role
                if soft_delete_role(ekey):
                    st.toast("Eintrag gelöscht")
                    st.session_state["rn_selected_key"] = ""
                    st.session_state["rn_edit_key"] = ""
                    st.session_state["rn_form_clear"] = True
                    st.rerun()
    # Kein 'Neu' mehr – Speichern leert das Formular
    st.markdown('</div>', unsafe_allow_html=True)
