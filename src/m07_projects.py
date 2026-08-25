from __future__ import annotations
from pathlib import Path
import re
import json
import pandas as pd
from sqlmodel import select
from .m01_config import get_settings
from .m02_paths import project_dir
from .m03_db import Project, Task, Role, Context, get_session

S = get_settings()

def _slugify(raw: str) -> str:
    k = (raw or "").strip().lower()
    k = re.sub(r"[^a-z0-9_-]+", "-", k)
    return re.sub(r"-+", "-", k).strip("-") or "project"

def _proj_md_path(key: str) -> Path:
    return project_dir() / f"{key}.md"


def _get_task_keys_for_roles(role_keys: list[str]) -> list[str]:
    if not role_keys:
        return []
    
    task_keys = []
    with get_session() as ses:
        for role_key in role_keys:
            tasks = ses.exec(
                select(Task)
                .where(Task.source_role_key == role_key)
                .where(Task.is_deleted == False)
            ).all()
            task_keys.extend([t.key for t in tasks])
    
    return list(set(task_keys))


def _index_project_chunks_to_chromadb(project_key: str, project_id: int) -> None:
    """
    Indexiert alle Projekt-Daten als separate Chunks zu ChromaDB:
    - Projekt-Beschreibung
    - Rollen (Namen + Beschreibungen)
    - Tasks der Rollen (Titel + Beschreibung)
    - Kontexte (Namen + Beschreibungen)
    """
    try:
        from .m07_chroma import get_or_create_project_collection, add_chunks_to_collection
        from .m09_rag import embed_texts_batch

        with get_session() as ses:
            proj = ses.exec(select(Project).where(Project.key == project_key)).first()
            if not proj:
                return

            collection = get_or_create_project_collection(project_id, "text-embedding-3-small")

            raw_texts = []
            chunk_ids = []
            project_index = 0

            # Projektbeschreibung
            proj_text = f"{proj.title}\n{proj.description}"
            if proj.text_path and Path(proj.text_path).exists():
                try:
                    proj_text += f"\n{Path(proj.text_path).read_text(encoding='utf-8')}"
                except:
                    pass
            raw_texts.append(proj_text[:1000])
            chunk_ids.append(f"project_{project_id}_chunk_{project_index}")
            project_index += 1

            # Rollen + Tasks
            if proj.role_keys:
                try:
                    role_keys = json.loads(proj.role_keys) if isinstance(proj.role_keys, str) else proj.role_keys
                    if isinstance(role_keys, list):
                        for role_key in role_keys:
                            role = ses.exec(select(Role).where(Role.key == role_key)).first()
                            if not role:
                                continue
                            role_text = f"Rolle: {role.title}"
                            if role.description:
                                role_text += f"\n{role.description}"
                            if role.responsibilities:
                                role_text += f"\n{role.responsibilities}"
                            raw_texts.append(role_text[:1000])
                            chunk_ids.append(f"project_{project_id}_chunk_{project_index}")
                            project_index += 1

                            role_tasks = ses.exec(
                                select(Task)
                                .where(Task.source_role_key == role_key)
                                .where(Task.is_deleted == False)
                            ).all()
                            for task in role_tasks:
                                task_text = f"Task von Rolle {role.title}: {task.title}"
                                if task.description:
                                    task_text += f"\n{task.description}"
                                if task.text_path and Path(task.text_path).exists():
                                    try:
                                        task_text += f"\n{Path(task.text_path).read_text(encoding='utf-8')[:500]}"
                                    except:
                                        pass
                                raw_texts.append(task_text[:1000])
                                chunk_ids.append(f"project_{project_id}_chunk_{project_index}")
                                project_index += 1
                except Exception:
                    pass

            # Kontexte
            if proj.context_keys:
                try:
                    context_keys = json.loads(proj.context_keys) if isinstance(proj.context_keys, str) else proj.context_keys
                    if isinstance(context_keys, list):
                        for context_key in context_keys:
                            context = ses.exec(select(Context).where(Context.key == context_key)).first()
                            if not context:
                                continue
                            context_text = f"Kontext: {context.title}"
                            if context.description:
                                context_text += f"\n{context.description}"
                            if context.text_path and Path(context.text_path).exists():
                                try:
                                    context_text += f"\n{Path(context.text_path).read_text(encoding='utf-8')}"
                                except:
                                    pass
                            raw_texts.append(context_text[:1000])
                            chunk_ids.append(f"project_{project_id}_chunk_{project_index}")
                            project_index += 1
                except Exception:
                    pass

            if raw_texts:
                # Alle Texte in einem einzigen API-Call embedden
                embeddings_to_add = embed_texts_batch(raw_texts)
                collection.add(
                    ids=chunk_ids,
                    documents=raw_texts,
                    embeddings=embeddings_to_add,
                    metadatas=[{"source": "project_data", "project_id": str(project_id)} for _ in raw_texts]
                )
    except Exception as e:
        pass


