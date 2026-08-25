"""
Visualisierungs-Labor — Prompt-Tests für PNG / PPTX / Vorschau.

Sandbox zum Testen von Darstellungen. Cloud-PNG: Prompt wird DSGVO-gefiltert.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Boolean, Column, String, text
from sqlmodel import Field, Session, SQLModel, select

from .m01_config import get_settings
from .m03_db import engine, get_session
from .m16_idea_visual import (
    DEFAULT_OPENAI_IMAGE_MODEL,
    OPENAI_IMAGE_MODELS,
    DeckContent,
    build_deck_preview_png,
    build_pptx_bytes,
    deck_content_from_lab_prompt,
    generate_openai_illustration,
    sanitize_for_cloud_text,
)

log = logging.getLogger(__name__)

VISUAL_LAB_KINDS = ("png", "pptx", "preview")


class VisualLabRun(SQLModel, table=True):
    __tablename__ = "visual_lab_run"
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(sa_column=Column(String(16), nullable=False))
    prompt_input: str
    refinement: Optional[str] = None
    prompt_used: Optional[str] = None
    file_path: str = Field(sa_column=Column(String(200), nullable=False))
    preview_path: Optional[str] = Field(default=None, sa_column=Column(String(200)))
    image_model: Optional[str] = Field(default=None, sa_column=Column(String(80)))
    llm_provider: Optional[str] = Field(default=None, sa_column=Column(String(40)))
    llm_model: Optional[str] = Field(default=None, sa_column=Column(String(80)))
    cloud_used: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    created_by: Optional[int] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def migrate_visual_lab_db() -> None:
    with engine.begin() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "visual_lab_run" in tables:
            return
        # create_all legt Tabelle an


def visual_lab_dir() -> Path:
    d = Path(get_settings().data_dir) / "visual_lab"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_visual_lab_runs(limit: int = 40) -> list[VisualLabRun]:
    with get_session() as ses:
        rows = list(ses.exec(select(VisualLabRun)).all())
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]


def get_visual_lab_run(run_id: int) -> Optional[VisualLabRun]:
    with get_session() as ses:
        return ses.get(VisualLabRun, run_id)


def _merge_prompt(prompt: str, refinement: str | None) -> str:
    base = (prompt or "").strip()
    ref = sanitize_for_cloud_text((refinement or "").strip())
    if ref:
        return f"{base}\n\nAnpassung: {ref}".strip()
    return base


def run_visual_lab(
    *,
    kind: str,
    prompt: str,
    refinement: str | None = None,
    image_model: str = DEFAULT_OPENAI_IMAGE_MODEL,
    llm_provider: str = "openai",
    llm_model: str = "",
    created_by: int | None = None,
) -> Optional[VisualLabRun]:
    kind = (kind or "").strip().lower()
    if kind not in VISUAL_LAB_KINDS:
        return None
    merged_input = _merge_prompt(prompt, refinement)
    if not merged_input:
        return None

    file_path: str | None = None
    preview_path: str | None = None
    prompt_used: str | None = None
    cloud = False

    if kind == "png":
        safe = sanitize_for_cloud_text(merged_input)
        if len(safe) < 10:
            return None
        prompt_used = safe[:500]
        img = generate_openai_illustration(safe, image_model)
        if not img:
            return None
        file_path = f"lab_{uuid.uuid4().hex[:12]}.png"
        (visual_lab_dir() / file_path).write_bytes(img)
        cloud = True
    elif kind in ("pptx", "preview"):
        content = deck_content_from_lab_prompt(merged_input, llm_provider, llm_model)
        if not content:
            return None
        prompt_used = merged_input[:500]
        if kind == "pptx":
            file_path = f"lab_{uuid.uuid4().hex[:12]}.pptx"
            (visual_lab_dir() / file_path).write_bytes(build_pptx_bytes(content))
            preview_path = f"lab_prev_{uuid.uuid4().hex[:10]}.png"
            (visual_lab_dir() / preview_path).write_bytes(build_deck_preview_png(content))
        else:
            file_path = f"lab_{uuid.uuid4().hex[:12]}.png"
            (visual_lab_dir() / file_path).write_bytes(build_deck_preview_png(content))

    if not file_path:
        return None

    with get_session() as ses:
        row = VisualLabRun(
            kind=kind,
            prompt_input=prompt[:2000],
            refinement=(refinement or "")[:500] or None,
            prompt_used=prompt_used,
            file_path=file_path,
            preview_path=preview_path,
            image_model=image_model if cloud else None,
            llm_provider=llm_provider,
            llm_model=llm_model or None,
            cloud_used=cloud,
            created_by=created_by,
        )
        ses.add(row)
        ses.commit()
        ses.refresh(row)
        return row
