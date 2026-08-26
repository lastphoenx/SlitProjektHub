"""Visual-Lab — Referenz-Uploads (PDF, Bilder, Text) für Prompt-Kontext."""
from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .m08_llm import (
    get_model_id,
    get_available_models,
    have_key,
    model_supports_vision,
    try_models_with_messages,
)

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".txt", ".md"}
)
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 5
MAX_PDF_PAGES_TEXT = 12
MAX_PDF_PAGES_VISION = 4
MAX_REF_TEXT_CHARS = 12000
MAX_IMAGE_VISION = 6

_VISION_DESCRIBE_SYSTEM = (
    "Beschreibe Referenzmaterial für eine Portfolio-Visualisierung (öffentliche Verwaltung Schweiz).\n"
    "Fokus: Struktur, Phasen, Diagramm-Logik, Stil, Farben — keine Personennamen.\n"
    "Kurz und sachlich (max. 600 Wörter), Deutsch."
)


@dataclass
class LabReferenceBundle:
    text_blocks: list[str] = field(default_factory=list)
    images: list[tuple[bytes, str, str]] = field(default_factory=list)  # data, mime, label
    stored: list[dict[str, Any]] = field(default_factory=list)

    def merged_text(self) -> str:
        if not self.text_blocks:
            return ""
        return "\n\n".join(self.text_blocks)[:MAX_REF_TEXT_CHARS]

    def image_payload(self) -> list[tuple[bytes, str]]:
        return [(d, m) for d, m, _ in self.images[:MAX_IMAGE_VISION]]


def visual_lab_attachments_dir(base: Path) -> Path:
    d = base / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(name: str) -> str:
    base = Path(name).name
    keep = []
    for ch in base:
        if ch.isalnum() or ch in "._-":
            keep.append(ch)
        elif ch == " ":
            keep.append("_")
    return "".join(keep)[:80] or "file"


def _extract_pdf_text(path: Path) -> str:
    try:
        import pdfplumber

        parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:MAX_PDF_PAGES_TEXT]:
                t = (page.extract_text() or "").strip()
                if t:
                    parts.append(t)
        return "\n\n".join(parts)
    except Exception as exc:
        log.warning("PDF text extract failed %s: %s", path, exc)
        return ""


def _pdf_page_images(path: Path) -> list[tuple[bytes, str, str]]:
    try:
        from pdf2image import convert_from_path

        pil_pages = convert_from_path(
            str(path),
            dpi=120,
            first_page=1,
            last_page=MAX_PDF_PAGES_VISION,
        )
        out: list[tuple[bytes, str, str]] = []
        for i, img in enumerate(pil_pages):
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out.append((buf.getvalue(), "image/png", f"{path.name} Seite {i + 1}"))
        return out
    except Exception as exc:
        log.warning("PDF rasterize failed %s: %s", path, exc)
        return []


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_REF_TEXT_CHARS]
    except Exception:
        return ""


def process_upload_bytes(
    filename: str,
    data: bytes,
    store_dir: Path,
) -> tuple[Optional[str], Optional[LabReferenceBundle]]:
    """Speichert eine Datei und extrahiert Referenz-Kontext."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return "invalid_type", None
    if len(data) > MAX_ATTACHMENT_BYTES:
        return "too_large", None
    if not data:
        return "empty", None

    safe = _safe_filename(filename)
    rel = f"att_{uuid.uuid4().hex[:10]}_{safe}"
    dest = store_dir / rel
    dest.write_bytes(data)

    bundle = LabReferenceBundle()
    meta: dict[str, Any] = {
        "path": rel,
        "original_name": filename[:200],
        "kind": "other",
        "bytes": len(data),
    }

    if ext in IMAGE_EXTENSIONS:
        mime = MIME_BY_EXT.get(ext, "image/png")
        bundle.images.append((data, mime, filename))
        meta["kind"] = "image"
    elif ext == ".pdf":
        meta["kind"] = "pdf"
        text = _extract_pdf_text(dest)
        if len(text.strip()) >= 80:
            bundle.text_blocks.append(f"[PDF {filename}]\n{text[:8000]}")
        else:
            pages = _pdf_page_images(dest)
            if pages:
                bundle.images.extend(pages)
                bundle.text_blocks.append(
                    f"[PDF {filename}: wenig Text — {len(pages)} Seite(n) als Bild für Vision]"
                )
            elif text.strip():
                bundle.text_blocks.append(f"[PDF {filename}]\n{text}")
    else:
        meta["kind"] = "text"
        text = _read_text_file(dest)
        if text.strip():
            bundle.text_blocks.append(f"[{filename}]\n{text[:6000]}")

    bundle.stored.append(meta)
    return None, bundle


def merge_bundles(bundles: list[LabReferenceBundle]) -> LabReferenceBundle:
    out = LabReferenceBundle()
    for b in bundles:
        out.text_blocks.extend(b.text_blocks)
        out.images.extend(b.images)
        out.stored.extend(b.stored)
    return out


def resolve_vision_provider_model(
    preferred_provider: str,
    preferred_model: str,
) -> tuple[str, str]:
    """Vision-fähiges Provider/Modell für Bildbeschreibung (lokal bevorzugt)."""
    p = (preferred_provider or "").strip().lower()
    m = (preferred_model or "").strip()
    if p and m and model_supports_vision(p, get_model_id(p, m)):
        return p, m
    if p == "ollama" and have_key("ollama"):
        live = get_available_models("ollama")
        for cand in live:
            if model_supports_vision("ollama", cand):
                return "ollama", cand
    for prov in ("openai", "anthropic", "ollama"):
        if not have_key(prov):
            continue
        if prov == "ollama":
            models = get_available_models("ollama")
        else:
            models = [
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-4o",
                "gpt-4o-mini",
                "sonnet-4.6",
                "opus-4.6",
                "haiku-4.5",
            ]
        for cand in models:
            mid = get_model_id(prov, cand) if prov != "ollama" else cand
            if model_supports_vision(prov, mid):
                return prov, cand if prov == "ollama" else cand
    return preferred_provider or "openai", preferred_model or ""


def describe_reference_images(
    bundle: LabReferenceBundle,
    provider: str,
    model: str,
    extra_prompt: str = "",
) -> Optional[str]:
    if not bundle.images:
        return None
    vp, vm = resolve_vision_provider_model(provider, model)
    if not model_supports_vision(vp, get_model_id(vp, vm) or vm):
        return None
    user = (extra_prompt or "Beschreibe die Referenzbilder für eine Folien-Visualisierung.").strip()
    if bundle.merged_text():
        user += "\n\nBereits extrahierter Text:\n" + bundle.merged_text()[:2000]
    raw = try_models_with_messages(
        vp,
        _VISION_DESCRIBE_SYSTEM,
        [{"role": "user", "content": user}],
        max_tokens=900,
        temperature=0.3,
        model=vm or None,
        images=bundle.image_payload(),
    )
    return (raw or "").strip() or None


def build_prompt_with_references(
    base_prompt: str,
    bundle: LabReferenceBundle | None,
    *,
    describe_images: bool = False,
    vision_provider: str = "",
    vision_model: str = "",
) -> str:
    parts = [base_prompt.strip()]
    if not bundle:
        return parts[0]
    text = bundle.merged_text()
    if describe_images and bundle.images:
        desc = describe_reference_images(bundle, vision_provider, vision_model)
        if desc:
            parts.append("--- Referenz-Bildbeschreibung ---\n" + desc)
    if text:
        parts.append("--- Referenzmaterial (extrahiert) ---\n" + text)
    return "\n\n".join(parts).strip()
