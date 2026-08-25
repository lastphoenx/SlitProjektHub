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
from typing import Any, Optional

from sqlalchemy import Boolean, Column, Float, String, text
from sqlmodel import Field, Session, SQLModel, select

from .m03_db import engine, get_session
from .m08_llm import try_models_with_messages

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
        # zukünftige Spalten hier ergänzen


def create_idea(
    *,
    idea_text: str,
    title: str | None = None,
    fachabteilung: str | None = None,
    internal_pt_human: float | None = None,
    external_cost_human: float | None = None,
    image_path: str | None = None,
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

_SYSTEM_PROMPT = (
    "Du bist eine erfahrene Projektportfolio-Managerin in einer öffentlichen Verwaltung "
    "(Schweiz). Du beurteilst eine ROHE PROJEKTIDEE, bevor ein formales Projekt existiert. "
    "Ziel: der Fachabteilung und dem Projektportfolio-Board eine strukturierte, nüchterne "
    "Ersteinschätzung liefern — keine Marketingsprache, keine Übertreibungen, realistische "
    "Grössenordnungen. Wenn Angaben fehlen, schätze konservativ und mache die Unsicherheit "
    "in der Begründung explizit.\n\n"
    "Antworte AUSSCHLIESSLICH mit einem einzigen JSON-Objekt, exakt in diesem Schema "
    "(keine weiteren Felder, kein Fliesstext davor oder danach):\n"
    "{\n"
    '  "project_name": "kurzer, prägnanter Projektname, max. 6 Wörter",\n'
    '  "summary": "Zusammenfassung der Idee in 3-5 Sätzen, sachlich",\n'
    '  "internal_pt": Zahl,  // geschätzte Personentage der Fachabteilung, ganze Zahl\n'
    '  "internal_pt_reasoning": "kurze Begründung der Schätzung",\n'
    '  "external_cost_chf": Zahl,  // geschätzte externe Kosten in CHF (Dienstleister/Lizenzen)\n'
    '  "external_cost_reasoning": "kurze Begründung der Schätzung",\n'
    '  "challenges": [\n'
    '    {"title": "kurzer Titel", "description": "1-2 Sätze", "severity": "niedrig|mittel|hoch"}\n'
    "  ],\n"
    '  "phases": [\n'
    '    {"name": "Phasenname", "description": "1-2 Sätze", "duration_estimate": "grobe Dauer, '
    'z.B. \'2-3 Wochen\' oder \'1-2 Monate\' — NIEMALS ein Kalenderdatum"}\n'
    "  ],\n"
    '  "recommendation": "1-2 Sätze Handlungsempfehlung: z.B. weiterverfolgen, mit welchen '
    'offenen Fragen, oder eher nicht"\n'
    "}\n\n"
    "Regeln:\n"
    "- Phasen: 3 bis 6 grobe Phasen (z.B. Analyse, Konzept, Umsetzung, Test, Einführung) — "
    "keine Datumsangaben, nur Durchlaufzeiten pro Phase.\n"
    "- challenges: 2 bis 5 Einträge, die grössten Risiken/Unsicherheiten zuerst.\n"
    "- Zahlen sind reine Zahlen ohne Einheiten/Tausendertrennzeichen im JSON."
)


def _build_user_prompt(idea: ProjectIdea) -> str:
    parts = [f"Projektidee (Rohtext):\n{idea.idea_text}"]
    if idea.title:
        parts.append(f"Arbeitstitel der Fachabteilung: {idea.title}")
    if idea.fachabteilung:
        parts.append(f"Einreichende Fachabteilung: {idea.fachabteilung}")
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
) -> Optional[ProjectIdea]:
    """KI-Vorbewertung einer Projektidee. Schreibt ausschliesslich in ai_*-Spalten."""
    idea = get_idea(idea_id)
    if not idea:
        return None

    messages = [{"role": "user", "content": _build_user_prompt(idea)}]
    raw = try_models_with_messages(
        provider,
        _SYSTEM_PROMPT,
        messages,
        max_tokens=1800,
        temperature=0.3,
        model=model,
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
        obj.ai_project_name = (parsed.get("project_name") or "")[:120] or None
        obj.ai_summary = parsed.get("summary") or None
        obj.ai_internal_pt = _num("internal_pt")
        obj.ai_internal_pt_reasoning = parsed.get("internal_pt_reasoning") or None
        obj.ai_external_cost = _num("external_cost_chf")
        obj.ai_external_cost_reasoning = parsed.get("external_cost_reasoning") or None
        obj.ai_challenges_json = json.dumps(challenges, ensure_ascii=False)
        obj.ai_phases_json = json.dumps(phases, ensure_ascii=False)
        obj.ai_recommendation = parsed.get("recommendation") or None
        obj.ai_provider = provider
        obj.ai_model = model
        obj.ai_raw_json = raw or None
        obj.ai_assessed_at = datetime.now(timezone.utc)
        obj.status = "bewertet" if parsed else obj.status
        obj.updated_at = datetime.now(timezone.utc)
        ses.add(obj)
        ses.commit()
        ses.refresh(obj)
        return obj
