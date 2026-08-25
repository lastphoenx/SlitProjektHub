"""FastAPI routes — Projektideen (autonome KI-Vorbewertung, kein Projektbezug)."""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from backend.app.jinja_env import templates

from src.m01_config import get_settings, load_user_settings
from src.m14_auth import get_user_id, get_username_by_id, is_super_user, session_username
from src.m08_llm import have_key
from src.m16_idea import (
    ALLOWED_IMAGE_EXT,
    assess_project_idea_with_ai,
    create_idea,
    get_idea,
    list_ideas,
    soft_delete_idea,
    update_idea_intake,
)
from src.m16_idea_visual import (
    DEFAULT_OPENAI_IMAGE_MODEL,
    OPENAI_IMAGE_MODELS,
    generate_cloud_illustration,
    generate_portfolio_deck,
    idea_decks_dir,
    idea_images_dir,
)

router = APIRouter()


def _username(request: Request) -> str:
    from src.m14_auth import validate_session_token

    token = request.cookies.get("_auth_token", "")
    s = get_settings()
    if validate_session_token(token, max_age_seconds=s.auth_session_timeout_minutes * 60):
        return session_username(token) or ""
    return ""


def _may_edit_idea(idea, user_id: int | None, who: str) -> bool:
    if is_super_user(who):
        return True
    if idea.submitted_by is None:
        return True
    return user_id is not None and idea.submitted_by == user_id


def _idea_images_dir() -> Path:
    return idea_images_dir()


@router.get("/idea", response_class=HTMLResponse)
async def idea_list(request: Request):
    who = _username(request)
    ideas = list_ideas()
    return templates.TemplateResponse(
        "idea/index.html",
        {
            "request": request,
            "active_page": "idea",
            "ideas": ideas,
            "submitters": {i.id: (get_username_by_id(i.submitted_by) if i.submitted_by else None) for i in ideas},
            "username": who,
            "error": None,
        },
    )


