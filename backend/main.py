# backend/main.py - FastAPI Backend für SlitProjektHub
from __future__ import annotations
import json
import sys
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # DB-Pfad Referenz

# ── Src imports ────────────────────────────────────────────────────────────
from src.m07_roles import list_roles_df, load_role, upsert_role, soft_delete_role, function_suggestions
from src.m07_tasks import list_tasks_df, load_task, upsert_task, soft_delete_task
from src.m07_projects import list_projects_df, load_project, upsert_project, soft_delete_project
from src.m07_contexts import list_contexts_df
from src.m03_db import get_session, Project, Role, Task, Context
from src.m08_llm import providers_available, generate_role_text, generate_summary
from sqlmodel import select

# ── FastAPI app ────────────────────────────────────────────────────────────
app = FastAPI(
    title="SlitProjektHub API",
    description="Backend API für das Projektmanagement-Tool",
    version="2.0.0",
)

# ── Static files & Templates ───────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=BACKEND_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BACKEND_DIR / "templates")

# ── CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://localhost:8502", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Project types (shared constant) ───────────────────────────────────────
PROJECT_TYPES = ["Intern", "Extern", "Studie", "Prototyp", "Betrieb", "Sonstiges"]

# ── Helper: build project list for templates ───────────────────────────────
def _projects_list() -> list[dict]:
    df = list_projects_df(include_deleted=False)
    if df is None or df.empty:
        return []
    return df.rename(columns={
        "Key": "key", "Titel": "title", "KurzTitel": "short_title",
        "Kürzel": "short_code", "Typ": "type", "Beschreibung": "description",
    }).to_dict("records")

# ── Helper: stats for dashboard ────────────────────────────────────────────
def _dashboard_stats() -> dict:
    stats = {"projects": 0, "roles": 0, "tasks": 0, "contexts": 0}
    try:
        with get_session() as ses:
            stats["projects"] = ses.exec(select(Project).where(Project.is_deleted == False)).all().__len__()
            stats["roles"] = ses.exec(select(Role).where(Role.is_deleted == False)).all().__len__()
            stats["tasks"] = ses.exec(select(Task).where(Task.is_deleted == False)).all().__len__()
            stats["contexts"] = ses.exec(select(Context).where(Context.is_deleted == False)).all().__len__()
    except Exception:
        pass
    return stats


# ════════════════════════════════════════════════════════════════════════════
# HTML VIEWS (Jinja2 + HTMX)
# ════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_page": "dashboard",
        "stats": _dashboard_stats(),
    })


# ── Projects HTML views ────────────────────────────────────────────────────

@app.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    return templates.TemplateResponse("projects/index.html", {
        "request": request,
        "active_page": "projects",
        "projects": _projects_list(),
    })


@app.get("/projects/new", response_class=HTMLResponse)
async def projects_new_form(request: Request):
    """HTMX partial: Neues-Projekt-Formular"""
    return templates.TemplateResponse("projects/_form.html", {
        "request": request,
        "project": None,
        "body_text": "",
        "project_types": PROJECT_TYPES,
    })


@app.get("/projects/{key}/edit", response_class=HTMLResponse)
async def projects_edit_form(request: Request, key: str):
    """HTMX partial: Bearbeiten-Formular für bestehendes Projekt"""
    proj, body = load_project(key)
    if not proj:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return templates.TemplateResponse("projects/_form.html", {
        "request": request,
        "project": proj,
        "body_text": body,
        "project_types": PROJECT_TYPES,
    })


@app.get("/projects/{key}/confirm-delete", response_class=HTMLResponse)
async def projects_confirm_delete(request: Request, key: str):
    """HTMX partial: Lösch-Bestätigung"""
    proj, _ = load_project(key)
    if not proj:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return templates.TemplateResponse("projects/_confirm_delete.html", {
        "request": request,
        "project": proj,
    })


