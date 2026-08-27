"""PII-Sanitizer — Dokumente/Text für sichere Cloud-Nutzung aufbereiten."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from backend.app.jinja_env import templates
from src.m19_sanitize import pii_pipeline_status, run_sanitize_job

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/sanitize", response_class=HTMLResponse)
async def sanitize_page(request: Request):
    return templates.TemplateResponse(
        "sanitize/index.html",
        {
            "request": request,
            "active_page": "sanitize",
            "result": None,
            "error": None,
            "warnings": [],
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
    file_bytes: bytes | None = None
    file_name: str | None = None
    if file and file.filename:
        file_name = file.filename
        file_bytes = await file.read()

    pipeline_on = full_pipeline in ("1", "true", "on", "yes")
    error: str | None = None
    result = None
    warnings: list[str] = []
    label = file_name or "Eingabetext"

    try:
        error, result, warnings = await asyncio.to_thread(
            run_sanitize_job,
            source_text=source_text,
            file_name=file_name,
            file_bytes=file_bytes,
            full_pipeline=pipeline_on,
        )
        if result:
            label = result.pop("input_label", label)
    except RuntimeError as exc:
        log.warning("Sanitize-Lauf abgebrochen: %s", exc)
        error = str(exc)
    except Exception:
        log.exception("Sanitize-Lauf unerwarteter Fehler")
        error = (
            "Sanitizer abgebrochen (502/OOM). Kürzeres Dokument versuchen oder "
            "`journalctl -u projekthub-backend` prüfen."
        )

    return templates.TemplateResponse(
        "sanitize/index.html",
        {
            "request": request,
            "active_page": "sanitize",
            "result": result,
            "error": error,
            "warnings": warnings,
            "input_label": label,
            "pii_status": pii_pipeline_status(),
        },
    )