@router.post("/idea", response_class=HTMLResponse)
async def idea_create(
    request: Request,
    title: str = Form(""),
    idea_text: str = Form(...),
    fachabteilung: str = Form(""),
    internal_pt_human: str = Form(""),
    external_cost_human: str = Form(""),
    image: UploadFile | None = File(None),
):
    who = _username(request)
    user_id = get_user_id(who) if who else None

    def _f(v: str) -> float | None:
        v = (v or "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    image_path = None
    if image is not None and image.filename:
        ext = Path(image.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXT:
            raise HTTPException(400, f"Bildformat nicht erlaubt (erlaubt: {', '.join(ALLOWED_IMAGE_EXT)})")
        content = await image.read()
        if len(content) > 8 * 1024 * 1024:
            raise HTTPException(400, "Bild zu gross (max. 8 MB)")
        fname = f"{uuid.uuid4().hex}{ext}"
        (_idea_images_dir() / fname).write_bytes(content)
        image_path = fname

    obj = create_idea(
        idea_text=idea_text,
        title=title,
        fachabteilung=fachabteilung,
        internal_pt_human=_f(internal_pt_human),
        external_cost_human=_f(external_cost_human),
        image_path=image_path,
        submitted_by=user_id,
    )
    return RedirectResponse(url=f"/idea/{obj.id}", status_code=303)


@router.get("/idea/{idea_id}", response_class=HTMLResponse)
async def idea_detail(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    who = _username(request)
    user_id = get_user_id(who) if who else None
    settings = load_user_settings()
    may_edit = _may_edit_idea(idea, user_id, who)
    qp = request.query_params
    return templates.TemplateResponse(
        "idea/detail.html",
        {
            "request": request,
            "active_page": "idea",
            "idea": idea,
            "challenges": idea.challenges,
            "phases": idea.phases,
            "submitter": get_username_by_id(idea.submitted_by) if idea.submitted_by else None,
            "may_edit": may_edit,
            "assess_error": qp.get("assess_error"),
            "deck_error": qp.get("deck_error"),
            "illustration_error": qp.get("illustration_error"),
            "llm_provider": settings.get("provider", "openai"),
            "llm_model": settings.get("model", ""),
            "openai_image_models": OPENAI_IMAGE_MODELS,
            "default_image_model": DEFAULT_OPENAI_IMAGE_MODEL,
            "openai_key_ok": have_key("openai"),
        },
    )


@router.post("/idea/{idea_id}/assess", response_class=HTMLResponse)
async def idea_assess(
    request: Request,
    idea_id: int,
    provider: str = Form("openai"),
    model: str = Form(""),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    result = assess_project_idea_with_ai(idea_id, provider=provider, model=model)
    if not result or result.status != "bewertet":
        return RedirectResponse(url=f"/idea/{idea_id}?assess_error=1", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}", status_code=303)


@router.post("/idea/{idea_id}/generate-deck", response_class=HTMLResponse)
async def idea_generate_deck(
    request: Request,
    idea_id: int,
    refinement_notes: str = Form(""),
    llm_provider: str = Form("openai"),
    llm_model: str = Form(""),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    if idea.status != "bewertet":
        return RedirectResponse(url=f"/idea/{idea_id}?deck_error=1", status_code=303)
    result = generate_portfolio_deck(
        idea_id,
        refinement_notes=refinement_notes,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )
    if not result:
        return RedirectResponse(url=f"/idea/{idea_id}?deck_error=1", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}", status_code=303)


@router.post("/idea/{idea_id}/generate-illustration", response_class=HTMLResponse)
async def idea_generate_illustration(
    request: Request,
    idea_id: int,
    image_model: str = Form(DEFAULT_OPENAI_IMAGE_MODEL),
    llm_provider: str = Form("openai"),
    llm_model: str = Form(""),
    refinement_notes: str = Form(""),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    if not have_key("openai"):
        return RedirectResponse(url=f"/idea/{idea_id}?illustration_error=no_key", status_code=303)
    if idea.status != "bewertet":
        return RedirectResponse(url=f"/idea/{idea_id}?illustration_error=not_assessed", status_code=303)
    if image_model not in OPENAI_IMAGE_MODELS:
        image_model = DEFAULT_OPENAI_IMAGE_MODEL
    result = generate_cloud_illustration(
        idea_id,
        image_model=image_model,
        llm_provider=llm_provider,
        llm_model=llm_model,
        refinement_notes=refinement_notes,
    )
    if not result:
        return RedirectResponse(url=f"/idea/{idea_id}?illustration_error=1", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}", status_code=303)


@router.post("/idea/{idea_id}/edit", response_class=HTMLResponse)
async def idea_edit(
    request: Request,
    idea_id: int,
    title: str = Form(""),
    idea_text: str = Form(...),
    fachabteilung: str = Form(""),
    internal_pt_human: str = Form(""),
    external_cost_human: str = Form(""),
):
    def _f(v: str) -> float | None:
        v = (v or "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    who = _username(request)
    user_id = get_user_id(who) if who else None
    if not _may_edit_idea(idea, user_id, who):
        raise HTTPException(403, "Keine Berechtigung")

    obj = update_idea_intake(
        idea_id,
        title=title or None,
        idea_text=idea_text,
        fachabteilung=fachabteilung or None,
        internal_pt_human=_f(internal_pt_human),
        external_cost_human=_f(external_cost_human),
    )
    if not obj:
        raise HTTPException(404, "Idee nicht gefunden")
    return RedirectResponse(url=f"/idea/{idea_id}", status_code=303)


@router.post("/idea/{idea_id}/delete", response_class=HTMLResponse)
async def idea_delete(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    who = _username(request)
    user_id = get_user_id(who) if who else None
    if not _may_edit_idea(idea, user_id, who):
        raise HTTPException(403, "Keine Berechtigung")
    soft_delete_idea(idea_id)
    return RedirectResponse(url="/idea", status_code=303)


@router.get("/idea/{idea_id}/preview-image", response_class=HTMLResponse)
async def idea_preview_image(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or not idea.image_path:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "idea/_preview_image.html",
        {"request": request, "idea": idea},
    )


@router.get("/idea/{idea_id}/preview-deck", response_class=HTMLResponse)
async def idea_preview_deck(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or not idea.deck_path:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "idea/_preview_deck.html",
        {"request": request, "idea": idea},
    )


@router.get("/idea/image/{idea_id}")
async def idea_image(idea_id: int, disposition: str = "attachment"):
    idea = get_idea(idea_id)
    if not idea or not idea.image_path:
        raise HTTPException(404, "Kein Bild vorhanden")
    path = _idea_images_dir() / Path(idea.image_path).name
    if not path.exists():
        raise HTTPException(404, "Kein Bild vorhanden")
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    disp = "inline" if disposition == "inline" else "attachment"
    resp = FileResponse(path, media_type=ctype, content_disposition_type=disp)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@router.get("/idea/deck-preview/{idea_id}")
async def idea_deck_preview(idea_id: int, disposition: str = "inline"):
    idea = get_idea(idea_id)
    if not idea or not idea.deck_preview_path:
        raise HTTPException(404, "Keine Folien-Vorschau")
    path = _idea_images_dir() / Path(idea.deck_preview_path).name
    if not path.exists():
        raise HTTPException(404, "Keine Folien-Vorschau")
    resp = FileResponse(
        path,
        media_type="image/png",
        content_disposition_type="inline" if disposition == "inline" else "attachment",
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@router.get("/idea/deck/{idea_id}")
async def idea_deck_download(idea_id: int):
    idea = get_idea(idea_id)
    if not idea or not idea.deck_path:
        raise HTTPException(404, "Keine Portfolio-Folie")
    path = idea_decks_dir() / Path(idea.deck_path).name
    if not path.exists():
        raise HTTPException(404, "Keine Portfolio-Folie")
    title = (idea.ai_project_name or idea.title or f"Projektidee_{idea_id}").replace("/", "-")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{title[:60]}.pptx",
    )
