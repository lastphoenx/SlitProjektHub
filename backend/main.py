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
from src.m07_contexts import list_contexts_df, load_context, upsert_context, soft_delete_context
from src.m03_db import get_session, Project, Role, Task, Context
from src.m08_llm import (providers_available, generate_role_text, generate_summary,
                          try_models_with_messages, get_available_models, AVAILABLE_MODELS,
                          generate_role_details, generate_project_details, generate_context_details,
                          have_key, test_connection, DEFAULT_MODELS, rewrite_query_for_retrieval)
from src.m10_chat import (save_message, load_history, find_latest_session_for_project, build_project_map,
                           update_message_metadata, delete_message, delete_history, purge_history,
                           save_rag_feedback)
from src.m09_rag import retrieve_relevant_chunks_hybrid, build_rag_context_from_search, deduplicate_results
from src.m01_config import load_user_settings, save_user_settings
from sqlmodel import select
import uuid as _uuid

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
# HTML Routes — Contexts
# ════════════════════════════════════════════════════════════════════════════

def _contexts_list() -> list[dict]:
    df = list_contexts_df(include_body=False)
    if df.empty:
        return []
    return [
        {
            "key": row["Key"],
            "title": row["Titel"],
            "short_title": row.get("KurzTitel", ""),
            "short_code": row.get("Kürzel", ""),
            "description": row.get("Beschreibung", ""),
        }
        for _, row in df.iterrows()
    ]


@app.get("/contexts", response_class=HTMLResponse)
async def contexts_page(request: Request):
    return templates.TemplateResponse("contexts/index.html", {
        "request": request,
        "contexts": _contexts_list(),
    })


@app.get("/contexts/new", response_class=HTMLResponse)
async def contexts_new_form(request: Request):
    return templates.TemplateResponse("contexts/_form.html", {
        "request": request,
        "context": None,
        "body_text": "",
    })


@app.get("/contexts/{key}/edit", response_class=HTMLResponse)
async def contexts_edit_form(request: Request, key: str):
    ctx_obj, body = load_context(key)
    if not ctx_obj:
        raise HTTPException(status_code=404, detail="Kontext nicht gefunden")
    ctx = {
        "key": ctx_obj.key,
        "title": ctx_obj.title,
        "short_title": ctx_obj.short_title or "",
        "short_code": ctx_obj.short_code or "",
        "description": ctx_obj.description or "",
    }
    return templates.TemplateResponse("contexts/_form.html", {
        "request": request,
        "context": ctx,
        "body_text": body,
    })


@app.get("/contexts/{key}/confirm-delete", response_class=HTMLResponse)
async def contexts_confirm_delete(request: Request, key: str):
    ctx_obj, _ = load_context(key)
    if not ctx_obj:
        raise HTTPException(status_code=404, detail="Kontext nicht gefunden")
    return templates.TemplateResponse("contexts/_confirm_delete.html", {
        "request": request,
        "context": {"key": ctx_obj.key, "title": ctx_obj.title},
    })


@app.post("/contexts", response_class=HTMLResponse)
async def contexts_create(
    request: Request,
    title: str = Form(...),
    short_title: str = Form(""),
    short_code: str = Form(""),
    description: str = Form(""),
    body_text: str = Form(""),
):
    upsert_context(
        title=title,
        body_text=body_text,
        short_title=short_title or None,
        short_code=short_code or None,
        description=description or None,
    )
    response = templates.TemplateResponse("contexts/_table.html", {
        "request": request,
        "contexts": _contexts_list(),
    })
    response.headers["HX-Toast"] = json.dumps({"message": f'Kontext "{title}" erstellt', "type": "success"})
    return response


@app.put("/contexts/{key}", response_class=HTMLResponse)
async def contexts_update(
    request: Request,
    key: str,
    title: str = Form(...),
    short_title: str = Form(""),
    short_code: str = Form(""),
    description: str = Form(""),
    body_text: str = Form(""),
):
    ctx_obj, _ = load_context(key)
    if not ctx_obj:
        raise HTTPException(status_code=404, detail="Kontext nicht gefunden")
    upsert_context(
        key=key,
        title=title,
        body_text=body_text,
        short_title=short_title or None,
        short_code=short_code or None,
        description=description or None,
    )
    response = templates.TemplateResponse("contexts/_table.html", {
        "request": request,
        "contexts": _contexts_list(),
    })
    response.headers["HX-Toast"] = json.dumps({"message": f'Kontext "{title}" gespeichert', "type": "success"})
    return response


