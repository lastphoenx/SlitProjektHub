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
    html_path: Optional[str] = Field(default=None, sa_column=Column(String(400)))
    html_generated_at: Optional[datetime] = None
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

    # -- Fachliche Overlay-Einschätzung (User, nie mit ai_* vermischt) --
    user_summary: Optional[str] = None
    user_internal_pt: Optional[float] = Field(default=None, sa_column=Column(Float))
    user_internal_pt_reasoning: Optional[str] = None
    user_external_cost: Optional[float] = Field(default=None, sa_column=Column(Float))
    user_external_cost_reasoning: Optional[str] = None
    user_challenges_json: Optional[str] = None
    user_phases_json: Optional[str] = None
    user_recommendation: Optional[str] = None
    user_assessed_at: Optional[datetime] = None

    @property
    def challenges(self) -> list[dict[str, Any]]:
        return _safe_json_list(self.ai_challenges_json)

    @property
    def phases(self) -> list[dict[str, Any]]:
        return _safe_json_list(self.ai_phases_json)

    @property
    def has_user_assessment(self) -> bool:
        return self.user_assessed_at is not None


def _safe_json_list(raw: Optional[str]) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


_LEVELS = ("niedrig", "mittel", "hoch")
_DURATION_RE = re.compile(
    r"(?P<a>\d+(?:[.,]\d+)?)(?:\s*(?:[-–—]|bis)\s*(?P<b>\d+(?:[.,]\d+)?))?\s*"
    r"(?P<u>jahre?|monate?|wochen?|tage?)",
    re.IGNORECASE,
)


def _parse_level(val: Any, default: str = "mittel") -> str:
    s = str(val or "").strip().lower()
    if s in _LEVELS:
        return s
    aliases = {"high": "hoch", "medium": "mittel", "low": "niedrig", "hoch": "hoch"}
    return aliases.get(s, default)


