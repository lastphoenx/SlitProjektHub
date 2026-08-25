# Rollen-Patch (pfad-unabhängig) – in den Ordner der ps1-Datei schreiben
$ErrorActionPreference = "Stop"
$BASE = Split-Path -Parent $MyInvocation.MyCommand.Path  # == $PSScriptRoot
Set-Location $BASE

# Datei ersetzen: src\m03_db.py
@'
from __future__ import annotations
from typing import Optional
from sqlmodel import SQLModel, Field, create_engine, Session
from sqlalchemy import text
from .m01_config import get_settings

S = get_settings()
engine = create_engine(S.db_url, echo=False)

class Role(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str
    title: str
    group_name: Optional[str] = None   # z.B. CIO, CFO, CISO …
    text_path: str
    rag_path: str
    is_deleted: bool = False           # Soft-Delete

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str
    title: str
    text_path: str
    rag_path: str

class Context(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str
    title: str
    text_path: str
    rag_path: str

class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str
    title: str
    description: str
    type: str | None = None
    role_key: Optional[str] = None
    task_key: Optional[str] = None
    context_key: Optional[str] = None
    text_path: Optional[str] = None
    rag_path: Optional[str] = None

def _column_exists(table: str, col: str) -> bool:
    with engine.connect() as conn:
        res = conn.execute(text(f"PRAGMA table_info({table});")).mappings().all()
    return any(r["name"] == col for r in res)

def migrate_db() -> None:
    with engine.begin() as conn:
        if not _column_exists("Role", "group_name"):
            conn.execute(text("ALTER TABLE Role ADD COLUMN group_name VARCHAR;"))
        if not _column_exists("Role", "is_deleted"):
            conn.execute(text("ALTER TABLE Role ADD COLUMN is_deleted BOOLEAN DEFAULT 0;"))

def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    migrate_db()

def get_session() -> Session:
    return Session(engine)
'@ | Set-Content "$BASE\src\m03_db.py" -Encoding UTF8

# Datei anlegen: src\m07_roles.py
@'
from __future__ import annotations
from pathlib import Path
import re
import pandas as pd
from sqlmodel import select
from .m01_config import get_settings
from .m02_paths import role_dir
from .m03_db import Role, get_session

S = get_settings()

def sanitize_key(raw: str) -> str:
    k = raw.strip().lower()
    k = re.sub(r"[^a-z0-9_-]+", "-", k)
    return re.sub(r"-+", "-", k).strip("-") or "role"

def role_md_path(key: str) -> Path:
    return role_dir() / f"{key}.md"

def upsert_role(*, key: str, title: str, group_name: str | None, body_text: str) -> Role:
    key = sanitize_key(key)
    p = role_md_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body_text or "", encoding="utf-8")

    with get_session() as ses:
        obj = ses.exec(select(Role).where(Role.key == key)).first()
        if obj:
            obj.title = title
            obj.group_name = group_name
            obj.text_path = str(p)
            ses.add(obj)
        else:
            obj = Role(
                key=key, title=title, group_name=group_name,
                text_path=str(p), rag_path="", is_deleted=False
            )
            ses.add(obj)
        ses.commit()
        ses.refresh(obj)
        return obj

def list_roles_df(include_deleted: bool=False):
    with get_session() as ses:
        rows = ses.exec(select(Role)).all()
    data = []
    for r in rows:
        if not include_deleted and r.is_deleted:
            continue
        data.append({
            "Key": r.key,
            "Titel": r.title,
            "Funktion": r.group_name or "",
            "Textdatei": r.text_path,
            "Gelöscht?": r.is_deleted,
        })
    import pandas as pd
    if not data:
        return pd.DataFrame(columns=["Key","Titel","Funktion","Textdatei","Gelöscht?"])
    return (pd.DataFrame(data)
            .sort_values(["Funktion","Titel","Key"], na_position="last")
            .reset_index(drop=True))

def load_role(key: str):
    key = sanitize_key(key)
    with get_session() as ses:
        obj = ses.exec(select(Role).where(Role.key == key)).first()
    if not obj:
        return None, ""
    body = Path(obj.text_path).read_text(encoding="utf-8") if obj.text_path and Path(obj.text_path).exists() else ""
    return obj, body

def soft_delete_role(key: str) -> bool:
    key = sanitize_key(key)
    with get_session() as ses:
        obj = ses.exec(select(Role).where(Role.key == key)).first()
        if not obj:
            return False
        obj.is_deleted = True
        ses.add(obj)
        ses.commit()
    return True
'@ | Set-Content "$BASE\src\m07_roles.py" -Encoding UTF8

# Seite anlegen: app\pages\03_Roles.py
@'
import streamlit as st
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m06_ui import page_header, search_box, filter_dataframe, table, form_scaffold
from src.m07_roles import upsert_role, list_roles_df, load_role, soft_delete_role

page_header("Rollen / Funktionen", "Einheitliche Anlage inkl. Dateiablage & Soft-Delete.")

left, right = st.columns([1, 2])

with left:
    st.subheader("Neue Rolle / Bearbeiten")
    default_key = st.session_state.get("role_edit_key", "")
    obj, body = load_role(default_key) if default_key else (None, "")
    fields = {
        "key":        {"label": "Key", "value": (obj.key if obj else ""), "placeholder": "z.B. cio, cfo-finance"},
        "title":      {"label": "Titel", "value": (obj.title if obj else ""), "placeholder": "z.B. Chief Information Officer"},
        "group_name": {"label": "Funktion", "value": (obj.group_name if obj else ""), "placeholder": "CIO / CFO / CEO / CISO / DPO …"},
        "body_text":  {"label": "Beschreibung / Profil (Markdown)", "value": body, "type": "area", "placeholder": "Rollenbeschreibung, Verantwortlichkeiten …"},
    }
    submitted, values = form_scaffold("role_form", fields)
    if submitted:
        if not values["key"] or not values["title"]:
            st.error("Bitte mindestens **Key** und **Titel** angeben.")
        else:
            r = upsert_role(
                key=values["key"],
                title=values["title"],
                group_name=values["group_name"] or None,
                body_text=values["body_text"] or "",
            )
            st.session_state["role_edit_key"] = r.key
            st.success(f"Gespeichert: {r.key}")
            st.rerun()

with right:
    st.subheader("Bestehende Rollen")
    df = list_roles_df(include_deleted=False)
    q = search_box("Suchen (Key/Titel/Funktion)", key="role_search")
    fdf = filter_dataframe(df, q)
    table(fdf, height=420, key="role_table")

    st.markdown("**Bearbeiten/Löschen**")
    keys = [""] + (fdf["Key"].tolist() if not fdf.empty else [])
    sel = st.selectbox("Rolle wählen", options=keys, index=0, key="role_select")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Bearbeiten", disabled=(sel == "")):
            st.session_state["role_edit_key"] = sel
            st.toast(f"Bearbeite: {sel}")
            st.rerun()
    with col2:
        if st.button("Löschen (soft)", type="secondary", disabled=(sel == "")):
            if sel and soft_delete_role(sel):
                st.toast(f"Als gelöscht markiert: {sel}")
                if st.session_state.get("role_edit_key") == sel:
                    st.session_state["role_edit_key"] = ""
                st.rerun()
'@ | Set-Content "$BASE\app\pages\03_Roles.py" -Encoding UTF8

Write-Host "✅ Rollen-Patch abgeschlossen. Starte die App neu:"
Write-Host "   .\.venv\Scripts\Activate.ps1"
Write-Host "   streamlit run app/streamlit_app.py"