@app.delete("/contexts/{key}", response_class=HTMLResponse)
async def contexts_delete(key: str):
    if not soft_delete_context(key):
        raise HTTPException(status_code=404, detail="Kontext nicht gefunden")
    response = HTMLResponse(content="")
    response.headers["HX-Toast"] = json.dumps({"message": "Kontext gelöscht", "type": "success"})
    return response


@app.post("/contexts/ai-suggest", response_class=HTMLResponse)
async def contexts_ai_suggest(
    request: Request,
    ai_description: str = Form(""),
    context_key: str = Form(""),
    provider: str = Form("openai"),
    model: str = Form(""),
    temperature: float = Form(0.7),
):
    if not ai_description.strip():
        return HTMLResponse('<p class="text-muted" style="font-size:.8rem;padding:.5rem 0">Bitte Beschreibung eingeben.</p>')
    try:
        title, short_code, short_title, description, body_md = generate_context_details(
            provider=provider,
            description=ai_description.strip(),
            context_key=context_key or None,
            model=model or None,
            temperature=temperature,
        )
    except Exception as e:
        return HTMLResponse(f'<p style="color:var(--color-danger);font-size:.8rem">Fehler: {e}</p>')

    return HTMLResponse(f"""<script>
(function(){{
  var f = (id, val) => {{ var el = document.getElementById(id); if(el) el.value = val; }};
  f('c-title', {json.dumps(title)});
  f('c-short-code', {json.dumps(short_code or "")});
  f('c-short-title', {json.dumps(short_title or "")});
  f('c-description', {json.dumps(description or "")});
  f('c-body', {json.dumps(body_md or "")});
}})();
</script>
<p style="color:var(--color-success);font-size:.8rem;padding:.25rem 0">
  <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="display:inline;vertical-align:middle"><path d="M20 6L9 17l-5-5"/></svg>
  KI-Vorschlag eingefügt!
</p>""")


# ════════════════════════════════════════════════════════════════════════════
# HTML Routes — Chat
# ════════════════════════════════════════════════════════════════════════════

def _all_models_json() -> str:
    """Returns AVAILABLE_MODELS as JSON string for use in JS."""
    result = {p: list(m.keys()) for p, m in AVAILABLE_MODELS.items()}
    return json.dumps(result)


def _default_provider() -> str:
    avail = providers_available()
    return avail[0] if avail else "openai"


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, project_key: str | None = None, provider: str | None = None):
    projects = _projects_list()
    s = _load_settings_ctx()
    sel_project = project_key or (projects[0]["key"] if projects else "")
    providers = providers_available() or ["openai"]
    sel_provider = provider or s["provider"] or providers[0]
    # Use saved model if it exists for this provider, else first available
    saved_model = s["model"] if s["model"] in get_available_models(sel_provider) else (get_available_models(sel_provider)[0] if get_available_models(sel_provider) else "")
    session_id = ""
    if sel_project and sel_provider:
        session_id = find_latest_session_for_project(sel_provider, sel_project) or str(_uuid.uuid4())
    return templates.TemplateResponse("chat/index.html", {
        "request": request,
        "projects": projects,
        "providers": providers,
        "sel_project": sel_project,
        "sel_provider": sel_provider,
        "sel_model": saved_model,
        "temperature": s["temperature"],
        "rag_enabled": s["rag_enabled"],
        "session_id": session_id,
        "all_models_json": _all_models_json(),
        "active_page": "chat",
    })


@app.get("/chat/history", response_class=HTMLResponse)
async def chat_history(
    request: Request,
    project_key: str = "",
    provider: str = "openai",
):
    history = []
    session_id = ""
    if project_key and provider:
        session_id = find_latest_session_for_project(provider, project_key) or str(_uuid.uuid4())
        if session_id:
            history = load_history(provider, session_id)
    return templates.TemplateResponse("chat/_history.html", {
        "request": request,
        "history": history,
        "session_id": session_id,
    })


