"""HTML-Rendering für Offertbeurteilungs-Exporte (PDF/ZIP)."""
from __future__ import annotations

from src.m15_evaluation import EvaluationExportContext


def render_evaluation_export_html(
    ctx: EvaluationExportContext,
    *,
    image_mode: str = "file",
) -> str:
    """
    image_mode:
      url  — Browser-HTML (/documents/…/pages/…, klein, Login nötig)
      file — WeasyPrint-PDF (file://-URIs, keine Base64-Monster)
    """
    from backend.app.jinja_env import templates

    return templates.env.get_template("evaluation/export_report.html").render(
        ctx=ctx,
        eignung_rows=[r for r in ctx.top_rows if r.criterion_kind == "eignung"],
        zuschlag_rows=[r for r in ctx.top_rows if r.criterion_kind == "zuschlag"],
        export_image_mode=image_mode,
    )