def upsert_project(*, title: str, type_name: str | None, body_text: str, key: str | None = None,
                   short_title: str | None = None, short_code: str | None = None,
                   description: str | None = None,
                   role_keys: list[str] | None = None,
                   context_keys: list[str] | None = None) -> tuple[Project, bool]:
    if not title or not title.strip():
        raise ValueError("Titel fehlt.")
    if key:
        skey = _slugify(key)
    else:
        base = _slugify(title)
        skey = base
        i = 1
        with get_session() as ses:
            while ses.exec(select(Project).where(Project.key == skey)).first() is not None:
                skey = f"{base}-{i}"
                i += 1
    p = _proj_md_path(skey)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body_text or "", encoding="utf-8")

    with get_session() as ses:
        obj = ses.exec(select(Project).where(Project.key == skey)).first()
        created = False
        if obj:
            obj.title = title.strip()
            obj.short_title = (short_title or "").strip() if short_title is not None else obj.short_title
            obj.short_code = (short_code or "").strip() if short_code is not None else obj.short_code
            obj.description = (description or "").strip() if description is not None else obj.description
            obj.type = type_name or None
            obj.text_path = str(p)
            if role_keys is not None:
                obj.role_keys = json.dumps(role_keys)
            if context_keys is not None:
                obj.context_keys = json.dumps(context_keys)
            ses.add(obj)
        else:
            obj = Project(
                key=skey,
                title=title.strip(),
                short_title=(short_title or "").strip() if short_title is not None else None,
                short_code=(short_code or "").strip() if short_code is not None else None,
                description=(description or "").strip() if description is not None else "",
                type=(type_name or None),
                text_path=str(p),
                rag_path=None,
                role_key=None,
                task_key=None,
                context_key=None,
                role_keys=json.dumps(role_keys) if role_keys is not None else None,
                context_keys=json.dumps(context_keys) if context_keys is not None else None,
            )
            ses.add(obj)
            created = True
        
        propagated_task_keys = []
        if role_keys:
            propagated_task_keys = _get_task_keys_for_roles(role_keys)
        
        if propagated_task_keys:
            existing_tasks = []
            if obj.task_keys:
                try:
                    existing_tasks = json.loads(obj.task_keys)
                    if not isinstance(existing_tasks, list):
                        existing_tasks = []
                except:
                    existing_tasks = []
            
            merged_tasks = list(set(existing_tasks + propagated_task_keys))
            obj.task_keys = json.dumps(merged_tasks)
        
        ses.commit()
        ses.refresh(obj)
    
    try:
        from .m09_rag import index_project
        index_project(obj.key)
    except Exception:
        pass
    
    _index_project_chunks_to_chromadb(obj.key, obj.id)
    
    return obj, created

