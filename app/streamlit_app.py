import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.auth_gate import is_authenticated, require_auth

st.set_page_config(page_title="KI-Projekt Hub", page_icon="🚀", layout="wide")

if not is_authenticated():
    require_auth()
    st.stop()

require_auth()

PAGES_DIR = Path(__file__).resolve().parent / "nav_pages"


def _page_title(path: Path) -> str:
    stem = path.stem
    if "_" in stem:
        return stem.split("_", 1)[1].replace("_", " ")
    return stem.replace("_", " ")


page_files = sorted(
    p for p in PAGES_DIR.glob("*.py") if not p.name.startswith("_")
)
nav_pages = [st.Page(str(p), title=_page_title(p)) for p in page_files]
if not nav_pages:
    st.error("Keine Seiten in app/nav_pages/ gefunden.")
    st.stop()

pg = st.navigation(nav_pages, position="sidebar")
pg.run()
