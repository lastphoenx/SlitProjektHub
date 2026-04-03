from __future__ import annotations
import pandas as pd
import streamlit as st

try:
    from rapidfuzz import process as rf_process  # optional
    _HAS_RF = True
except Exception:
    _HAS_RF = False

def page_header(title: str, subtitle: str | None = None):
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.divider()

def search_box(label="Suchen", key="global_search"):
    return st.text_input(label, placeholder="Tippen zum Filtern …", key=key)

def filter_dataframe(df: pd.DataFrame, query: str) -> pd.DataFrame:
    if df is None or df.empty or not query:
        return df
    q = query.strip().lower()
    contains = df.astype(str).apply(lambda col: col.str.lower().str.contains(q, na=False))
    out = df[contains.any(axis=1)]
    if out.empty and _HAS_RF and len(df) <= 5000:
        # fuzzy fallback
        keep = []
        for idx, row in df.astype(str).iterrows():
            text = " | ".join(row.values)
            if not text:
                continue
            score = rf_process.extractOne(q, [text])[1]
            if score >= 60:
                keep.append(idx)
        out = df.loc[keep]
    return out

def table(df, height: int = 400, key: str = "grid"):
    st.dataframe(df, width="stretch", height=height, hide_index=True, key=key)

def grid_select(
    df: pd.DataFrame,
    height: int = 400,
    key: str = "grid_select",
    *,
    hide_key: bool = True,
    pick_label: str = "Wählen",
    enforce_single: bool = True,
) -> str:
    """Zeigt eine Tabelle mit einer logischen Einzelauswahl und gibt den Key zurück.
    - hide_key: blendet die sichtbare "Key"-Spalte aus (intern weiter genutzt)
    - Es wird nur eine Auswahl berücksichtigt (die erste true-Zeile).
    """
    if df is None or df.empty:
        st.dataframe(df if df is not None else pd.DataFrame(), height=height, hide_index=True, key=f"{key}_empty")
        return ""

    view = df.reset_index(drop=True).copy()
    display = view.copy()
    if hide_key and "Key" in display.columns:
        display = display.drop(columns=["Key"])  # "Key" intern behalten, sichtbar ausblenden

    # Füge eine Auswahlspalte hinzu (nicht persistent)
    pick_col = "Auswahl"
    if pick_col not in display.columns:
        display.insert(0, pick_col, False)

    # Vorbelegung aus Session: genau eine Zeile aktiv halten
    sel_state_key = f"{key}__selected_key"
    pre_key = st.session_state.get(sel_state_key, "")
    if pre_key and "Key" in view.columns:
        try:
            pre_idx = view.index[view["Key"].astype(str) == str(pre_key)].tolist()
            if pre_idx:
                display.loc[:, pick_col] = False
                display.loc[pre_idx[0], pick_col] = True
        except Exception:
            pass

    edited = st.data_editor(
        display,
        height=height,
        hide_index=True,
        key=key,
        num_rows="fixed",
        column_config={
            pick_col: st.column_config.CheckboxColumn(label=pick_label, help="Zeile auswählen", default=False)
        },
        disabled=False,
    )

    # Ermittle erste aktivierte Zeile
    try:
        sel_idx = edited.index[edited[pick_col] == True].tolist()
        if sel_idx:
            # Enforce single selection: nimm die erste, speichere in Session und rerender, falls mehrere
            idx = sel_idx[0]
            try:
                key_val = str(view.iloc[idx]["Key"]) if "Key" in view.columns else ""
            except Exception:
                key_val = ""
            if enforce_single and len(sel_idx) > 1:
                st.session_state[sel_state_key] = key_val
                st.rerun()
            # Speichere aktuelle Auswahl
            st.session_state[sel_state_key] = key_val
            return key_val
    except Exception:
        pass
    return ""

def form_scaffold(form_key: str, fields: dict[str, dict]):
    with st.form(form_key, border=True):
        values = {}
        for name, cfg in fields.items():
            label = cfg.get("label", name)
            placeholder = cfg.get("placeholder", "")
            value = cfg.get("value", "")
            if cfg.get("type","text") == "area":
                values[name] = st.text_area(label, value=value, placeholder=placeholder, height=160)
            else:
                values[name] = st.text_input(label, value=value, placeholder=placeholder)
        submitted = st.form_submit_button("Speichern")
        return submitted, values

