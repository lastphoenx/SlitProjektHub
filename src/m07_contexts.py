from __future__ import annotations
from pathlib import Path
import re
import pandas as pd
from sqlmodel import select
from .m01_config import get_settings
from .m02_paths import context_dir
from .m03_db import Context, get_session

S = get_settings()

MAX_KEY_LEN = 80

def _slugify(raw: str) -> str:
    k = (raw or "").strip().lower()
    k = re.sub(r"[^a-z0-9_-]+", "-", k)
    k = re.sub(r"-+", "-", k).strip("-")
    if len(k) > MAX_KEY_LEN:
        k = k[:MAX_KEY_LEN].rstrip("-")
    return k or "context"

def _ctx_md_path(key: str) -> Path:
    return context_dir() / f"{key}.md"

def upsert_context(*, title: str, body_text: str, key: str | None = None, short_title: str | None = None,
                   short_code: str | None = None, description: str | None = None) -> tuple[Context, bool]:
    if not title or not title.strip():
        raise ValueError("Titel fehlt.")
    if key:
        skey = _slugify(key)
    else:
        base = _slugify(title)
        skey = base
        i = 1
        with get_session() as ses:
            while ses.exec(select(Context).where(Context.key == skey)).first() is not None:
                skey = f"{base}-{i}"
                i += 1
    p = _ctx_md_path(skey)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body_text or "", encoding="utf-8")

    with get_session() as ses:
        obj = ses.exec(select(Context).where(Context.key == skey)).first()
        created = False
        if obj:
            obj.title = title.strip()
            obj.short_title = (short_title or "").strip() if short_title is not None else obj.short_title
            obj.short_code = (short_code or "").strip() if short_code is not None else obj.short_code
            obj.description = (description or "").strip() if description is not None else obj.description
            obj.text_path = str(p)
            ses.add(obj)
        else:
            obj = Context(
                key=skey,
                title=title.strip(),
                short_title=(short_title or "").strip() if short_title is not None else None,
                short_code=(short_code or "").strip() if short_code is not None else None,
                description=(description or "").strip() if description is not None else None,
                text_path=str(p),
                rag_path="",
            )
            ses.add(obj)
            created = True
        ses.commit()
        ses.refresh(obj)
    
    try:
        from .m09_rag import index_context
        index_context(obj.key)
    except Exception:
        pass
    
    return obj, created

def list_contexts_df(include_body: bool=False) -> pd.DataFrame:
    with get_session() as ses:
        rows = ses.exec(select(Context)).all()
    data = []
    for r in rows:
        row = {
            "Key": r.key,
            "Titel": r.title,
            "KurzTitel": getattr(r, "short_title", None) or "",
            "Kürzel": getattr(r, "short_code", None) or "",
            "Beschreibung": (getattr(r, "description", None) or "")[:100] + ("..." if len(getattr(r, "description", None) or "") > 100 else ""),
        }
        if include_body:
            try:
                body = Path(r.text_path).read_text(encoding="utf-8") if r.text_path and Path(r.text_path).exists() else ""
            except Exception:
                body = ""
            row["Inhalt"] = body
        data.append(row)
    if not data:
        cols = ["Key","Titel","KurzTitel","Kürzel","Beschreibung"] + (['Inhalt'] if include_body else [])
        return pd.DataFrame(columns=cols)
    return (pd.DataFrame(data).sort_values(["Titel","Key"], na_position="last").reset_index(drop=True))

def load_context(key: str) -> tuple[Context | None, str]:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Context).where(Context.key == skey)).first()
    if not obj:
        return None, ""
    body = Path(obj.text_path).read_text(encoding="utf-8") if obj.text_path and Path(obj.text_path).exists() else ""
    return obj, body

def delete_context(key: str) -> bool:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Context).where(Context.key == skey)).first()
        if not obj:
            return False
        try:
            if obj.text_path and Path(obj.text_path).exists():
                Path(obj.text_path).unlink(missing_ok=True)
        except Exception:
            pass
        ses.delete(obj)
        ses.commit()
    return True

def soft_delete_context(key: str) -> bool:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Context).where(Context.key == skey)).first()
        if not obj:
            return False
        obj.is_deleted = True
        ses.add(obj)
        ses.commit()
    return True