@app.post("/projects", response_class=HTMLResponse)
async def projects_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    short_title: str = Form(""),
    short_code: str = Form(""),
    type: str = Form(""),
    body_text: str = Form(""),
):
    """HTMX: Projekt erstellen → gibt aktualisierten table-body zurück"""
    try:
        upsert_project(
            title=title.strip(),
            type_name=type.strip() or None,
            body_text=body_text,
            short_title=short_title.strip() or None,
            short_code=short_code.strip() or None,
            description=description.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    response = templates.TemplateResponse("projects/_table.html", {
        "request": request,
        "projects": _projects_list(),
    })
    response.headers["HX-Toast"] = json.dumps({"message": f'Projekt "{title}" erstellt', "type": "success"})
    return response


@app.put("/projects/{key}", response_class=HTMLResponse)
async def projects_update(
    request: Request,
    key: str,
    title: str = Form(...),
    description: str = Form(...),
    short_title: str = Form(""),
    short_code: str = Form(""),
    type: str = Form(""),
    body_text: str = Form(""),
):
    """HTMX: Projekt aktualisieren → gibt aktualisierten table-body zurück"""
    proj, _ = load_project(key)
    if not proj:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    try:
        upsert_project(
            key=key,
            title=title.strip(),
            type_name=type.strip() or None,
            body_text=body_text,
            short_title=short_title.strip() or None,
            short_code=short_code.strip() or None,
            description=description.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    response = templates.TemplateResponse("projects/_table.html", {
        "request": request,
        "projects": _projects_list(),
    })
    response.headers["HX-Toast"] = json.dumps({"message": f'Projekt "{title}" gespeichert', "type": "success"})
    return response


@app.delete("/projects/{key}", response_class=HTMLResponse)
async def projects_delete(key: str):
    """HTMX: Projekt soft-delete → gibt leeren String zurück (Row verschwindet)"""
    success = soft_delete_project(key)
    if not success:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    # Return empty string — HTMX swaps out the row with nothing
    response = HTMLResponse(content="")
    response.headers["HX-Toast"] = json.dumps({"message": "Projekt gelöscht", "type": "success"})
    return response


# ── Roles helper ───────────────────────────────────────────────────────────

def _roles_list() -> list[dict]:
    df = list_roles_df(include_deleted=False)
    if df is None or df.empty:
        return []
    return df.rename(columns={
        "Key": "key",
        "Rollenbezeichnung": "title",
        "Rollenkürzel": "short_code",
        "Beschreibung": "description",
        "Hauptverantwortlichkeiten": "responsibilities",
        "Qualifikationen": "qualifications",
        "Expertise": "expertise",
    }).to_dict("records")


def _role_full(key: str) -> dict | None:
    """Lädt eine Rolle mit allen Feldern (ungekürzt) für das Bearbeitungs-Formular."""
    role_obj, body = load_role(key)
    if not role_obj:
        return None
    return {
        "key": role_obj.key,
        "title": role_obj.title,
        "short_code": role_obj.short_code or "",
        "description": role_obj.description or "",
        "responsibilities": role_obj.responsibilities or "",
        "qualifications": role_obj.qualifications or "",
        "expertise": role_obj.expertise or "",
        "rag_status": role_obj.rag_status,
        "body_text": body,
    }


# ── Roles HTML views ────────────────────────────────────────────────────────

@app.get("/roles", response_class=HTMLResponse)
async def roles_page(request: Request):
    return templates.TemplateResponse("roles/index.html", {
        "request": request,
        "active_page": "roles",
        "roles": _roles_list(),
    })


@app.get("/roles/new", response_class=HTMLResponse)
async def roles_new_form(request: Request):
    """HTMX partial: Neue-Rolle-Formular"""
    return templates.TemplateResponse("roles/_form.html", {
        "request": request,
        "role": None,
        "body_text": "",
        "suggestions": function_suggestions(),
    })


@app.get("/roles/{key}/edit", response_class=HTMLResponse)
async def roles_edit_form(request: Request, key: str):
    """HTMX partial: Bearbeiten-Formular"""
    role = _role_full(key)
    if not role:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    return templates.TemplateResponse("roles/_form.html", {
        "request": request,
        "role": role,
        "body_text": role["body_text"],
        "suggestions": function_suggestions(),
    })


@app.get("/roles/{key}/confirm-delete", response_class=HTMLResponse)
async def roles_confirm_delete(request: Request, key: str):
    """HTMX partial: Lösch-Bestätigung"""
    role_obj, _ = load_role(key)
    if not role_obj:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    return templates.TemplateResponse("roles/_confirm_delete.html", {
        "request": request,
        "role": role_obj,
    })


@app.post("/roles", response_class=HTMLResponse)
async def roles_create(
    request: Request,
    title: str = Form(...),
    short_code: str = Form(""),
    description: str = Form(""),
    responsibilities: str = Form(""),
    qualifications: str = Form(""),
    expertise: str = Form(""),
    body_text: str = Form(""),
):
    """HTMX: Rolle erstellen → gibt aktualisierten table-body zurück"""
    try:
        upsert_role(
            title=title.strip(),
            body_text=body_text,
            short_code=short_code.strip() or None,
            description=description.strip() or None,
            responsibilities=responsibilities.strip() or None,
            qualifications=qualifications.strip() or None,
            expertise=expertise.strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    response = templates.TemplateResponse("roles/_table.html", {
        "request": request,
        "roles": _roles_list(),
    })
    response.headers["HX-Toast"] = json.dumps({"message": f'Rolle "{title}" erstellt', "type": "success"})
    return response


@app.put("/roles/{key}", response_class=HTMLResponse)
async def roles_update(
    request: Request,
    key: str,
    title: str = Form(...),
    short_code: str = Form(""),
    description: str = Form(""),
    responsibilities: str = Form(""),
    qualifications: str = Form(""),
    expertise: str = Form(""),
    body_text: str = Form(""),
):
    """HTMX: Rolle aktualisieren → gibt aktualisierten table-body zurück"""
    role_obj, _ = load_role(key)
    if not role_obj:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    try:
        upsert_role(
            key=key,
            title=title.strip(),
            body_text=body_text,
            short_code=short_code.strip() or None,
            description=description.strip() or None,
            responsibilities=responsibilities.strip() or None,
            qualifications=qualifications.strip() or None,
            expertise=expertise.strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    response = templates.TemplateResponse("roles/_table.html", {
        "request": request,
        "roles": _roles_list(),
    })
    response.headers["HX-Toast"] = json.dumps({"message": f'Rolle "{title}" gespeichert', "type": "success"})
    return response


@app.delete("/roles/{key}", response_class=HTMLResponse)
async def roles_delete(key: str):
    """HTMX: Rolle soft-delete → Row verschwindet"""
    if not soft_delete_role(key):
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    response = HTMLResponse(content="")
    response.headers["HX-Toast"] = json.dumps({"message": "Rolle gelöscht", "type": "success"})
    return response


# ── Tasks helper ─────────────────────────────────────────────────────────

def _tasks_list(role_filter: str | None = None) -> list[dict]:
    df = list_tasks_df(include_deleted=False, include_metadata=True)
    if df is None or df.empty:
        return []
    records = df.rename(columns={
        "Key": "key",
        "Titel": "title",
        "KurzTitel": "short_title",
        "Kürzel": "short_code",
        "Funktion": "function",
        "Beschreibung": "description",
        "Quell-Rolle": "source_role_key",
        "Verantwortlichkeit": "source_responsibility",
    }).to_dict("records")
    if role_filter:
        records = [r for r in records if r.get("source_role_key") == role_filter]
    return records


def _roles_for_select() -> list[dict]:
    """Alle nicht-gelöschten Rollen als {key, title} für Select-Dropdowns."""
    df = list_roles_df(include_deleted=False)
    if df is None or df.empty:
        return []
    return (
        df[["Key", "Rollenbezeichnung"]]
        .rename(columns={"Key": "key", "Rollenbezeichnung": "title"})
        .to_dict("records")
    )


# ── Tasks HTML views ──────────────────────────────────────────────────────

@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, role: str | None = None):
    return templates.TemplateResponse("tasks/index.html", {
        "request": request,
        "active_page": "tasks",
        "tasks": _tasks_list(role_filter=role),
        "roles": _roles_for_select(),
        "active_role_filter": role or "",
    })


@app.get("/tasks/new", response_class=HTMLResponse)
async def tasks_new_form(request: Request, role_key: str = ""):
    return templates.TemplateResponse("tasks/_form.html", {
        "request": request,
        "task": None,
        "body_text": "",
        "roles": _roles_for_select(),
        "preselect_role": role_key,
    })


@app.get("/tasks/{key}/edit", response_class=HTMLResponse)
async def tasks_edit_form(request: Request, key: str):
    task_obj, body = load_task(key)
    if not task_obj:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    task = {
        "key": task_obj.key,
        "title": task_obj.title,
        "short_title": task_obj.short_title or "",
        "short_code": task_obj.short_code or "",
        "description": task_obj.description or "",
        "source_role_key": task_obj.source_role_key or "",
        "source_responsibility": task_obj.source_responsibility or "",
    }
    return templates.TemplateResponse("tasks/_form.html", {
        "request": request,
        "task": task,
        "body_text": body,
        "roles": _roles_for_select(),
        "preselect_role": task_obj.source_role_key or "",
    })


@app.get("/tasks/{key}/confirm-delete", response_class=HTMLResponse)
async def tasks_confirm_delete(request: Request, key: str):
    task_obj, _ = load_task(key)
    if not task_obj:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    return templates.TemplateResponse("tasks/_confirm_delete.html", {
        "request": request,
        "task": task_obj,
    })


@app.post("/tasks", response_class=HTMLResponse)
async def tasks_create(
    request: Request,
    title: str = Form(...),
    short_title: str = Form(""),
    short_code: str = Form(""),
    description: str = Form(""),
    source_role_key: str = Form(""),
    body_text: str = Form(""),
):
    try:
        upsert_task(
            title=title.strip(),
            body_text=body_text,
            short_title=short_title.strip() or None,
            short_code=short_code.strip() or None,
            description=description.strip() or None,
            source_role_key=source_role_key.strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    response = templates.TemplateResponse("tasks/_table.html", {
        "request": request,
        "tasks": _tasks_list(),
        "roles": _roles_for_select(),
    })
    response.headers["HX-Toast"] = json.dumps({"message": f'Aufgabe "{title}" erstellt', "type": "success"})
    return response


@app.put("/tasks/{key}", response_class=HTMLResponse)
async def tasks_update(
    request: Request,
    key: str,
    title: str = Form(...),
    short_title: str = Form(""),
    short_code: str = Form(""),
    description: str = Form(""),
    source_role_key: str = Form(""),
    body_text: str = Form(""),
):
    task_obj, _ = load_task(key)
    if not task_obj:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    try:
        upsert_task(
            key=key,
            title=title.strip(),
            body_text=body_text,
            short_title=short_title.strip() or None,
            short_code=short_code.strip() or None,
            description=description.strip() or None,
            source_role_key=source_role_key.strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    response = templates.TemplateResponse("tasks/_table.html", {
        "request": request,
        "tasks": _tasks_list(),
        "roles": _roles_for_select(),
    })
    response.headers["HX-Toast"] = json.dumps({"message": f'Aufgabe "{title}" gespeichert', "type": "success"})
    return response


@app.delete("/tasks/{key}", response_class=HTMLResponse)
async def tasks_delete(key: str):
    if not soft_delete_task(key):
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    response = HTMLResponse(content="")
    response.headers["HX-Toast"] = json.dumps({"message": "Aufgabe gelöscht", "type": "success"})
    return response


# ════════════════════════════════════════════════════════════════════════════
# JSON REST API (bleibt erhalten für externe Clients / Swagger)
# ════════════════════════════════════════════════════════════════════════════

# ── Pydantic models ────────────────────────────────────────────────────────

class RoleCreate(BaseModel):
    title: str
    group_name: Optional[str] = None
    body_text: Optional[str] = ""

class ProjectCreate(BaseModel):
    title: str
    description: str
    short_title: Optional[str] = None
    short_code: Optional[str] = None
    type: Optional[str] = None
    body_text: Optional[str] = ""

# ── Roles ──────────────────────────────────────────────────────────────────

@app.get("/api/roles", response_model=List[dict], tags=["Roles"])
async def get_roles():
    """Alle Rollen abrufen"""
    try:
        df = list_roles_df(include_deleted=False)
        if df is None or df.empty:
            return []
        return df.to_dict("records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/roles/{role_key}", tags=["Roles"])
async def get_role(role_key: str):
    """Einzelne Rolle abrufen"""
    role_obj, role_body = load_role(role_key)
    if not role_obj:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    return {
        "key": role_obj.key,
        "title": role_obj.title,
        "short_code": role_obj.short_code,
        "body_text": role_body or "",
    }


@app.post("/api/roles", tags=["Roles"])
async def create_role(role: RoleCreate):
    """Neue Rolle erstellen"""
    try:
        role_obj, created = upsert_role(title=role.title, body_text=role.body_text)
        return {"key": role_obj.key, "title": role_obj.title, "short_code": role_obj.short_code, "created": created}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/roles/{role_key}", tags=["Roles"])
async def delete_role(role_key: str):
    """Rolle löschen"""
    if not soft_delete_role(role_key):
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    return {"success": True}


# ── Projects ───────────────────────────────────────────────────────────────

@app.get("/api/projects", response_model=List[dict], tags=["Projects"])
async def api_get_projects():
    """Alle Projekte abrufen (JSON)"""
    return _projects_list()


@app.get("/api/projects/{key}", tags=["Projects"])
async def api_get_project(key: str):
    """Einzelnes Projekt abrufen (JSON)"""
    proj, body = load_project(key)
    if not proj:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return {
        "key": proj.key, "title": proj.title, "short_title": proj.short_title,
        "short_code": proj.short_code, "type": proj.type, "description": proj.description,
        "body_text": body,
        "role_keys": json.loads(proj.role_keys) if proj.role_keys else [],
        "context_keys": json.loads(proj.context_keys) if proj.context_keys else [],
    }


@app.post("/api/projects", tags=["Projects"])
async def api_create_project(project: ProjectCreate):
    """Neues Projekt erstellen (JSON)"""
    try:
        obj, created = upsert_project(
            title=project.title,
            description=project.description,
            short_title=project.short_title,
            short_code=project.short_code,
            type_name=project.type,
            body_text=project.body_text or "",
        )
        return {"key": obj.key, "title": obj.title, "created": created}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/projects/{key}", tags=["Projects"])
async def api_delete_project(key: str):
    """Projekt löschen (JSON)"""
    if not soft_delete_project(key):
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")
    return {"success": True}


# ── LLM ───────────────────────────────────────────────────────────────────

@app.get("/api/llm/providers", tags=["LLM"])
async def get_llm_providers():
    try:
        return {"providers": providers_available()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/llm/generate-role", tags=["LLM"])
async def generate_role_endpoint(provider: str, title: str, group_name: Optional[str] = None):
    try:
        return {"generated_text": generate_role_text(provider, title, group_name)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/llm/generate-summary", tags=["LLM"])
async def generate_summary_endpoint(provider: str, title: str, content: str):
    try:
        return {"summary": generate_summary(provider, title, content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "healthy", "message": "SlitProjektHub API is running", "version": "2.0.0"}


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
