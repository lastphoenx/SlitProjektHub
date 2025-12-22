from __future__ import annotations
from pathlib import Path
import re
import pandas as pd
from datetime import datetime
from sqlmodel import select
from .m01_config import get_settings
from .m02_paths import role_dir
from .m03_db import Role, get_session

S = get_settings()

COMMON_FUNCTIONS = [
    "CEO","CFO","CIO","CTO","COO","CPO","CISO","DPO",
    "Head of Research","Head of Data","Data Steward","Product Owner",
    "Projektleiter:in","Architekt:in","QA/Reviewer","Tester:in"
]

def _slugify(raw: str) -> str:
    k = (raw or "").strip().lower()
    k = re.sub(r"[^a-z0-9_-]+", "-", k)
    return re.sub(r"-+", "-", k).strip("-") or "role"

def _role_md_path(key: str) -> Path:
    return role_dir() / f"{key}.md"

def upsert_role(*, title: str, body_text: str, key: str | None = None,
                short_code: str | None = None, description: str | None = None,
                responsibilities: str | None = None, qualifications: str | None = None, 
                expertise: str | None = None, attached_docs: str | None = None) -> tuple[Role, bool]:
    """
    Legt Rolle an/aktualisiert sie.
    - key optional; wenn None -> wird aus title erzeugt (slug)
    returns: (role, created_flag)
    """
    if not title or not title.strip():
        raise ValueError("Titel fehlt.")

    # Wenn ein key übergeben wurde, normalisieren wir ihn.
    # Wenn kein key übergeben wurde (neue Rolle / generieren aus Titel),
    # dann erzeugen wir eine eindeutige Slug-Basis und hängen bei Konflikten
    # einen numerischen Suffix an: role, role-1, role-2 ...
    if key:
        skey = _slugify(key)
    else:
        base = _slugify(title)
        skey = base
        # prüfe auf Konflikte und erhöhe Suffix bei Bedarf
        i = 1
        with get_session() as ses:
            while ses.exec(select(Role).where(Role.key == skey)).first() is not None:
                skey = f"{base}-{i}"
                i += 1
    p = _role_md_path(skey)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body_text or "", encoding="utf-8")

    with get_session() as ses:
        obj = ses.exec(select(Role).where(Role.key == skey)).first()
        created = False
        now = datetime.now()
        
        if obj:
            # Update existing role
            obj.title = title.strip()
            obj.short_code = (short_code or None)
            obj.description = (description or None)
            obj.responsibilities = (responsibilities or None)
            obj.qualifications = (qualifications or None)
            obj.expertise = (expertise or None)
            obj.markdown_content_path = str(p)
            obj.attached_docs = (attached_docs or None)
            obj.updated_at = now
            ses.add(obj)
        else:
            # Create new role
            obj = Role(
                key=skey,
                title=title.strip(),
                short_code=(short_code or None),
                description=(description or None),
                responsibilities=(responsibilities or None),
                qualifications=(qualifications or None),
                expertise=(expertise or None),
                markdown_content_path=str(p),
                attached_docs=(attached_docs or None),
                rag_indexed_at=None,
                rag_chunk_count=None,
                rag_status=None,
                is_deleted=False,
                created_at=now,
                updated_at=now
            )
            ses.add(obj)
            created = True
        ses.commit()
        ses.refresh(obj)
    
    try:
        from .m09_rag import index_role
        index_role(obj.key)
    except Exception:
        pass
    
    return obj, created

def list_roles_df(include_deleted: bool=False, include_body: bool=False) -> pd.DataFrame:
    with get_session() as ses:
        rows = ses.exec(select(Role)).all()
    data = []
    for r in rows:
        if not include_deleted and r.is_deleted:
            continue
        row = {
            "Key": r.key,
            "Rollenbezeichnung": r.title,
            "Rollenkürzel": r.short_code or "",
            "Beschreibung": (r.description or "")[:100] + ("..." if len(r.description or "") > 100 else ""),
            "Hauptverantwortlichkeiten": (r.responsibilities or "")[:50] + ("..." if len(r.responsibilities or "") > 50 else ""),
            "Qualifikationen": (r.qualifications or "")[:50] + ("..." if len(r.qualifications or "") > 50 else ""),
            "Expertise": (r.expertise or "")[:50] + ("..." if len(r.expertise or "") > 50 else ""),
            "Gelöscht?": r.is_deleted,
        }
        if include_body:
            try:
                body = Path(r.markdown_content_path).read_text(encoding="utf-8") if r.markdown_content_path and Path(r.markdown_content_path).exists() else ""
            except Exception:
                body = ""
            row["Inhalt"] = body
        data.append(row)
    if not data:
        cols = ["Key","Rollenbezeichnung","Rollenkürzel","Beschreibung","Hauptverantwortlichkeiten","Qualifikationen","Expertise","Gelöscht?"] + (["Inhalt"] if include_body else [])
        return pd.DataFrame(columns=cols)
    return (pd.DataFrame(data)
            .sort_values(["Rollenbezeichnung","Key"], na_position="last")
            .reset_index(drop=True))

def load_role(key: str) -> tuple[Role | None, str]:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Role).where(Role.key == skey)).first()
    if not obj:
        return None, ""
    body = Path(obj.markdown_content_path).read_text(encoding="utf-8") if obj.markdown_content_path and Path(obj.markdown_content_path).exists() else ""
    return obj, body

def soft_delete_role(key: str) -> bool:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Role).where(Role.key == skey)).first()
        if not obj:
            return False
        obj.is_deleted = True
        ses.add(obj)
        ses.commit()
    return True

def function_suggestions() -> list[str]:
    with get_session() as ses:
        rows = ses.exec(select(Role.short_code).where(Role.short_code.is_not(None))).all()
    existing = sorted({(r[0] if isinstance(r, tuple) else r) for r in rows if (r and (r[0] if isinstance(r, tuple) else r))})
    seen, out = set(), []
    for v in COMMON_FUNCTIONS + existing:
        if v and v not in seen:
            out.append(v); seen.add(v)
    return out
