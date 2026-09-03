"""FastAPI routes — Phase C Offertbeurteilung."""
from __future__ import annotations

import asyncio
import csv
import io
import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from backend.app.jinja_env import templates

from src.m07_projects import list_projects_df
from src.m09_docs import get_project_documents, ingest_document, calculate_sha256, get_document_by_sha256, link_document_to_project
from src.m14_auth import (
    can_evaluate,
    can_view_evaluator_details,
    get_user_id,
    is_super_user,
    session_username,
)
from src.m15_evaluation import (
    ANGEbot_CLASSIFICATION,
    ANGEbot_SUBTYPES,
    CRITERION_KINDS,
    TENDER_ROLE_LABELS,
    TENDER_ROLES,
    compute_bidder_tco,
    compute_rankings,
    create_bidder,
    create_criterion,
    delete_price_item,
    extract_criteria_from_tender_docs,
    extract_price_from_bidder_doc,
    extract_price_structure_from_tender,
    get_bidder_document_ids,
    get_bidder_preisblatt_doc_ids,
    get_score,
    get_tender_document_ids,
    import_criteria_payload,
    link_document_to_bidder,
    link_tender_doc,
    list_bidders,
    list_criteria,
    list_price_items,
    list_scores_for_cell,
    list_scores_for_project,
    list_tender_docs,
    merge_price_structure_for_bidder,
    official_score,
    rolled_up_score,
    soft_delete_bidder,
    soft_delete_criterion,
    seed_price_structure_for_bidder,
    suggest_score_with_rag,
    sync_price_criterion_scores,
    unlink_document_from_bidder,
    unlink_tender_doc,
    upsert_price_item,
    upsert_score,
    validate_evaluation_cloud_gate,
    validate_tender_cloud_gate,
)
from src.m01_config import load_user_settings

router = APIRouter()


def _username(request: Request) -> str:
    from src.m14_auth import validate_session_token
    from src.m01_config import get_settings

    token = request.cookies.get("_auth_token", "")
    s = get_settings()
    if validate_session_token(token, max_age_seconds=s.auth_session_timeout_minutes * 60):
        return session_username(token) or ""
    return ""


def _projects_list() -> list[dict]:
    df = list_projects_df()
    if df is None or df.empty:
        return []
    return [{"key": row["Key"], "title": row["Titel"]} for _, row in df.iterrows()]


def _project_title(project_key: str) -> str:
    for p in _projects_list():
        if p["key"] == project_key:
            return p["title"]
    return project_key


def _llm_picker_context() -> dict:
    from src.m16_idea_visual import (
        visual_text_models_map,
        visual_text_providers_available,
        visual_vision_models_map,
    )

    settings = load_user_settings()
    return {
        "llm_provider": settings.get("provider", "openai"),
        "llm_model": settings.get("model", ""),
        "visual_llm_providers": visual_text_providers_available(),
        "visual_llm_models": visual_text_models_map(),
        "visual_vision_models": visual_vision_models_map(),
        "form_visual_provider": "",
        "form_visual_model": "",
        "form_input_provider": "",
        "form_input_model": "",
    }