def list_projects_df(include_body: bool=False, include_deleted: bool=False) -> pd.DataFrame:
    with get_session() as ses:
        rows = ses.exec(select(Project)).all()
    data = []
    for r in rows:
        if not include_deleted and r.is_deleted:
            continue
        row = {
            "Key": r.key,
            "Titel": r.title,
            "KurzTitel": getattr(r, "short_title", None) or "",
            "Kürzel": getattr(r, "short_code", None) or "",
            "Typ": r.type or "",
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
        cols = ["Key","Titel","KurzTitel","Kürzel","Typ","Beschreibung"] + (['Inhalt'] if include_body else [])
        return pd.DataFrame(columns=cols)
    return (pd.DataFrame(data).sort_values(["Typ","Titel","Key"], na_position="last").reset_index(drop=True))

def load_project(key: str) -> tuple[Project | None, str]:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Project).where(Project.key == skey)).first()
    if not obj:
        return None, ""
    body = Path(obj.text_path).read_text(encoding="utf-8") if obj.text_path and Path(obj.text_path).exists() else ""
    return obj, body

def delete_project(key: str) -> bool:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Project).where(Project.key == skey)).first()
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

def soft_delete_project(key: str) -> bool:
    skey = _slugify(key)
    with get_session() as ses:
        obj = ses.exec(select(Project).where(Project.key == skey)).first()
        if not obj:
            return False
        obj.is_deleted = True
        ses.add(obj)
        ses.commit()
    return True

def get_project_structure(key: str) -> str:
    """Liefert eine Markdown-Liste der verknüpften Rollen, Kontexte und Aufgaben."""
    skey = _slugify(key)
    parts = []
    with get_session() as ses:
        proj = ses.exec(select(Project).where(Project.key == skey)).first()
        if not proj:
            return ""
        
        # Roles
        if proj.role_keys:
            try:
                rkeys = json.loads(proj.role_keys) if isinstance(proj.role_keys, str) else proj.role_keys
                if rkeys and isinstance(rkeys, list):
                    roles = ses.exec(select(Role).where(Role.key.in_(rkeys))).all()
                    if roles:
                        parts.append("### 👥 Projekt-Rollen (Übersicht):")
                        for r in roles:
                            parts.append(f"- {r.title}")
            except Exception: pass

        # Contexts
        if proj.context_keys:
            try:
                ckeys = json.loads(proj.context_keys) if isinstance(proj.context_keys, str) else proj.context_keys
                if ckeys and isinstance(ckeys, list):
                    ctxs = ses.exec(select(Context).where(Context.key.in_(ckeys))).all()
                    if ctxs:
                        parts.append("\n### 🎓 Projekt-Kontexte (Übersicht):")
                        for c in ctxs:
                            parts.append(f"- {c.title}")
            except Exception: pass
            
        # Tasks
        if proj.task_keys:
             try:
                tkeys = json.loads(proj.task_keys) if isinstance(proj.task_keys, str) else proj.task_keys
                if tkeys and isinstance(tkeys, list):
                    tasks = ses.exec(select(Task).where(Task.key.in_(tkeys))).all()
                    if tasks:
                        parts.append("\n### ✅ Projekt-Aufgaben (Übersicht):")
                        for t in tasks:
                            parts.append(f"- {t.title}")
             except Exception: pass
             
    return "\n".join(parts)


def get_project_roles(key: str) -> list[Role]:
    """
    Holt alle Rollen die einem Projekt zugeordnet sind.
    Returns: Liste von Role-Objekten (leer falls keine Rollen oder Projekt nicht gefunden)
    """
    skey = _slugify(key)
    with get_session() as ses:
        proj = ses.exec(select(Project).where(Project.key == skey)).first()
        if not proj or not proj.role_keys:
            return []
        
        try:
            role_keys = json.loads(proj.role_keys) if isinstance(proj.role_keys, str) else proj.role_keys
            if not role_keys or not isinstance(role_keys, list):
                return []
            
            roles = ses.exec(select(Role).where(Role.key.in_(role_keys))).all()
            return list(roles)
        except:
            return []
