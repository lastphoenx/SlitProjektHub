# app/pages/01_Dashboard.py
import sys
from pathlib import Path
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m06_ui import render_global_llm_settings

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")

with st.sidebar:
    render_global_llm_settings()
    st.markdown("---")

st.header("Dashboard")
st.info("Hier kommen später Projekt-Übersichten, Metriken, letzter Chat-Kontext etc.")