@app.post("/chat/send", response_class=HTMLResponse)
async def chat_send(
    request: Request,
    project_key: str = Form(""),
    session_id: str = Form(""),
    provider: str = Form("openai"),
    model: str = Form(""),
    temperature: float = Form(0.7),
    rag_enabled: str = Form("true"),
    message: str = Form(""),
):
    if not message.strip():
        raise HTTPException(status_code=400, detail="Leere Nachricht")
    if not session_id:
        session_id = str(_uuid.uuid4())

    use_rag = rag_enabled.lower() in ("true", "1", "on", "yes")

    # Save user message
    save_message(
        provider=provider,
        session_id=session_id,
        role="user",
        content=message.strip(),
        project_key=project_key or None,
        model_name=model or None,
        model_temperature=temperature,
    )

    # Build system prompt
    project_map = ""
    proj_body = ""
    proj_title = project_key or "Unbekanntes Projekt"
    if project_key:
        try:
            proj_obj, proj_body = load_project(project_key)
            if proj_obj:
                proj_title = proj_obj.title
                project_map = build_project_map(project_key, query=message.strip())
        except Exception:
            pass

    # RAG retrieval — with optional query distillation
    rag_section = ""
    rag_sources: list[dict] = []
    rag_results: dict = {}
    search_query = message.strip()
    if use_rag and project_key:
        try:
            from src.m01_retrieval_config import get_retrieval_config
            rc = get_retrieval_config()
            if rc.query.enable_distillation:
                try:
                    distilled = rewrite_query_for_retrieval(
                        message.strip(),
                        provider=rc.query.distillation_provider,
                        model=rc.query.distillation_model,
                    )
                    if distilled:
                        search_query = distilled
                except Exception:
                    pass
        except Exception:
            pass
        try:
            s = _load_settings_ctx()
            rag_results = retrieve_relevant_chunks_hybrid(
                search_query,
                project_key=project_key,
                limit=s.get("rag_top_k", 7),
                threshold=s.get("rag_similarity_threshold", 0.45),
            )
            rag_results = deduplicate_results(rag_results)
            rag_text = build_rag_context_from_search(rag_results)
            if rag_text:
                rag_section = f"\n\n{rag_text}"
            docs = rag_results.get("documents", [])
            rag_sources = [
                {
                    "filename": d.get("filename", ""),
                    "similarity": d.get("similarity", 0),
                    "document_id": d.get("document_id", 0),
                }
                for d in docs[:5]
            ]
        except Exception:
            pass

    system_prompt = f"""Du bist ein erfahrener Projekt-Assistent für "{proj_title}".

PROJEKT-STRUKTUR:
{project_map or "(Keine Strukturdaten verfügbar)"}

PROJEKT-BRIEF:
{proj_body or "(Kein Projektbeschrieb vorhanden)"}
{rag_section}

Antworte prägnant, strukturiert und mit direktem Bezug zu den Projekt-Anforderungen. Antworte auf Deutsch."""

    # Build chat history for context
    prev_history = load_history(provider, session_id)
    messages_for_llm = [{"role": m["role"], "content": m["content"]} for m in prev_history]
    messages_for_llm.append({"role": "user", "content": message.strip()})

    # Call LLM
    used_model: list = []
    ai_response = None
    error_text = None
    try:
        ai_response = try_models_with_messages(
            provider=provider,
            system=system_prompt,
            messages=messages_for_llm,
            max_tokens=2000,
            temperature=temperature,
            model=model or None,
            _used_model=used_model,
        )
    except Exception as e:
        error_text = str(e)

    actual_model = used_model[0] if used_model else model

    ai_msg_id = None
    user_msg_id = None
    if ai_response:
        ai_saved = save_message(
            provider=provider,
            session_id=session_id,
            role="assistant",
            content=ai_response,
            project_key=project_key or None,
            model_name=actual_model,
            model_temperature=temperature,
            rag_sources=rag_sources or None,
        )
        ai_msg_id = ai_saved.id if ai_saved else None
    else:
        ai_response = error_text or "Keine Antwort vom KI-Modell erhalten."

    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M")

    return templates.TemplateResponse("chat/_message_pair.html", {
        "request": request,
        "user_message": message.strip(),
        "ai_response": ai_response,
        "model": actual_model or model,
        "temperature": temperature,
        "rag_sources": rag_sources,
        "timestamp": timestamp,
        "error": error_text is not None and ai_response == error_text,
        "ai_msg_id": ai_msg_id,
        "user_msg_id": user_msg_id,
    })