def _parse_opt_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace("'", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_duration_weeks(text: str) -> Optional[float]:
    """Grobe Dauer in Kalenderwochen. '2-3 Monate' → 10.75, '4 Wochen' → 4."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    m = _DURATION_RE.search(raw)
    if not m:
        return None

    def _n(s: str) -> float:
        return float(s.replace(",", "."))

    a = _n(m.group("a"))
    b = _n(m.group("b")) if m.group("b") else a
    avg = (a + b) / 2.0
    unit = m.group("u")
    if unit.startswith("jahr"):
        return round(avg * 52, 2)
    if unit.startswith("monat"):
        return round(avg * 4.3, 2)
    if unit.startswith("woche"):
        return round(avg, 2)
    if unit.startswith("tag"):
        return round(avg / 7.0, 2)
    return None


def normalize_challenge(item: Any) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    sev = _parse_level(item.get("severity"))
    like = _parse_level(item.get("likelihood") or item.get("probability") or sev)
    return {
        "title": title[:160],
        "description": str(item.get("description") or "").strip()[:800],
        "severity": sev,
        "likelihood": like,
    }


def normalize_phase(item: Any) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or item.get("title") or "").strip()
    if not name:
        return None
    dur = str(item.get("duration_estimate") or item.get("duration") or "").strip()[:80]
    pt = _parse_opt_float(item.get("internal_pt"))
    weeks = parse_duration_weeks(dur)
    return {
        "name": name[:160],
        "description": str(item.get("description") or "").strip()[:800],
        "duration_estimate": dur,
        "duration_weeks": weeks,
        "internal_pt": pt,
    }


def _normalize_list(raw: list[Any], fn) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw:
        n = fn(item)
        if n:
            out.append(n)
    return out


def _pick_text(user_val: Optional[str], ai_val: Optional[str], saved: bool) -> Optional[str]:
    if saved and (user_val or "").strip():
        return user_val.strip()
    return (ai_val or "").strip() or None


def _pick_num(user_val: Optional[float], ai_val: Optional[float], saved: bool) -> Optional[float]:
    if saved and user_val is not None:
        return user_val
    return ai_val


def _pick_list(user_json: Optional[str], ai_json: Optional[str], saved: bool, fn) -> list[dict[str, Any]]:
    if saved and user_json is not None:
        return _normalize_list(_safe_json_list(user_json), fn)
    return _normalize_list(_safe_json_list(ai_json), fn)


def effective_assessment(idea: ProjectIdea) -> dict[str, Any]:
    """Report-/Visual-Werte: User, sobald gespeichert und Feld gesetzt, sonst KI."""
    saved = bool(idea.user_assessed_at)
    challenges = _pick_list(idea.user_challenges_json, idea.ai_challenges_json, saved, normalize_challenge)
    phases = _pick_list(idea.user_phases_json, idea.ai_phases_json, saved, normalize_phase)
    internal_pt = _pick_num(idea.user_internal_pt, idea.ai_internal_pt, saved)
    if internal_pt is not None and phases:
        known = [p["internal_pt"] for p in phases if p.get("internal_pt") is not None]
        if not known:
            share = round(internal_pt / len(phases), 1)
            for p in phases:
                p["internal_pt"] = share
    return {
        "saved": saved,
        "summary": _pick_text(idea.user_summary, idea.ai_summary, saved),
        "internal_pt": internal_pt,
        "internal_pt_reasoning": _pick_text(
            idea.user_internal_pt_reasoning, idea.ai_internal_pt_reasoning, saved
        ),
        "external_cost": _pick_num(idea.user_external_cost, idea.ai_external_cost, saved),
        "external_cost_reasoning": _pick_text(
            idea.user_external_cost_reasoning, idea.ai_external_cost_reasoning, saved
        ),
        "challenges": challenges,
        "phases": phases,
        "recommendation": _pick_text(idea.user_recommendation, idea.ai_recommendation, saved),
    }


def ai_defaults_from_idea(idea: ProjectIdea) -> dict[str, Any]:
    return {
        "summary": idea.ai_summary or "",
        "internal_pt": idea.ai_internal_pt,
        "internal_pt_reasoning": idea.ai_internal_pt_reasoning or "",
        "external_cost": idea.ai_external_cost,
        "external_cost_reasoning": idea.ai_external_cost_reasoning or "",
        "challenges": _normalize_list(idea.challenges, normalize_challenge),
        "phases": _normalize_list(idea.phases, normalize_phase),
        "recommendation": idea.ai_recommendation or "",
    }


def form_defaults_from_idea(idea: ProjectIdea) -> dict[str, Any]:
    """Vorbelegung der User-Felder: gespeicherte User-Werte, sonst 1:1 KI."""
    saved = bool(idea.user_assessed_at)
    if saved:
        ch = _normalize_list(_safe_json_list(idea.user_challenges_json), normalize_challenge)
        ph = _normalize_list(_safe_json_list(idea.user_phases_json), normalize_phase)
        return {
            "summary": idea.user_summary or "",
            "internal_pt": idea.user_internal_pt,
            "internal_pt_reasoning": idea.user_internal_pt_reasoning or "",
            "external_cost": idea.user_external_cost,
            "external_cost_reasoning": idea.user_external_cost_reasoning or "",
            "challenges": ch,
            "phases": ph,
            "recommendation": idea.user_recommendation or "",
        }
    return ai_defaults_from_idea(idea)


def save_user_assessment(
    idea_id: int,
    *,
    summary: str,
    internal_pt: Optional[float],
    internal_pt_reasoning: str,
    external_cost: Optional[float],
    external_cost_reasoning: str,
    challenges: list[Any],
    phases: list[Any],
    recommendation: str,
) -> Optional[ProjectIdea]:
    ch = _normalize_list(challenges, normalize_challenge)
    ph = _normalize_list(phases, normalize_phase)
    with get_session() as ses:
        obj = ses.get(ProjectIdea, idea_id)
        if not obj:
            return None
        obj.user_summary = (summary or "").strip() or None
        obj.user_internal_pt = internal_pt
        obj.user_internal_pt_reasoning = (internal_pt_reasoning or "").strip() or None
        obj.user_external_cost = external_cost
        obj.user_external_cost_reasoning = (external_cost_reasoning or "").strip() or None
        obj.user_challenges_json = json.dumps(ch, ensure_ascii=False)
        obj.user_phases_json = json.dumps(ph, ensure_ascii=False)
        obj.user_recommendation = (recommendation or "").strip() or None
        obj.user_assessed_at = datetime.now(timezone.utc)
        obj.updated_at = datetime.now(timezone.utc)
        ses.add(obj)
        ses.commit()
        ses.refresh(obj)
        ses.expunge(obj)
        return obj


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
            ("html_path", "ALTER TABLE project_idea ADD COLUMN html_path VARCHAR(400)"),
            ("html_generated_at", "ALTER TABLE project_idea ADD COLUMN html_generated_at DATETIME"),
            ("source_attachments_json", "ALTER TABLE project_idea ADD COLUMN source_attachments_json TEXT"),
            ("source_reference_text", "ALTER TABLE project_idea ADD COLUMN source_reference_text TEXT"),
            ("user_summary", "ALTER TABLE project_idea ADD COLUMN user_summary TEXT"),
            ("user_internal_pt", "ALTER TABLE project_idea ADD COLUMN user_internal_pt FLOAT"),
            ("user_internal_pt_reasoning", "ALTER TABLE project_idea ADD COLUMN user_internal_pt_reasoning TEXT"),
            ("user_external_cost", "ALTER TABLE project_idea ADD COLUMN user_external_cost FLOAT"),
            ("user_external_cost_reasoning", "ALTER TABLE project_idea ADD COLUMN user_external_cost_reasoning TEXT"),
            ("user_challenges_json", "ALTER TABLE project_idea ADD COLUMN user_challenges_json TEXT"),
            ("user_phases_json", "ALTER TABLE project_idea ADD COLUMN user_phases_json TEXT"),
            ("user_recommendation", "ALTER TABLE project_idea ADD COLUMN user_recommendation TEXT"),
            ("user_assessed_at", "ALTER TABLE project_idea ADD COLUMN user_assessed_at DATETIME"),
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
        obj = ses.get(ProjectIdea, idea_id)
        if obj is not None:
            ses.expunge(obj)
        return obj


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
            '    {"title": "kurzer Titel", "description": "1-2 Sätze", '
            '"severity": "niedrig|mittel|hoch", '
            '"likelihood": "niedrig|mittel|hoch"}\n'
            "  ],"
        )
    if "phases" in tasks:
        fields.append(
            '  "phases": [\n'
            '    {"name": "Phasenname", "description": "1-2 Sätze", '
            '"duration_estimate": "grobe Dauer, z.B. \'2-3 Wochen\' oder \'1-2 Monate\' — NIEMALS ein Kalenderdatum", '
            '"internal_pt": Zahl}\n'
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
        "- challenges: 2 bis 5 Einträge, die grössten Risiken zuerst; severity = Auswirkung, likelihood = Eintrittswahrscheinlichkeit.",
        "- Phasen: 3 bis 6 grobe Phasen — keine Datumsangaben, nur Durchlaufzeiten pro Phase.",
        "- internal_pt je Phase: grobe interne Personentage; Summe ungefähr gleich dem Feld internal_pt.",
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


_KIND_LABELS = {
    "pdf": "PDF",
    "docx": "Word",
    "image": "Bild",
    "text": "Text",
    "other": "Datei",
}


def source_preview_kind(name: str, kind: str) -> str:
    ext = Path(name or "").suffix.lower()
    k = (kind or "").lower()
    if k == "image" or ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return "image"
    if k == "pdf" or ext == ".pdf":
        return "pdf"
    if k == "text" or ext in {".txt", ".md"}:
        return "text"
    if k == "docx" or ext == ".docx":
        return "docx"
    return "other"


def list_source_attachment_views(idea: ProjectIdea) -> list[dict[str, Any]]:
    """Anzeige-Metadaten für gespeicherte Unterlagen (existiert, Grösse, Vorschau)."""
    base = idea_source_attachments_dir()
    out: list[dict[str, Any]] = []
    for item in _load_stored_attachments(idea):
        rel = Path(str(item.get("path") or "")).name
        if not rel:
            continue
        fp = base / rel
        name = str(item.get("original_name") or rel)
        kind = str(item.get("kind") or "other")
        exists = fp.is_file()
        size = item.get("bytes")
        if not isinstance(size, int) or size <= 0:
            size = fp.stat().st_size if exists else 0
        prev = source_preview_kind(name, kind)
        out.append({
            "path": rel,
            "original_name": name,
            "kind": kind,
            "kind_label": _KIND_LABELS.get(kind, kind or "Datei"),
            "bytes": size,
            "exists": exists,
            "preview_kind": prev,
            "previewable": exists and prev in {"image", "pdf", "text", "docx"},
        })
    return out


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

    challenges = _normalize_list(parsed.get("challenges") if isinstance(parsed.get("challenges"), list) else [], normalize_challenge)
    phases = _normalize_list(parsed.get("phases") if isinstance(parsed.get("phases"), list) else [], normalize_phase)

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
