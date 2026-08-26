"""
Visualisierungs-Labor — Prompt-Tests für PNG / PPTX / Vorschau.

Sandbox zum Testen von Darstellungen. Cloud-PNG: Prompt wird DSGVO-gefiltert.
Referenz-Uploads: PDF/Bilder/Text fließen in Prompt oder Vision-LLM ein.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Boolean, Column, String, Text, text
from sqlmodel import Field, SQLModel, select

from .m01_config import get_settings
from .m03_db import engine, get_session
from .m08_llm import get_model_id, model_supports_vision
from .m16_idea_visual import (
    DEFAULT_OPENAI_IMAGE_MODEL,
    OPENAI_IMAGE_MODELS,
    DeckContent,
    _phase_details_from_lines,
    build_deck_composite_preview_png,
    build_deck_preview_png,
    build_pptx_bytes,
    build_vertical_process_diagram_png,
    deck_content_from_lab_prompt,
    generate_openai_illustration,
    sanitize_for_cloud_text,
)
from .m17_visual_lab_refs import (
    LabReferenceBundle,
    MAX_ATTACHMENTS,
    build_prompt_with_references,
    merge_bundles,
    process_upload_bytes,
    visual_lab_attachments_dir,
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
    attachments_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    reference_context: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_by: Optional[int] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def migrate_visual_lab_db() -> None:
    with engine.begin() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "visual_lab_run" not in tables:
            return
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(visual_lab_run)")).fetchall()}
        migrations = [
            ("attachments_json", "ALTER TABLE visual_lab_run ADD COLUMN attachments_json TEXT"),
            ("reference_context", "ALTER TABLE visual_lab_run ADD COLUMN reference_context TEXT"),
        ]
        for col, stmt in migrations:
            if col not in cols:
                conn.execute(text(stmt))


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
    ref = (refinement or "").strip()
    if ref:
        return f"{base}\n\nAnpassung: {ref}".strip()
    return base


def _delete_attachment_files(stored: list[dict[str, Any]]) -> None:
    att_dir = visual_lab_attachments_dir(visual_lab_dir())
    for item in stored:
        path = item.get("path")
        if not path:
            continue
        fp = att_dir / Path(path).name
        if fp.exists():
            fp.unlink()


def delete_visual_lab_run(run_id: int) -> bool:
    run = get_visual_lab_run(run_id)
    if not run:
        return False
    d = visual_lab_dir()
    for name in (run.file_path, run.preview_path):
        if name:
            fp = d / Path(name).name
            if fp.exists():
                fp.unlink()
    if run.attachments_json:
        try:
            stored = json.loads(run.attachments_json)
            if isinstance(stored, list):
                _delete_attachment_files(stored)
        except json.JSONDecodeError:
            pass
    with get_session() as ses:
        obj = ses.get(VisualLabRun, run_id)
        if not obj:
            return False
        ses.delete(obj)
        ses.commit()
    return True


def process_reference_uploads(
    uploads: list[tuple[str, bytes]],
) -> tuple[Optional[str], Optional[LabReferenceBundle]]:
    if len(uploads) > MAX_ATTACHMENTS:
        return "too_many_files", None
    att_dir = visual_lab_attachments_dir(visual_lab_dir())
    bundles: list[LabReferenceBundle] = []
    for filename, data in uploads:
        err, bundle = process_upload_bytes(filename, data, att_dir)
        if err:
            return err, None
        if bundle:
            bundles.append(bundle)
    if not bundles:
        return None, None
    return None, merge_bundles(bundles)


def run_visual_lab(
    *,
    kind: str,
    prompt: str,
    refinement: str | None = None,
    image_model: str = DEFAULT_OPENAI_IMAGE_MODEL,
    llm_provider: str = "openai",
    llm_model: str = "",
    created_by: int | None = None,
    reference_bundle: LabReferenceBundle | None = None,
    use_refs_for_cloud_png: bool = False,
    vision_cloud_ok: bool = False,
) -> tuple[Optional[VisualLabRun], Optional[str]]:
    kind = (kind or "").strip().lower()
    if kind not in VISUAL_LAB_KINDS:
        return None, "invalid_kind"
    merged_input = _merge_prompt(prompt, refinement)
    if not merged_input.strip() and not (reference_bundle and (reference_bundle.text_blocks or reference_bundle.images)):
        return None, "empty_prompt"

    file_path: str | None = None
    preview_path: str | None = None
    prompt_used: str | None = None
    cloud = False
    ref_text = reference_bundle.merged_text() if reference_bundle else ""
    ref_images = reference_bundle.image_payload() if reference_bundle else []

    if kind == "png":
        describe = use_refs_for_cloud_png and reference_bundle and reference_bundle.images
        if describe and not vision_cloud_ok:
            return None, "vision_cloud_confirm"
        enriched = build_prompt_with_references(
            merged_input,
            reference_bundle,
            describe_images=describe,
            vision_provider=llm_provider,
            vision_model=llm_model,
        )
        safe = sanitize_for_cloud_text(enriched)
        if len(safe) < 10:
            return None, "prompt_short"
        prompt_used = safe[:500]
        img = generate_openai_illustration(safe, image_model)
        if not img:
            return None, "png_failed"
        file_path = f"lab_{uuid.uuid4().hex[:12]}.png"
        (visual_lab_dir() / file_path).write_bytes(img)
        cloud = True
    elif kind in ("pptx", "preview"):
        model_probe = get_model_id(llm_provider, llm_model) or llm_model
        imgs_for_llm = ref_images if ref_images and model_supports_vision(llm_provider, model_probe) else None
        content = deck_content_from_lab_prompt(
            merged_input,
            llm_provider,
            llm_model,
            reference_text=ref_text,
            reference_images=imgs_for_llm,
        )
        if not content:
            return None, "llm_failed"
        prompt_used = merged_input[:500]
        if ref_text:
            prompt_used = (prompt_used + " [+Referenz]")[:520]
        if kind == "pptx":
            file_path = f"lab_{uuid.uuid4().hex[:12]}.pptx"
            (visual_lab_dir() / file_path).write_bytes(build_pptx_bytes(content))
            preview_path = f"lab_prev_{uuid.uuid4().hex[:10]}.png"
            (visual_lab_dir() / preview_path).write_bytes(build_deck_composite_preview_png(content))
        else:
            file_path = f"lab_{uuid.uuid4().hex[:12]}.png"
            details = content.phase_details or _phase_details_from_lines(content.phase_lines)
            if len(details) >= 2:
                png_bytes = build_vertical_process_diagram_png(details, content.title[:60])
            else:
                png_bytes = build_deck_preview_png(content)
            (visual_lab_dir() / file_path).write_bytes(png_bytes)

    if not file_path:
        return None, "unknown"

    attachments_json: str | None = None
    if reference_bundle and reference_bundle.stored:
        attachments_json = json.dumps(reference_bundle.stored, ensure_ascii=False)

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
            attachments_json=attachments_json,
            reference_context=ref_text[:8000] or None,
            created_by=created_by,
        )
        ses.add(row)
        ses.commit()
        ses.refresh(row)
        return row, None