@app.patch("/chat/messages/{msg_id}/metadata", response_class=HTMLResponse)
async def chat_update_metadata(
    msg_id: int,
    message_type: str = Form(""),
    message_status: str = Form("ungeprüft"),
):
    update_message_metadata(
        msg_id,
        message_type=message_type or None,
        message_status=message_status or "ungeprüft",
    )
    return HTMLResponse(f"""
<span style="font-size:.72rem;color:var(--color-success);padding:.1rem .3rem">
  <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>
  Gespeichert
</span>""")


@app.delete("/chat/messages/{msg_id}", response_class=HTMLResponse)
async def chat_delete_message(msg_id: int):
    delete_message(msg_id)
    return HTMLResponse("")   # OOB remove handled in template


@app.post("/chat/session/hide", response_class=HTMLResponse)
async def chat_session_hide(
    provider: str = Form(""),
    session_id: str = Form(""),
):
    if provider and session_id:
        delete_history(provider, session_id)
    response = HTMLResponse('<p class="text-muted" style="text-align:center;padding:2rem">Verlauf ausgeblendet.</p>')
    return response


@app.post("/chat/session/purge", response_class=HTMLResponse)
async def chat_session_purge(
    provider: str = Form(""),
    session_id: str = Form(""),
):
    if provider and session_id:
        purge_history(provider, session_id)
    response = HTMLResponse('<p class="text-muted" style="text-align:center;padding:2rem">Verlauf endgültig gelöscht.</p>')
    return response


@app.post("/chat/feedback", response_class=HTMLResponse)
async def chat_feedback(
    message_id: int = Form(...),
    document_id: int = Form(0),
    helpful: str = Form("true"),
):
    doc_id = document_id if document_id else 0
    is_helpful = helpful.lower() in ("true", "1", "yes")
    save_rag_feedback(message_id, doc_id, is_helpful)
    icon = "👍" if is_helpful else "👎"
    return HTMLResponse(f'<span style="font-size:.85rem">{icon}</span>')


# ════════════════════════════════════════════════════════════════════════════
# HTML Routes — KI-Vorschlag (Roles + Projects + Contexts)
# ════════════════════════════════════════════════════════════════════════════

@app.post("/roles/ai-suggest", response_class=HTMLResponse)
async def roles_ai_suggest(
    request: Request,
    ai_description: str = Form(""),
    role_key: str = Form(""),
    provider: str = Form("openai"),
    model: str = Form(""),
    temperature: float = Form(0.7),
):
    if not ai_description.strip():
        return HTMLResponse('<p class="text-muted" style="font-size:.8rem;padding:.5rem 0">Bitte Beschreibung eingeben.</p>')
    try:
        title, short_code, responsibilities, qualifications, expertise, prose = generate_role_details(
            provider=provider,
            description=ai_description.strip(),
            role_key=role_key or None,
            model=model or None,
            temperature=temperature,
        )
    except Exception as e:
        return HTMLResponse(f'<p style="color:var(--color-danger);font-size:.8rem">Fehler: {e}</p>')

    # Return JS that fills the form fields
    return HTMLResponse(f"""<script>
(function(){{
  var s = v => v.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  var f = (id, val) => {{ var el = document.getElementById(id); if(el) el.value = val; }};
  f('r-title', {json.dumps(title)});
  f('r-short-code', {json.dumps(short_code or "")});
  f('r-responsibilities', {json.dumps(responsibilities or "")});
  f('r-qualifications', {json.dumps(qualifications or "")});
  f('r-expertise', {json.dumps(expertise or "")});
  f('r-body', {json.dumps(prose or "")});
}})();
</script>
<p style="color:var(--color-success);font-size:.8rem;padding:.25rem 0">
  <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="display:inline;vertical-align:middle"><path d="M20 6L9 17l-5-5"/></svg>
  KI-Vorschlag eingefügt!
</p>""")


@app.post("/projects/ai-suggest", response_class=HTMLResponse)
async def projects_ai_suggest(
    request: Request,
    ai_description: str = Form(""),
    project_key: str = Form(""),
    provider: str = Form("openai"),
    model: str = Form(""),
    temperature: float = Form(0.7),
):
    if not ai_description.strip():
        return HTMLResponse('<p class="text-muted" style="font-size:.8rem;padding:.5rem 0">Bitte Beschreibung eingeben.</p>')
    try:
        title, short_code, short_title, description, body_md = generate_project_details(
            provider=provider,
            description=ai_description.strip(),
            project_key=project_key or None,
            model=model or None,
            temperature=temperature,
        )
    except Exception as e:
        return HTMLResponse(f'<p style="color:var(--color-danger);font-size:.8rem">Fehler: {e}</p>')

    return HTMLResponse(f"""<script>
(function(){{
  var f = (id, val) => {{ var el = document.getElementById(id); if(el) el.value = val; }};
  f('f-title', {json.dumps(title)});
  f('f-short-code', {json.dumps(short_code or "")});
  f('f-short-title', {json.dumps(short_title or "")});
  f('f-description', {json.dumps(description or "")});
  f('f-body', {json.dumps(body_md or "")});
}})();
</script>
<p style="color:var(--color-success);font-size:.8rem;padding:.25rem 0">
  <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" style="display:inline;vertical-align:middle"><path d="M20 6L9 17l-5-5"/></svg>
  KI-Vorschlag eingefügt!
</p>""")