@router.get("/evaluation", response_class=HTMLResponse)
async def evaluation_page(request: Request, project_key: str = ""):
    projects = _projects_list()
    if not project_key and projects:
        project_key = projects[0]["key"]

    bidders = list_bidders(project_key) if project_key else []
    criteria = list_criteria(project_key) if project_key else []
    scores = list_scores_for_project(project_key) if project_key else []
    scores_by_cell: dict[tuple[int, int], list] = {}
    for s in scores:
        scores_by_cell.setdefault((s.bidder_id, s.criterion_id), []).append(s)
    rankings = compute_rankings(project_key) if project_key else []

    # Nur Top-Level-Kriterien in der Matrix - Unterfragen (parent_id gesetzt) sind
    # Beleg-/KI-Hilfsebene und werden über die Zell-Details der Elternzeile erreicht.
    top_criteria = [c for c in criteria if c.parent_id is None]
    has_children = {c.parent_id for c in criteria if c.parent_id}
    matrix_rows = []
    for crit in top_criteria:
        row = {
            "id": crit.id,
            "name": crit.name,
            "kind": crit.kind,
            "auto_price": crit.auto_price,
            "has_children": crit.id in has_children,
            "weight": crit.weight_pct if crit.kind == "zuschlag" else None,
        }
        crit_children = [c for c in criteria if c.parent_id == crit.id]
        cells = []
        for bidder in bidders:
            cell_scores = scores_by_cell.get((bidder.id, crit.id), [])
            ai_row = next((s for s in cell_scores if s.source_key == "ai"), None)
            user_rows = [s for s in cell_scores if s.source_key.startswith("user:")]
            if crit.kind == "zuschlag":
                official, answered, total = rolled_up_score(bidder.id, crit, criteria, scores_by_cell)
                display = f"{official:.2f}" if official is not None else "—"
            elif crit_children:
                child_vals = [official_score(bidder.id, ch, scores_by_cell.get((bidder.id, ch.id), [])) for ch in crit_children]
                answered = sum(1 for v in child_vals if v is not None)
                total = len(crit_children)
                official = None
                if answered == total and total:
                    official = 0.0 if any(v == 0 for v in child_vals) else 1.0
                display = "K.O." if official == 0.0 else ("erfüllt" if official == 1.0 else f"{answered}/{total}")
            else:
                official = official_score(bidder.id, crit, cell_scores)
                answered, total = (1, 1) if official is not None else (0, 1)
                display = ("Ja" if official == 1 else "Nein") if official is not None else "—"
            cells.append(
                {
                    "bidder_id": bidder.id,
                    "criterion_id": crit.id,
                    "official": official,
                    "display": display,
                    "ai_value": ai_row.value if ai_row else None,
                    "evaluator_count": len(user_rows),
                    "answered": answered,
                    "total": total,
                }
            )
        row["cells"] = cells
        matrix_rows.append(row)

    offer_docs = []
    bidder_docs: dict[int, list[int]] = {}
    tender_doc_roles: dict[int, str] = {}
    project_source_docs = []
    if project_key:
        all_project_docs = get_project_documents(project_key)
        offer_docs = [
            d for d in all_project_docs
            if d.classification == ANGEbot_CLASSIFICATION
        ]
        project_source_docs = [
            d for d in all_project_docs
            if d.classification != ANGEbot_CLASSIFICATION
        ]
        for row in list_tender_docs(project_key):
            tender_doc_roles[row.document_id] = row.tender_role
        for b in bidders:
            bidder_docs[b.id] = get_bidder_document_ids(b.id)

    who = _username(request)
    user_id = get_user_id(who) if who else None
    ctx = {
        "request": request,
        "active_page": "evaluation",
        "projects": projects,
        "project_key": project_key,
        "project_title": _project_title(project_key) if project_key else "",
        "bidders": bidders,
        "criteria": criteria,
        "top_criteria": top_criteria,
        "rankings": rankings,
        "matrix_rows": matrix_rows,
        "offer_docs": offer_docs,
        "project_source_docs": project_source_docs,
        "tender_doc_roles": tender_doc_roles,
        "tender_roles": TENDER_ROLES,
        "tender_role_labels": TENDER_ROLE_LABELS,
        "has_tender_docs": bool(get_tender_document_ids(project_key)) if project_key else False,
        "bidder_docs": bidder_docs,
        "criterion_kinds": CRITERION_KINDS,
        "angebot_class": ANGEbot_CLASSIFICATION,
        "angebot_subtypes": ANGEbot_SUBTYPES,
        "may_evaluate": can_evaluate(who),
        "may_see_evaluators": can_view_evaluator_details(who),
        "super_user": is_super_user(who),
        "user_id": user_id,
        "error": None,
        "message": None,
        "doc_upload": request.query_params.get("doc_upload"),
    }
    ctx.update(_llm_picker_context())
    return templates.TemplateResponse("evaluation/index.html", ctx)


