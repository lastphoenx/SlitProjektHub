"""FastAPI routes — Phase C Offertbeurteilung."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from backend.app.jinja_env import templates

from src.m07_projects import list_projects_df
from src.m09_docs import get_project_documents
from src.m14_auth import (
    can_evaluate,
    can_view_evaluator_details,
    get_user_id,
    is_super_user,
    session_username,
)
from src.m15_evaluation import (
    ANGEbot_CLASSIFICATION,
    CRITERION_KINDS,
    compute_rankings,
    create_bidder,
    create_criterion,
    get_bidder_document_ids,
    get_score,
    link_document_to_bidder,
    list_bidders,
    list_criteria,
    list_scores_for_project,
    soft_delete_bidder,
    soft_delete_criterion,
    suggest_score_with_rag,
    unlink_document_from_bidder,
    upsert_score,
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


@router.get("/evaluation", response_class=HTMLResponse)
async def evaluation_page(request: Request, project_key: str = ""):
    projects = _projects_list()
    if not project_key and projects:
        project_key = projects[0]["key"]

    bidders = list_bidders(project_key) if project_key else []
    criteria = list_criteria(project_key) if project_key else []
    scores = list_scores_for_project(project_key) if project_key else []
    score_map = {(s.bidder_id, s.criterion_id): s for s in scores}
    rankings = compute_rankings(project_key) if project_key else []

    matrix_rows = []
    for crit in criteria:
        row = {"name": crit.name, "kind": crit.kind, "weight": crit.weight_pct if crit.kind == "zuschlag" else None}
        cells = []
        for bidder in bidders:
            sc = score_map.get((bidder.id, crit.id))
            cells.append(
                {
                    "bidder_id": bidder.id,
                    "criterion_id": crit.id,
                    "value": sc.value if sc else None,
                    "display": f"{sc.value:.1f}" if sc else "—",
                }
            )
        row["cells"] = cells
        matrix_rows.append(row)

    offer_docs = []
    bidder_docs: dict[int, list[int]] = {}
    if project_key:
        offer_docs = [
            d for d in get_project_documents(project_key)
            if d.classification == ANGEbot_CLASSIFICATION
        ]
        for b in bidders:
            bidder_docs[b.id] = get_bidder_document_ids(b.id)

    who = _username(request)
    user_id = get_user_id(who) if who else None
    settings = load_user_settings()

    return templates.TemplateResponse(
        "evaluation/index.html",
        {
            "request": request,
            "active_page": "evaluation",
            "projects": projects,
            "project_key": project_key,
            "project_title": _project_title(project_key) if project_key else "",
            "bidders": bidders,
            "criteria": criteria,
            "rankings": rankings,
            "matrix_rows": matrix_rows,
            "offer_docs": offer_docs,
            "bidder_docs": bidder_docs,
            "criterion_kinds": CRITERION_KINDS,
            "angebot_class": ANGEbot_CLASSIFICATION,
            "may_evaluate": can_evaluate(who),
            "may_see_evaluators": can_view_evaluator_details(who),
            "super_user": is_super_user(who),
            "user_id": user_id,
            "error": None,
            "message": None,
            "llm_provider": settings.get("provider", "openai"),
            "llm_model": settings.get("model", ""),
        },
    )


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
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    create_criterion(project_key, kind, name, weight_pct=weight_pct, scale_max=scale_max)
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
):
    who = _username(request)
    if not can_evaluate(who):
        raise HTTPException(403, "Keine Berechtigung")
    uid = get_user_id(who)
    if not uid:
        raise HTTPException(401, "Nicht angemeldet")
    upsert_score(
        bidder_id,
        criterion_id,
        uid,
        value,
        justification=justification or None,
        source_chunk_ref=source_chunk_ref or None,
        allow_override=is_super_user(who),
    )
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.post("/evaluation/suggest", response_class=HTMLResponse)
async def evaluation_suggest(
    request: Request,
    project_key: str = Form(...),
    bidder_id: int = Form(...),
    criterion_id: int = Form(...),
    provider: str = Form("openai"),
    model: str = Form(""),
):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    from src.m15_evaluation import Criterion
    from src.m03_db import get_session

    with get_session() as session:
        crit = session.get(Criterion, criterion_id)
    if not crit:
        raise HTTPException(404, "Kriterium nicht gefunden")
    suggestion = suggest_score_with_rag(
        project_key,
        bidder_id,
        crit,
        provider=provider,
        model=model or None,
    )
    return templates.TemplateResponse(
        "evaluation/_suggestion.html",
        {"request": request, "suggestion": suggestion, "project_key": project_key, "bidder_id": bidder_id, "criterion_id": criterion_id},
    )


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


@router.post("/evaluation/delete-bidder", response_class=HTMLResponse)
async def evaluation_delete_bidder(request: Request, project_key: str = Form(...), bidder_id: int = Form(...)):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    soft_delete_bidder(bidder_id)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.post("/evaluation/delete-criterion", response_class=HTMLResponse)
async def evaluation_delete_criterion(request: Request, project_key: str = Form(...), criterion_id: int = Form(...)):
    if not can_evaluate(_username(request)):
        raise HTTPException(403, "Keine Berechtigung")
    soft_delete_criterion(criterion_id)
    return RedirectResponse(url=f"/evaluation?project_key={project_key}", status_code=303)


@router.get("/evaluation/export.csv")
async def evaluation_export_csv(request: Request, project_key: str):
    who = _username(request)
    if not who:
        raise HTTPException(401)
    bidders = list_bidders(project_key)
    criteria = list_criteria(project_key)
    scores = list_scores_for_project(project_key)
    score_map = {(s.bidder_id, s.criterion_id): s for s in scores}
    rankings = {r["bidder_id"]: r for r in compute_rankings(project_key)}
    may_see = can_view_evaluator_details(who)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Projekt", "Bieter", "Kriterium", "Art", "Gewicht %", "Wert", "Skala", "Begründung", "Quelle", "Bewerter", "Rang", "Gesamt %"]
    )
    for crit in criteria:
        for bidder in bidders:
            sc = score_map.get((bidder.id, crit.id))
            rank_row = rankings.get(bidder.id, {})
            writer.writerow(
                [
                    _project_title(project_key),
                    bidder.name,
                    crit.name,
                    crit.kind,
                    crit.weight_pct if crit.kind == "zuschlag" else "",
                    sc.value if sc else "",
                    crit.scale_max,
                    sc.justification if sc else "",
                    sc.source_chunk_ref if sc else "",
                    sc.evaluator_user_id if sc and may_see else "",
                    rank_row.get("rank"),
                    rank_row.get("total_score"),
                ]
            )
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

    bidders = list_bidders(project_key)
    criteria = list_criteria(project_key)
    scores = list_scores_for_project(project_key)
    score_map = {(s.bidder_id, s.criterion_id): s for s in scores}
    rankings = {r["bidder_id"]: r for r in compute_rankings(project_key)}
    may_see = can_view_evaluator_details(who)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bewertungen"
    headers = ["Bieter", "Kriterium", "Art", "Gewicht %", "Wert", "Begründung", "Quelle", "Rang", "Gesamt %"]
    ws.append(headers)
    for crit in criteria:
        for bidder in bidders:
            sc = score_map.get((bidder.id, crit.id))
            rank_row = rankings.get(bidder.id, {})
            ws.append(
                [
                    bidder.name,
                    crit.name,
                    crit.kind,
                    crit.weight_pct if crit.kind == "zuschlag" else "",
                    sc.value if sc else "",
                    sc.justification if sc else "",
                    sc.source_chunk_ref if sc else "",
                    rank_row.get("rank"),
                    rank_row.get("total_score"),
                ]
            )
    ws2 = wb.create_sheet("Rangfolge")
    ws2.append(["Rang", "Bieter", "Gesamt %", "KO"])
    for r in compute_rankings(project_key):
        ws2.append([r.get("rank"), r.get("bidder_name"), r.get("total_score"), r.get("ko")])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=bewertung_{project_key[:24]}.xlsx"},
    )
