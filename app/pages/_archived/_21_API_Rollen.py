# app/pages/21_API_Rollen.py - Streamlit Frontend mit FastAPI Backend
import streamlit as st
import sys
from pathlib import Path

st.set_page_config(page_title="API Rollen", page_icon="🔗", layout="wide")

# Add src to path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api_client import get_api_client

st.title("🔗 API-basierte Rollen")
st.markdown("**Experimentelle Frontend-Backend-Trennung mit FastAPI**")

# API Client
api = get_api_client()

# Health Check
if not api.health_check():
    st.error("❌ Backend nicht erreichbar. Bitte starten Sie den FastAPI-Server:")
    st.code("cd backend && python main.py")
    st.stop()
else:
    st.success("✅ Backend-Verbindung aktiv")

# ============ TABS ============
tab1, tab2, tab3 = st.tabs(["📋 Rollen anzeigen", "➕ Rolle erstellen", "🤖 KI-Integration"])

# ============ TAB 1: ROLLEN ANZEIGEN ============
with tab1:
    st.subheader("📋 Alle Rollen")
    
    # Roles laden via API
    roles = api.get_roles()
    
    if roles:
        # Search
        search = st.text_input("🔍 Suchen:", placeholder="Rollentitel eingeben...")
        
        # Filter roles
        filtered_roles = roles
        if search:
            filtered_roles = [r for r in roles if search.lower() in r.get('Titel', '').lower()]
        
        # Display roles
        for role in filtered_roles:
            role_key = role.get('Key', '')
            role_title = role.get('Titel', 'Unbekannt')
            role_group = role.get('Funktion', '')
            
            with st.container(border=True):
                col1, col2, col3 = st.columns([3, 1, 1])
                
                with col1:
                    st.markdown(f"**{role_title}**")
                    if role_group:
                        st.caption(f"Gruppe: {role_group}")
                
                with col2:
                    if st.button("👁 Details", key=f"view_{role_key}"):
                        role_details = api.get_role(role_key)
                        if role_details:
                            st.session_state[f"role_details_{role_key}"] = role_details
                
                with col3:
                    if st.button("🗑 Löschen", key=f"delete_{role_key}", type="secondary"):
                        if api.delete_role(role_key):
                            st.success("✅ Rolle gelöscht")
                            st.rerun()
                
                # Show details if requested
                if f"role_details_{role_key}" in st.session_state:
                    details = st.session_state[f"role_details_{role_key}"]
                    with st.expander("📄 Rollenbeschreibung", expanded=True):
                        st.markdown(details.get('body_text', 'Keine Beschreibung'))
                        if st.button("❌ Schließen", key=f"close_{role_key}"):
                            del st.session_state[f"role_details_{role_key}"]
                            st.rerun()
    else:
        st.info("📭 Keine Rollen gefunden oder Backend-Fehler")

# ============ TAB 2: ROLLE ERSTELLEN ============
with tab2:
    st.subheader("➕ Neue Rolle erstellen")
    
    with st.form("create_role_form"):
        title = st.text_input("🎭 Rollenbezeichnung:", placeholder="z.B. Chief Technology Officer")
        group_name = st.text_input("🏷 Gruppe/Kürzel:", placeholder="z.B. CTO")
        body_text = st.text_area("📝 Beschreibung:", height=200, placeholder="Rollenbeschreibung...")
        
        submitted = st.form_submit_button("💾 Rolle erstellen", type="primary")
        
        if submitted:
            if not title.strip():
                st.error("❌ Rollenbezeichnung ist erforderlich")
            else:
                result = api.create_role(
                    title=title.strip(),
                    group_name=group_name.strip() or None,
                    body_text=body_text.strip()
                )
                
                if result:
                    action = "erstellt" if result.get('created') else "aktualisiert"
                    st.success(f"✅ Rolle {action}: {result['key']}")
                    # Clear form
                    st.rerun()

# ============ TAB 3: KI-INTEGRATION ============
with tab3:
    st.subheader("🤖 KI-gestützte Rollenerstellung")
    
    # LLM Providers laden
    providers = api.get_llm_providers()
    
    if providers:
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.markdown("#### 🎯 Rolle generieren")
            
            provider = st.selectbox("KI-Provider:", providers)
            role_title = st.text_input("Rollenbezeichnung:", placeholder="Chief Data Officer")
            role_group = st.text_input("Gruppe (optional):", placeholder="CDO")
            
            if st.button("✨ KI-Beschreibung generieren", type="primary"):
                if role_title.strip():
                    with st.spinner("🤖 KI generiert Rollenbeschreibung..."):
                        generated_text = api.generate_role_text(
                            provider=provider,
                            title=role_title.strip(),
                            group_name=role_group.strip() or None
                        )
                    
                    if generated_text:
                        st.session_state["generated_role"] = {
                            "title": role_title.strip(),
                            "group_name": role_group.strip() or None,
                            "body_text": generated_text
                        }
                        st.success("✅ Beschreibung generiert!")
                        st.rerun()
                    else:
                        st.error("❌ Generierung fehlgeschlagen")
                else:
                    st.error("❌ Bitte Rollenbezeichnung eingeben")
        
        with col2:
            st.markdown("#### 📝 Generierte Rolle")
            
            if "generated_role" in st.session_state:
                gen_role = st.session_state["generated_role"]
                
                st.markdown(f"**Titel:** {gen_role['title']}")
                if gen_role['group_name']:
                    st.markdown(f"**Gruppe:** {gen_role['group_name']}")
                
                st.markdown("**Beschreibung:**")
                st.markdown(gen_role['body_text'])
                
                col_save, col_clear = st.columns(2)
                
                with col_save:
                    if st.button("💾 Rolle speichern", type="primary", use_container_width=True):
                        result = api.create_role(
                            title=gen_role['title'],
                            group_name=gen_role['group_name'],
                            body_text=gen_role['body_text']
                        )
                        
                        if result:
                            st.success(f"✅ Rolle gespeichert: {result['key']}")
                            del st.session_state["generated_role"]
                            st.rerun()
                
                with col_clear:
                    if st.button("🗑 Verwerfen", use_container_width=True):
                        del st.session_state["generated_role"]
                        st.rerun()
            else:
                st.info("👆 Generiere zuerst eine Rollenbeschreibung")
    else:
        st.error("❌ Keine KI-Provider verfügbar")

# ============ SIDEBAR: API INFO ============
with st.sidebar:
    st.markdown("### 🔗 API-Informationen")
    st.markdown(f"**Backend URL:** `{api.base_url}`")
    
    if st.button("🔄 Health Check"):
        if api.health_check():
            st.success("✅ API erreichbar")
        else:
            st.error("❌ API nicht erreichbar")
    
    st.markdown("---")
    st.markdown("### 📚 Verfügbare Endpoints")
    st.code("""
GET /api/roles
GET /api/roles/{key}  
POST /api/roles
DELETE /api/roles/{key}
GET /api/llm/providers
POST /api/llm/generate-role
    """)