# ════════════════════════════════════════════════════════════════════════════
# HTML Routes — Task-Generierung
# ════════════════════════════════════════════════════════════════════════════

@app.get("/taskgen", response_class=HTMLResponse)
async def taskgen_page(request: Request, role_key: str | None = None):
    roles = _roles_list()
    s = _load_settings_ctx()
    sel_role = None
    if role_key:
        for r in roles:
            if r["key"] == role_key:
                sel_role = r
                break
    return templates.TemplateResponse("taskgen/index.html", {
        "request": request,
        "active_page": "taskgen",
        "roles": roles,
        "sel_role": sel_role,
        "providers": providers_available() or ["openai"],
        "all_models_json": _all_models_json(),
        "settings": s,
    })


@app.post("/taskgen/generate", response_class=HTMLResponse)
async def taskgen_generate(
    request: Request,
    role_key: str = Form(""),
    provider: str = Form("openai"),
    model: str = Form(""),
    temperature: float = Form(0.7),
    min_per_resp: int = Form(2),
    max_per_resp: int = Form(5),
    rag_enabled: str = Form("false"),
):
    if not role_key:
        return HTMLResponse('<p class="text-muted" style="padding:1rem">Bitte eine Rolle auswählen.</p>')

    role_obj, _ = load_role(role_key)
    if not role_obj:
        return HTMLResponse('<p style="color:var(--color-danger);padding:1rem">Rolle nicht gefunden.</p>')

    responsibilities = getattr(role_obj, "responsibilities", "") or ""
    if not responsibilities.strip():
        return HTMLResponse('<p style="color:var(--color-danger);padding:1rem">Die Rolle hat keine Verantwortlichkeiten definiert. Bitte zuerst in der Rollenverwaltung ergänzen.</p>')

    s = _load_settings_ctx()
    use_rag = rag_enabled.lower() in ("true", "1", "on", "yes")

    try:
        from src.m12_task_generation import generate_tasks_from_role
        tasks = generate_tasks_from_role(
            provider=provider,
            role_title=role_obj.title,
            role_key=role_key,
            responsibilities=responsibilities,
            min_per_resp=min_per_resp,
            max_per_resp=max_per_resp,
            model_name=model or s.get("model") or None,
            temperature=temperature,
            rag_enabled=use_rag,
            rag_top_k=s.get("rag_top_k", 5),
            rag_similarity_threshold=s.get("rag_similarity_threshold", 0.45),
            rag_chunk_size=s.get("rag_chunk_size", 1000),
        )
    except Exception as e:
        return HTMLResponse(f'<div style="color:var(--color-danger);padding:1rem">Fehler beim Generieren: {e}</div>')

    return templates.TemplateResponse("taskgen/_results.html", {
        "request": request,
        "tasks": tasks,
        "role_key": role_key,
        "role_title": role_obj.title,
        "count": len(tasks),
    })


@app.post("/taskgen/save", response_class=HTMLResponse)
async def taskgen_save(request: Request):
    """Save selected generated tasks. Accepts JSON body with list of task dicts."""
    body = await request.json()
    tasks_to_save = body.get("tasks", [])
    saved = 0
    errors = []
    for t in tasks_to_save:
        try:
            upsert_task(
                title=t.get("title", ""),
                body_text=t.get("description", ""),
                short_title=t.get("title", "")[:50],
                short_code=t.get("short_code") or None,
                description=t.get("description", ""),
                source_role_key=t.get("source_role_key") or None,
                source_responsibility=t.get("source_responsibility") or None,
                generation_batch_id=t.get("generation_batch_id") or None,
            )
            saved += 1
        except Exception as e:
            errors.append(str(e))

    msg = f"{saved} Aufgabe(n) gespeichert."
    if errors:
        msg += f" {len(errors)} Fehler."
    return HTMLResponse(f"""
<div style="padding:.75rem 1rem;background:var(--color-surface-2);border:1px solid var(--color-border);border-radius:.5rem;display:flex;align-items:center;gap:.6rem">
  <svg width="16" height="16" fill="none" stroke="var(--color-success)" stroke-width="2" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>
  <span style="font-size:.9rem">{msg} <a href="/tasks" style="color:var(--color-primary)">Zu den Aufgaben →</a></span>
</div>""")