@router.post("/evaluation/bidder", response_class=HTMLResponse)
async def evaluation_create_bidder(request: Request, project_key: str = Form(...), name: str = Form(...)):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    create_bidder(project_key, name)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.post("/evaluation/criterion", response_class=HTMLResponse)
async def evaluation_create_criterion(
    request: Request,
    project_key: str = Form(...),
    name: str = Form(...),
    kind: str = Form(...),
    weight_pct: float = Form(0.0),
    scale_max: int = Form(10),
    parent_id: str = Form(""),
    auto_price: str = Form("false"),
    description: str = Form(""),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    create_criterion(
        project_key,
        kind,
        name,
        weight_pct=weight_pct,
        scale_max=scale_max,
        parent_id=int(parent_id) if parent_id.strip() else None,
        auto_price=auto_price in ("true", "on", "1", "yes"),
        description=description or None,
    )
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.post("/evaluation/score", response_class=HTMLResponse)
async def evaluation_save_score(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    criterion_id: int = Form(...),
    value: float = Form(...),
    justification: str = Form(""),
    source_chunk_ref: str = Form(""),
    target_user_id: str = Form(""),
):
    who = _username(request)
    if not can_evaluate(who):
        raise HTTPException(403, "Keine Berechtigung")
    uid = get_user_id(who)
    if not uid:
        raise HTTPException(401, "Nicht angemeldet")
    # Standard: eigene Zeile. Nur Super-User darf eine fremde (target_user_id) korrigieren.
    write_uid = uid
    if target_user_id.strip():
        if not is_super_user(who):
            raise HTTPException(403, "Nur Super-User dürfen fremde Bewertungen ändern")
        write_uid = int(target_user_id)
    upsert_score(
        bidder_id,
        criterion_id,
        write_uid,
        value,
        justification=justification or None,
        source_chunk_ref=source_chunk_ref or None,
    )
    if request.headers.get("hx-request"):
        return await evaluation_cell(request, bidder_id=bidder_id, criterion_id=criterion_id, project_key=project_key)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.post("/evaluation/score/ai", response_class=HTMLResponse)
async def evaluation_save_ai_score(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    criterion_id: int = Form(...),
    value: float = Form(...),
    justification: str = Form(""),
    source_chunk_ref: str = Form(""),
):
    """Speichert einen KI-Vorschlag als eigene, klar markierte Spalte (source='ai') -
    NICHT als Bewertung einer Person. Getrennt von /evaluation/score."""
    who = _username(request)
    if not can_evaluate(who):
        raise HTTPException(403, "Keine Berechtigung")
    upsert_score(
        bidder_id,
        criterion_id,
        evaluator_user_id=0,
        value=value,
        justification=justification or None,
        source_chunk_ref=source_chunk_ref or None,
        as_source="ai",
    )
    if request.headers.get("hx-request"):
        return await evaluation_cell(request, bidder_id=bidder_id, criterion_id=criterion_id, project_key=project_key)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.get("/evaluation/cell", response_class=HTMLResponse)
async def evaluation_cell(request: Request, bidder_id: int, criterion_id: int, project_key: str = ""):
    """Detail-Panel einer Matrix-Zelle: KI-Vorschlag + jede Bewerter-Zeile einzeln,
    plus Formular fuer die eigene Bewertung. Das ist die 'mehrere Spalten'-Ansicht."""
    from src.m15_evaluation import Criterion, Bidder
    from src.m03_db import get_session

    who = _username(request)
    with get_session() as session:
        crit = session.get(Criterion, criterion_id)
        bidder = session.get(Bidder, bidder_id)
    if not crit or not bidder:
        raise HTTPException(404, "Nicht gefunden")

    all_criteria = list_criteria(project_key) if project_key else []
    children = [c for c in all_criteria if c.parent_id == crit.id]

    if children:
        # Elternkriterium mit Einzelanforderungen: gemaess Ausschreibungsvorgabe
        # ("Punkte = erreichte Punktzahl / Anzahl der Einzelanforderungen") wird
        # NICHT das Elternkriterium direkt bewertet, sondern jede Anforderung
        # einzeln - der Mittelwert ergibt automatisch den Elternwert.
        scores_by_cell: dict[tuple[int, int], list] = {}
        for s in list_scores_for_project(project_key):
            scores_by_cell.setdefault((s.bidder_id, s.criterion_id), []).append(s)
        child_rows = []
        for ch in children:
            cell = scores_by_cell.get((bidder_id, ch.id), [])
            ai_row = next((s for s in cell if s.source_key == "ai"), None)
            child_rows.append(
                {
                    "criterion": ch,
                    "official": official_score(bidder_id, ch, cell),
                    "ai_value": ai_row.value if ai_row else None,
                    "evaluator_count": len([s for s in cell if s.source_key.startswith("user:")]),
                }
            )
        rollup_val, answered, total = rolled_up_score(bidder_id, crit, all_criteria, scores_by_cell)
        return templates.TemplateResponse(
            "evaluation/_cell_parent.html",
            {
                "request": request,
                "project_key": project_key,
                "bidder": bidder,
                "criterion": crit,
                "child_rows": child_rows,
                "official": rollup_val,
                "answered": answered,
                "total": total,
                "may_evaluate": can_evaluate(who),
            },
        )

    cell_scores = list_scores_for_cell(bidder_id, criterion_id)
    ai_row = next((s for s in cell_scores if s.source_key == "ai"), None)
    user_rows = [s for s in cell_scores if s.source_key.startswith("user:")]
    uid = get_user_id(who) if who else None
    ctx = {
        "request": request,
        "project_key": project_key,
        "bidder": bidder,
        "criterion": crit,
        "ai_row": ai_row,
        "user_rows": user_rows,
        "official": official_score(bidder_id, crit, cell_scores),
        "may_evaluate": can_evaluate(who),
        "may_see_evaluators": can_view_evaluator_details(who),
        "super_user": is_super_user(who),
        "my_user_id": uid,
        "has_bidder_docs": bool(get_bidder_document_ids(bidder_id)),
    }
    ctx.update(_llm_picker_context())
    return templates.TemplateResponse("evaluation/_cell.html", ctx)


@router.post("/evaluation/suggest", response_class=HTMLResponse)
async def evaluation_suggest(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    criterion_id: int = Form(...),
    provider: str = Form("openai"),
    model: str = Form(""),
    visual_llm_provider: str = Form(""),
    visual_llm_model: str = Form(""),
    cloud_confirm: str = Form(""),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    from src.m15_evaluation import Criterion
    from src.m03_db import get_session
    from src.m16_idea_visual import resolve_visual_llm

    with get_session() as session:
        crit = session.get(Criterion, criterion_id)
    if not crit:
        raise HTTPException(404, "Kriterium nicht gefunden")

    settings = load_user_settings()
    fallback_p = (provider or "").strip() or settings.get("provider", "openai")
    fallback_m = (model or "").strip() or settings.get("model", "")
    ap, am = resolve_visual_llm(visual_llm_provider, visual_llm_model, fallback_p, fallback_m)
    gate_err = validate_evaluation_cloud_gate(
        ap, bidder_id, cloud_confirm in ("1", "true", "on", "yes")
    )
    tpl_ctx = {
        "request": request,
        "suggestion": {"value": None},
        "gate_error": gate_err,
        "cloud_provider": ap,
        "project_key": project_key,
        "bidder_id": bidder_id,
        "criterion_id": criterion_id,
    }
    if gate_err:
        return templates.TemplateResponse("evaluation/_suggestion.html", tpl_ctx, status_code=400)

    suggestion = suggest_score_with_rag(
        project_key,
        bidder_id,
        crit,
        provider=ap,
        model=am or None,
    )
    tpl_ctx["suggestion"] = suggestion
    tpl_ctx["gate_error"] = None
    return templates.TemplateResponse("evaluation/_suggestion.html", tpl_ctx)


@router.post("/evaluation/tender-doc", response_class=HTMLResponse)
async def evaluation_tender_doc(
    request: Request,
    project_key: str = Form(...),
    document_id: int = Form(...),
    tender_role: str = Form(""),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    role = (tender_role or "").strip().lower()
    if role:
        link_tender_doc(project_key, document_id, role)
    else:
        unlink_tender_doc(project_key, document_id)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.post("/evaluation/tender-doc-upload", response_class=HTMLResponse)
async def evaluation_tender_doc_upload(
    request: Request,
    project_key: str = Form(...),
    file: UploadFile = File(...),
    classification: str = Form(""),
    tender_role: str = Form(...),
    chunk_size: int = Form(1000),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    file_bytes = await file.read()
    redirect = f"/evaluation?project_key={project_key}"
    if not file_bytes or not file.filename:
        return RedirectResponse(url=f"{redirect}&tender_upload=error", status_code=303)

    from src.m03_db import DOCUMENT_CLASSIFICATIONS

    cls = (classification or "").strip() or "Sonstiges"
    if cls not in DOCUMENT_CLASSIFICATIONS:
        cls = "Sonstiges"
    role = (tender_role or "").strip().lower()
    if role not in TENDER_ROLES:
        return RedirectResponse(url=f"{redirect}&tender_upload=error", status_code=303)
    try:
        chunk_size = max(200, min(4000, int(chunk_size)))
    except (TypeError, ValueError):
        chunk_size = 1000

    success, _msg = ingest_document(
        file_name=file.filename,
        file_bytes=file_bytes,
        classification=cls,
        chunk_size=chunk_size,
    )
    doc = get_document_by_sha256(calculate_sha256(file_bytes), include_deleted=True)
    if not doc:
        return RedirectResponse(url=f"{redirect}&tender_upload=error", status_code=303)
    link_document_to_project(project_key, doc.id)
    link_tender_doc(project_key, doc.id, role)
    status = "ok" if success else "linked"
    return RedirectResponse(url=f"{redirect}&tender_upload={status}", status_code=303)


@router.post("/evaluation/bidder-doc", response_class=HTMLResponse)
async def evaluation_bidder_doc(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    document_id: int = Form(...),
    linked: str = Form("false"),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    if linked in ("true", "on", "1", "yes"):
        link_document_to_bidder(bidder_id, document_id)
    else:
        unlink_document_from_bidder(bidder_id, document_id)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.post("/evaluation/bidder-doc-upload", response_class=HTMLResponse)
async def evaluation_bidder_doc_upload(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    file: UploadFile = File(...),
    classification: str = Form(""),
    doc_subtype: str = Form(""),
    chunk_size: int = Form(1000),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    file_bytes = await file.read()
    redirect = f"/evaluation?project_key={project_key}"
    if not file_bytes or not file.filename:
        return RedirectResponse(url=f"{redirect}&doc_upload=error", status_code=303)

    cls = (classification or "").strip() or ANGEbot_CLASSIFICATION
    subtype = (doc_subtype or "").strip() or None
    if subtype and subtype not in ANGEbot_SUBTYPES:
        subtype = None
    try:
        chunk_size = max(200, min(4000, int(chunk_size)))
    except (TypeError, ValueError):
        chunk_size = 1000

    success, _msg = ingest_document(
        file_name=file.filename,
        file_bytes=file_bytes,
        classification=cls,
        chunk_size=chunk_size,
        doc_subtype=subtype,
    )
    doc = get_document_by_sha256(calculate_sha256(file_bytes), include_deleted=True)
    if not doc:
        return RedirectResponse(url=f"{redirect}&doc_upload=error", status_code=303)
    link_document_to_project(project_key, doc.id)
    link_document_to_bidder(bidder_id, doc.id)
    status = "ok" if success else "linked"
    return RedirectResponse(url=f"{redirect}&doc_upload={status}", status_code=303)


@router.post("/evaluation/delete-bidder", response_class=HTMLResponse)
async def evaluation_delete_bidder(request: Request, project_key: str = Form(...), bidder_id: int = Form(...)):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    soft_delete_bidder(bidder_id)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.post("/evaluation/extract-criteria", response_class=HTMLResponse)
async def evaluation_extract_criteria(
    request: Request,
    project_key: str = Form(...),
    provider: str = Form(""),
    model: str = Form(""),
    cloud_confirm: str = Form("false"),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    settings = load_user_settings()
    ap = (provider or settings.get("provider") or "openai").strip()
    am = (model or settings.get("model") or "").strip()
    gate = validate_tender_cloud_gate(ap, project_key, cloud_confirm in ("true", "on", "1", "yes"))
    if gate:
        return templates.TemplateResponse(
            "evaluation/_criteria_extract.html",
            {
                "request": request,
                "project_key": project_key,
                "project_title": _project_title(project_key),
                "error": "cloud_confirm",
                "payload": {},
                "criteria_json": "{}",
                "may_evaluate": True,
            },
        )
    result = await asyncio.to_thread(
        extract_criteria_from_tender_docs, project_key, ap, am or None
    )
    payload = result.get("payload") or {}
    return templates.TemplateResponse(
        "evaluation/_criteria_extract.html",
        {
            "request": request,
            "project_key": project_key,
            "project_title": _project_title(project_key),
            "error": result.get("error"),
            "payload": payload,
            "criteria_json": json.dumps(payload, ensure_ascii=False, indent=2),
            "raw_llm": result.get("raw_llm"),
            "may_evaluate": True,
        },
    )


@router.post("/evaluation/apply-criteria", response_class=HTMLResponse)
async def evaluation_apply_criteria(
    request: Request,
    project_key: str = Form(...),
    criteria_json: str = Form(...),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    try:
        data = json.loads(criteria_json or "{}")
    except json.JSONDecodeError:
        return RedirectResponse(
            url=f"/evaluation?project_key={project_key}&criteria_apply=invalid",
            status_code=303,
        )
    stats = import_criteria_payload(project_key, data, skip_existing=True)
    return RedirectResponse(
        url=(
            f"/evaluation?project_key={project_key}"
            f"&criteria_apply=ok&created={stats['created']}&skipped={stats['skipped']}"
        ),
        status_code=303,
    )


@router.post("/evaluation/delete-criterion", response_class=HTMLResponse)
async def evaluation_delete_criterion(request: Request, project_key: str = Form(...), criterion_id: int = Form(...)):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    soft_delete_criterion(criterion_id)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


# ── Preisblatt (TCO) ────────────────────────────────────────────────────────

@router.get("/evaluation/price", response_class=HTMLResponse)
async def evaluation_price_page(request: Request, project_key: str, bidder_id: int = 0):
    who = _username(request)
    bidders = list_bidders(project_key)
    if not bidder_id and bidders:
        bidder_id = bidders[0].id
    items = list_price_items(bidder_id) if bidder_id else []
    einmalig = [i for i in items if i.category == "einmalig"]
    wiederkehrend = [i for i in items if i.category == "wiederkehrend"]
    by_year: dict[int, list] = {}
    for i in wiederkehrend:
        by_year.setdefault(i.year, []).append(i)
    tco = compute_bidder_tco(bidder_id) if bidder_id else None
    has_price_template = bool(
        get_tender_document_ids(project_key, roles=("preisblatt_vorlage",))
    ) if project_key else False
    has_bidder_preisblatt = bool(get_bidder_preisblatt_doc_ids(bidder_id)) if bidder_id else False
    return templates.TemplateResponse(
        "evaluation/_price.html",
        {
            "request": request,
            "project_key": project_key,
            "project_title": _project_title(project_key),
            "bidders": bidders,
            "bidder_id": bidder_id,
            "einmalig": einmalig,
            "by_year": dict(sorted(by_year.items())),
            "tco": tco,
            "may_evaluate": can_evaluate(who),
            "has_price_template": has_price_template,
            "has_bidder_preisblatt": has_bidder_preisblatt,
            "price_message": request.query_params.get("price_msg"),
        },
    )


@router.post("/evaluation/price-item", response_class=HTMLResponse)
async def evaluation_save_price_item(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    item_id: str = Form(""),
    category: str = Form(...),
    leistungsbeschreibung: str = Form(...),
    anzahl: float = Form(0.0),
    kosten_pro_einheit: float = Form(0.0),
    year: str = Form(""),
    einheit: str = Form(""),
    referenz: str = Form(""),
    bemerkung: str = Form(""),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    upsert_price_item(
        int(item_id) if item_id.strip() else None,
        bidder_id,
        category,
        leistungsbeschreibung,
        anzahl=anzahl,
        kosten_pro_einheit=kosten_pro_einheit,
        year=int(year) if year.strip() else None,
        einheit=einheit or None,
        referenz=referenz or None,
        bemerkung=bemerkung or None,
    )
    sync_price_criterion_scores(project_key)
    return RedirectResponse(url=f"/evaluation/price?project_key={project_key}&bidder_id={bidder_id}", status_code=303)


@router.post("/evaluation/price-item/delete", response_class=HTMLResponse)
async def evaluation_delete_price_item(
    request: Request, project_key: str = Form(...), bidder_id: int = Form(...), item_id: int = Form(...)
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    delete_price_item(item_id)
    sync_price_criterion_scores(project_key)
    return RedirectResponse(url=f"/evaluation/price?project_key={project_key}&bidder_id={bidder_id}", status_code=303)


@router.post("/evaluation/price-template/seed", response_class=HTMLResponse)
async def evaluation_price_template_seed(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    provider: str = Form(""),
    model: str = Form(""),
    cloud_confirm: str = Form("false"),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    settings = load_user_settings()
    ap = (provider or settings.get("provider") or "openai").strip()
    am = (model or settings.get("model") or "").strip()
    gate = validate_tender_cloud_gate(ap, project_key, cloud_confirm in ("true", "on", "1", "yes"))
    if gate:
        return RedirectResponse(
            url=f"/evaluation/price?project_key={project_key}&bidder_id={bidder_id}&price_msg=cloud_confirm",
            status_code=303,
        )
    extracted = await asyncio.to_thread(
        extract_price_structure_from_tender, project_key, ap, am or None
    )
    if extracted.get("error"):
        msg = "template_error"
    else:
        stats = seed_price_structure_for_bidder(
            bidder_id, extracted.get("structure") or {}, only_if_empty=True
        )
        sync_price_criterion_scores(project_key)
        msg = f"seed_ok_{stats['created']}"
    return RedirectResponse(
        url=f"/evaluation/price?project_key={project_key}&bidder_id={bidder_id}&price_msg={msg}",
        status_code=303,
    )


@router.post("/evaluation/price-template/parse", response_class=HTMLResponse)
async def evaluation_price_template_parse(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    provider: str = Form(""),
    model: str = Form(""),
    cloud_confirm: str = Form("false"),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    settings = load_user_settings()
    ap = (provider or settings.get("provider") or "openai").strip()
    am = (model or settings.get("model") or "").strip()
    gate = validate_evaluation_cloud_gate(
        ap, bidder_id, cloud_confirm in ("true", "on", "1", "yes")
    )
    if gate:
        return RedirectResponse(
            url=f"/evaluation/price?project_key={project_key}&bidder_id={bidder_id}&price_msg=cloud_confirm",
            status_code=303,
        )
    extracted = await asyncio.to_thread(
        extract_price_from_bidder_doc, bidder_id, project_key, ap, am or None
    )
    if extracted.get("error"):
        msg = "parse_error"
    else:
        stats = merge_price_structure_for_bidder(bidder_id, extracted.get("structure") or {})
        sync_price_criterion_scores(project_key)
        msg = f"parse_ok_{stats['created']}_{stats['updated']}"
    return RedirectResponse(
        url=f"/evaluation/price?project_key={project_key}&bidder_id={bidder_id}&price_msg={msg}",
        status_code=303,
    )


def _export_rows(project_key: str, may_see: bool) -> tuple[list[str], list[list]]:
    """Eine Zeile pro (Bieter, Top-Level-Kriterium): KI-Spalte, Ø/offizielle Spalte,
    je eine Spalte pro Bewerter (nur wenn may_see) - das ist die 'mehrere Spalten'-
    Anforderung: KI vs. jede Person einzeln vs. offizieller Wert, nebeneinander."""
    from src.m14_auth import get_username_by_id

    bidders = list_bidders(project_key)
    criteria = [c for c in list_criteria(project_key) if c.parent_id is None]
    scores = list_scores_for_project(project_key)
    scores_by_cell: dict[tuple[int, int], list] = {}
    for s in scores:
        scores_by_cell.setdefault((s.bidder_id, s.criterion_id), []).append(s)
    rankings = {r["bidder_id"]: r for r in compute_rankings(project_key)}

    evaluator_ids: list[int] = []
    if may_see:
        seen = set()
        for s in scores:
            if s.source_key.startswith("user:") and s.evaluator_user_id not in seen:
                seen.add(s.evaluator_user_id)
                evaluator_ids.append(s.evaluator_user_id)
        evaluator_ids.sort()
    evaluator_names = {uid: (get_username_by_id(uid) or f"User {uid}") for uid in evaluator_ids}

    headers = ["Projekt", "Bieter", "Kriterium", "Art", "Gewicht %", "Skala", "KI-Vorschlag"]
    for uid in evaluator_ids:
        headers.append(f"Bewerter: {evaluator_names[uid]}")
    headers += ["Ø / Offiziell", "Rang", "Gesamt %"]

    rows: list[list] = []
    for crit in criteria:
        for bidder in bidders:
            cell = scores_by_cell.get((bidder.id, crit.id), [])
            ai_row = next((s for s in cell if s.source_key == "ai"), None)
            by_uid = {s.evaluator_user_id: s for s in cell if s.source_key.startswith("user:")}
            rank_row = rankings.get(bidder.id, {})
            row = [
                _project_title(project_key),
                bidder.name,
                crit.name,
                crit.kind,
                crit.weight_pct if crit.kind == "zuschlag" else "",
                crit.scale_max,
                ai_row.value if ai_row else "",
            ]
            for uid in evaluator_ids:
                sc = by_uid.get(uid)
                row.append(sc.value if sc else "")
            row += [
                official_score(bidder.id, crit, cell),
                rank_row.get("rank"),
                rank_row.get("total_score"),
            ]
            rows.append(row)
    return headers, rows


@router.get("/evaluation/export.csv")
async def evaluation_export_csv(request: Request, project_key: str):
    who = _username(request)
    if not who:
        raise HTTPException(401)
    headers, rows = _export_rows(project_key, can_view_evaluator_details(who))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=bewertung_{project_key[:24]}.csv"},
    )


@router.get("/evaluation/export.xlsx")
async def evaluation_export_xlsx(request: Request, project_key: str):
    who = _username(request)
    if not who:
        raise HTTPException(401)
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(500, "openpyxl nicht installiert")

    headers, rows = _export_rows(project_key, can_view_evaluator_details(who))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bewertungen"
    ws.append(headers)
    for row in rows:
        ws.append(row)

    ws2 = wb.create_sheet("Rangfolge")
    ws2.append(["Rang", "Bieter", "Gesamt %", "KO"])
    for r in compute_rankings(project_key):
        ws2.append([r.get("rank"), r.get("bidder_name"), r.get("total_score"), r.get("ko")])

    ws3 = wb.create_sheet("Preisblatt")
    ws3.append(["Bieter", "Einmalig CHF", "2027", "2028", "2029", "2030", "Total exkl. MwSt", "MwSt", "Total inkl. MwSt"])
    for bidder in list_bidders(project_key):
        tco = compute_bidder_tco(bidder.id)
        by_year = tco["by_year"]
        ws3.append([
            bidder.name, tco["einmalig_total"],
            by_year.get(2027, 0), by_year.get(2028, 0), by_year.get(2029, 0), by_year.get(2030, 0),
            tco["total_exkl_mwst"], tco["mwst"], tco["total_inkl_mwst"],
        ])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=bewertung_{project_key[:24]}.xlsx"},
    )
