"""Visual-Lab — Prompt-Tests für PNG / PPTX / Vorschau."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from backend.app.jinja_env import templates

from src.m01_config import load_user_settings
from src.m08_llm import have_key
from src.m14_auth import get_user_id, session_username
from src.m16_idea_visual import (
    DEFAULT_OPENAI_IMAGE_MODEL,
    OPENAI_IMAGE_MODELS,
    resolve_visual_llm,
    visual_text_models_map,
    visual_text_providers_available,
)
from src.m17_visual_lab import (
    VISUAL_LAB_KINDS,
    delete_visual_lab_run,
    get_visual_lab_run,
    list_visual_lab_runs,
    run_visual_lab,
    visual_lab_dir,
)

router = APIRouter()


def _username(request: Request) -> str:
    from src.m14_auth import validate_session_token
    from src.m01_config import get_settings

    token = request.cookies.get("_auth_token", "")
    s = get_settings()
    if validate_session_token(token, max_age_seconds=s.auth_session_timeout_minutes * 60):
        return session_username(token) or ""
    return ""


def _lab_context(request: Request, **extra):
    settings = load_user_settings()
    base = {
        "request": request,
        "active_page": "visual-lab",
        "runs": list_visual_lab_runs(),
        "kinds": VISUAL_LAB_KINDS,
        "llm_provider": settings.get("provider", "openai"),
        "llm_model": settings.get("model", ""),
        "openai_image_models": OPENAI_IMAGE_MODELS,
        "default_image_model": DEFAULT_OPENAI_IMAGE_MODEL,
        "openai_key_ok": have_key("openai"),
        "visual_llm_providers": visual_text_providers_available(),
        "visual_llm_models": visual_text_models_map(),
        "form_prompt": "",
        "form_refinement": "",
        "form_kind": "pptx",
        "form_visual_provider": "",
        "form_visual_model": "",
        "error": None,
        "ok": False,
    }
    base.update(extra)
    return base


@router.get("/visual-lab", response_class=HTMLResponse)
async def visual_lab_index(request: Request):
    qp = request.query_params
    ctx = _lab_context(request)
    ctx["error"] = qp.get("error")
    ctx["ok"] = qp.get("ok") == "1"
    if qp.get("prompt"):
        ctx["form_prompt"] = qp.get("prompt", "")
        ctx["form_refinement"] = qp.get("refinement", "")
        ctx["form_kind"] = qp.get("kind", "pptx")
    elif qp.get("run_id"):
        run = get_visual_lab_run(int(qp["run_id"]))
        if run:
            ctx["form_prompt"] = run.prompt_input
            ctx["form_refinement"] = run.refinement or ""
            ctx["form_kind"] = run.kind
            ctx["form_visual_provider"] = run.llm_provider or ""
            ctx["form_visual_model"] = run.llm_model or ""
    return templates.TemplateResponse("visual_lab/index.html", ctx)


@router.post("/visual-lab/generate", response_class=HTMLResponse)
async def visual_lab_generate(
    request: Request,
    kind: str = Form(...),
    prompt: str = Form(...),
    refinement: str = Form(""),
    image_model: str = Form(DEFAULT_OPENAI_IMAGE_MODEL),
    llm_provider: str = Form("openai"),
    llm_model: str = Form(""),
    visual_llm_provider: str = Form(""),
    visual_llm_model: str = Form(""),
):
    who = _username(request)
    user_id = get_user_id(who) if who else None
    settings = load_user_settings()
    fb_p = settings.get("provider", "openai")
    fb_m = settings.get("model", "")
    vp, vm = resolve_visual_llm(visual_llm_provider, visual_llm_model, fb_p, fb_m)

    form_ctx = {
        "form_prompt": prompt,
        "form_refinement": refinement,
        "form_kind": kind,
        "form_visual_provider": visual_llm_provider,
        "form_visual_model": visual_llm_model,
    }

    if kind == "png" and not have_key("openai"):
        ctx = _lab_context(request, error="no_key", **form_ctx)
        return templates.TemplateResponse("visual_lab/index.html", ctx)

    row, err = run_visual_lab(
        kind=kind,
        prompt=prompt,
        refinement=refinement,
        image_model=image_model,
        llm_provider=vp,
        llm_model=vm,
        created_by=user_id,
    )
    if not row:
        ctx = _lab_context(request, error=err or "unknown", **form_ctx)
        return templates.TemplateResponse("visual_lab/index.html", ctx)

    return RedirectResponse(url=f"/visual-lab?ok=1&run_id={row.id}", status_code=303)


@router.post("/visual-lab/{run_id}/delete", response_class=HTMLResponse)
async def visual_lab_delete(run_id: int):
    if not delete_visual_lab_run(run_id):
        raise HTTPException(404)
    return RedirectResponse(url="/visual-lab", status_code=303)


@router.get("/visual-lab/{run_id}/preview", response_class=HTMLResponse)
async def visual_lab_preview(request: Request, run_id: int):
    run = get_visual_lab_run(run_id)
    if not run:
        raise HTTPException(404)
    inline_url = None
    download_url = f"/visual-lab/{run_id}/file"
    if run.kind == "png" or run.kind == "preview":
        inline_url = f"/visual-lab/{run_id}/file?disposition=inline"
    elif run.kind == "pptx" and run.preview_path:
        inline_url = f"/visual-lab/{run_id}/thumb?disposition=inline"
    return templates.TemplateResponse(
        "visual_lab/_preview.html",
        {
            "request": request,
            "run": run,
            "inline_url": inline_url,
            "download_url": download_url,
            "is_image": run.kind in ("png", "preview"),
            "is_pptx": run.kind == "pptx",
            "thumb_url": f"/visual-lab/{run_id}/thumb" if run.preview_path else None,
        },
    )


@router.get("/visual-lab/{run_id}/file")
async def visual_lab_file(run_id: int, disposition: str = "attachment"):
    run = get_visual_lab_run(run_id)
    if not run:
        raise HTTPException(404)
    path = visual_lab_dir() / Path(run.file_path).name
    if not path.exists():
        raise HTTPException(404)
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    disp = "inline" if disposition == "inline" and run.kind in ("png", "preview") else "attachment"
    resp = FileResponse(path, media_type=ctype, content_disposition_type=disp)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@router.get("/visual-lab/{run_id}/thumb")
async def visual_lab_thumb(run_id: int, disposition: str = "inline"):
    run = get_visual_lab_run(run_id)
    if not run or not run.preview_path:
        raise HTTPException(404)
    path = visual_lab_dir() / Path(run.preview_path).name
    if not path.exists():
        raise HTTPException(404)
    disp = "inline" if disposition == "inline" else "attachment"
    resp = FileResponse(path, media_type="image/png", content_disposition_type=disp)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
