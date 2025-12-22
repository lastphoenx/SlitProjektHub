import streamlit as st

# Keep this lightweight: start script depends on this entrypoint.
st.set_page_config(page_title="KI-Projekt Hub", page_icon="🚀", layout="wide")

# Hide the root (Home/Streamlit App) entry from the sidebar page navigation
# so it doesn't appear as an extra menu item alongside the real pages.
st.markdown(
	"""
	<style>
	/* In multipage apps the first nav item is the root script (this file). */
	[data-testid="stSidebarNav"] ul li:first-child { display: none !important; }
	</style>
	""",
	unsafe_allow_html=True,
)

# Immediately route users to the actual landing page inside pages/
try:
	st.switch_page("pages/00_KI-Projekt Hub.py")
except Exception:
	# Fallback: very light placeholder in case of older Streamlit versions
	st.write("Lade Startseite…")