def chips(
    options: list[str],
    target_title_key: str | None = None,       # z.B. "role_title_input"
    target_function_key: str | None = None,    # z.B. "role_group_input"
    state_key: str = "role_quickpick",
    label: str = "Schnellauswahl",
    title_map: dict[str, str] | None = None,   # z.B. {"CFO": "Chief Financial Officer", ...}
):
    st.caption(label)
    if not options:
        return
    cols = st.columns(min(4, max(2, len(options))))
    for i, opt in enumerate(options):
        with cols[i % len(cols)]:
            if st.button(opt, key=f"chip_{state_key}_{i}", width="stretch"):
                st.session_state[state_key] = opt
                # Titel ableiten (Map oder Default "Chief <opt> Officer")
                title_val = (title_map or {}).get(opt, f"Chief {opt} Officer")
                if target_title_key:
                    st.session_state[target_title_key] = title_val
                if target_function_key:
                    st.session_state[target_function_key] = opt
                # Wichtig: KEIN st.rerun() hier -> Streamlit rerendert ohnehin nach Button

def md_editor_with_preview(label: str, value: str, key: str, *, height: int = 180):
    """Markdown-Editor mit Vorschau.
    Vermeidet Streamlit-Warnung: Entweder initialer value ODER Session-State, nicht beides gleichzeitig.
    height: Höhe des Textbereichs in Pixeln (Default 180)
    """
    tab_edit, tab_preview = st.tabs(["Bearbeiten", "Vorschau"])
    with tab_edit:
        if key in st.session_state:
            txt = st.text_area(label, key=key, height=height, placeholder="Markdown …")
        else:
            txt = st.text_area(label, value=value, key=key, height=height, placeholder="Markdown …")
    with tab_preview:
        st.markdown(txt or "_(leer)_")
    return txt

