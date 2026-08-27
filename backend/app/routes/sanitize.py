"""PII-Sanitizer — Dokumente/Text für sichere Cloud-Nutzung aufbereiten."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from backend.app.jinja_env import templates
from src.m19_sanitize import extract_text_from_bytes, pii_pipeline_status, sanitize_plaintext

router = APIRouter()


@router.get("/sanitize", response_class=HTMLResponse)
async def sanitize_page(request: Request):
    return templates.TemplateResponse(
        "sanitize/index.html",
        {
            "request": request,
            "active_page": "sanitize",
            "result": None,
            "error": None,
            "input_label": "",
            "pii_status": pii_pipeline_status(),
        },
    )


@router.post("/sanitize", response_class=HTMLResponse)
async def sanitize_run(
    request: Request,
    source_text: str = Form(""),
    full_pipeline: str = Form("1"),
    file: UploadFile | None = File(None),
):
    label = "Eingabetext"
    text = (source_text or "").strip()
    error = None

    if file and file.filename:
        data = await file.read()
        ok, extracted = extract_text_from_bytes(file.filename, data)
        if not ok:
            error = extracted
            text = ""
        else:
            text = extracted
            label = file.filename

    if not error and not text:
        error = "Bitte Text einfügen oder eine Datei hochladen."

    result = None
    if not error:
        result = sanitize_plaintext(text, full_pipeline=full_pipeline in ("1", "true", "on", "yes"))

    return templates.TemplateResponse(
        "sanitize/index.html",
        {
            "request": request,
            "active_page": "sanitize",
            "result": result,
            "error": error,
            "input_label": label,
            "pii_status": pii_pipeline_status(),
        },
    )