# ════════════════════════════════════════════════════════════════════════════
# HTML Routes — KI-Einstellungen
# ════════════════════════════════════════════════════════════════════════════

def _load_settings_ctx() -> dict:
    """Merge config defaults with user_settings.yaml overrides."""
    from src.m01_config import get_settings as _gs
    defaults = _gs().llm_defaults
    user = load_user_settings()
    merged = {**defaults, **user}
    return {
        "provider": merged.get("provider", "openai"),
        "model": merged.get("model", "gpt-4o"),
        "temperature": float(merged.get("temperature", 0.7)),
        "rag_top_k": int(merged.get("rag_top_k", 5)),
        "rag_chunk_size": int(merged.get("rag_chunk_size", 1000)),
        "rag_similarity_threshold": float(merged.get("rag_similarity_threshold", 0.45)),
        "rag_enabled": bool(merged.get("rag_enabled", True)),
    }


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    ctx = _load_settings_ctx()
    return templates.TemplateResponse("settings/index.html", {
        "request": request,
        "active_page": "settings",
        "all_models_json": _all_models_json(),
        "providers": ["openai", "anthropic", "mistral"],
        "provider_status": {p: have_key(p) for p in ["openai", "anthropic", "mistral"]},
        **ctx,
    })


@app.post("/settings", response_class=HTMLResponse)
async def settings_save(
    request: Request,
    provider: str = Form("openai"),
    model: str = Form(""),
    temperature: float = Form(0.7),
    rag_top_k: int = Form(5),
    rag_chunk_size: int = Form(1000),
    rag_similarity_threshold: float = Form(0.45),
    rag_enabled: str = Form("false"),
):
    settings_dict = {
        "provider": provider,
        "model": model or DEFAULT_MODELS.get(provider, ""),
        "temperature": round(temperature, 2),
        "rag_top_k": rag_top_k,
        "rag_chunk_size": rag_chunk_size,
        "rag_similarity_threshold": round(rag_similarity_threshold, 3),
        "rag_enabled": rag_enabled in ("true", "on", "1", "yes"),
    }
    save_user_settings(settings_dict)
    response = templates.TemplateResponse("settings/index.html", {
        "request": request,
        "active_page": "settings",
        "all_models_json": _all_models_json(),
        "providers": ["openai", "anthropic", "mistral"],
        "provider_status": {p: have_key(p) for p in ["openai", "anthropic", "mistral"]},
        **settings_dict,
        "saved": True,
    })
    response.headers["HX-Toast"] = json.dumps({"message": "Einstellungen gespeichert", "type": "success"})
    return response


@app.get("/settings/test-connection", response_class=HTMLResponse)
async def settings_test_connection(request: Request, provider: str = "openai"):
    ok, msg = test_connection(provider)
    status = "success" if ok else "error"
    icon = '<path d="M20 6L9 17l-5-5"/>' if ok else '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'
    color = "var(--color-success)" if ok else "var(--color-danger)"
    label = f"Verbindung OK — {provider}" if ok else f"Fehler: {msg}"
    return HTMLResponse(f"""
<span style="color:{color};font-size:.82rem;display:flex;align-items:center;gap:.35rem">
  <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">{icon}</svg>
  {label}
</span>""")


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


# ── Jinja2 globals: inject KI settings + model list into every template ───

def _ki_global_json() -> str:
    """Returns all KI settings + providers + allModels as single JSON string for window.KI."""
    ctx = _load_settings_ctx()
    ctx["providers"] = providers_available() or ["openai"]
    ctx["allModels"] = {p: list(m.keys()) for p, m in AVAILABLE_MODELS.items()}
    ctx["keyStatus"] = {p: have_key(p) for p in ["openai", "anthropic", "mistral"]}
    return json.dumps(ctx)

templates.env.globals["_ki_global_json"] = _ki_global_json


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
