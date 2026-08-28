"""FastAPI routes — Projektideen (autonome KI-Vorbewertung, kein Projektbezug)."""
from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from backend.app.jinja_env import templates

from src.m01_config import get_settings, load_user_settings
from src.m14_auth import get_user_id, get_username_by_id, is_super_user, session_username
from src.m08_llm import have_key
from src.m03_db import get_session
from src.m16_idea import (
    ALLOWED_IMAGE_EXT,
    IDEA_ASSESS_TASKS,
    ProjectIdea,
    assess_project_idea_with_ai,
    append_source_attachments,
    create_idea,
    get_idea,
    idea_source_attachments_dir,
    list_ideas,
    list_source_attachment_views,
    remove_source_attachment,
    soft_delete_idea,
    save_user_assessment,
    reset_user_assessment_from_ai,
    form_defaults_from_idea,
    ai_defaults_from_idea,
)
from src.m16_idea_jobs import (
    consume_done_job,
    enqueue as enqueue_idea_job,
    idea_job_status,
)
from src.m17_visual_lab_refs import (
    MAX_ATTACHMENTS,
    merge_bundles,
    parse_task_selection,
    process_upload_bytes,
    SOURCE_PROCESS_TASKS,
)
from src.m16_idea_visual import (
    DEFAULT_OPENAI_IMAGE_MODEL,
    IDEA_VISUAL_OUTPUT_FORMATS,
    OPENAI_IMAGE_MODELS,
    generate_cloud_illustration,
    generate_idea_visual,
    generate_portfolio_deck,
    idea_assess_provider_defaults,
    idea_decks_dir,
    idea_docx_dir,
    idea_html_dir,
    idea_images_dir,
    resolve_visual_llm,
    validate_assess_cloud_gates,
    visual_text_models_map,
    visual_vision_models_map,
    visual_text_providers_available,
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


def _require_may_edit(request: Request, idea) -> None:
    who = _username(request)
    user_id = get_user_id(who) if who else None
    if not _may_edit_idea(idea, user_id, who):
        raise HTTPException(403, "Keine Berechtigung")


def _idea_images_dir() -> Path:
    return idea_images_dir()


async def _ingest_attachment_uploads(idea_id: int, attachments: list[UploadFile]) -> str | None:
    """Speichert neue Unterlagen. Gibt Fehlercode oder None zurück."""
    named = [f for f in attachments if f and f.filename]
    if not named:
        return None
    att_dir = idea_source_attachments_dir()
    bundles = []
    for f in named:
        data = await f.read()
        err, bundle = process_upload_bytes(f.filename, data, att_dir)
        if err:
            return err
        if bundle:
            bundles.append(bundle)
    if bundles:
        return append_source_attachments(idea_id, bundles)
    return None


@router.get("/idea", response_class=HTMLResponse)
async def idea_list(request: Request):
    who = _username(request)
    ideas = list_ideas()
    qp = request.query_params
    return templates.TemplateResponse(
        "idea/index.html",
        {
            "request": request,
            "active_page": "idea",
            "ideas": ideas,
            "submitters": {i.id: (get_username_by_id(i.submitted_by) if i.submitted_by else None) for i in ideas},
            "username": who,
            "error": qp.get("error"),
            "form_title": qp.get("title", ""),
            "form_idea_text": qp.get("idea_text", ""),
            "form_fachabteilung": qp.get("fachabteilung", ""),
            "max_attachments": MAX_ATTACHMENTS,
        },
    )


@router.post("/idea", response_class=HTMLResponse)
async def idea_create(
    request: Request,
    title: str = Form(""),
    idea_text: str = Form(""),
    fachabteilung: str = Form(""),
    internal_pt_human: str = Form(""),
    external_cost_human: str = Form(""),
    attachments: list[UploadFile] = File(default=[]),
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

    def _form_redirect(error: str) -> HTMLResponse:
        from urllib.parse import urlencode

        q = urlencode({
            "error": error,
            "title": title,
            "idea_text": idea_text,
            "fachabteilung": fachabteilung,
        })
        return RedirectResponse(url=f"/idea?{q}", status_code=303)

    if not (idea_text or "").strip():
        return _form_redirect("empty_text")

    named = [f for f in attachments if f.filename]
    if len(named) > MAX_ATTACHMENTS:
        return _form_redirect("too_many_files")

    import json

    att_dir = idea_source_attachments_dir()
    bundles = []
    for f in attachments:
        if not f.filename:
            continue
        data = await f.read()
        err, bundle = process_upload_bytes(f.filename, data, att_dir)
        if err:
            return _form_redirect(err)
        if bundle:
            bundles.append(bundle)
    ref_bundle = merge_bundles(bundles) if bundles else None

    image_path = None
    if ref_bundle and ref_bundle.images:
        data, mime, name = ref_bundle.images[0]
        ext = Path(name).suffix.lower() if Path(name).suffix else ".png"
        if ext in ALLOWED_IMAGE_EXT:
            fname = f"{uuid.uuid4().hex}{ext}"
            (_idea_images_dir() / fname).write_bytes(data)
            image_path = fname

    obj = create_idea(
        idea_text=idea_text,
        title=title,
        fachabteilung=fachabteilung,
        internal_pt_human=_f(internal_pt_human),
        external_cost_human=_f(external_cost_human),
        image_path=image_path,
        source_attachments_json=json.dumps(ref_bundle.stored, ensure_ascii=False) if ref_bundle and ref_bundle.stored else None,
        source_reference_text=ref_bundle.merged_text() if ref_bundle else None,
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
    source_attachments: list = list_source_attachment_views(idea)
    assess_defaults = idea_assess_provider_defaults(settings)
    ai_def = ai_defaults_from_idea(idea)
    ki_job = consume_done_job(idea_id)
    just_done = (ki_job or {}).get("status") if ki_job else None
    if just_done == "done":
        idea = get_idea(idea_id) or idea
        ai_def = ai_defaults_from_idea(idea)
        done_kind = (ki_job or {}).get("kind")
        ki_job = None
    else:
        done_kind = None
    return templates.TemplateResponse(
        "idea/detail.html",
        {
            "request": request,
            "active_page": "idea",
            "idea": idea,
            "challenges": idea.challenges,
            "phases": idea.phases,
            "user_defaults": form_defaults_from_idea(idea),
            "ai_defaults": ai_def,
            "ai_defaults_json": json.dumps(ai_def, ensure_ascii=False).replace("<", "\\u003c"),
            "user_ok": qp.get("user_ok"),
            "submitter": get_username_by_id(idea.submitted_by) if idea.submitted_by else None,
            "may_edit": may_edit,
            "assess_error": qp.get("assess_error"),
            "deck_error": qp.get("deck_error"),
            "illustration_error": qp.get("illustration_error"),
            "visual_error": qp.get("visual_error"),
            "visual_ok": qp.get("visual_ok") or ("1" if done_kind == "visual" else None),
            "assess_ok": qp.get("assess_ok") or ("1" if done_kind == "assess" else None),
            "llm_provider": settings.get("provider", "openai"),
            "llm_model": settings.get("model", ""),
            "default_assess_provider": assess_defaults[0],
            "default_assess_model": assess_defaults[1],
            "ollama_ok": have_key("ollama"),
            "openai_image_models": OPENAI_IMAGE_MODELS,
            "default_image_model": DEFAULT_OPENAI_IMAGE_MODEL,
            "openai_key_ok": have_key("openai"),
            "visual_llm_providers": visual_text_providers_available(),
            "visual_llm_models": visual_text_models_map(),
            "visual_vision_models": visual_vision_models_map(),
            "idea_visual_formats": IDEA_VISUAL_OUTPUT_FORMATS,
            "source_attachments": source_attachments,
            "source_process_tasks": SOURCE_PROCESS_TASKS,
            "assess_tasks": IDEA_ASSESS_TASKS,
            "attach_error": qp.get("attach_error"),
            "max_attachments": MAX_ATTACHMENTS,
            "html_path": getattr(idea, "html_path", None),
            "ki_job": ki_job,
            "user_reset_ok": qp.get("user_reset_ok"),
        },
    )


@router.post("/idea/{idea_id}/assess", response_class=HTMLResponse)
async def idea_assess(
    request: Request,
    idea_id: int,
    provider: str = Form("openai"),
    model: str = Form(""),
    input_llm_provider: str = Form(""),
    input_llm_model: str = Form(""),
    visual_llm_provider: str = Form(""),
    visual_llm_model: str = Form(""),
    source_tasks: list[str] = Form(default=[]),
    assess_tasks: list[str] = Form(default=[]),
    cloud_confirm: str = Form(""),
    vision_cloud_confirm: str = Form(""),
    overwrite_user: str = Form(""),
    attachments: list[UploadFile] = File(default=[]),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    _require_may_edit(request, idea)
    ingest_err = await _ingest_attachment_uploads(idea_id, attachments)
    if ingest_err:
        return RedirectResponse(url=f"/idea/{idea_id}?attach_error={ingest_err}#idea-attachments", status_code=303)
    idea = get_idea(idea_id) or idea
    settings = load_user_settings()
    dp, dm = idea_assess_provider_defaults(settings)
    ip, im = resolve_visual_llm(input_llm_provider, input_llm_model, dp, dm)
    ap, am = resolve_visual_llm(visual_llm_provider, visual_llm_model, provider or dp, model or dm)
    src = parse_task_selection(source_tasks, SOURCE_PROCESS_TASKS)
    at = parse_task_selection(assess_tasks, IDEA_ASSESS_TASKS)
    gate_err = validate_assess_cloud_gates(
        idea,
        ap,
        am,
        ip,
        src,
        cloud_confirm == "1",
        vision_cloud_confirm == "1",
    )
    if gate_err:
        return RedirectResponse(url=f"/idea/{idea_id}?assess_error={gate_err}", status_code=303)
    replace_user = overwrite_user == "1" and bool(idea.user_assessed_at)

    def _run():
        return assess_project_idea_with_ai(
            idea_id,
            provider=ap,
            model=am,
            assess_tasks=at,
            source_tasks=src,
            input_provider=ip,
            input_model=im,
        )

    job = enqueue_idea_job(
        kind="assess",
        idea_id=idea_id,
        run=_run,
        provider=ap,
        model=am,
        overwrite_user=replace_user,
    )
    if job.get("already_running"):
        return RedirectResponse(url=f"/idea/{idea_id}?assess_error=already_running", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}?job=1#idea-job-banner", status_code=303)


def _opt_float_field(v: str | None) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v.replace("'", "").replace(",", "."))
    except ValueError:
        return None


def _indexed_challenges(form) -> list[dict]:
    out = []
    for i in range(12):
        title = str(form.get(f"ch_title_{i}") or "").strip()
        desc = str(form.get(f"ch_desc_{i}") or "").strip()
        if not title and not desc:
            continue
        out.append({
            "title": title or f"Risiko {len(out) + 1}",
            "description": desc,
            "severity": str(form.get(f"ch_severity_{i}") or "mittel"),
            "likelihood": str(form.get(f"ch_likelihood_{i}") or "mittel"),
        })
    return out


def _indexed_phases(form) -> list[dict]:
    out = []
    for i in range(12):
        name = str(form.get(f"ph_name_{i}") or "").strip()
        desc = str(form.get(f"ph_desc_{i}") or "").strip()
        if not name and not desc:
            continue
        out.append({
            "name": name or f"Phase {len(out) + 1}",
            "description": desc,
            "duration_estimate": str(form.get(f"ph_duration_{i}") or "").strip(),
            "internal_pt": _opt_float_field(str(form.get(f"ph_pt_{i}") or "")),
        })
    return out


@router.post("/idea/{idea_id}/save-user-assessment", response_class=HTMLResponse)
async def idea_save_user_assessment(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    _require_may_edit(request, idea)
    form = await request.form()
    save_user_assessment(
        idea_id,
        summary=str(form.get("user_summary") or ""),
        internal_pt=_opt_float_field(str(form.get("user_internal_pt") or "")),
        internal_pt_reasoning=str(form.get("user_internal_pt_reasoning") or ""),
        external_cost=_opt_float_field(str(form.get("user_external_cost") or "")),
        external_cost_reasoning=str(form.get("user_external_cost_reasoning") or ""),
        challenges=_indexed_challenges(form),
        phases=_indexed_phases(form),
        recommendation=str(form.get("user_recommendation") or ""),
    )
    return RedirectResponse(url=f"/idea/{idea_id}?user_ok=1#idea-assessment", status_code=303)


@router.post("/idea/{idea_id}/reset-user-assessment", response_class=HTMLResponse)
async def idea_reset_user_assessment(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    _require_may_edit(request, idea)
    if not idea.ai_assessed_at:
        return RedirectResponse(url=f"/idea/{idea_id}?assess_error=not_assessed#idea-assessment", status_code=303)
    if not reset_user_assessment_from_ai(idea_id):
        return RedirectResponse(url=f"/idea/{idea_id}?assess_error=1#idea-assessment", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}?user_reset_ok=1#idea-assessment", status_code=303)


@router.get("/idea/{idea_id}/job-status")
async def idea_job_status_endpoint(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    return JSONResponse(idea_job_status(idea_id))


@router.post("/idea/{idea_id}/add-source-attachments", response_class=HTMLResponse)
async def idea_add_source_attachments(
    request: Request,
    idea_id: int,
    attachments: list[UploadFile] = File(default=[]),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    _require_may_edit(request, idea)
    ingest_err = await _ingest_attachment_uploads(idea_id, attachments)
    if ingest_err:
        return RedirectResponse(url=f"/idea/{idea_id}?attach_error={ingest_err}#idea-attachments", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}#idea-attachments", status_code=303)


@router.post("/idea/{idea_id}/delete-source-attachment", response_class=HTMLResponse)
async def idea_delete_source_attachment(
    request: Request,
    idea_id: int,
    att_path: str = Form(...),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    _require_may_edit(request, idea)
    if not remove_source_attachment(idea_id, att_path):
        return RedirectResponse(url=f"/idea/{idea_id}?attach_error=not_found#idea-attachments", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}#idea-attachments", status_code=303)


@router.post("/idea/{idea_id}/generate-visual", response_class=HTMLResponse)
async def idea_generate_visual(
    request: Request,
    idea_id: int,
    output_format: str = Form(...),
    refinement_notes: str = Form(""),
    llm_provider: str = Form("openai"),
    llm_model: str = Form(""),
    visual_llm_provider: str = Form(""),
    visual_llm_model: str = Form(""),
    input_llm_provider: str = Form(""),
    input_llm_model: str = Form(""),
    source_tasks: list[str] = Form(default=[]),
    image_model: str = Form(DEFAULT_OPENAI_IMAGE_MODEL),
    cloud_confirm: str = Form(""),
    vision_cloud_confirm: str = Form(""),
    attachments: list[UploadFile] = File(default=[]),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    _require_may_edit(request, idea)
    ingest_err = await _ingest_attachment_uploads(idea_id, attachments)
    if ingest_err:
        return RedirectResponse(url=f"/idea/{idea_id}?attach_error={ingest_err}#idea-attachments", status_code=303)
    idea = get_idea(idea_id) or idea
    if idea.status != "bewertet":
        return RedirectResponse(url=f"/idea/{idea_id}?visual_error=not_assessed", status_code=303)
    fmt = (output_format or "").strip().lower()
    if fmt == "png_cloud" and cloud_confirm != "1":
        return RedirectResponse(url=f"/idea/{idea_id}?visual_error=cloud_confirm", status_code=303)
    vp, vm = resolve_visual_llm(visual_llm_provider, visual_llm_model, llm_provider, llm_model)
    ip, im = resolve_visual_llm(input_llm_provider, input_llm_model, llm_provider, llm_model)
    src = parse_task_selection(source_tasks, SOURCE_PROCESS_TASKS)
    gate_err = validate_assess_cloud_gates(
        idea,
        vp,
        vm,
        ip,
        src,
        cloud_confirm == "1",
        vision_cloud_confirm == "1",
    )
    if gate_err:
        return RedirectResponse(url=f"/idea/{idea_id}?visual_error={gate_err}", status_code=303)
    if image_model not in OPENAI_IMAGE_MODELS:
        image_model = DEFAULT_OPENAI_IMAGE_MODEL

    def _run():
        return generate_idea_visual(
            idea_id,
            output_format=output_format,
            refinement_notes=refinement_notes,
            llm_provider=vp,
            llm_model=vm,
            image_model=image_model,
            input_llm_provider=ip,
            input_llm_model=im,
            source_tasks=src,
        )

    job = enqueue_idea_job(
        kind="visual",
        idea_id=idea_id,
        run=_run,
        provider=vp,
        model=vm,
    )
    if job.get("already_running"):
        return RedirectResponse(url=f"/idea/{idea_id}?visual_error=already_running", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}?job=1#idea-job-banner", status_code=303)


@router.post("/idea/{idea_id}/generate-deck", response_class=HTMLResponse)
async def idea_generate_deck(
    request: Request,
    idea_id: int,
    refinement_notes: str = Form(""),
    llm_provider: str = Form("openai"),
    llm_model: str = Form(""),
    visual_llm_provider: str = Form(""),
    visual_llm_model: str = Form(""),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    _require_may_edit(request, idea)
    if idea.status != "bewertet":
        return RedirectResponse(url=f"/idea/{idea_id}?deck_error=1", status_code=303)
    vp, vm = resolve_visual_llm(visual_llm_provider, visual_llm_model, llm_provider, llm_model)
    result = generate_portfolio_deck(
        idea_id,
        refinement_notes=refinement_notes,
        llm_provider=vp,
        llm_model=vm,
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
    visual_llm_provider: str = Form(""),
    visual_llm_model: str = Form(""),
    cloud_confirm: str = Form(""),
):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404, "Idee nicht gefunden")
    _require_may_edit(request, idea)
    if not have_key("openai"):
        return RedirectResponse(url=f"/idea/{idea_id}?illustration_error=no_key", status_code=303)
    if idea.status != "bewertet":
        return RedirectResponse(url=f"/idea/{idea_id}?illustration_error=not_assessed", status_code=303)
    if cloud_confirm != "1":
        return RedirectResponse(url=f"/idea/{idea_id}?illustration_error=cloud_confirm", status_code=303)
    if image_model not in OPENAI_IMAGE_MODELS:
        image_model = DEFAULT_OPENAI_IMAGE_MODEL
    vp, vm = resolve_visual_llm(visual_llm_provider, visual_llm_model, llm_provider, llm_model)
    result = generate_cloud_illustration(
        idea_id,
        image_model=image_model,
        llm_provider=vp,
        llm_model=vm,
        refinement_notes=refinement_notes,
    )
    if not result:
        return RedirectResponse(url=f"/idea/{idea_id}?illustration_error=1", status_code=303)
    return RedirectResponse(url=f"/idea/{idea_id}", status_code=303)


@router.post("/idea/{idea_id}/clear-deck", response_class=HTMLResponse)
async def idea_clear_deck(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404)
    who = _username(request)
    user_id = get_user_id(who) if who else None
    if not _may_edit_idea(idea, user_id, who):
        raise HTTPException(403)
    if idea.deck_path:
        p = idea_decks_dir() / Path(idea.deck_path).name
        if p.exists():
            p.unlink()
    if idea.deck_preview_path:
        p = _idea_images_dir() / Path(idea.deck_preview_path).name
        if p.exists():
            p.unlink()
    if idea.html_path:
        hp = idea_html_dir() / Path(idea.html_path).name
        if hp.exists():
            hp.unlink()
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if obj:
            obj.deck_path = None
            obj.deck_preview_path = None
            obj.deck_generated_at = None
            obj.html_path = None
            obj.html_generated_at = None
            ses.add(obj)
            ses.commit()
    return RedirectResponse(url=f"/idea/{idea_id}", status_code=303)


@router.post("/idea/{idea_id}/clear-illustration", response_class=HTMLResponse)
async def idea_clear_illustration(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404)
    who = _username(request)
    user_id = get_user_id(who) if who else None
    if not _may_edit_idea(idea, user_id, who):
        raise HTTPException(403)
    if idea.image_source not in ("dalle", "diagram"):
        return RedirectResponse(url=f"/idea/{idea_id}", status_code=303)
    if idea.image_path:
        p = _idea_images_dir() / Path(idea.image_path).name
        if p.exists():
            p.unlink()
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if obj:
            obj.image_path = None
            obj.image_source = None
            obj.illustration_model = None
            obj.illustration_prompt_safe = None
            obj.illustration_generated_at = None
            ses.add(obj)
            ses.commit()
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
    if not idea or not (idea.deck_path or idea.deck_preview_path or idea.html_path):
        raise HTTPException(404)
    return templates.TemplateResponse(
        "idea/_preview_deck.html",
        {"request": request, "idea": idea},
    )


@router.get("/idea/{idea_id}/preview-html", response_class=HTMLResponse)
async def idea_preview_html(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or not getattr(idea, "html_path", None):
        raise HTTPException(404, "Kein HTML-Bericht")
    return templates.TemplateResponse(
        "idea/_preview_html.html",
        {"request": request, "idea": idea},
    )


@router.get("/idea/{idea_id}/preview-source/{att_name}", response_class=HTMLResponse)
async def idea_preview_source(request: Request, idea_id: int, att_name: str):
    idea = get_idea(idea_id)
    if not idea:
        raise HTTPException(404)
    safe = Path(att_name).name
    views = [a for a in list_source_attachment_views(idea) if a["path"] == safe]
    if not views:
        raise HTTPException(404)
    att = views[0]
    preview_text = ""
    if att["preview_kind"] in {"text", "docx"}:
        from src.m17_visual_lab_refs import load_bundle_from_stored

        bundle = load_bundle_from_stored([{"path": att["path"], "kind": att["kind"], "original_name": att["original_name"]}], idea_source_attachments_dir())
        preview_text = (bundle.merged_text() if bundle else "")[:12000]
    return templates.TemplateResponse(
        "idea/_preview_source.html",
        {"request": request, "idea": idea, "att": att, "preview_text": preview_text},
    )


@router.get("/idea/{idea_id}/source-file/{att_name}")
async def idea_source_file(idea_id: int, att_name: str, disposition: str = "attachment"):
    idea = get_idea(idea_id)
    if not idea:
        raise HTTPException(404)
    safe = Path(att_name).name
    views = [a for a in list_source_attachment_views(idea) if a["path"] == safe]
    if not views or not views[0]["exists"]:
        raise HTTPException(404)
    path = idea_source_attachments_dir() / safe
    if not path.exists():
        raise HTTPException(404)
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    disp = "inline" if disposition == "inline" else "attachment"
    extra = {"filename": views[0]["original_name"]} if disp == "attachment" else {}
    resp = FileResponse(path, media_type=ctype, content_disposition_type=disp, **extra)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


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


@router.get("/idea/report/{idea_id}")
async def idea_html_report(idea_id: int, disposition: str = "inline"):
    idea = get_idea(idea_id)
    html_name = getattr(idea, "html_path", None) if idea else None
    if not idea or not html_name:
        raise HTTPException(404, "Kein HTML-Bericht")
    path = idea_html_dir() / Path(html_name).name
    if not path.exists():
        raise HTTPException(404, "Kein HTML-Bericht")
    title = (idea.ai_project_name or idea.title or f"Projektidee_{idea_id}").replace("/", "-")
    disp = "inline" if disposition == "inline" else "attachment"
    resp = FileResponse(
        path,
        media_type="text/html",
        filename=f"{title[:60]}.html",
        content_disposition_type=disp,
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@router.get("/idea/docx/{idea_id}")
async def idea_docx_download(idea_id: int):
    idea = get_idea(idea_id)
    if not idea or not idea.docx_path:
        raise HTTPException(404, "Kein Word-Bericht")
    path = idea_docx_dir() / Path(idea.docx_path).name
    if not path.exists():
        raise HTTPException(404, "Kein Word-Bericht")
    title = (idea.ai_project_name or idea.title or f"Projektidee_{idea_id}").replace("/", "-")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{title[:60]}.docx",
    )


@router.post("/idea/{idea_id}/clear-docx", response_class=HTMLResponse)
async def idea_clear_docx(request: Request, idea_id: int):
    idea = get_idea(idea_id)
    if not idea or idea.is_deleted:
        raise HTTPException(404)
    who = _username(request)
    user_id = get_user_id(who) if who else None
    if not _may_edit_idea(idea, user_id, who):
        raise HTTPException(403)
    if idea.docx_path:
        p = idea_docx_dir() / Path(idea.docx_path).name
        if p.exists():
            p.unlink()
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if obj:
            obj.docx_path = None
            obj.docx_generated_at = None
            ses.add(obj)
            ses.commit()
    return RedirectResponse(url=f"/idea/{idea_id}", status_code=303)