# --- Gemeinsame CSS-Injektion für Textareas & Scrollbars ---
def inject_form_css(
    *,
    ta_min: int = 60,
    ta_max: int = 520,
    scope_ids: list[str] | tuple[str, ...] = (),
    strong_scrollbar: bool = True,
):
    """Injiziert globale Styles:
    - Textareas: vertikales Resizing erlauben, Min/Max aus Parametern
    - Scrollbars: optisch deutlicher im angegebenen Scope (div-IDs)

    scope_ids: Liste der Container-IDs ohne '#', z.B. ("tk_form", "cx_form")
    strong_scrollbar: True = breite/kontrastreiche Scrollbar, False = dezente Variante
    """
    scopes = []
    parent_scopes = []
    for sid in scope_ids or []:
        s = sid.strip()
        if not s:
            continue
        if not s.startswith("#"):
            s = f"#{s}"
        scopes.append(s)
        # Versuche auch den unmittelbaren Parent ohne eigene ID zu treffen:
        # moderne Browser unterstützen :has(). So erreichen wir den äußeren
        # Container (z.B. st.container mit fester Höhe), der den Scrollbar
        # tatsächlich rendert.
        parent_scopes.append(f"div:has(>{s})")
        parent_scopes.append(f"section:has(>{s})")
    scope_sel_self = ", ".join(scopes) if scopes else "body"
    scope_sel_parent = ", ".join(parent_scopes) if parent_scopes else ""

    sb_width = 12 if strong_scrollbar else 6
    sb_thumb = "#94a3b8" if strong_scrollbar else "#cbd5e1"
    sb_track = "#e5e7eb" if strong_scrollbar else "#f3f4f6"
    sb_fw    = "auto" if strong_scrollbar else "thin"

    css = f"""
    <style>
    /* Textareas: Resize erlauben + Min/Max aus Parametern */
    .stTextArea textarea,
    div[data-testid="stTextArea"] textarea,
    div[data-baseweb="textarea"] textarea,
    {scope_sel_self} textarea{{
        resize: vertical !important;
        overflow: auto !important;
        min-height: {ta_min}px !important;
        max-height: {ta_max}px !important;
    }}

    /* Scrollbars im Scope deutlicher darstellen */
    {scope_sel_self}{',' if scope_sel_parent else ''}{scope_sel_parent}{{
        scrollbar-gutter: stable both-edges;
        scrollbar-width: {sb_fw};               /* Firefox */
        scrollbar-color: {sb_thumb} {sb_track};  /* Firefox */
    }}
    {scope_sel_self}::-webkit-scrollbar{{ width: {sb_width}px; height: {sb_width}px; }}
    {scope_sel_self}::-webkit-scrollbar-thumb{{ background-color: {sb_thumb}; border-radius: 8px; border: 2px solid {sb_track}; }}
    {scope_sel_self}::-webkit-scrollbar-track{{ background: {sb_track}; }}
    {scope_sel_parent}::-webkit-scrollbar{{ width: {sb_width}px; height: {sb_width}px; }}
    {scope_sel_parent}::-webkit-scrollbar-thumb{{ background-color: {sb_thumb}; border-radius: 8px; border: 2px solid {sb_track}; }}
    {scope_sel_parent}::-webkit-scrollbar-track{{ background: {sb_track}; }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

def render_global_llm_settings():
    """
    Rendert die globalen KI-Einstellungen in der Sidebar.
    Sollte in jeder Page aufgerufen werden, um die Einstellungen überall verfügbar zu machen.
    Mit Persistenz und Live-Status-Indikator.
    """
    from .m08_llm import providers_available, get_available_models, have_key, test_connection
    from .m01_config import get_settings, load_user_settings, save_user_settings
    
    S = get_settings()
    
    # Stelle sicher, dass der Root-Navi-Eintrag (Home/Streamlit App) ausgeblendet ist
    _inject_hide_root_nav()
    
    st.markdown("### 🤖 KI-Einstellungen (Global)")
    
    # Lade persistente Settings beim ersten Aufruf
    if "settings_loaded" not in st.session_state:
        user_settings = load_user_settings()
        defaults = S.llm_defaults
        st.session_state["global_llm_provider"] = user_settings.get("provider", defaults.get("provider", "anthropic"))
        st.session_state["global_llm_model"] = user_settings.get("model", defaults.get("model", "sonnet"))
        st.session_state["global_llm_temperature"] = user_settings.get("temperature", defaults.get("temperature", 0.7))
        st.session_state["global_llm_rag_top_k"] = user_settings.get("rag_top_k", defaults.get("rag_top_k", 5))
        st.session_state["global_rag_chunk_size"] = user_settings.get("rag_chunk_size", defaults.get("rag_chunk_size", 1000))
        st.session_state["global_rag_similarity_threshold"] = user_settings.get("rag_similarity_threshold", defaults.get("rag_similarity_threshold", 0.5))
        st.session_state["global_rag_enabled"] = user_settings.get("rag_enabled", defaults.get("rag_enabled", True))
        st.session_state["settings_loaded"] = True
        st.session_state["_provider_test_cache"] = None
    
    available_providers = providers_available()
    if available_providers and available_providers != ["none"]:
        provider = st.selectbox(
            "KI-Provider",
            options=available_providers,
            index=available_providers.index(st.session_state.get("global_llm_provider")) if st.session_state.get("global_llm_provider") in available_providers else 0,
            key="sidebar_provider_select",
            help="Gilt für alle Seiten"
        )

        provider_changed = st.session_state["global_llm_provider"] != provider
        if provider_changed:
            st.session_state["global_llm_provider"] = provider
            st.session_state["_provider_test_result"] = None
            _save_settings_to_disk()

        # Modell-Select VOR Status-Anzeige, damit model_changed bekannt ist
        available_models = get_available_models(provider)
        if available_models:
            # Sicherstellen dass gespeichertes Modell für neuen Provider gültig ist
            saved_model = st.session_state.get("global_llm_model", "")
            model_index = available_models.index(saved_model) if saved_model in available_models else 0
            model = st.selectbox(
                "Modell",
                options=available_models,
                index=model_index,
                key="sidebar_model_select",
                help="Gilt für alle Seiten"
            )
            model_changed = st.session_state["global_llm_model"] != model
            if model_changed:
                st.session_state["global_llm_model"] = model
                st.session_state["_provider_test_result"] = None  # Re-Test erzwingen
                _save_settings_to_disk()
        else:
            model_changed = False

        # Status-Indikator: Test nur bei Provider-Wechsel (nicht bei bloßem Modell-Wechsel)
        if provider_changed and have_key(provider):
            with st.spinner(f"🔗 {provider.upper()} wird geprüft..."):
                connected, error_msg = test_connection(provider, timeout=10.0)
            st.session_state["_provider_test_result"] = (connected, error_msg)
            if connected:
                st.success(f"✓ {provider.upper()} verbunden", icon="✅")
            else:
                st.error(f"✗ {provider.upper()} fehlgeschlagen: {error_msg[:80]}", icon="❌")
        elif model_changed and have_key(provider):
            # Modell gewechselt: Key ist bekannt-gültig, kein Volltest nötig
            st.session_state["_provider_test_result"] = (True, "")
            st.success(f"✓ {provider.upper()} · Modell gespeichert", icon="✅")
        elif have_key(provider):
            cached_result = st.session_state.get("_provider_test_result")
            if cached_result is not None and not cached_result[0]:
                st.error(f"✗ {provider.upper()} fehlgeschlagen: {cached_result[1][:80]}", icon="❌")
            else:
                st.info(f"✓ {provider.upper()} konfiguriert", icon="ℹ️")
        else:
            st.error(f"✗ {provider.upper()} nicht konfiguriert", icon="❌")
        
        temperature = st.slider(
            "Temperatur",
            min_value=0.0,
            max_value=2.0,
            value=st.session_state.get("global_llm_temperature", 0.7),
            step=0.1,
            key="sidebar_temperature_slider",
            help="0.0=präzise, 1.0=kreativ, 2.0=variabel"
        )
        if st.session_state["global_llm_temperature"] != temperature:
            st.session_state["global_llm_temperature"] = temperature
            _save_settings_to_disk()
        
        top_k = st.slider(
            "RAG-Kontexte",
            min_value=1,
            max_value=25,
            value=st.session_state.get("global_llm_rag_top_k", 5),
            step=1,
            key="sidebar_rag_top_k_slider",
            help="Anzahl der RAG-Chunks für Kontext (mehr = detaillierter)"
        )
        if st.session_state["global_llm_rag_top_k"] != top_k:
            st.session_state["global_llm_rag_top_k"] = top_k
            _save_settings_to_disk()

        chunk_size = st.slider(
            "Standard Chunk-Größe (Upload)",
            min_value=100,
            max_value=5000,
            value=st.session_state.get("global_rag_chunk_size", 1000),
            step=100,
            key="sidebar_rag_chunk_size_slider",
            help="Standard-Chunk-Größe für neue Dokument-Uploads (in Zeichen). Kann pro Dokument angepasst werden."
        )
        if st.session_state["global_rag_chunk_size"] != chunk_size:
            st.session_state["global_rag_chunk_size"] = chunk_size
            _save_settings_to_disk()

        threshold = st.slider(
            "Similarity-Threshold",
            min_value=0.0,
            max_value=1.0,
            value=st.session_state.get("global_rag_similarity_threshold", 0.5),
            step=0.05,
            key="sidebar_rag_threshold_slider",
            help="Mindest-Ähnlichkeit für RAG-Treffer (0.0=alles, 1.0=exakt)"
        )
        if st.session_state["global_rag_similarity_threshold"] != threshold:
            st.session_state["global_rag_similarity_threshold"] = threshold
            _save_settings_to_disk()

        rag_enabled = st.toggle(
            "RAG aktivieren",
            value=st.session_state.get("global_rag_enabled", True),
            key="sidebar_rag_enabled_toggle",
            help="RAG-Kontext im Chat nutzen"
        )
        if st.session_state["global_rag_enabled"] != rag_enabled:
            st.session_state["global_rag_enabled"] = rag_enabled
            _save_settings_to_disk()
        
        # Info-Expander: Parameter-Erklärungen
        with st.expander("ℹ️ Was bedeuten diese Werte?", expanded=False):
            st.markdown("""
            **RAG-Kontexte (Top-K):** `7`  
            Anzahl der Dokument-Chunks die als Kontext eingefügt werden.
            - **5:** Schnell, Budget-freundlich
            - **7:** ✅ **Optimal** (3-5 verschiedene Dokumente dank Diversity-Filter)
            - **10+:** Sehr detailliert, aber mehr Tokens
            
            ---
            
            **Chunk-Größe:** `1200`  
            Zeichenanzahl pro Dokument-Abschnitt beim Upload.
            - **800-1000:** Für kurze FAQ/Snippets
            - **1200:** ✅ **Optimal für Pflichtenheft** (2-4 Absätze, Tabellen bleiben zusammen)
            - **2000+:** Für sehr lange Fließtexte
            
            ---
            
            **Similarity-Threshold:** `0.45` (45%)  
            Mindest-Ähnlichkeit zwischen Frage und Dokument-Chunk.
            - **0.60:** Sehr hohe Präzision, wenig Recall (nur perfekte Matches)
            - **0.45:** ✅ **Balance** (getestet: findet relevante Dokumente, filtert Noise)
            - **0.30:** Viel Recall, aber irrelevante Chunks möglich
            
            💡 **Tipp:** Bei "zu wenig Kontext" → senken auf 0.35  
            💡 **Tipp:** Bei "zu viel Irrelevantes" → erhöhen auf 0.55
            
            ---
            
            **Temperatur:** `0.7`  
            Kreativität vs. Faktentreue der KI-Antworten.
            - **0.0-0.3:** Deterministisch, sehr faktisch (Code, Fakten)
            - **0.7:** ✅ **Optimal für Pflichtenheft** (variabel aber korrekt)
            - **1.0+:** Kreativ, Brainstorming (Risiko: Halluzinationen)
            
            💡 **Tipp:** Für Marketing/Storytelling → 1.0  
            💡 **Tipp:** Für technische Spezifikationen → 0.3-0.5
            
            ---
            
            **RAG aktivieren:** `✅`  
            ⚠️ **Muss aktiviert sein** sonst antwortet KI ohne Pflichtenheft-Kontext!
            """)
    else:
        st.warning("⚠️ Kein KI-Provider verfügbar. Bitte .env konfigurieren.")

def _save_settings_to_disk():
    """Hilfsfunktion zum Speichern der Settings"""
    from .m01_config import save_user_settings
    settings = {
        "provider": st.session_state.get("global_llm_provider"),
        "model": st.session_state.get("global_llm_model"),
        "temperature": st.session_state.get("global_llm_temperature"),
        "rag_top_k": st.session_state.get("global_llm_rag_top_k"),
        "rag_chunk_size": st.session_state.get("global_rag_chunk_size"),
        "rag_similarity_threshold": st.session_state.get("global_rag_similarity_threshold"),
        "rag_enabled": st.session_state.get("global_rag_enabled"),
    }
    save_user_settings(settings)


def _inject_hide_root_nav():
    """Blendet den Root-Menüeintrag (Home/Streamlit App) in der Sidebar aus.
    Nutzt CSS + JS (MutationObserver), damit es auch nach Navigationen stabil bleibt.
    """
    # 1) CSS-Fallback (sofortiges Ausblenden, falls Reihenfolge passt)
    css = """
    <style>
    [data-testid="stSidebarNav"] ul li:first-child { display: none !important; }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

    # 2) JS: Entferne gezielt den Home/Root-Eintrag bei jeder Sidebar-Neurenderung
    try:
        from streamlit.components.v1 import html as _html
        js = """
        <script>
        (function() {
            function hideHome() {
                const nav = document.querySelector('[data-testid="stSidebarNav"] ul');
                if (!nav) return;
                const items = Array.from(nav.querySelectorAll('li'));
                for (const li of items) {
                    const a = li.querySelector('a');
                    if (!a) continue;
                    const txt = (a.textContent || '').trim().toLowerCase();
                    const href = (a.getAttribute('href') || '').toLowerCase();
                    const isRoot = (
                        txt === 'home' ||
                        txt === 'streamlit app' ||
                        txt === 'ki-projekt hub' ||
                        href === '/' || href === '#/' ||
                        href.includes('streamlit_app')
                    );
                    if (isRoot) {
                        li.style.display = 'none';
                    }
                }
            }
            const target = document.querySelector('[data-testid="stSidebar"]');
            if (target) {
                const mo = new MutationObserver(() => hideHome());
                mo.observe(target, { childList: true, subtree: true });
            }
            // Initial attempt
            hideHome();
        })();
        </script>
        """
        _html(js, height=0)
    except Exception:
        pass
