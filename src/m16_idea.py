"""
Projektideen — autonome Vorbewertung, bevor ein formales Projekt existiert.

Bewusst ohne Abhängigkeiten zu Projekten/Rollen/Workflows: jeder eingeloggte
Nutzer legt eine Idee an, die KI bewertet sie gemäss Prompt/Modell und schreibt
das Ergebnis in eigene Spalten (nie vermischt mit den Angaben der Einreicherin/
des Einreichers). Verknüpfungen zu echten Projekten folgen ggf. später.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Boolean, Column, Float, String, Text, text
from sqlmodel import Field, Session, SQLModel, select

from .m01_config import get_settings
from .m03_db import engine, get_session
from .m08_llm import try_models_with_messages, model_supports_vision, get_model_id
from .m17_visual_lab_refs import (
    DEFAULT_SOURCE_TASKS,
    describe_reference_images,
    filter_bundle_for_source_tasks,
    load_bundle_from_stored,
    MAX_ATTACHMENTS,
    parse_task_selection,
    SOURCE_PROCESS_TASKS,
)

log = logging.getLogger(__name__)

IDEA_STATUS = ("neu", "bewertet")
ALLOWED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


class ProjectIdea(SQLModel, table=True):
    __tablename__ = "project_idea"
    id: Optional[int] = Field(default=None, primary_key=True)

    # -- Eingabe (Mensch, Fachabteilung) --
    title: Optional[str] = Field(default=None, sa_column=Column(String(200)))
    idea_text: str
    fachabteilung: Optional[str] = Field(default=None, sa_column=Column(String(120)))
    internal_pt_human: Optional[float] = Field(default=None, sa_column=Column(Float))
    external_cost_human: Optional[float] = Field(default=None, sa_column=Column(Float))
    image_path: Optional[str] = Field(default=None, sa_column=Column(String(400)))
    image_source: Optional[str] = Field(default=None, sa_column=Column(String(20)))
    deck_path: Optional[str] = Field(default=None, sa_column=Column(String(400)))
    deck_preview_path: Optional[str] = Field(default=None, sa_column=Column(String(400)))
    deck_generated_at: Optional[datetime] = None
    docx_path: Optional[str] = Field(default=None, sa_column=Column(String(400)))
    docx_generated_at: Optional[datetime] = None
    source_attachments_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    source_reference_text: Optional[str] = Field(default=None, sa_column=Column(Text))
    illustration_model: Optional[str] = Field(default=None, sa_column=Column(String(80)))
    illustration_prompt_safe: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    illustration_generated_at: Optional[datetime] = None

    submitted_by: Optional[int] = None
    status: str = Field(default="neu", sa_column=Column(String(20), nullable=False))
    is_deleted: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # -- Ergebnis (KI, eigene Spalten, nie mit den Eingaben vermischt) --
    ai_project_name: Optional[str] = Field(default=None, sa_column=Column(String(120)))
    ai_summary: Optional[str] = None
    ai_internal_pt: Optional[float] = Field(default=None, sa_column=Column(Float))
    ai_internal_pt_reasoning: Optional[str] = None
    ai_external_cost: Optional[float] = Field(default=None, sa_column=Column(Float))
    ai_external_cost_reasoning: Optional[str] = None
    ai_challenges_json: Optional[str] = None
    ai_phases_json: Optional[str] = None
    ai_recommendation: Optional[str] = None
    ai_provider: Optional[str] = Field(default=None, sa_column=Column(String(40)))
    ai_model: Optional[str] = Field(default=None, sa_column=Column(String(80)))
    ai_raw_json: Optional[str] = None
    ai_assessed_at: Optional[datetime] = None

    @property
    def challenges(self) -> list[dict[str, Any]]:
        return _safe_json_list(self.ai_challenges_json)

    @property
    def phases(self) -> list[dict[str, Any]]:
        return _safe_json_list(self.ai_phases_json)


def _safe_json_list(raw: Optional[str]) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def migrate_idea_db() -> None:
    """Platzhalter für künftige Leichtmigrationen (Tabelle wird per create_all angelegt)."""
    with engine.begin() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "project_idea" not in tables:
            return
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(project_idea)")).fetchall()}
        migrations = [
            ("image_source", "ALTER TABLE project_idea ADD COLUMN image_source VARCHAR(20)"),
            ("deck_path", "ALTER TABLE project_idea ADD COLUMN deck_path VARCHAR(400)"),
            ("deck_preview_path", "ALTER TABLE project_idea ADD COLUMN deck_preview_path VARCHAR(400)"),
            ("deck_generated_at", "ALTER TABLE project_idea ADD COLUMN deck_generated_at DATETIME"),
            ("illustration_model", "ALTER TABLE project_idea ADD COLUMN illustration_model VARCHAR(80)"),
            ("illustration_prompt_safe", "ALTER TABLE project_idea ADD COLUMN illustration_prompt_safe VARCHAR(500)"),
            ("illustration_generated_at", "ALTER TABLE project_idea ADD COLUMN illustration_generated_at DATETIME"),
            ("docx_path", "ALTER TABLE project_idea ADD COLUMN docx_path VARCHAR(400)"),
            ("docx_generated_at", "ALTER TABLE project_idea ADD COLUMN docx_generated_at DATETIME"),
            ("source_attachments_json", "ALTER TABLE project_idea ADD COLUMN source_attachments_json TEXT"),
            ("source_reference_text", "ALTER TABLE project_idea ADD COLUMN source_reference_text TEXT"),
        ]
        for col, stmt in migrations:
            if col not in cols:
                conn.execute(text(stmt))
        # zukünftige Spalten hier ergänzen


def idea_source_attachments_dir() -> Path:
    d = Path(get_settings().data_dir) / "idea_source_attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_idea(
    *,
    idea_text: str,
    title: str | None = None,
    fachabteilung: str | None = None,
    internal_pt_human: float | None = None,
    external_cost_human: float | None = None,
    image_path: str | None = None,
    source_attachments_json: str | None = None,
    source_reference_text: str | None = None,
    submitted_by: int | None = None,
) -> ProjectIdea:
    if not idea_text or not idea_text.strip():
        raise ValueError("Beschreibung der Idee fehlt.")
    with get_session() as ses:
        obj = ProjectIdea(
            idea_text=idea_text.strip(),
            title=(title or "").strip() or None,
            fachabteilung=(fachabteilung or "").strip() or None,
            internal_pt_human=internal_pt_human,
            external_cost_human=external_cost_human,
            image_path=image_path,
            image_source="upload" if image_path else None,
            source_attachments_json=source_attachments_json,
            source_reference_text=source_reference_text,
            submitted_by=submitted_by,
        )
        ses.add(obj)
        ses.commit()
        ses.refresh(obj)
        return obj


def list_ideas(include_deleted: bool = False) -> list[ProjectIdea]:
    with get_session() as ses:
        stmt = select(ProjectIdea)
        if not include_deleted:
            stmt = stmt.where(ProjectIdea.is_deleted == False)  # noqa: E712
        rows = ses.exec(stmt).all()
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows


def get_idea(idea_id: int) -> Optional[ProjectIdea]:
    with get_session() as ses:
        return ses.get(ProjectIdea, idea_id)


def update_idea_intake(idea_id: int, **fields: Any) -> Optional[ProjectIdea]:
    allowed = {
        "title", "idea_text", "fachabteilung",
        "internal_pt_human", "external_cost_human", "image_path",
    }
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if not obj:
            return None
        for k, v in fields.items():
            if k in allowed:
                setattr(obj, k, v)
        obj.updated_at = datetime.now(timezone.utc)
        ses.add(obj)
        ses.commit()
        ses.refresh(obj)
        return obj


def soft_delete_idea(idea_id: int) -> None:
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if obj:
            obj.is_deleted = True
            ses.add(obj)
            ses.commit()


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")

IDEA_ASSESS_TASKS: dict[str, str] = {
    "project_name": "Projektname vorschlagen",
    "summary": "Zusammenfassung (3–5 Sätze)",
    "resources": "Ressourcen (Personentage + externe Kosten)",
    "challenges": "Herausforderungen / Risiken",
    "phases": "Grobe Phasenplanung",
    "recommendation": "Handlungsempfehlung",
}
DEFAULT_ASSESS_TASKS = frozenset(IDEA_ASSESS_TASKS.keys())

_ASSESS_PROMPT_HEADER = (
    "Du bist eine erfahrene Projektportfolio-Managerin in einer öffentlichen Verwaltung "
    "(Schweiz). Du beurteilst eine ROHE PROJEKTIDEE, bevor ein formales Projekt existiert. "
    "Ziel: der Fachabteilung und dem Projektportfolio-Board eine strukturierte, nüchterne "
    "Ersteinschätzung liefern — keine Marketingsprache, keine Übertreibungen, realistische "
    "Grössenordnungen. Wenn Angaben fehlen, schätze konservativ und mache die Unsicherheit "
    "in der Begründung explizit.\n\n"
    "Antworte AUSSCHLIESSLICH mit einem einzigen JSON-Objekt, exakt in diesem Schema "
    "(keine weiteren Felder, kein Fliesstext davor oder danach):\n"
)


def _build_assess_system_prompt(tasks: set[str]) -> str:
    fields: list[str] = []
    if "project_name" in tasks:
        fields.append('  "project_name": "kurzer, prägnanter Projektname, max. 6 Wörter",')
    if "summary" in tasks:
        fields.append('  "summary": "Zusammenfassung der Idee in 3-5 Sätzen, sachlich",')
    if "resources" in tasks:
        fields.extend([
            '  "internal_pt": Zahl,  // geschätzte Personentage der Fachabteilung, ganze Zahl',
            '  "internal_pt_reasoning": "kurze Begründung der Schätzung",',
            '  "external_cost_chf": Zahl,  // geschätzte externe Kosten in CHF (Dienstleister/Lizenzen)',
            '  "external_cost_reasoning": "kurze Begründung der Schätzung",',
        ])
    if "challenges" in tasks:
        fields.append(
            '  "challenges": [\n'
            '    {"title": "kurzer Titel", "description": "1-2 Sätze", "severity": "niedrig|mittel|hoch"}\n'
            "  ],"
        )
    if "phases" in tasks:
        fields.append(
            '  "phases": [\n'
            '    {"name": "Phasenname", "description": "1-2 Sätze", "duration_estimate": "grobe Dauer, '
            'z.B. \'2-3 Wochen\' oder \'1-2 Monate\' — NIEMALS ein Kalenderdatum"}\n'
            "  ],"
        )
    if "recommendation" in tasks:
        fields.append(
            '  "recommendation": "1-2 Sätze Handlungsempfehlung: z.B. weiterverfolgen, mit welchen '
            'offenen Fragen, oder eher nicht"'
        )
    if not fields:
        fields.append('  "summary": "kurze Zusammenfassung"')
    body = "{\n" + "\n".join(fields) + "\n}"
    rules = [
        "Regeln:",
        "- challenges: 2 bis 5 Einträge, die grössten Risiken/Unsicherheiten zuerst.",
        "- Phasen: 3 bis 6 grobe Phasen — keine Datumsangaben, nur Durchlaufzeiten pro Phase.",
        "- Zahlen sind reine Zahlen ohne Einheiten/Tausendertrennzeichen im JSON.",
    ]
    if "challenges" not in tasks:
        rules = [r for r in rules if "challenges" not in r]
    if "phases" not in tasks:
        rules = [r for r in rules if "Phasen" not in r]
    return _ASSESS_PROMPT_HEADER + body + "\n\n" + "\n".join(rules)


def _idea_source_bundle(idea: ProjectIdea):
    from .m17_visual_lab_refs import load_bundle_from_stored

    if not idea.source_attachments_json:
        return None
    try:
        stored = json.loads(idea.source_attachments_json)
        if not isinstance(stored, list):
            return None
    except json.JSONDecodeError:
        return None
    return load_bundle_from_stored(stored, idea_source_attachments_dir())


def _load_stored_attachments(idea: ProjectIdea) -> list[dict[str, Any]]:
    if not idea.source_attachments_json:
        return []
    try:
        raw = json.loads(idea.source_attachments_json)
        return raw if isinstance(raw, list) else []
    except json.JSONDecodeError:
        return []


def _sync_source_metadata(idea_id: int, stored: list[dict[str, Any]]) -> None:
    bundle = load_bundle_from_stored(stored, idea_source_attachments_dir()) if stored else None
    ref_text = bundle.merged_text() if bundle else None
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if not obj:
            return
        obj.source_attachments_json = (
            json.dumps(stored, ensure_ascii=False) if stored else None
        )
        obj.source_reference_text = ref_text or None
        obj.updated_at = datetime.now(timezone.utc)
        ses.add(obj)
        ses.commit()


def append_source_attachments(
    idea_id: int,
    bundles: list,
) -> Optional[str]:
    """Hängt Unterlagen an eine Idee an. Gibt Fehlercode oder None bei Erfolg."""
    idea = get_idea(idea_id)
    if not idea:
        return "not_found"
    stored = _load_stored_attachments(idea)
    for b in bundles:
        if b and b.stored:
            stored.extend(b.stored)
    if len(stored) > MAX_ATTACHMENTS:
        return "too_many"
    _sync_source_metadata(idea_id, stored)
    return None


def remove_source_attachment(idea_id: int, att_path: str) -> bool:
    idea = get_idea(idea_id)
    if not idea:
        return False
    safe = Path(att_path).name
    stored = _load_stored_attachments(idea)
    new_stored = [x for x in stored if Path(x.get("path", "")).name != safe]
    if len(new_stored) == len(stored):
        return False
    fp = idea_source_attachments_dir() / safe
    if fp.is_file():
        fp.unlink(missing_ok=True)
    _sync_source_metadata(idea_id, new_stored)
    return True


def _build_user_prompt(
    idea: ProjectIdea,
    source_tasks: set[str],
    input_provider: str = "",
    input_model: str = "",
    assess_cloud: bool = False,
) -> str:
    from .m16_idea_visual import sanitize_for_cloud_text, sanitize_structured_field

    def _txt(text: str, structured: bool = False) -> str:
        if not text:
            return ""
        if not assess_cloud:
            return text
        return (
            sanitize_structured_field(text) if structured
            else sanitize_for_cloud_text(text)
        )

    parts = [f"Projektidee (Rohtext):\n{_txt(idea.idea_text)}"]
    bundle = _idea_source_bundle(idea)
    if bundle:
        filtered = filter_bundle_for_source_tasks(bundle, source_tasks)
        if "extract_text" in source_tasks:
            ref = filtered.merged_text().strip()
            if not ref and idea.source_reference_text:
                ref = (idea.source_reference_text or "").strip()
            if ref:
                parts.append(
                    f"Angehängte Unterlagen (extrahiert, lokal):\n{_txt(ref[:10000])}"
                )
        if "vision_describe" in source_tasks and filtered.images:
            desc = describe_reference_images(filtered, input_provider, input_model)
            if desc:
                parts.append(f"Referenz-Bildbeschreibung (KI):\n{_txt(desc[:4000])}")
    elif idea.source_reference_text and "extract_text" in source_tasks:
        ref = (idea.source_reference_text or "").strip()
        if ref:
            parts.append(
                f"Angehängte Unterlagen (extrahiert, lokal):\n{_txt(ref[:10000])}"
            )
    if idea.title:
        parts.append(f"Arbeitstitel der Fachabteilung: {_txt(idea.title, structured=True)}")
    if idea.fachabteilung:
        parts.append(
            f"Einreichende Fachabteilung: {_txt(idea.fachabteilung, structured=True)}"
        )
    if idea.internal_pt_human is not None:
        parts.append(
            f"Eigene Schätzung der Fachabteilung: {idea.internal_pt_human} Personentage intern "
            "(als Vergleichswert, nicht ungeprüft übernehmen)."
        )
    if idea.external_cost_human is not None:
        parts.append(
            f"Eigene Schätzung der Fachabteilung: CHF {idea.external_cost_human} externe Kosten "
            "(als Vergleichswert, nicht ungeprüft übernehmen)."
        )
    parts.append("\nJSON:")
    return "\n\n".join(parts)


def assess_project_idea_with_ai(
    idea_id: int,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    assess_tasks: set[str] | None = None,
    source_tasks: set[str] | None = None,
    input_provider: str = "",
    input_model: str = "",
) -> Optional[ProjectIdea]:
    """KI-Vorbewertung einer Projektidee. Schreibt ausschliesslich in ai_*-Spalten."""
    idea = get_idea(idea_id)
    if not idea:
        return None

    tasks = assess_tasks or set(DEFAULT_ASSESS_TASKS)
    src_tasks = source_tasks or set(DEFAULT_SOURCE_TASKS)
    system_prompt = _build_assess_system_prompt(tasks)
    from .m16_idea_visual import is_cloud_llm_provider

    assess_cloud = is_cloud_llm_provider(provider)

    messages = [{"role": "user", "content": _build_user_prompt(
        idea, src_tasks, input_provider, input_model, assess_cloud=assess_cloud,
    )}]
    bundle = _idea_source_bundle(idea)
    images = None
    if bundle and "vision_images" in src_tasks:
        filtered = filter_bundle_for_source_tasks(bundle, src_tasks)
        imgs = filtered.image_payload()
        model_id = get_model_id(provider, model) or model
        if imgs and model_supports_vision(provider, model_id):
            images = imgs
    raw = try_models_with_messages(
        provider,
        system_prompt,
        messages,
        max_tokens=1800,
        temperature=0.3,
        model=model,
        images=images,
    )

    parsed: dict[str, Any] = {}
    if raw:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, count=1)
            text = re.sub(r"\s*```\s*$", "", text)
        m = _JSON_BLOCK_RE.search(text)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                log.warning("Idea-Assessment: LLM JSON parse failed: %s", raw[:300])

    def _num(key: str) -> Optional[float]:
        v = parsed.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    if not parsed:
        # KI-Antwort nicht auswertbar (Timeout, Rate-Limit, kaputtes JSON) — bestehende
        # ai_*-Werte NICHT anfassen. Aufrufer erkennt den Fehlschlag an None-Rueckgabe.
        log.warning("Idea-Assessment fuer idea_id=%s ohne verwertbares JSON.", idea_id)
        return None

    challenges = parsed.get("challenges")
    if not isinstance(challenges, list):
        challenges = []
    phases = parsed.get("phases")
    if not isinstance(phases, list):
        phases = []

    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if not obj:
            return None
        if "project_name" in tasks:
            obj.ai_project_name = (parsed.get("project_name") or "")[:120] or None
        if "summary" in tasks:
            obj.ai_summary = parsed.get("summary") or None
        if "resources" in tasks:
            obj.ai_internal_pt = _num("internal_pt")
            obj.ai_internal_pt_reasoning = parsed.get("internal_pt_reasoning") or None
            obj.ai_external_cost = _num("external_cost_chf")
            obj.ai_external_cost_reasoning = parsed.get("external_cost_reasoning") or None
        if "challenges" in tasks:
            obj.ai_challenges_json = json.dumps(challenges, ensure_ascii=False)
        if "phases" in tasks:
            obj.ai_phases_json = json.dumps(phases, ensure_ascii=False)
        if "recommendation" in tasks:
            obj.ai_recommendation = parsed.get("recommendation") or None
        obj.ai_provider = provider
        obj.ai_model = model
        obj.ai_raw_json = raw or None
        obj.ai_assessed_at = datetime.now(timezone.utc)
        obj.status = "bewertet"
        obj.updated_at = datetime.now(timezone.utc)
        ses.add(obj)
        ses.commit()
        ses.refresh(obj)
        return obj
