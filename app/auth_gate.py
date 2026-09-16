"""
app/auth_gate.py – Streamlit Auth-Gate.

Globale Absicherung über app/streamlit_app.py (st.navigation) +
require_auth() in jeder Page als zweite Linie.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.m01_config import get_settings
from src.m03_db import init_db
from src.m14_auth import (
    authenticate,
    authenticate_totp,
    can_evaluate,
    get_admin_username,
    get_totp_uri,
    is_2fa_enabled,
    is_setup_required,
    list_login_usernames,
    resolve_client_ip,
    revoke_sessions,
    session_username,
    setup_admin,
    validate_session_token,
)


def _direct_peer_ip() -> str | None:
    ip = getattr(st.context, "ip_address", None)
    if ip:
        return str(ip)
    return None


def _get_client_ip() -> str:
    try:
        headers = dict(st.context.headers)
    except Exception:
        headers = {}
    return resolve_client_ip(_direct_peer_ip(), headers)


def _hide_sidebar_nav() -> None:
    """Keine Multipage-Navigation auf Login/Setup; schmale zentrierte Form."""
    st.markdown(
        """
        <style>
        [data-testid="stSidebarNav"] { display: none !important; }
        [data-testid="stSidebar"] { min-width: 0 !important; width: 0 !important; }
        [data-testid="stSidebar"] > div { display: none !important; }
        [data-testid="stAppViewContainer"] > section.main .block-container {
            max-width: 26rem !important;
            margin: 2rem auto 0 auto !important;
            padding-top: 1rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def is_authenticated() -> bool:
    init_db()
    s = get_settings()
    if not s.auth_enabled:
        return True
    if is_setup_required():
        return False
    token = st.session_state.get("_auth_token", "")
    timeout = s.auth_session_timeout_minutes * 60
    return validate_session_token(token, max_age_seconds=timeout)


def _render_setup_page() -> None:
    _hide_sidebar_nav()
    st.title("🔐 Ersteinrichtung")
    st.warning(
        "Setup ist ein Wettlauf: wer zuerst kommt, wird Admin. "
        "Sicherer: `python scripts/maintenance/setup_admin.py` auf dem Server, "
        "**bevor** der Port öffentlich erreichbar ist."
    )

    with st.form("setup_form"):
        username = st.text_input("Benutzername", value="admin")
        pw = st.text_input("Passwort", type="password")
        pw2 = st.text_input("Passwort bestätigen", type="password")
        submitted = st.form_submit_button("Account erstellen")

    if submitted:
        if not username or not pw:
            st.error("Benutzername und Passwort sind erforderlich.")
        elif pw != pw2:
            st.error("Passwörter stimmen nicht überein.")
        elif len(pw) < 10:
            st.error("Passwort muss mindestens 10 Zeichen haben.")
        else:
            totp_secret = setup_admin(username, pw)
            st.session_state["setup_done"] = True
            st.session_state["totp_secret_new"] = totp_secret
            st.session_state["setup_username"] = username
            st.rerun()

    if st.session_state.get("setup_done"):
        username = st.session_state.get("setup_username", "admin")
        st.success("Account erstellt!")
        if is_2fa_enabled():
            st.subheader("2FA einrichten")
            try:
                import io
                import qrcode

                uri = get_totp_uri(username)
                img = qrcode.make(uri)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                st.image(buf.getvalue(), width=250)
            except ImportError:
                st.code(get_totp_uri(username), language=None)
            st.info("Nach dem Einrichten der App kannst du dich einloggen.")
        else:
            st.info("2FA ist deaktiviert. Direkt einloggen.")
        if st.button("Zum Login"):
            for k in ("setup_done", "totp_secret_new", "setup_username"):
                st.session_state.pop(k, None)
            st.rerun()
    st.stop()


def _render_login_page() -> None:
    _hide_sidebar_nav()
    st.title("🔐 SlitProjektHub – Login")
    ip = _get_client_ip()

    if st.session_state.get("_auth_pre_token"):
        st.subheader("Zwei-Faktor-Authentifizierung")
        with st.form("totp_form"):
            code = st.text_input("Authenticator-Code (6 Stellen)", max_chars=6)
            submitted = st.form_submit_button("Bestätigen")
        if submitted:
            result = authenticate_totp(st.session_state["_auth_pre_token"], code)
            if result["ok"]:
                st.session_state["_auth_token"] = result["token"]
                st.session_state.pop("_auth_pre_token", None)
                st.rerun()
            else:
                st.error(result["error"])
        if st.button("↩ Zurück zum Login"):
            st.session_state.pop("_auth_pre_token", None)
            st.rerun()
        st.stop()

    with st.form("login_form"):
        username = st.text_input("Benutzername")
        password = st.text_input("Passwort", type="password")
        submitted = st.form_submit_button("Einloggen")

    if submitted:
        if not username or not password:
            st.error("Bitte Benutzername und Passwort eingeben.")
        else:
            result = authenticate(ip, username, password)
            if result["ok"]:
                if result.get("needs_2fa"):
                    st.session_state["_auth_pre_token"] = result["pre_token"]
                    st.rerun()
                else:
                    st.session_state["_auth_token"] = result["token"]
                    st.rerun()
            else:
                st.error(result["error"])
    st.stop()


def require_auth() -> None:
    """Einstieg jeder Page. Bei st.navigation läuft das zusätzlich im Entrypoint."""
    init_db()
    s = get_settings()
    if not s.auth_enabled:
        return

    if is_setup_required():
        _render_setup_page()
        return

    token = st.session_state.get("_auth_token", "")
    timeout = s.auth_session_timeout_minutes * 60

    if not validate_session_token(token, max_age_seconds=timeout):
        st.session_state.pop("_auth_token", None)
        _render_login_page()
        return

    with st.sidebar:
        who = session_username(token) or get_admin_username()
        rec = next((u for u in list_login_usernames() if u["username"].casefold() == who.casefold()), None)
        role = (rec or {}).get("app_role_key") or "unassigned"
        st.caption(f"Angemeldet: {who}")
        st.caption(f"App-Rolle: {role}")
        if not can_evaluate(who):
            st.caption("Phase C (Bewertung) erst nach App-Rolle Super-User / PL intern / PO.")
        if st.button("Abmelden"):
            logout()


def logout() -> None:
    token = st.session_state.get("_auth_token", "")
    who = session_username(token)
    if who:
        revoke_sessions(who)
    st.session_state.pop("_auth_token", None)
    st.session_state.pop("_auth_pre_token", None)
    st.rerun()
