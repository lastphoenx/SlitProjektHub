"""
Projektideen — Portfolio-Folie (PPTX) und Cloud-Illustration (OpenAI Images).

PPTX nutzt lokale Bewertungsdaten (ai_*). Cloud-Illustration: nur DSGVO-geprüfter
Prompt an OpenAI — kein Rohtext, keine Personennamen.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

from .m01_config import get_settings
from .m03_db import get_session
from .m08_llm import have_key, try_models_with_messages
from .m16_idea import ProjectIdea, get_idea

log = logging.getLogger(__name__)

OPENAI_IMAGE_MODELS: dict[str, str] = {
    "dall-e-3": "dall-e-3",
    "dall-e-2": "dall-e-2",
    "gpt-image-1": "gpt-image-1",
    "gpt-image-1-mini": "gpt-image-1-mini",
}
DEFAULT_OPENAI_IMAGE_MODEL = "dall-e-3"

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+41|0)\s*[\d\s./-]{8,}")
_PERSON_LINE_RE = re.compile(
    r"(?:Herr|Frau|Dr\.|Prof\.)\s+[A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+)?",
    re.IGNORECASE,
)
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


@dataclass
class DeckContent:
    title: str
    subtitle: str = ""
    summary_lines: list[str] = field(default_factory=list)
    resource_lines: list[str] = field(default_factory=list)
    challenge_lines: list[str] = field(default_factory=list)
    phase_lines: list[str] = field(default_factory=list)
    recommendation_lines: list[str] = field(default_factory=list)


def sanitize_for_cloud_text(text: str) -> str:
    """Entfernt offensichtliche PII vor Cloud-Prompt."""
    if not text:
        return ""
    t = _EMAIL_RE.sub("", text)
    t = _PHONE_RE.sub("", t)
    t = _PERSON_LINE_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def idea_images_dir() -> Path:
    d = Path(get_settings().data_dir) / "idea_images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def idea_decks_dir() -> Path:
    d = Path(get_settings().data_dir) / "idea_decks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1)
        text = re.sub(r"\s*```\s*$", "", text)
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


_ILLUSTRATION_SYSTEM = (
    "Du erstellst einen ENGLISCHEN Bild-Prompt für DALL-E (abstrakte Portfolio-Illustration).\n"
    "DSGVO / Datenschutz — STRIKT:\n"
    "- KEINE Personennamen, keine Anreden mit Namen, keine E-Mail, Telefon, Adressen.\n"
    "- KEINE konkreten internen Vorgänge, Aktenzeichen, IDs oder Rohtext der Idee.\n"
    "- Erlaubt: generischer Projekttyp, abstrakte Symbole, Farben, Stimmung.\n"
    "- Firmen-/Behörden-/Fachbereichsname DARF einmal vorkommen wenn sachlich nützlich.\n"
    "- Keine fotorealistischen Personen, keine Gesichter.\n"
    "Stil: professionell, öffentliche Verwaltung Schweiz, minimal, blau-grau.\n"
    "Antwort: NUR der Prompt-Text (max. 900 Zeichen), ohne Anführungszeichen oder Markdown."
)


def _build_illustration_user_prompt(idea: ProjectIdea, refinement_notes: str = "") -> str:
    parts: list[str] = []
    if idea.ai_project_name:
        parts.append(f"Projektname (generisch): {sanitize_for_cloud_text(idea.ai_project_name)}")
    if idea.fachabteilung:
        parts.append(f"Fachbereich/Organisation: {sanitize_for_cloud_text(idea.fachabteilung)}")
    if idea.ai_summary:
        parts.append(f"Sachliche Kurzfassung: {sanitize_for_cloud_text(idea.ai_summary)}")
    if idea.ai_recommendation:
        parts.append(f"Empfehlung (abstrakt): {sanitize_for_cloud_text(idea.ai_recommendation)}")
    if not parts:
        parts.append("Generisches öffentliches Verwaltungsprojekt, Innovation, abstrakt.")
    ref = sanitize_for_cloud_text(refinement_notes)
    if ref:
        parts.append(f"Anpassung für neue Illustration (ohne Personennamen): {ref}")
    return "\n".join(parts)


def build_dsgvo_illustration_prompt(
    idea: ProjectIdea,
    llm_provider: str = "openai",
    llm_model: str = "",
    refinement_notes: str = "",
) -> Optional[str]:
    user = _build_illustration_user_prompt(idea, refinement_notes)
    raw = try_models_with_messages(
        llm_provider,
        _ILLUSTRATION_SYSTEM,
        [{"role": "user", "content": user}],
        max_tokens=500,
        temperature=0.4,
        model=llm_model or None,
    )
    prompt = sanitize_for_cloud_text((raw or "").strip().strip('"').strip("'"))
    if len(prompt) < 20:
        title = sanitize_for_cloud_text(idea.ai_project_name or idea.title or "Public sector project")
        dept = sanitize_for_cloud_text(idea.fachabteilung or "")
        ref = sanitize_for_cloud_text(refinement_notes)
        prompt = (
            f"Abstract minimalist illustration for a Swiss public administration portfolio concept: "
            f"{title}. "
            f"{f'Context: {dept}. ' if dept else ''}"
            f"{f'Adjustment: {ref}. ' if ref else ''}"
            "Professional blue and grey tones, geometric icons, no people, no text, no logos."
        )
    return prompt[:900]


def generate_openai_illustration(prompt: str, image_model: str = DEFAULT_OPENAI_IMAGE_MODEL) -> Optional[bytes]:
    if not have_key("openai"):
        log.warning("OpenAI key missing for illustration")
        return None
    model_id = OPENAI_IMAGE_MODELS.get(image_model, image_model)
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    except Exception as exc:
        log.warning("OpenAI client init failed: %s", exc)
        return None

    try:
        if model_id == "dall-e-3":
            resp = client.images.generate(
                model=model_id,
                prompt=prompt,
                size="1792x1024",
                quality="standard",
                n=1,
            )
        elif model_id == "dall-e-2":
            resp = client.images.generate(model=model_id, prompt=prompt, size="1024x1024", n=1)
        else:
            resp = client.images.generate(model=model_id, prompt=prompt, size="1024x1024", n=1)
        item = resp.data[0]
        if getattr(item, "b64_json", None):
            import base64

            return base64.b64decode(item.b64_json)
        if getattr(item, "url", None):
            import urllib.request

            with urllib.request.urlopen(item.url, timeout=120) as r:
                return r.read()
    except Exception as exc:
        log.warning("OpenAI image generation failed: %s", exc)
        return None
    return None


def deck_content_from_idea(
    idea: ProjectIdea,
    refinement_notes: str = "",
    llm_provider: str = "openai",
    llm_model: str = "",
) -> DeckContent:
    base = DeckContent(
        title=idea.ai_project_name or idea.title or f"Projektidee #{idea.id}",
        subtitle=" · ".join(
            [x for x in [
                idea.fachabteilung,
                "Projektportfolio — Ersteinschätzung",
                idea.created_at.strftime("%d.%m.%Y"),
            ] if x]
        ),
        summary_lines=[s.strip() for s in re.split(r"(?<=[.!?])\s+", idea.ai_summary or "") if s.strip()][:6],
        resource_lines=_resource_lines_from_idea(idea),
        challenge_lines=[
            f"{c.get('title', '')}: {c.get('description', '')} [{c.get('severity', '')}]"
            for c in idea.challenges[:5]
        ],
        phase_lines=[
            f"{p.get('name', '')} ({p.get('duration_estimate', '')}): {p.get('description', '')}"
            for p in idea.phases[:6]
        ],
        recommendation_lines=[idea.ai_recommendation] if idea.ai_recommendation else [],
    )
    ref = (refinement_notes or "").strip()
    if not ref:
        return base

    system = (
        "Du passt Portfolio-Folieninhalt an. Antwort NUR als JSON:\n"
        '{"title":"...","subtitle":"...","summary_lines":[],"resource_lines":[],'
        '"challenge_lines":[],"phase_lines":[],"recommendation_lines":[]}\n'
        "Wende die Anpassungswünsche an, bleibe sachlich. Keine neuen Personennamen erfinden."
    )
    user = (
        f"Bisheriger Inhalt:\n{json.dumps(base.__dict__, ensure_ascii=False)}\n\n"
        f"Anpassungswünsche:\n{ref}"
    )
    raw = try_models_with_messages(
        llm_provider,
        system,
        [{"role": "user", "content": user}],
        max_tokens=1200,
        temperature=0.3,
        model=llm_model or None,
    )
    parsed = _parse_json_dict(raw)
    if not parsed:
        base.recommendation_lines = base.recommendation_lines + [f"Anpassung: {ref}"]
        return base
    return DeckContent(
        title=(parsed.get("title") or base.title)[:120],
        subtitle=(parsed.get("subtitle") or base.subtitle)[:200],
        summary_lines=_as_str_list(parsed.get("summary_lines")) or base.summary_lines,
        resource_lines=_as_str_list(parsed.get("resource_lines")) or base.resource_lines,
        challenge_lines=_as_str_list(parsed.get("challenge_lines")) or base.challenge_lines,
        phase_lines=_as_str_list(parsed.get("phase_lines")) or base.phase_lines,
        recommendation_lines=_as_str_list(parsed.get("recommendation_lines")) or base.recommendation_lines,
    )


def _as_str_list(val: Any) -> list[str]:
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if str(x).strip()]


def _resource_lines_from_idea(idea: ProjectIdea) -> list[str]:
    lines: list[str] = []
    if idea.ai_internal_pt is not None:
        lines.append(f"Intern: {idea.ai_internal_pt:.0f} Personentage")
        if idea.ai_internal_pt_reasoning:
            lines.append(idea.ai_internal_pt_reasoning)
    if idea.ai_external_cost is not None:
        lines.append(f"Extern: CHF {idea.ai_external_cost:,.0f}".replace(",", "'"))
        if idea.ai_external_cost_reasoning:
            lines.append(idea.ai_external_cost_reasoning)
    return lines


_LAB_DECK_SYSTEM = (
    "Erstelle Portfolio-Folieninhalt für eine öffentliche Verwaltung (Schweiz) aus der Beschreibung.\n"
    "Antwort NUR als JSON:\n"
    '{"title":"kurzer Titel","subtitle":"optional","summary_lines":["..."],'
    '"resource_lines":[],"challenge_lines":[],"phase_lines":[],"recommendation_lines":[]}\n'
    "Sachlich, keine Personennamen."
)


def deck_content_from_lab_prompt(
    prompt: str,
    llm_provider: str = "openai",
    llm_model: str = "",
) -> Optional[DeckContent]:
    raw = try_models_with_messages(
        llm_provider,
        _LAB_DECK_SYSTEM,
        [{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.4,
        model=llm_model or None,
    )
    parsed = _parse_json_dict(raw)
    if not parsed:
        title = prompt.strip().split("\n")[0][:80] or "Visual Test"
        return DeckContent(title=title, summary_lines=[prompt[:400]])
    return DeckContent(
        title=(parsed.get("title") or "Visual Test")[:120],
        subtitle=(parsed.get("subtitle") or "SlitProjektHub Visual-Lab")[:200],
        summary_lines=_as_str_list(parsed.get("summary_lines")),
        resource_lines=_as_str_list(parsed.get("resource_lines")),
        challenge_lines=_as_str_list(parsed.get("challenge_lines")),
        phase_lines=_as_str_list(parsed.get("phase_lines")),
        recommendation_lines=_as_str_list(parsed.get("recommendation_lines")),
    )


def _add_title_slide(prs: Presentation, content: DeckContent) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(0.6), Inches(1.2), Inches(9), Inches(1.2))
    p = box.text_frame.paragraphs[0]
    p.text = content.title
    p.font.size = Pt(32)
    p.font.bold = True
    p.font.color.rgb = RGBColor(0x1e, 0x3a, 0x5f)
    if content.subtitle:
        sub = slide.shapes.add_textbox(Inches(0.6), Inches(2.4), Inches(9), Inches(1))
        sp = sub.text_frame.paragraphs[0]
        sp.text = content.subtitle
        sp.font.size = Pt(14)
        sp.font.color.rgb = RGBColor(0x55, 0x55, 0x55)


def _add_bullet_slide(prs: Presentation, heading: str, lines: list[str]) -> None:
    if not lines:
        return
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    head = slide.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(9), Inches(0.6))
    hp = head.text_frame.paragraphs[0]
    hp.text = heading
    hp.font.size = Pt(24)
    hp.font.bold = True
    hp.font.color.rgb = RGBColor(0x1e, 0x3a, 0x5f)
    body = slide.shapes.add_textbox(Inches(0.6), Inches(1.3), Inches(9), Inches(5))
    tf = body.text_frame
    tf.word_wrap = True
    for i, line in enumerate(lines[:12]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line if line.startswith("•") else f"• {line}"
        p.font.size = Pt(14)
        p.space_after = Pt(6)


def build_pptx_bytes(content: DeckContent) -> bytes:
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)
    _add_title_slide(prs, content)
    if content.summary_lines:
        _add_bullet_slide(prs, "Zusammenfassung", content.summary_lines)
    if content.resource_lines:
        _add_bullet_slide(prs, "Ressourcen & Kosten", content.resource_lines)
    if content.challenge_lines:
        _add_bullet_slide(prs, "Herausforderungen", content.challenge_lines)
    if content.phase_lines:
        _add_bullet_slide(prs, "Grobe Phasenplanung", content.phase_lines)
    if content.recommendation_lines:
        _add_bullet_slide(prs, "Empfehlung", content.recommendation_lines)
    foot = prs.slides.add_slide(prs.slide_layouts[6])
    fp = foot.shapes.add_textbox(Inches(0.6), Inches(3), Inches(9), Inches(1)).text_frame.paragraphs[0]
    fp.text = "KI-Vorbewertung — ersetzt keine fachliche Prüfung · SlitProjektHub"
    fp.font.size = Pt(11)
    fp.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def build_deck_preview_png(content: DeckContent) -> bytes:
    w, h = 1280, 720
    img = Image.new("RGB", (w, h), color=(30, 58, 95))
    draw = ImageDraw.Draw(img)
    title = content.title[:80]
    try:
        font_l = ImageFont.truetype("arial.ttf", 42)
        font_s = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font_l = ImageFont.load_default()
        font_s = font_l
    draw.text((60, 80), title, fill=(255, 255, 255), font=font_l)
    y = 180
    if content.summary_lines:
        summary = content.summary_lines[0][:280]
        draw.text((60, y), summary, fill=(200, 210, 220), font=font_s)
        y += 80
    for line in content.resource_lines[:2]:
        draw.text((60, y), line[:100], fill=(180, 200, 230), font=font_s)
        y += 36
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_portfolio_deck(
    idea_id: int,
    refinement_notes: str = "",
    llm_provider: str = "openai",
    llm_model: str = "",
) -> Optional[ProjectIdea]:
    idea = get_idea(idea_id)
    if not idea or idea.status != "bewertet":
        return None
    content = deck_content_from_idea(idea, refinement_notes, llm_provider, llm_model)
    deck_name = f"deck_{idea_id}_{uuid.uuid4().hex[:10]}.pptx"
    preview_name = f"deck_preview_{idea_id}.png"
    (idea_decks_dir() / deck_name).write_bytes(build_pptx_bytes(content))
    (idea_images_dir() / preview_name).write_bytes(build_deck_preview_png(content))
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if not obj:
            return None
        obj.deck_path = deck_name
        obj.deck_preview_path = preview_name
        obj.deck_generated_at = _now()
        obj.updated_at = _now()
        ses.add(obj)
        ses.commit()
        ses.refresh(obj)
        return obj


def generate_cloud_illustration(
    idea_id: int,
    image_model: str = DEFAULT_OPENAI_IMAGE_MODEL,
    llm_provider: str = "openai",
    llm_model: str = "",
    refinement_notes: str = "",
) -> Optional[ProjectIdea]:
    idea = get_idea(idea_id)
    if not idea or idea.status != "bewertet":
        return None
    safe_prompt = build_dsgvo_illustration_prompt(
        idea, llm_provider, llm_model, refinement_notes=refinement_notes
    )
    if not safe_prompt:
        return None
    img_bytes = generate_openai_illustration(safe_prompt, image_model)
    if not img_bytes:
        return None
    fname = f"ill_{idea_id}_{uuid.uuid4().hex[:10]}.png"
    (idea_images_dir() / fname).write_bytes(img_bytes)
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if not obj:
            return None
        obj.image_path = fname
        obj.image_source = "dalle"
        obj.illustration_model = OPENAI_IMAGE_MODELS.get(image_model, image_model)
        obj.illustration_prompt_safe = safe_prompt[:500]
        obj.illustration_generated_at = _now()
        obj.updated_at = _now()
        ses.add(obj)
        ses.commit()
        ses.refresh(obj)
        return obj
