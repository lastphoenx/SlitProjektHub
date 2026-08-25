from __future__ import annotations
from pathlib import Path
import re
import pandas as pd
from sqlmodel import select
from .m01_config import get_settings
from .m02_paths import task_dir
from .m03_db import Task, get_session

# Versuche Rollen-Suggestions wiederzuverwenden
try:  # noqa: SIM105
    from .m07_roles import function_suggestions as _role_function_suggestions  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _role_function_suggestions = None  # type: ignore

S = get_settings()

MAX_KEY_LEN = 80  # begrenze Dateinamen-Länge (Windows MAX_PATH)

def _slugify(raw: str) -> str:
    k = (raw or "").strip().lower()
    k = re.sub(r"[^a-z0-9_-]+", "-", k)
    k = re.sub(r"-+", "-", k).strip("-")
    if len(k) > MAX_KEY_LEN:
        k = k[:MAX_KEY_LEN].rstrip("-")
    return k or "task"

def _task_md_path(key: str) -> Path:
    return task_dir() / f"{key}.md"

def upsert_task(*, title: str, body_text: str, key: str | None = None, short_title: str | None = None, 
                short_code: str | None = None, group_name: str | None = None, description: str | None = None,
                source_role_key: str | None = None, source_responsibility: str | None = None, 
                generation_batch_id: str | None = None, generated_at = None) -> tuple[Task, bool]:
    """
    Legt Aufgabe an/aktualisiert sie.
    - title: Titel (für die DB); in der "Native"-Page wird dieser meist als Kurz‑Titel verwendet
    - body_text: Markdown-Inhalt
    - key: optional fixer Schlüssel; wenn None -> aus Titel generiert, bei Konflikt mit Suffix
    - short_title/short_code: optionale Kurzfelder (werden auch als Spalten bereitgestellt)
    - group_name: Alias für short_code (Kompatibilität mit älteren Pages)
    - description: Kurzbeschreibung
    - source_role_key: Key der Quell-Rolle (bei Generierung)
    - source_responsibility: Verantwortlichkeits-Text (bei Generierung)
    - generation_batch_id: UUID für Batch-Operations
    - generated_at: Zeitstempel der Generierung
    returns: (Task, created_flag)
    """
    if not title or not title.strip():
        raise ValueError("Titel fehlt.")
    # Kompatibilität: group_name als Alias für short_code akzeptieren
    if (group_name is not None) and (short_code is None):
        short_code = group_name
    if key:
        skey = _slugify(key)
    else:
        base = _slugify(title)
        skey = base
        i = 1
        with get_session() as ses:
            while ses.exec(select(Task).where(Task.key == skey)).first() is not None:
                skey = f"{base}-{i}"
                i += 1
    p = _task_md_path(skey)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body_text or "", encoding="utf-8")

    with get_session() as ses:
        obj = ses.exec(select(Task).where(Task.key == skey)).first()
        created = False
        if obj:
            obj.title = title.strip()
            if short_title is not None:
                obj.short_title = (short_title or "").strip()
            if short_code is not None:
                obj.short_code = (short_code or "").strip()
            if description is not None:
                obj.description = (description or "").strip()
            obj.text_path = str(p)
            # Update generation metadata if provided
            if source_role_key is not None:
                obj.source_role_key = source_role_key
            if source_responsibility is not None:
                obj.source_responsibility = source_responsibility
            if generation_batch_id is not None:
                obj.generation_batch_id = generation_batch_id
            if generated_at is not None:
                obj.generated_at = generated_at
            ses.add(obj)
        else:
            obj = Task(
                key=skey,
                title=title.strip(),
                short_title=(short_title or "").strip() if short_title is not None else None,
                short_code=(short_code or "").strip() if short_code is not None else None,
                description=(description or "").strip() if description is not None else None,
                text_path=str(p),
                rag_path="",
                source_role_key=source_role_key,
                source_responsibility=source_responsibility,
                generation_batch_id=generation_batch_id,
                generated_at=generated_at
            )
            ses.add(obj)
            created = True
        ses.commit()
        ses.refresh(obj)
    
    try:
        from .m09_rag import index_task
        index_task(obj.key)
    except Exception:
        pass
    
    return obj, created

def list_tasks_df(include_deleted: bool=False, include_body: bool=False, include_metadata: bool=True) -> pd.DataFrame:  # include_deleted ignoriert (kein Flag in Task)
    with get_session() as ses:
        rows = ses.exec(select(Task)).all()
    data = []
    for r in rows:
        row = {
            "Key": r.key,
            "Titel": r.title,
            # Für NativePro-Page: "Funktion" als Kürzel/Kategorie liefern (Mapping auf short_code)
            "Funktion": getattr(r, "short_code", None) or "",
        }
        # optional mit in DataFrame aufnehmen
        try:
            row["KurzTitel"] = getattr(r, "short_title", None)
            row["Kürzel"] = getattr(r, "short_code", None)
            row["Beschreibung"] = getattr(r, "description", None)
        except Exception:
            pass
        
        # Generation metadata
        if include_metadata:
            try:
                row["Quell-Rolle"] = getattr(r, "source_role_key", None)
                row["Verantwortlichkeit"] = getattr(r, "source_responsibility", None)
                row["Batch-ID"] = getattr(r, "generation_batch_id", None)
                row["Generiert am"] = getattr(r, "generated_at", None)
            except Exception:
                pass
        
        if include_body:
            try:
                body = Path(r.text_path).read_text(encoding="utf-8") if r.text_path and Path(r.text_path).exists() else ""
            except Exception:
                body = ""
            row["Inhalt"] = body
        data.append(row)
    if not data:
        cols = ["Key","Titel","Funktion"] + (["Inhalt"] if include_body else [])
        if include_metadata:
            cols += ["Quell-Rolle", "Verantwortlichkeit", "Batch-ID", "Generiert am"]
        return pd.DataFrame(columns=cols)
    return (pd.DataFrame(data)
            .sort_values(["Titel","Key"], na_position="last")
            .reset_index(drop=True))

def load_task(key: str) -> tuple[Task | None, str]:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Task).where(Task.key == skey)).first()
    if not obj:
        return None, ""
    body = Path(obj.text_path).read_text(encoding="utf-8") if obj.text_path and Path(obj.text_path).exists() else ""
    return obj, body

def delete_task(key: str) -> bool:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Task).where(Task.key == skey)).first()
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

# Soft-Delete-Stub: aktuell kein is_deleted-Flag auf Task – wir löschen hart
def soft_delete_task(key: str) -> bool:
    """Soft-Delete-Wrapper für Kompatibilität – führt derzeit ein hartes Löschen aus."""
    return delete_task(key)

def function_suggestions() -> list[str]:
    """Liefert Vorschläge für Task-Kategorien/Kürzel.
    Bevorzugt Re-Export aus Rollen, fällt bei Fehlern auf eine fixe Liste zurück.
    """
    try:
        if _role_function_suggestions:
            return _role_function_suggestions()
    except Exception:
        pass
    return [
        "Bug","Feature","Research","Review","ETL","Audit","Migration","Meeting Notes"
    ]
