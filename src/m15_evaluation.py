"""
Phase C — Offertbeurteilung: Bieter, Kriterien, Scores, Rangfolge.

AppRole-Gating über m14_auth (can_evaluate, can_view_evaluator_details).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4

from sqlalchemy import Boolean, Column, Float, Integer, String, UniqueConstraint, text
from sqlmodel import Field, Session, SQLModel, select

from .m03_db import engine, get_session
from .m08_llm import try_models_with_messages
from .m09_docs import get_document_by_id
from .m09_rag import retrieve_relevant_chunks_hybrid
from .m16_idea_visual import is_cloud_llm_provider, sanitize_for_cloud_text

log = logging.getLogger(__name__)

_CRITERIA_PREVIEW_CACHE: dict[str, dict[str, Any]] = {}
_CRITERIA_PREVIEW_TTL_SEC = 3600

CRITERION_KINDS = ("eignung", "zuschlag")
ANGEbot_CLASSIFICATION = "Angebot (Bieter)"
ANGEbot_SUBTYPES = (
    "Preisblatt",
    "Bilanz/Erfolgsrechnung",
    "Referenzprojektblatt",
    "Vorbehaltsliste",
    "Management Summary",
    "Grobkonzept/Lösungskonzept",
    "Eignungsnachweis",
    "Zertifizierung",
    "Proof of Concept",
    "Vorstellung Lieferantin",
    "Sonstiges",
)

# Rolle in der Offertbeurteilung (zusätzlich zur globalen DOCUMENT_CLASSIFICATION)
TENDER_ROLES = (
    "eignungskriterien",
    "zuschlagskriterien",
    "bewertungsvorgaben",
    "preisblatt_vorlage",
    "ausschreibungsunterlage",
    "interne_richtlinie",
)
TENDER_ROLE_LABELS = {
    "eignungskriterien": "Eignungskriterien (Vorgabe)",
    "zuschlagskriterien": "Zuschlagskriterien (Vorgabe)",
    "bewertungsvorgaben": "Bewertungsvorgaben (Framework)",
    "preisblatt_vorlage": "Preisblatt-Vorlage (Framework)",
    "ausschreibungsunterlage": "Ausschreibungsunterlage",
    "interne_richtlinie": "Interne Beurteilungsrichtlinie",
}

DEFAULT_PRICE_YEARS = (2027, 2028, 2029, 2030)
VERGABE_SYSTEM_RULES = (
    "Vergaberecht Schweiz (BöB/IVöB): Eignungskriterien sind K.O.-Kriterien (binär erfüllt/nicht erfüllt, "
    "keine Teilpunkte). Zuschlagskriterien werden gewichtet; Gewichte der Top-Level-Zuschlagskriterien "
    "sollten 100% ergeben. Preis-Kriterium nur mit auto_price=true."
)

# Bieter-Subtypen bevorzugt pro Kriterium-Art (für RAG-Filter)
EIGNUNG_BIDDER_SUBTYPES = (
    "Eignungsnachweis", "Referenzprojektblatt", "Zertifizierung", "Bilanz/Erfolgsrechnung",
)
ZUSCHLAG_BIDDER_SUBTYPES = (
    "Grobkonzept/Lösungskonzept", "Management Summary", "Proof of Concept",
    "Referenzprojektblatt", "Vorbehaltsliste",
)

def normalize_chunk_size(raw: int | str | None, *, fallback: int = 1000) -> int:
    """0 = zeilenbasiert (CSV/XLSX); sonst 200–4000."""
    try:
        size = int(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        size = fallback
    if size == 0:
        return 0
    return max(200, min(4000, size))


CHUNK_SIZE_HINTS = {
    "pflichtenheft": 1800,
    "anforderung": 1200,
    "preisblatt": 500,
    "angebot": 1200,
    "faq_csv": 0,
    "xlsx": 0,
    "default": 1000,
}

# Zuschlags-Ranking: Phase 1 = ZK1–7, Phase 2 = Präsentation (z. B. A-01)
RANKING_PHASES = (1, 2)
RANKING_PHASE_LABELS = {
    1: "ZK (Phase 1)",
    2: "Präsentation (Phase 2)",
}

# Preis-Punkte: reciprocal = max × (günstigstes / Angebot) — Unisport-Vorgabe
PRICE_FORMULAS = ("reciprocal", "linear_minmax")
PRICE_FORMULA_LABELS = {
    "reciprocal": "Reziprok: max × (günstigstes Angebot / dieses Angebot)",
    "linear_minmax": "Linear: günstig = max, teuer = 0",
}
DEFAULT_PRICE_FORMULA = "reciprocal"
DEFAULT_RAG_CHUNKS_EXTRACTION = 36
ENRICH_CHILDREN_RAG_LIMIT = 24
ENRICH_CHILDREN_RAG_THRESHOLD = 0.20


class EvaluationProjectConfig(SQLModel, table=True):
    """Projekt-spezifische Offertbeurteilungs-Einstellungen."""
    __tablename__ = "evaluation_project_config"
    project_key: str = Field(sa_column=Column(String(80), primary_key=True, nullable=False))
    price_years_json: str = Field(
        default=json.dumps(list(DEFAULT_PRICE_YEARS)),
        sa_column=Column(String(120), nullable=False),
    )
    vergabe_notes: Optional[str] = Field(default=None, sa_column=Column(String))
    rag_chunks_per_role: int = Field(default=12, sa_column=Column(Integer, nullable=False, default=12))
    rag_chunks_extraction: int = Field(default=36, sa_column=Column(Integer, nullable=False, default=36))
    price_formula: str = Field(
        default=DEFAULT_PRICE_FORMULA,
        sa_column=Column(String(20), nullable=False, default=DEFAULT_PRICE_FORMULA),
    )
    # Default KI für Phase ② Kriterien-Extraktion und ③ Preisblatt (Picker pro Lauf überschreibbar)
    vorgaben_ki_provider: Optional[str] = Field(default=None, sa_column=Column(String(40)))
    vorgaben_ki_model: Optional[str] = Field(default=None, sa_column=Column(String(80)))
    # Default KI für Phase ④ Matrix-Bewertungsvorschlag (ein LLM-Call — nur Output-Rolle)
    bewertung_ki_provider: Optional[str] = Field(default=None, sa_column=Column(String(40)))
    bewertung_ki_model: Optional[str] = Field(default=None, sa_column=Column(String(80)))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Bidder(SQLModel, table=True):
    __tablename__ = "bidder"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_key: str = Field(sa_column=Column(String(80), nullable=False, index=True))
    name: str = Field(sa_column=Column(String(120), nullable=False))
    sort_order: int = Field(default=0)
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Criterion(SQLModel, table=True):
    __tablename__ = "criterion"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_key: str = Field(sa_column=Column(String(80), nullable=False, index=True))
    kind: str = Field(sa_column=Column(String(16), nullable=False))  # eignung | zuschlag
    name: str = Field(sa_column=Column(String(200), nullable=False))
    # Voller Anforderungstext (kann lang sein, z.B. mehrere Absaetze) - name bleibt
    # das kurze Label fuer Matrix/Listen.
    description: Optional[str] = Field(default=None, sa_column=Column(String))
    weight_pct: float = Field(default=0.0, sa_column=Column(Float, nullable=False, default=0.0))
    parent_id: Optional[int] = Field(default=None, foreign_key="criterion.id")
    scale_max: int = Field(default=10, sa_column=Column(Integer, nullable=False, default=10))
    # Zuschlagskriterien wie "Preis": Wert kommt automatisch aus dem Preisblatt
    # (linear zum guenstigsten Angebot), keine manuelle 0-10-Schaetzung.
    auto_price: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    # Zuschlags-Ranking: 1 = ZK (Zwischenrang), 2 = Präsentation nach Einladung (z. B. A-01)
    ranking_phase: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1))
    # Referenzschlüssel aus Pflichtenheft: EK1, F01, T01, F01-001 (name bleibt lesbares Label)
    referenz: Optional[str] = Field(default=None, sa_column=Column(String(16)))
    sort_order: int = 0
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Score(SQLModel, table=True):
    """
    Eine Bewertung pro (Bieter, Kriterium, Quelle) - nicht mehr pro (Bieter, Kriterium)!
    source_key ist "ai" (KI-Vorschlag, gespeichert), "system" (automatisch berechnet,
    z.B. Preis) oder "user:<app_user.id>" (eine Person). Mehrere Personen koennen denselben
    Bieter/Kriterium unabhaengig bewerten - jede Zeile bleibt einzeln sichtbar (Spalten in
    der Matrix), der offizielle Wert fuer die Rangfolge ist der Mittelwert aller "user:*"
    Zeilen (oder der "system"-Wert bei auto_price-Kriterien).
    """
    __tablename__ = "score"
    __table_args__ = (
        UniqueConstraint("bidder_id", "criterion_id", "source_key", name="uq_score_bidder_criterion_source"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    bidder_id: int = Field(foreign_key="bidder.id", index=True)
    criterion_id: int = Field(foreign_key="criterion.id", index=True)
    source_key: str = Field(sa_column=Column(String(32), nullable=False, index=True))
    evaluator_user_id: Optional[int] = Field(default=None, foreign_key="app_user.id", index=True)
    value: float = Field(sa_column=Column(Float, nullable=False))
    justification: Optional[str] = None
    source_chunk_ref: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BidderDocumentLink(SQLModel, table=True):
    __tablename__ = "bidder_document_link"
    __table_args__ = (UniqueConstraint("bidder_id", "document_id", name="uq_bidder_document"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    bidder_id: int = Field(foreign_key="bidder.id", index=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BidderDocumentSubtype(SQLModel, table=True):
    """Mehrfach-Subtypen pro Bieter-Dokument (Ticket 9, analog EvaluationTenderDoc)."""
    __tablename__ = "bidder_document_subtype"
    __table_args__ = (
        UniqueConstraint(
            "bidder_id", "document_id", "doc_subtype",
            name="uq_bidder_doc_subtype",
        ),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    bidder_id: int = Field(foreign_key="bidder.id", index=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    doc_subtype: str = Field(sa_column=Column(String(80), nullable=False))
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EvaluationTenderDoc(SQLModel, table=True):
    """Projekt-Vorgaben/Frameworks für die Offertbeurteilung (verweist auf bestehende Document-Zeilen)."""
    __tablename__ = "evaluation_tender_doc"
    __table_args__ = (
        UniqueConstraint(
            "project_key", "document_id", "tender_role",
            name="uq_eval_tender_doc_role",
        ),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    project_key: str = Field(sa_column=Column(String(80), nullable=False, index=True))
    document_id: int = Field(foreign_key="document.id", index=True)
    tender_role: str = Field(sa_column=Column(String(40), nullable=False))
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PriceItem(SQLModel, table=True):
    """Preisblatt-Zeile eines Bieters: Anzahl x Kosten/Einheit = CHF."""
    __tablename__ = "price_item"
    id: Optional[int] = Field(default=None, primary_key=True)
    bidder_id: int = Field(foreign_key="bidder.id", index=True)
    category: str = Field(sa_column=Column(String(16), nullable=False))  # einmalig | wiederkehrend
    year: Optional[int] = Field(default=None)  # nur bei wiederkehrend
    referenz: Optional[str] = Field(default=None, sa_column=Column(String(16)))  # z.B. "F-01"
    leistungsbeschreibung: str = Field(sa_column=Column(String(300), nullable=False))
    anzahl: float = Field(default=0.0, sa_column=Column(Float, nullable=False, default=0.0))
    einheit: Optional[str] = Field(default=None, sa_column=Column(String(60)))
    kosten_pro_einheit: float = Field(default=0.0, sa_column=Column(Float, nullable=False, default=0.0))
    bemerkung: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    sort_order: int = 0
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def chf(self) -> float:
        return round(self.anzahl * self.kosten_pro_einheit, 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_bidders(project_key: str, include_deleted: bool = False) -> list[Bidder]:
    with get_session() as session:
        q = select(Bidder).where(Bidder.project_key == project_key)
        if not include_deleted:
            q = q.where(Bidder.is_deleted == False)
        return list(session.exec(q.order_by(Bidder.sort_order, Bidder.name)).all())


def create_bidder(project_key: str, name: str) -> Bidder:
    name = (name or "").strip()
    if not name:
        raise ValueError("Bietername erforderlich")
    with get_session() as session:
        bidders = session.exec(
            select(Bidder).where(Bidder.project_key == project_key, Bidder.is_deleted == False)
        ).all()
        bidder = Bidder(
            project_key=project_key,
            name=name,
            sort_order=len(bidders) + 1,
        )
        session.add(bidder)
        session.commit()
        session.refresh(bidder)
        return bidder


def soft_delete_bidder(bidder_id: int) -> None:
    with get_session() as session:
        bidder = session.get(Bidder, bidder_id)
        if bidder:
            bidder.is_deleted = True
            session.add(bidder)
            session.commit()


def list_criteria(project_key: str, include_deleted: bool = False) -> list[Criterion]:
    with get_session() as session:
        q = select(Criterion).where(Criterion.project_key == project_key)
        if not include_deleted:
            q = q.where(Criterion.is_deleted == False)
        return list(session.exec(q.order_by(Criterion.kind, Criterion.sort_order, Criterion.name)).all())


def infer_ranking_phase(name: str, description: str | None = None) -> int:
    """Heuristik: Präsentationskriterien (A-01 etc.) → Phase 2."""
    n = (name or "").strip().lower()
    d = (description or "").strip().lower()
    if re.search(r"\ba-0?1\b", n, re.I):
        return 2
    for token in (
        "angebotspräsentation",
        "angebotspraesentation",
        "präsentation",
        "praesentation",
        "referat",
        "pitch",
    ):
        if token in n or token in d:
            return 2
    return 1


def create_criterion(
    project_key: str,
    kind: str,
    name: str,
    weight_pct: float = 0.0,
    scale_max: int = 10,
    parent_id: Optional[int] = None,
    auto_price: bool = False,
    description: Optional[str] = None,
    ranking_phase: int | None = None,
    referenz: Optional[str] = None,
) -> Criterion:
    kind = (kind or "").strip().lower()
    if kind not in CRITERION_KINDS:
        raise ValueError(f"kind muss {' oder '.join(CRITERION_KINDS)} sein")
    name = (name or "").strip()
    if not name:
        raise ValueError("Kriteriumname erforderlich")
    # Eignungskriterien (BöB/IVöB): immer binär erfüllt/nicht erfüllt - keine
    # Teilpunkte, keine Gewichtung. scale_max=1 erzwingen, sonst waere ein
    # K.O.-Check wie "5 von 10 Punkten besteht" moeglich, was rechtlich falsch ist.
    scale_max = 1 if kind == "eignung" else max(1, int(scale_max))
    if kind == "zuschlag" and weight_pct < 0:
        raise ValueError("Gewicht muss >= 0 sein")
    if auto_price and kind != "zuschlag":
        raise ValueError("auto_price nur bei Zuschlagskriterien sinnvoll")
    phase = 1
    if kind == "zuschlag" and parent_id is None:
        phase = ranking_phase if ranking_phase is not None else infer_ranking_phase(name, description)
        phase = max(1, min(2, int(phase)))
    with get_session() as session:
        criteria = session.exec(
            select(Criterion).where(Criterion.project_key == project_key, Criterion.is_deleted == False)
        ).all()
        crit = Criterion(
            project_key=project_key,
            kind=kind,
            name=name,
            description=(description or "").strip() or None,
            referenz=_store_referenz(referenz),
            weight_pct=float(weight_pct) if kind == "zuschlag" else 0.0,
            parent_id=parent_id,
            scale_max=scale_max,
            auto_price=bool(auto_price),
            ranking_phase=phase,
            sort_order=len(criteria) + 1,
        )
        session.add(crit)
        session.commit()
        session.refresh(crit)
        return crit


def soft_delete_criterion(criterion_id: int) -> None:
    with get_session() as session:
        crit = session.get(Criterion, criterion_id)
        if crit:
            crit.is_deleted = True
            session.add(crit)
            session.commit()


def update_criterion(
    criterion_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    weight_pct: float | None = None,
    scale_max: int | None = None,
    ranking_phase: int | None = None,
    auto_price: bool | None = None,
    referenz: str | None = None,
) -> Criterion:
    with get_session() as session:
        crit = session.get(Criterion, criterion_id)
        if not crit or crit.is_deleted:
            raise ValueError("Kriterium nicht gefunden")
        if name is not None:
            n = (name or "").strip()
            if not n:
                raise ValueError("Kriteriumname erforderlich")
            crit.name = n
        if description is not None:
            crit.description = (description or "").strip() or None
        if crit.kind == "eignung":
            crit.scale_max = 1
        elif scale_max is not None:
            crit.scale_max = max(1, int(scale_max))
        if crit.kind == "zuschlag" and crit.parent_id is None:
            if weight_pct is not None:
                crit.weight_pct = float(weight_pct)
            if ranking_phase is not None:
                crit.ranking_phase = max(1, min(2, int(ranking_phase)))
            if auto_price is not None:
                crit.auto_price = bool(auto_price)
        if referenz is not None:
            crit.referenz = _store_referenz(referenz)
        session.add(crit)
        session.commit()
        session.refresh(crit)
        return crit


def _criterion_to_editor_dict(criterion: Criterion, children: list[Criterion]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": criterion.id,
        "name": criterion.name,
        "description": criterion.description or "",
        "requirement_ref": criterion.referenz or "",
        "scale_max": criterion.scale_max,
        "children": [
            {
                "id": ch.id,
                "name": ch.name,
                "description": ch.description or "",
                "requirement_ref": ch.referenz or "",
                "scale_max": ch.scale_max,
            }
            for ch in children
        ],
    }
    if criterion.kind == "zuschlag":
        row["weight_pct"] = criterion.weight_pct
        row["ranking_phase"] = criterion.ranking_phase or 1
        row["auto_price"] = criterion.auto_price
    return row


def criteria_editor_payload(project_key: str) -> dict[str, Any]:
    """Kriterien-Hierarchie für Tabellen-Editor (Ticket 7)."""
    all_c = list_criteria(project_key)
    children_map: dict[int, list[Criterion]] = {}
    top: list[Criterion] = []
    for c in all_c:
        if c.parent_id:
            children_map.setdefault(c.parent_id, []).append(c)
        else:
            top.append(c)
    for kids in children_map.values():
        kids.sort(key=lambda x: (x.sort_order, x.id or 0))

    eignung: list[dict[str, Any]] = []
    zuschlag: list[dict[str, Any]] = []
    for c in sorted(top, key=lambda x: (x.sort_order, x.id or 0)):
        row = _criterion_to_editor_dict(c, children_map.get(c.id, []))
        if c.kind == "eignung":
            eignung.append(row)
        else:
            zuschlag.append(row)
    return {"eignung": eignung, "zuschlag": zuschlag}


def save_criteria_editor_payload(
    project_key: str,
    data: dict[str, Any],
    *,
    deleted_ids: list[int] | None = None,
    confirm_active_evaluation: bool = False,
) -> dict[str, int]:
    """Upsert aus Tabellen-Editor (Ticket 7)."""
    validate_criteria_manage_save(
        project_key, data, deleted_ids, confirm_active_evaluation=confirm_active_evaluation,
    )
    stats = {"updated": 0, "created": 0, "deleted": 0}
    deleted_set: set[int] = set()
    for did in deleted_ids or []:
        try:
            deleted_set.add(int(did))
        except (TypeError, ValueError):
            pass

    def _save_row(kind: str, entry: dict[str, Any], parent_id: int | None) -> int | None:
        name = (entry.get("name") or "").strip()
        if not name:
            return parent_id
        is_child = parent_id is not None
        cid = entry.get("id")
        if cid is not None:
            try:
                if int(cid) in deleted_set:
                    return parent_id
            except (TypeError, ValueError):
                pass
        row_id: int | None = None
        if cid:
            try:
                update_criterion(
                    int(cid),
                    name=name,
                    description=entry.get("description"),
                    weight_pct=(
                        float(entry.get("weight_pct") or 0)
                        if not is_child and kind == "zuschlag"
                        else None
                    ),
                    scale_max=int(entry.get("scale_max") or (1 if kind == "eignung" else 10)),
                    ranking_phase=(
                        int(entry["ranking_phase"])
                        if not is_child and kind == "zuschlag" and entry.get("ranking_phase") is not None
                        else None
                    ),
                    auto_price=(
                        bool(entry.get("auto_price"))
                        if not is_child and kind == "zuschlag"
                        else None
                    ),
                    referenz=_entry_referenz(entry) or (entry.get("requirement_ref") or None),
                )
                stats["updated"] += 1
                row_id = int(cid)
            except (ValueError, TypeError):
                row_id = None
        if row_id is None:
            crit = create_criterion(
                project_key,
                kind,
                name,
                weight_pct=float(entry.get("weight_pct") or 0) if not is_child else 0,
                scale_max=int(entry.get("scale_max") or (1 if kind == "eignung" else 10)),
                parent_id=parent_id,
                auto_price=bool(entry.get("auto_price")),
                description=entry.get("description"),
                ranking_phase=(
                    int(entry["ranking_phase"])
                    if not is_child and entry.get("ranking_phase") is not None
                    else None
                ),
                referenz=_entry_referenz(entry) or (entry.get("requirement_ref") or None),
            )
            stats["created"] += 1
            row_id = crit.id
        for child in entry.get("children") or []:
            _save_row(kind, child, row_id)
        return row_id

    for entry in data.get("eignung") or []:
        _save_row("eignung", entry, None)
    for entry in data.get("zuschlag") or []:
        _save_row("zuschlag", entry, None)

    for did in deleted_set:
        try:
            soft_delete_criterion(did)
            stats["deleted"] += 1
        except (TypeError, ValueError):
            pass
    return stats


def update_criterion_ranking_phase(criterion_id: int, ranking_phase: int) -> None:
    """Phase 1 = ZK-Zwischenrang, Phase 2 = Präsentation (nur Top-Level-Zuschlag)."""
    phase = max(1, min(2, int(ranking_phase)))
    with get_session() as session:
        crit = session.get(Criterion, criterion_id)
        if not crit or crit.is_deleted:
            raise ValueError("Kriterium nicht gefunden")
        if crit.kind != "zuschlag" or crit.parent_id is not None:
            raise ValueError("ranking_phase nur für Top-Level-Zuschlagskriterien")
        crit.ranking_phase = phase
        session.add(crit)
        session.commit()


def _user_source_key(evaluator_user_id: int) -> str:
    return f"user:{evaluator_user_id}"


def get_score(bidder_id: int, criterion_id: int, source_key: str = "") -> Optional[Score]:
    """Ohne source_key: irgendeine Zeile (Altverhalten). Für UI immer source_key angeben."""
    with get_session() as session:
        q = select(Score).where(Score.bidder_id == bidder_id, Score.criterion_id == criterion_id)
        if source_key:
            q = q.where(Score.source_key == source_key)
        return session.exec(q).first()


def list_scores_for_cell(bidder_id: int, criterion_id: int) -> list[Score]:
    """Alle Quellen (KI, System, jeder Bewerter) für eine Matrix-Zelle."""
    with get_session() as session:
        return list(
            session.exec(
                select(Score)
                .where(Score.bidder_id == bidder_id, Score.criterion_id == criterion_id)
                .order_by(Score.source_key)
            ).all()
        )


def list_scores_for_project(project_key: str) -> list[Score]:
    with get_session() as session:
        bidder_ids = [
            b.id for b in session.exec(
                select(Bidder).where(Bidder.project_key == project_key, Bidder.is_deleted == False)
            ).all()
        ]
        if not bidder_ids:
            return []
        return list(session.exec(select(Score).where(Score.bidder_id.in_(bidder_ids))).all())


def official_score(
    bidder_id: int,
    criterion: Criterion,
    scores: Optional[list[Score]] = None,
) -> Optional[float]:
    """
    Offizieller Wert für Rangfolge/Matrix-Hauptzelle:
    - auto_price-Kriterium: der "system"-Wert (Preisblatt-Berechnung), falls vorhanden.
    - sonst: Mittelwert aller "user:*"-Zeilen (KI/System zählen nicht mit).
    """
    rows = scores if scores is not None else list_scores_for_cell(bidder_id, criterion.id)
    if criterion.auto_price:
        sys_row = next((s for s in rows if s.source_key == "system"), None)
        return sys_row.value if sys_row else None
    user_values = [s.value for s in rows if s.source_key.startswith("user:")]
    if not user_values:
        return None
    return round(sum(user_values) / len(user_values), 3)


def rolled_up_score(
    bidder_id: int,
    criterion: Criterion,
    all_criteria: list[Criterion],
    scores_by_cell: dict[tuple[int, int], list[Score]],
) -> tuple[Optional[float], int, int]:
    """
    Offizieller Wert eines TOP-LEVEL-Zuschlagskriteriums, inkl. Unterfragen-Rollup:
    hat das Kriterium Einzelanforderungen (Unterfragen), ist sein Wert per
    Ausschreibungs-Vorgabe der Mittelwert der einzeln bewerteten Anforderungen
    ("Punkte = erreichte Punktzahl / Anzahl der Einzelanforderungen") - NICHT ein
    eigener manueller Wert am Elternkriterium. Ohne Unterfragen (z.B. PoC,
    Angebotspräsentation) bleibt es eine direkte Bewertung.

    Gibt (wert, beantwortet, gesamt) zurück - beantwortet/gesamt für die Anzeige
    "5 von 8 Anforderungen bewertet".
    """
    children = [c for c in all_criteria if c.parent_id == criterion.id and not c.is_deleted]
    if not children:
        val = official_score(bidder_id, criterion, scores_by_cell.get((bidder_id, criterion.id), []))
        return val, (1 if val is not None else 0), 1
    child_vals = [
        official_score(bidder_id, ch, scores_by_cell.get((bidder_id, ch.id), []))
        for ch in children
    ]
    answered = [v for v in child_vals if v is not None]
    if not answered:
        return None, 0, len(children)
    return round(sum(answered) / len(answered), 3), len(answered), len(children)


def score_requires_justification(criterion: Criterion, value: float) -> bool:
    """Begründungspflicht: Eignung nicht erfüllt oder Zuschlag unter Maximalpunktzahl."""
    if criterion.auto_price:
        return False
    scale_max = max(1, criterion.scale_max)
    val = float(value)
    if criterion.kind == "eignung":
        return not _eignung_pass(val, scale_max)
    if criterion.kind == "zuschlag":
        return val < scale_max - 1e-6
    return False


def validate_score_justification(
    criterion: Criterion,
    value: float,
    justification: str | None,
    *,
    as_source: str | None = None,
) -> None:
    """Wirft ValueError wenn Pflicht-Begründung fehlt (nur menschliche Bewertungen)."""
    if as_source in ("ai", "system"):
        return
    if score_requires_justification(criterion, value) and not (justification or "").strip():
        if criterion.kind == "eignung":
            raise ValueError(
                f"«{criterion.name}»: Begründung erforderlich — Kriterium nicht erfüllt (Ausschluss)."
            )
        raise ValueError(
            f"«{criterion.name}»: Begründung erforderlich — Abzug von der Maximalpunktzahl "
            f"({value} / {criterion.scale_max})."
        )


def validate_user_score_not_blind_ai_copy(
    bidder_id: int,
    criterion_id: int,
    criterion: Criterion,
    value: float,
    justification: str | None,
    *,
    ai_reference_justification: str | None = None,
) -> None:
    """Verhindert 1:1-Übernahme der KI-Begründung unter eigenem Namen bei Abzug/K.O."""
    if not score_requires_justification(criterion, value):
        return
    user_j = (justification or "").strip()
    if not user_j:
        return

    ref = (ai_reference_justification or "").strip()
    if not ref:
        ai_row = get_score(bidder_id, criterion_id, "ai")
        if ai_row and ai_row.justification:
            ref = ai_row.justification.strip()

    if ref and user_j == ref:
        raise ValueError(
            f"«{criterion.name}»: Begründung ist identisch mit dem KI-Vorschlag — bitte lesen, "
            "anpassen oder ergänzen, bevor Sie unter Ihrem Namen speichern (Rekursfähigkeit BöB/IVöB)."
        )


def list_missing_justifications(project_key: str) -> list[dict[str, Any]]:
    """Offene Begründungspflichten (menschl. Bewertungen, inkl. Unterfragen)."""
    bidders = {b.id: b for b in list_bidders(project_key)}
    criteria = {c.id: c for c in list_criteria(project_key)}
    missing: list[dict[str, Any]] = []
    for score in list_scores_for_project(project_key):
        if not score.source_key.startswith("user:"):
            continue
        crit = criteria.get(score.criterion_id)
        bidder = bidders.get(score.bidder_id)
        if not crit or not bidder:
            continue
        if score_requires_justification(crit, score.value) and not (score.justification or "").strip():
            missing.append(
                {
                    "bidder_id": bidder.id,
                    "bidder_name": bidder.name,
                    "criterion_id": crit.id,
                    "criterion_name": crit.name,
                    "parent_id": crit.parent_id,
                    "kind": crit.kind,
                    "value": score.value,
                    "scale_max": crit.scale_max,
                }
            )
    return missing


EVALUATOR_DISCREPANCY_MIN_SPREAD = 3.0
EVALUATOR_DISCREPANCY_MIN_RATIO = 0.25


def project_evaluation_started(project_key: str) -> bool:
    """True sobald mindestens ein Score (beliebige Quelle) im Projekt existiert."""
    return bool(list_scores_for_project(project_key))


def criterion_ids_with_scores(project_key: str) -> set[int]:
    return {s.criterion_id for s in list_scores_for_project(project_key)}


def criterion_has_scores(criterion_id: int) -> bool:
    with get_session() as session:
        row = session.exec(
            select(Score).where(Score.criterion_id == criterion_id).limit(1)
        ).first()
        return row is not None


def _criterion_entry_structural_change(crit: Criterion, entry: dict[str, Any]) -> bool:
    if (entry.get("name") or "").strip() != (crit.name or "").strip():
        return True
    if crit.kind == "zuschlag":
        if abs(float(entry.get("weight_pct") or 0) - float(crit.weight_pct or 0)) > 1e-6:
            return True
        if int(entry.get("scale_max") or 10) != int(crit.scale_max or 10):
            return True
        if int(entry.get("ranking_phase") or 1) != int(crit.ranking_phase or 1):
            return True
        if bool(entry.get("auto_price")) != bool(crit.auto_price):
            return True
    return False


def _criteria_payload_has_risky_changes(
    project_key: str,
    data: dict[str, Any],
    deleted_ids: list[int] | None,
) -> bool:
    if deleted_ids:
        return True
    scored = criterion_ids_with_scores(project_key)
    if not scored:
        return False
    by_id = {c.id: c for c in list_criteria(project_key) if c.id is not None}

    def walk(entries: list[dict[str, Any]] | None) -> bool:
        for entry in entries or []:
            cid = entry.get("id")
            if cid is None:
                return True
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                return True
            crit = by_id.get(cid_int)
            if crit is None:
                return True
            if cid_int in scored and _criterion_entry_structural_change(crit, entry):
                return True
            if walk(entry.get("children")):
                return True
        return False

    if walk(data.get("eignung")) or walk(data.get("zuschlag")):
        return True
    return False


def validate_criteria_manage_save(
    project_key: str,
    data: dict[str, Any],
    deleted_ids: list[int] | None,
    *,
    confirm_active_evaluation: bool,
) -> None:
    """Blockiert strukturelle Kriterienänderungen nach Bewertungsbeginn ohne Bestätigung."""
    if not project_evaluation_started(project_key):
        return
    if not _criteria_payload_has_risky_changes(project_key, data, deleted_ids):
        return
    if not confirm_active_evaluation:
        raise ValueError(
            "Bewertung läuft bereits — strukturelle Änderungen (Gewicht, Löschen, neue Kriterien …) "
            "erfordern ausdrückliche Bestätigung wegen Rekursrisiko (BöB/IVöB)."
        )


def validate_criterion_change_during_evaluation(
    project_key: str,
    *,
    confirm_active_evaluation: bool,
    structural: bool = True,
) -> None:
    if not structural or not project_evaluation_started(project_key):
        return
    if not confirm_active_evaluation:
        raise ValueError(
            "Bewertung läuft bereits — diese Kriterienänderung erfordert Bestätigung "
            "(Rekursrisiko nachträgliche Änderung der Zuschlagskriterien)."
        )


def list_evaluator_score_discrepancies(project_key: str) -> list[dict[str, Any]]:
    """Zellen mit stark abweichenden Bewerter-Punkten (user:*), für Vier-Augen-Hinweis."""
    bidders = {b.id: b for b in list_bidders(project_key)}
    criteria = {c.id: c for c in list_criteria(project_key)}
    by_cell: dict[tuple[int, int], list[float]] = {}
    for score in list_scores_for_project(project_key):
        if not score.source_key.startswith("user:"):
            continue
        key = (score.bidder_id, score.criterion_id)
        by_cell.setdefault(key, []).append(float(score.value))

    out: list[dict[str, Any]] = []
    for (bidder_id, criterion_id), values in by_cell.items():
        if len(values) < 2:
            continue
        crit = criteria.get(criterion_id)
        bidder = bidders.get(bidder_id)
        if not crit or not bidder:
            continue
        spread = max(values) - min(values)
        if crit.kind == "eignung":
            if spread < 0.5:
                continue
        else:
            threshold = max(
                EVALUATOR_DISCREPANCY_MIN_SPREAD,
                EVALUATOR_DISCREPANCY_MIN_RATIO * max(1, crit.scale_max),
            )
            if spread < threshold:
                continue
        mean = round(sum(values) / len(values), 2)
        out.append(
            {
                "bidder_id": bidder.id,
                "bidder_name": bidder.name,
                "criterion_id": crit.id,
                "criterion_name": crit.name,
                "parent_id": crit.parent_id,
                "kind": crit.kind,
                "min_value": min(values),
                "max_value": max(values),
                "spread": round(spread, 2),
                "evaluator_count": len(values),
                "official_mean": mean,
                "scale_max": crit.scale_max,
            }
        )
    out.sort(key=lambda row: (-row["spread"], row["bidder_name"], row["criterion_name"]))
    return out


def price_offers_status(project_key: str) -> dict[str, Any]:
    """
    Preis-Punkte erst wenn alle Bieter ein vollständiges Preisblatt (TCO > 0) haben.
  """
    bidders = list_bidders(project_key)
    totals: dict[int, float] = {}
    missing: list[str] = []
    for b in bidders:
        tco = compute_bidder_tco(b.id)["total_inkl_mwst"]
        totals[b.id] = tco
        if not tco or tco <= 0:
            missing.append(b.name)
    ready = bool(bidders) and not missing
    priced = [t for t in totals.values() if t and t > 0]
    return {
        "ready": ready,
        "missing_bidders": missing,
        "bidder_count": len(bidders),
        "priced_count": len(priced),
        "totals": totals,
        "cheapest": min(priced) if priced else None,
        "priciest": max(priced) if priced else None,
    }


def clear_price_system_scores(project_key: str) -> None:
    """Entfernt automatische Preis-Scores (z. B. wenn nicht alle Offerten da sind)."""
    crit_ids = [c.id for c in list_criteria(project_key) if c.auto_price]
    if not crit_ids:
        return
    with get_session() as session:
        rows = session.exec(
            select(Score).where(
                Score.criterion_id.in_(crit_ids),
                Score.source_key == "system",
            )
        ).all()
        for row in rows:
            session.delete(row)
        session.commit()


def upsert_score(
    bidder_id: int,
    criterion_id: int,
    evaluator_user_id: int,
    value: float,
    justification: str | None = None,
    source_chunk_ref: str | None = None,
    as_source: str | None = None,
) -> Score:
    """
    Legt/aktualisiert die Zeile zu `evaluator_user_id` an (source_key "user:<id>") -
    andere Bewerter-Zeilen bleiben unberührt, das ist der ganze Punkt der
    Mehrspalten-Bewertung. `as_source="ai"`/`"system"` speichert stattdessen unter
    einer eigenen, klar markierten Spalte statt unter einem User.

    Rechteprüfung (darf `evaluator_user_id` vom aufrufenden User abweichen — nur
    Super-User dürfen fremde Bewertungen korrigieren) ist Sache der Route, nicht
    dieser Funktion.
    """
    with get_session() as session:
        crit = session.get(Criterion, criterion_id)
        if not crit or crit.is_deleted:
            raise ValueError("Kriterium nicht gefunden")
        scale_max = max(1, crit.scale_max)
        value = float(value)
        if value < 0 or value > scale_max:
            raise ValueError(f"Wert muss zwischen 0 und {scale_max} liegen")

        validate_score_justification(crit, value, justification, as_source=as_source)

        source_key = as_source if as_source in ("ai", "system") else _user_source_key(evaluator_user_id)

        existing = session.exec(
            select(Score).where(
                Score.bidder_id == bidder_id,
                Score.criterion_id == criterion_id,
                Score.source_key == source_key,
            )
        ).first()

        now = _now()
        if existing:
            existing.value = value
            existing.justification = justification
            existing.source_chunk_ref = source_chunk_ref
            existing.updated_at = now
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        score = Score(
            bidder_id=bidder_id,
            criterion_id=criterion_id,
            source_key=source_key,
            evaluator_user_id=evaluator_user_id if source_key.startswith("user:") else None,
            value=value,
            justification=justification,
            source_chunk_ref=source_chunk_ref,
            created_at=now,
            updated_at=now,
        )
        session.add(score)
        session.commit()
        session.refresh(score)
        return score


def link_document_to_bidder(bidder_id: int, document_id: int) -> bool:
    with get_session() as session:
        existing = session.exec(
            select(BidderDocumentLink).where(
                BidderDocumentLink.bidder_id == bidder_id,
                BidderDocumentLink.document_id == document_id,
            )
        ).first()
        if existing:
            return True
        link = BidderDocumentLink(bidder_id=bidder_id, document_id=document_id)
        session.add(link)
        session.commit()
        return True


def unlink_document_from_bidder(bidder_id: int, document_id: int) -> None:
    with get_session() as session:
        for row in session.exec(
            select(BidderDocumentSubtype).where(
                BidderDocumentSubtype.bidder_id == bidder_id,
                BidderDocumentSubtype.document_id == document_id,
            )
        ).all():
            session.delete(row)
        link = session.exec(
            select(BidderDocumentLink).where(
                BidderDocumentLink.bidder_id == bidder_id,
                BidderDocumentLink.document_id == document_id,
            )
        ).first()
        if link:
            session.delete(link)
            session.commit()


def get_bidder_document_ids(bidder_id: int) -> list[int]:
    with get_session() as session:
        return [
            row.document_id
            for row in session.exec(
                select(BidderDocumentLink).where(BidderDocumentLink.bidder_id == bidder_id)
            ).all()
        ]


def get_bidder_doc_subtypes(bidder_id: int, document_id: int) -> list[str]:
    """Subtypen am Bieter-Link; leer wenn keine gesetzt (Legacy: Document.doc_subtype)."""
    with get_session() as session:
        rows = list(
            session.exec(
                select(BidderDocumentSubtype).where(
                    BidderDocumentSubtype.bidder_id == bidder_id,
                    BidderDocumentSubtype.document_id == document_id,
                ).order_by(BidderDocumentSubtype.doc_subtype)
            ).all()
        )
    if rows:
        return [r.doc_subtype for r in rows]
    doc = get_document_by_id(document_id)
    if doc and (doc.doc_subtype or "").strip():
        return [(doc.doc_subtype or "").strip()]
    return []


def set_bidder_doc_subtypes(
    bidder_id: int,
    document_id: int,
    subtypes: Iterable[str],
) -> None:
    """Alle Subtypen eines Bieter-Dokuments setzen (Mehrfachauswahl, Ticket 9)."""
    wanted = {
        (s or "").strip()
        for s in subtypes
        if (s or "").strip() in ANGEbot_SUBTYPES
    }
    with get_session() as session:
        link = session.exec(
            select(BidderDocumentLink).where(
                BidderDocumentLink.bidder_id == bidder_id,
                BidderDocumentLink.document_id == document_id,
            )
        ).first()
        if not link:
            raise ValueError("Dokument ist nicht mit diesem Bieter verknüpft")
        rows = list(
            session.exec(
                select(BidderDocumentSubtype).where(
                    BidderDocumentSubtype.bidder_id == bidder_id,
                    BidderDocumentSubtype.document_id == document_id,
                )
            ).all()
        )
        current = {r.doc_subtype for r in rows}
        for st in wanted - current:
            session.add(
                BidderDocumentSubtype(
                    bidder_id=bidder_id,
                    document_id=document_id,
                    doc_subtype=st,
                )
            )
        for row in rows:
            if row.doc_subtype not in wanted:
                session.delete(row)
        session.commit()


def list_tender_docs(project_key: str) -> list[EvaluationTenderDoc]:
    with get_session() as session:
        return list(
            session.exec(
                select(EvaluationTenderDoc)
                .where(EvaluationTenderDoc.project_key == project_key)
                .order_by(EvaluationTenderDoc.tender_role, EvaluationTenderDoc.document_id)
            ).all()
        )


def get_tender_document_ids(
    project_key: str,
    roles: tuple[str, ...] | None = None,
) -> list[int]:
    with get_session() as session:
        q = select(EvaluationTenderDoc).where(EvaluationTenderDoc.project_key == project_key)
        rows = list(session.exec(q).all())
    if roles:
        role_set = set(roles)
        rows = [r for r in rows if r.tender_role in role_set]
    seen: set[int] = set()
    out: list[int] = []
    for r in rows:
        if r.document_id in seen:
            continue
        seen.add(r.document_id)
        out.append(r.document_id)
    return out


def get_tender_doc_roles(project_key: str, document_id: int) -> list[str]:
    with get_session() as session:
        rows = list(
            session.exec(
                select(EvaluationTenderDoc).where(
                    EvaluationTenderDoc.project_key == project_key,
                    EvaluationTenderDoc.document_id == document_id,
                ).order_by(EvaluationTenderDoc.tender_role)
            ).all()
        )
    return [r.tender_role for r in rows]


def link_tender_doc(project_key: str, document_id: int, tender_role: str) -> bool:
    role = (tender_role or "").strip().lower()
    if role not in TENDER_ROLES:
        raise ValueError(f"tender_role muss einer von {TENDER_ROLES} sein")
    with get_session() as session:
        existing = session.exec(
            select(EvaluationTenderDoc).where(
                EvaluationTenderDoc.project_key == project_key,
                EvaluationTenderDoc.document_id == document_id,
                EvaluationTenderDoc.tender_role == role,
            )
        ).first()
        if existing:
            return False
        session.add(
            EvaluationTenderDoc(
                project_key=project_key,
                document_id=document_id,
                tender_role=role,
            )
        )
        session.commit()
        return True


def set_tender_doc_roles(project_key: str, document_id: int, roles: Iterable[str]) -> None:
    """Alle Rollen eines Vorgabe-Dokuments setzen (Mehrfachauswahl)."""
    wanted = {
        (r or "").strip().lower()
        for r in roles
        if (r or "").strip().lower() in TENDER_ROLES
    }
    with get_session() as session:
        rows = list(
            session.exec(
                select(EvaluationTenderDoc).where(
                    EvaluationTenderDoc.project_key == project_key,
                    EvaluationTenderDoc.document_id == document_id,
                )
            ).all()
        )
        current = {r.tender_role for r in rows}
        for role in wanted - current:
            session.add(
                EvaluationTenderDoc(
                    project_key=project_key,
                    document_id=document_id,
                    tender_role=role,
                )
            )
        for row in rows:
            if row.tender_role not in wanted:
                session.delete(row)
        session.commit()


def unlink_tender_doc(project_key: str, document_id: int) -> None:
    with get_session() as session:
        row = session.exec(
            select(EvaluationTenderDoc).where(
                EvaluationTenderDoc.project_key == project_key,
                EvaluationTenderDoc.document_id == document_id,
            )
        ).first()
        if row:
            session.delete(row)
            session.commit()


def get_evaluation_config(project_key: str) -> dict[str, Any]:
    with get_session() as session:
        row = session.get(EvaluationProjectConfig, project_key)
    if not row:
        return {
            "price_years": list(DEFAULT_PRICE_YEARS),
            "vergabe_notes": "",
            "rag_chunks_per_role": 12,
            "rag_chunks_extraction": DEFAULT_RAG_CHUNKS_EXTRACTION,
            "price_formula": DEFAULT_PRICE_FORMULA,
            "vorgaben_ki_provider": "",
            "vorgaben_ki_model": "",
            "bewertung_ki_provider": "",
            "bewertung_ki_model": "",
        }
    try:
        years = json.loads(row.price_years_json or "[]")
        years = [int(y) for y in years if y]
    except (TypeError, ValueError, json.JSONDecodeError):
        years = list(DEFAULT_PRICE_YEARS)
    if not years:
        years = list(DEFAULT_PRICE_YEARS)
    formula = (row.price_formula or DEFAULT_PRICE_FORMULA).strip().lower()
    if formula not in PRICE_FORMULAS:
        formula = DEFAULT_PRICE_FORMULA
    return {
        "price_years": years,
        "vergabe_notes": (row.vergabe_notes or "").strip(),
        "rag_chunks_per_role": max(4, min(24, int(row.rag_chunks_per_role or 12))),
        "rag_chunks_extraction": max(
            16, min(48, int(getattr(row, "rag_chunks_extraction", None) or DEFAULT_RAG_CHUNKS_EXTRACTION))
        ),
        "price_formula": formula,
        "vorgaben_ki_provider": (row.vorgaben_ki_provider or "").strip(),
        "vorgaben_ki_model": (row.vorgaben_ki_model or "").strip(),
        "bewertung_ki_provider": (getattr(row, "bewertung_ki_provider", None) or "").strip(),
        "bewertung_ki_model": (getattr(row, "bewertung_ki_model", None) or "").strip(),
    }


def ki_busy_hint(provider: str = "", model: str = "") -> dict[str, Any]:
    """Kurz-Meldung für UI vor langen Offert-KI-Läufen (Ollama-VRAM, Ideen-Queue)."""
    from .m08_llm import have_key, ollama_runtime_status

    p = (provider or "").strip().lower()
    parts: list[str] = []
    if p == "ollama" and have_key("ollama"):
        st = ollama_runtime_status((model or "").strip() or None)
        if st.get("message"):
            parts.append(str(st["message"]))
    try:
        from .m16_idea_jobs import idea_ki_queue_size

        qs = idea_ki_queue_size()
        if qs > 0:
            parts.append(
                f"{qs} Ideen-Auftrag/Aufträge in der Warteschlange — Ollama kann belegt sein."
            )
    except Exception:
        pass
    tail = "KI läuft … bitte warten. Nicht erneut klicken."
    msg = f"{' '.join(parts)} {tail}".strip() if parts else tail
    return {"message": msg, "provider": p, "model": (model or "").strip()}


def resolve_vorgaben_ki(
    project_key: str,
    form_provider: str = "",
    form_model: str = "",
    *,
    global_provider: str = "openai",
    global_model: str = "",
) -> tuple[str, str]:
    """KI für ② Kriterien / ③ Preis: Picker > Projekt-Default > globale KI-Einstellungen."""
    from .m16_idea_visual import resolve_visual_llm

    cfg = get_evaluation_config(project_key)
    fallback_p = (cfg.get("vorgaben_ki_provider") or "").strip() or (global_provider or "openai").strip()
    fallback_m = (cfg.get("vorgaben_ki_model") or "").strip() or (global_model or "").strip()
    return resolve_visual_llm(form_provider, form_model, fallback_p, fallback_m)


def resolve_bewertung_ki(
    project_key: str,
    form_provider: str = "",
    form_model: str = "",
    *,
    global_provider: str = "openai",
    global_model: str = "",
) -> tuple[str, str]:
    """KI für ④ Matrix-Bewertungsvorschlag: Picker > Projekt-Default > globale KI-Einstellungen."""
    from .m16_idea_visual import resolve_visual_llm

    cfg = get_evaluation_config(project_key)
    fallback_p = (cfg.get("bewertung_ki_provider") or "").strip() or (global_provider or "openai").strip()
    fallback_m = (cfg.get("bewertung_ki_model") or "").strip() or (global_model or "").strip()
    return resolve_visual_llm(form_provider, form_model, fallback_p, fallback_m)


def save_evaluation_config(
    project_key: str,
    *,
    price_years: list[int] | None = None,
    vergabe_notes: str | None = None,
    rag_chunks_per_role: int | None = None,
    rag_chunks_extraction: int | None = None,
    price_formula: str | None = None,
    vorgaben_ki_provider: str | None = None,
    vorgaben_ki_model: str | None = None,
    bewertung_ki_provider: str | None = None,
    bewertung_ki_model: str | None = None,
) -> None:
    cfg = get_evaluation_config(project_key)
    if price_years is not None:
        cfg["price_years"] = [int(y) for y in price_years if y]
    if vergabe_notes is not None:
        cfg["vergabe_notes"] = vergabe_notes.strip()
    if rag_chunks_per_role is not None:
        cfg["rag_chunks_per_role"] = max(4, min(24, int(rag_chunks_per_role)))
    if rag_chunks_extraction is not None:
        cfg["rag_chunks_extraction"] = max(16, min(48, int(rag_chunks_extraction)))
    if price_formula is not None:
        pf = (price_formula or "").strip().lower()
        cfg["price_formula"] = pf if pf in PRICE_FORMULAS else DEFAULT_PRICE_FORMULA
    if vorgaben_ki_provider is not None:
        cfg["vorgaben_ki_provider"] = (vorgaben_ki_provider or "").strip()
    if vorgaben_ki_model is not None:
        cfg["vorgaben_ki_model"] = (vorgaben_ki_model or "").strip()
    if bewertung_ki_provider is not None:
        cfg["bewertung_ki_provider"] = (bewertung_ki_provider or "").strip()
    if bewertung_ki_model is not None:
        cfg["bewertung_ki_model"] = (bewertung_ki_model or "").strip()
    with get_session() as session:
        row = session.get(EvaluationProjectConfig, project_key)
        if not row:
            row = EvaluationProjectConfig(project_key=project_key)
        row.price_years_json = json.dumps(cfg["price_years"])
        row.vergabe_notes = cfg["vergabe_notes"] or None
        row.rag_chunks_per_role = cfg["rag_chunks_per_role"]
        row.rag_chunks_extraction = cfg["rag_chunks_extraction"]
        row.price_formula = cfg.get("price_formula", DEFAULT_PRICE_FORMULA)
        row.vorgaben_ki_provider = cfg.get("vorgaben_ki_provider") or None
        row.vorgaben_ki_model = cfg.get("vorgaben_ki_model") or None
        row.bewertung_ki_provider = cfg.get("bewertung_ki_provider") or None
        row.bewertung_ki_model = cfg.get("bewertung_ki_model") or None
        row.updated_at = _now()
        session.add(row)
        session.commit()


def suggest_tender_role(classification: str, filename: str) -> Optional[str]:
    """Heuristik: empfohlene tender_role aus Klassifikation + Dateiname."""
    fn = (filename or "").lower()
    cls = (classification or "").strip()
    if "preisblatt" in fn or "preis_blatt" in fn:
        return "preisblatt_vorlage"
    if "fragen" in fn or "simap" in fn or cls == "FAQ/Fragen-Katalog":
        return None
    if cls == "Pflichtenheft (Projekt)":
        return "ausschreibungsunterlage"
    if cls == "Anforderung/Feature":
        if any(k in fn for k in ("beilage", "bewertung", "matrix", "gewichtung")):
            return "bewertungsvorgaben"
        if any(k in fn for k in ("zuschlag", "wertung")):
            return "zuschlagskriterien"
        return "zuschlagskriterien"
    if cls == "Standard/Richtlinie":
        return "interne_richtlinie"
    if "agb" in fn or "richtlinie" in fn or "strategie" in fn:
        return "interne_richtlinie"
    if cls == "Sonstiges" and ("agb" in fn or "ikt" in fn):
        return "interne_richtlinie"
    return "ausschreibungsunterlage"


def recommended_chunk_size(
    classification: str,
    *,
    tender_role: str | None = None,
    filename: str = "",
    file_ext: str = "",
) -> int:
    """Empfohlene Chunk-Grösse je Dokumenttyp (0 = zeilenbasiert, kein Zeichen-Chunking)."""
    ext = (file_ext or Path(filename).suffix).lower()
    fn = (filename or "").lower()
    role = (tender_role or "").lower()
    if ext in (".csv",) and ("fragen" in fn or "faq" in fn or "simap" in fn):
        return CHUNK_SIZE_HINTS["faq_csv"]
    if ext in (".xlsx", ".xls"):
        return CHUNK_SIZE_HINTS["xlsx"]
    if role == "preisblatt_vorlage" or "preisblatt" in fn:
        return CHUNK_SIZE_HINTS["preisblatt"]
    if cls_match := classification:
        if "Pflichtenheft" in cls_match:
            return CHUNK_SIZE_HINTS["pflichtenheft"]
        if cls_match == "Anforderung/Feature":
            return CHUNK_SIZE_HINTS["anforderung"]
    if classification == ANGEbot_CLASSIFICATION or "angebot" in fn:
        return CHUNK_SIZE_HINTS["angebot"]
    return CHUNK_SIZE_HINTS["default"]


def parse_expected_child_count(text: str, ref: Optional[str] = None) -> Optional[int]:
    """Range-Hint aus Pflichtenheft: «18 Einzelanforderungen», «F01-001 bis F01-008»."""
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"(\d+)\s+Einzelanforderungen", t, re.I)
    if m:
        return int(m.group(1))
    m = re.search(
        r"(EK\d+)-0*(\d+)\s*(?:bis|–|-|to)\s*\1-?0*(\d+)",
        t,
        re.I,
    )
    if m:
        return max(1, int(m.group(3)) - int(m.group(2)) + 1)
    m = re.search(
        r"([A-Za-z])-?0*(\d+)-0*(\d+)\s*(?:bis|–|-|to)\s*\1-?0*(\d+)-0*(\d+)",
        t,
        re.I,
    )
    if m:
        return max(1, int(m.group(5)) - int(m.group(3)) + 1)
    m = re.search(
        r"([A-Za-z])-?0*(\d+)\s*(?:bis|–|-|to)\s*\1-?0*(\d+)",
        t,
        re.I,
    )
    if m:
        return max(1, int(m.group(3)) - int(m.group(2)) + 1)
    if ref:
        nums = _extract_line_numbers_from_text(t, ref)
        if len(nums) >= 2:
            return len(nums)
    return None


def criteria_child_completeness(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Vergleich erkannte vs. erwartete Unterfragen (nur Zuschlag mit Range-Hint)."""
    out: list[dict[str, Any]] = []
    for entry in payload.get("zuschlag") or []:
        if entry.get("auto_price"):
            continue
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        children = list(entry.get("children") or [])
        ref, _ = _resolve_requirement_search(entry)
        expected = parse_expected_child_count(entry.get("description") or "", ref)
        if expected is None:
            expected = parse_expected_child_count(name, ref)
        found = len(children)
        if expected is not None:
            out.append({
                "name": name,
                "ref": ref or "",
                "found": found,
                "expected": expected,
                "complete": found >= expected,
            })
    return out


def criteria_completeness_warnings(items: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for x in items:
        if x.get("complete"):
            continue
        warnings.append(
            f"«{x['name']}» ({x.get('ref') or '?'}): "
            f"{x['found']} von {x['expected']} Einzelanforderungen erkannt"
        )
    return warnings


def validate_criteria_payload(data: dict[str, Any]) -> list[str]:
    """Prüft extrahierte/importierte Kriterien — gibt Warnungen zurück."""
    warnings: list[str] = []
    eignung = data.get("eignung") or []
    zuschlag = data.get("zuschlag") or []
    if not eignung and not zuschlag:
        warnings.append("Keine Kriterien im Payload.")
        return warnings
    if not eignung:
        warnings.append("Keine Eignungskriterien — BöB/IVöB erwartet meist mindestens ein K.O.-Kriterium.")
    top_weights = [
        float(e.get("weight_pct") or 0)
        for e in zuschlag
        if not e.get("parent_id") and e.get("weight_pct") is not None
    ]
    if top_weights:
        total = sum(top_weights)
        if abs(total - 100.0) > 1.0:
            warnings.append(f"Zuschlags-Gewichte summieren sich auf {total:.0f}% (erwartet ~100%).")
    has_price = any(bool(e.get("auto_price")) for e in zuschlag)
    price_named = any("preis" in (e.get("name") or "").lower() for e in zuschlag)
    if price_named and not has_price:
        warnings.append("Preis-Kriterium erkannt, aber auto_price nicht gesetzt — ggf. manuell aktivieren.")
    for kind, entries in (("eignung", eignung), ("zuschlag", zuschlag)):
        for e in entries:
            ename = (e.get("name") or "").strip()
            if kind == "eignung":
                e["children"] = []
            if ename and not (e.get("description") or "").strip():
                warnings.append(f"«{ename}»: Beschreibung fehlt — bitte ergänzen oder in Vorgaben nachziehen.")
            if kind == "zuschlag" and not (e.get("requirement_ref") or "").strip():
                warnings.append(
                    f"«{ename}»: requirement_ref fehlt — Schritt 2 nutzt nur Namen/Beschreibung als Suchbegriff."
                )
            for ch in e.get("children") or []:
                cname = (ch.get("name") or "").strip()
                if cname and not (ch.get("description") or "").strip():
                    warnings.append(f"«{cname}» (Unterkriterium): Beschreibung fehlt.")
    return warnings


def criteria_preview_meta(data: dict[str, Any]) -> dict[str, Any]:
    """Zusammenfassung für die Kriterien-Vorschau (Tabellen-UI)."""
    eignung = data.get("eignung") or []
    zuschlag = data.get("zuschlag") or []
    top_weights = [float(e.get("weight_pct") or 0) for e in zuschlag]
    weight_total = sum(top_weights)
    weight_ok = not top_weights or abs(weight_total - 100.0) <= 1.0
    empty_descriptions: list[str] = []
    for kind, entries in (("eignung", eignung), ("zuschlag", zuschlag)):
        for e in entries:
            ename = (e.get("name") or "").strip()
            if ename and not (e.get("description") or "").strip():
                empty_descriptions.append(ename)
            for ch in e.get("children") or []:
                cname = (ch.get("name") or "").strip()
                if cname and not (ch.get("description") or "").strip():
                    empty_descriptions.append(cname)
    missing_eignung = not eignung
    requires_confirm = missing_eignung or not weight_ok
    completeness = criteria_child_completeness(data)
    return {
        "eignung_count": len(eignung),
        "zuschlag_count": len(zuschlag),
        "weight_total": round(weight_total, 1),
        "weight_ok": weight_ok,
        "missing_eignung": missing_eignung,
        "empty_descriptions": empty_descriptions,
        "requires_confirm": requires_confirm,
        "completeness": completeness,
    }


def store_criteria_preview(project_key: str, body: dict[str, Any]) -> str:
    """Kriterien-Vorschau zwischenlagern (Post-Redirect-Get, Reload-sicher)."""
    _purge_stale_criteria_previews()
    preview_id = uuid4().hex[:12]
    _CRITERIA_PREVIEW_CACHE[preview_id] = {
        "project_key": project_key,
        "stored_at": time.time(),
        **body,
    }
    return preview_id


def load_criteria_preview(preview_id: str, project_key: str) -> dict[str, Any] | None:
    row = _CRITERIA_PREVIEW_CACHE.get((preview_id or "").strip())
    if not row or row.get("project_key") != project_key:
        return None
    if time.time() - float(row.get("stored_at") or 0) > _CRITERIA_PREVIEW_TTL_SEC:
        _CRITERIA_PREVIEW_CACHE.pop(preview_id, None)
        return None
    return row


def _purge_stale_criteria_previews() -> None:
    now = time.time()
    for pid, row in list(_CRITERIA_PREVIEW_CACHE.items()):
        if now - float(row.get("stored_at") or 0) > _CRITERIA_PREVIEW_TTL_SEC:
            _CRITERIA_PREVIEW_CACHE.pop(pid, None)


def criteria_apply_requires_confirm(data: dict[str, Any]) -> bool:
    return bool(criteria_preview_meta(data).get("requires_confirm"))


def _dedupe_rag_docs(docs: list[dict]) -> list[dict]:
    seen: set[int] = set()
    out: list[dict] = []
    for d in docs:
        cid = d.get("chunk_id")
        if cid in seen:
            continue
        seen.add(cid)
        out.append(d)
    return out


def _retrieve_tender_context_multi(
    project_key: str,
    role_queries: list[tuple[tuple[str, ...], str]],
    *,
    limit_per_role: int = 12,
    max_format_chunks: int | None = None,
) -> str:
    """Mehrere RAG-Pässe nach tender_role, dedupliziert."""
    per_pass_docs: list[list[dict]] = []
    for roles, query in role_queries:
        ids = get_tender_document_ids(project_key, roles=roles)
        if not ids:
            per_pass_docs.append([])
            continue
        rag = retrieve_relevant_chunks_hybrid(
            query,
            project_key=project_key,
            limit=limit_per_role,
            threshold=0.28,
            document_ids=tuple(ids),
        )
        per_pass_docs.append(list(rag.get("documents", []))[:limit_per_role])

    merged: list[dict] = []
    for i in range(limit_per_role):
        for batch in per_pass_docs:
            if i < len(batch):
                merged.append(batch[i])

    max_chunks = limit_per_role * max(1, len(role_queries))
    docs = _dedupe_rag_docs(merged)[:max_chunks]
    format_cap = max_format_chunks if max_format_chunks is not None else len(docs)
    return _format_rag_context(
        docs,
        empty_msg="Keine passenden Vorgaben-Stellen gefunden.",
        max_chunks=min(len(docs), format_cap),
    )


def bidder_doc_ids_for_criterion(bidder_id: int, criterion: Criterion) -> list[int]:
    """Bevorzugte Bieter-Dokument-IDs passend zum Kriterium (Mehrfach-Subtyp, Ticket 9)."""
    all_ids = get_bidder_document_ids(bidder_id)
    if not all_ids:
        return []
    if criterion.auto_price or "preis" in (criterion.name or "").lower():
        preferred = ("Preisblatt",)
    elif criterion.kind == "eignung":
        preferred = EIGNUNG_BIDDER_SUBTYPES
    else:
        preferred = ZUSCHLAG_BIDDER_SUBTYPES
    by_subtype: dict[str, list[int]] = {}
    for doc_id in all_ids:
        for st in get_bidder_doc_subtypes(bidder_id, doc_id):
            by_subtype.setdefault(st, []).append(doc_id)
    matched: list[int] = []
    seen: set[int] = set()
    for st in preferred:
        for doc_id in by_subtype.get(st, []):
            if doc_id not in seen:
                seen.add(doc_id)
                matched.append(doc_id)
    return matched or all_ids


def tender_roles_for_criterion(criterion: Criterion) -> tuple[str, ...]:
    """Welche Vorgabe-Rollen für RAG zu einem Kriterium passen."""
    common = ("bewertungsvorgaben", "ausschreibungsunterlage", "interne_richtlinie")
    if criterion.kind == "eignung":
        return ("eignungskriterien",) + common
    return ("zuschlagskriterien",) + common


def _format_rag_context(docs: list[dict], *, empty_msg: str, max_chunks: int = 5) -> str:
    parts: list[str] = []
    for i, d in enumerate(docs[:max_chunks], 1):
        text = (d.get("text") or "")[:1200]
        fname = d.get("filename", "?")
        chunk_id = d.get("chunk_id")
        parts.append(f"[{i}] Datei: {fname}, Chunk {chunk_id}\n{text}")
    return "\n\n".join(parts) or empty_msg


def validate_evaluation_cloud_gate(
    provider: str,
    bidder_id: int,
    cloud_confirm: bool,
) -> Optional[str]:
    """Blockiert Cloud-LLM-Calls mit Bieter-Dokumenten ohne Bestätigungs-Checkbox."""
    if not is_cloud_llm_provider(provider):
        return None
    if get_bidder_document_ids(bidder_id) and not cloud_confirm:
        return "cloud_confirm"
    return None


# ── Preisblatt (TCO) ────────────────────────────────────────────────────────

PRICE_CATEGORIES = ("einmalig", "wiederkehrend")
_MWST_RATE = 0.081


def list_price_items(bidder_id: int, include_deleted: bool = False) -> list[PriceItem]:
    with get_session() as session:
        q = select(PriceItem).where(PriceItem.bidder_id == bidder_id)
        if not include_deleted:
            q = q.where(PriceItem.is_deleted == False)
        return list(session.exec(q.order_by(PriceItem.category, PriceItem.year, PriceItem.sort_order)).all())


def upsert_price_item(
    item_id: Optional[int],
    bidder_id: int,
    category: str,
    leistungsbeschreibung: str,
    anzahl: float,
    kosten_pro_einheit: float,
    year: Optional[int] = None,
    einheit: Optional[str] = None,
    referenz: Optional[str] = None,
    bemerkung: Optional[str] = None,
) -> PriceItem:
    category = (category or "").strip().lower()
    if category not in PRICE_CATEGORIES:
        raise ValueError(f"category muss {' oder '.join(PRICE_CATEGORIES)} sein")
    if category == "wiederkehrend" and not year:
        raise ValueError("Jahr erforderlich bei wiederkehrenden Kosten")
    desc = (leistungsbeschreibung or "").strip()
    if not desc:
        raise ValueError("Leistungsbeschreibung erforderlich")
    with get_session() as session:
        if item_id:
            item = session.get(PriceItem, item_id)
            if not item or item.bidder_id != bidder_id:
                raise ValueError("Preisposition nicht gefunden")
        else:
            existing = session.exec(select(PriceItem).where(PriceItem.bidder_id == bidder_id)).all()
            item = PriceItem(bidder_id=bidder_id, category=category, sort_order=len(existing) + 1)
        item.category = category
        item.year = int(year) if year else None
        item.referenz = (referenz or "").strip() or None
        item.leistungsbeschreibung = desc
        item.anzahl = float(anzahl)
        item.einheit = (einheit or "").strip() or None
        item.kosten_pro_einheit = float(kosten_pro_einheit)
        item.bemerkung = (bemerkung or "").strip() or None
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def delete_price_item(item_id: int) -> None:
    with get_session() as session:
        item = session.get(PriceItem, item_id)
        if item:
            item.is_deleted = True
            session.add(item)
            session.commit()


def compute_bidder_tco(bidder_id: int) -> dict[str, Any]:
    """4-Jahres-TCO: einmalig + wiederkehrend (alle Jahre), je exkl./inkl. MwSt."""
    items = list_price_items(bidder_id)
    einmalig = [i for i in items if i.category == "einmalig"]
    wiederkehrend = [i for i in items if i.category == "wiederkehrend"]

    einmalig_total = round(sum(i.chf for i in einmalig), 2)
    by_year: dict[int, float] = {}
    for i in wiederkehrend:
        by_year[i.year] = round(by_year.get(i.year, 0.0) + i.chf, 2)

    total_exkl = round(einmalig_total + sum(by_year.values()), 2)
    mwst = round(total_exkl * _MWST_RATE, 2)
    return {
        "bidder_id": bidder_id,
        "einmalig_total": einmalig_total,
        "by_year": by_year,
        "total_exkl_mwst": total_exkl,
        "mwst": mwst,
        "total_inkl_mwst": round(total_exkl + mwst, 2),
    }


def compute_price_criterion_value(
    scale_max: int,
    cheapest: float,
    total: float,
    *,
    priciest: float | None = None,
    formula: str = DEFAULT_PRICE_FORMULA,
) -> float:
    """
    Preis-Punkte nach Projekt-Formel.
    reciprocal: Punkte = max × (günstigstes Angebot / zu bewertendes Angebot)
    linear_minmax: günstigstes = max, teuerstes = 0, dazwischen linear
    """
    scale_max = max(1, int(scale_max))
    if total <= 0 or cheapest <= 0:
        return 0.0
    pf = (formula or DEFAULT_PRICE_FORMULA).strip().lower()
    if pf == "linear_minmax" and priciest is not None and priciest > cheapest:
        value = scale_max * (priciest - total) / (priciest - cheapest)
    else:
        value = scale_max * (cheapest / total)
    return max(0.0, min(float(scale_max), round(value, 3)))


def sync_price_criterion_scores(project_key: str) -> dict[str, Any]:
    """
    Schreibt für jedes auto_price-Kriterium den "system"-Score neu — nur wenn alle Bieter
    ein vollständiges Preisblatt haben.
    """
    criteria = [c for c in list_criteria(project_key) if c.auto_price]
    status = price_offers_status(project_key)
    cfg = get_evaluation_config(project_key)
    formula = cfg.get("price_formula", DEFAULT_PRICE_FORMULA)
    if not criteria:
        return {"synced": False, "reason": "no_auto_price_criterion", **status}
    if not status["ready"]:
        clear_price_system_scores(project_key)
        return {"synced": False, "reason": "incomplete_offers", **status}

    bidders = list_bidders(project_key)
    totals = status["totals"]
    cheapest = status["cheapest"]
    priciest = status["priciest"]
    assert cheapest is not None and priciest is not None

    for crit in criteria:
        scale_max = max(1, crit.scale_max)
        for bidder in bidders:
            total = totals[bidder.id]
            value = compute_price_criterion_value(
                scale_max, cheapest, total, priciest=priciest, formula=formula,
            )
            if formula == "linear_minmax":
                formula_note = (
                    f"linear min–max: günstigst CHF {cheapest:,.2f} → {scale_max} Pkt., "
                    f"teuerst CHF {priciest:,.2f} → 0 Pkt."
                )
            else:
                formula_note = (
                    f"reziprok: Punkte = {scale_max} × (günstigstes CHF {cheapest:,.2f} / "
                    f"dieses CHF {total:,.2f})"
                )
            upsert_score(
                bidder.id,
                crit.id,
                evaluator_user_id=0,
                value=value,
                justification=(
                    f"Automatisch (alle {len(bidders)} Offerten). {formula_note} "
                    f"→ {value:.2f}/{scale_max} Punkte."
                ),
                as_source="system",
            )
    return {"synced": True, "reason": None, "price_formula": formula, **status}


# ── Rangfolge ───────────────────────────────────────────────────────────────


def _eignung_pass(value: float, scale_max: int) -> bool:
    """Unterhalb der Hälfte der Skala = nicht geeignet (K.O.). Für Ja/Nein-Fragen (scale_max=1): 1=Ja."""
    return value >= (scale_max / 2.0)


def _zuschlag_weighted_score(
    bidder_id: int,
    zuschlag_criteria: list[Criterion],
    all_criteria: list[Criterion],
    scores_by_cell: dict[tuple[int, int], list[Score]],
    *,
    fill_missing_phase2_at_max: bool = False,
) -> tuple[Optional[float], list[dict[str, Any]]]:
    """
    Gewichteter Zuschlags-Score über die übergebene Kriterienliste (renormalisiert).
    fill_missing_phase2_at_max: Phase-2-Kriterien ohne Bewertung als volle Punktzahl annehmen
    (für «kann noch aufholen?»-Heuristik).
    """
    active = [c for c in zuschlag_criteria if c.weight_pct > 0]
    if not active:
        return None, []
    total_weight = sum(c.weight_pct for c in active)
    weighted_sum = 0.0
    details: list[dict[str, Any]] = []
    any_scored = False
    for crit in active:
        val, _answered, _total = rolled_up_score(bidder_id, crit, all_criteria, scores_by_cell)
        assumed = False
        if val is None:
            if fill_missing_phase2_at_max and int(crit.ranking_phase or 1) >= 2:
                val = float(crit.scale_max)
                assumed = True
            else:
                continue
        any_scored = True
        normalized = val / max(1, crit.scale_max)
        contrib = normalized * crit.weight_pct
        weighted_sum += contrib
        details.append(
            {
                "criterion_id": crit.id,
                "name": crit.name,
                "value": val,
                "weight_pct": crit.weight_pct,
                "ranking_phase": int(crit.ranking_phase or 1),
                "normalized": round(normalized, 4),
                "contrib": round(contrib, 4),
                "assumed_max": assumed,
            }
        )
    if not any_scored:
        return None, details
    return round((weighted_sum / total_weight) * 100.0, 2), details


def compute_rankings(project_key: str) -> list[dict[str, Any]]:
    """
    Rangfolge: erst Eignung (K.O.), dann gewichtete Zuschlagskriterien.
    Nur TOP-LEVEL-Kriterien (parent_id is None) fliessen in die Gewichtung ein.
    Bei Phase-2-Kriterien (z. B. A-01 Präsentation) zusätzlich:
    - interim_score / interim_rank: nur Phase 1, renormalisiert (Einladungsentscheid)
    - max_score: Phase 1 bewertet + Phase 2 hypothetisch voll
    - can_still_win: max_score >= führender interim_score
    """
    bidders = list_bidders(project_key)
    criteria = list_criteria(project_key)
    scores = list_scores_for_project(project_key)

    scores_by_cell: dict[tuple[int, int], list[Score]] = {}
    for s in scores:
        scores_by_cell.setdefault((s.bidder_id, s.criterion_id), []).append(s)

    eignung_top = [c for c in criteria if c.kind == "eignung" and c.parent_id is None]
    eignung_children: dict[int, list] = {}
    for c in criteria:
        if c.kind == "eignung" and c.parent_id is not None:
            eignung_children.setdefault(c.parent_id, []).append(c)
    zuschlag_top = [c for c in criteria if c.kind == "zuschlag" and c.parent_id is None]
    phase1_zuschlag = [c for c in zuschlag_top if int(c.ranking_phase or 1) == 1]
    phase2_zuschlag = [c for c in zuschlag_top if int(c.ranking_phase or 1) >= 2]
    has_phase2 = bool(phase2_zuschlag)
    total_weight = sum(c.weight_pct for c in zuschlag_top if c.weight_pct > 0)

    def _official(bidder_id: int, crit) -> Optional[float]:
        return official_score(bidder_id, crit, scores_by_cell.get((bidder_id, crit.id), []))

    rows: list[dict[str, Any]] = []
    for bidder in bidders:
        ko = False
        eignung_details: list[dict[str, Any]] = []
        for crit in eignung_top:
            children = eignung_children.get(crit.id, [])
            if children:
                child_vals = [(_official(bidder.id, ch), ch) for ch in children]
                answered = [(v, ch) for v, ch in child_vals if v is not None]
                failed = [ch.name for v, ch in answered if not _eignung_pass(v, ch.scale_max)]
                passed = bool(answered) and not failed
                if failed:
                    ko = True
                val = None if not answered else (0.0 if failed else 1.0)
            else:
                val = _official(bidder.id, crit)
                passed = _eignung_pass(val, crit.scale_max) if val is not None else False
                if val is not None and not passed:
                    ko = True
            eignung_details.append(
                {"criterion_id": crit.id, "name": crit.name, "value": val, "passed": passed}
            )

        total_score: Optional[float] = None
        interim_score: Optional[float] = None
        max_score: Optional[float] = None
        zuschlag_details: list[dict[str, Any]] = []
        interim_details: list[dict[str, Any]] = []

        if not ko and total_weight > 0:
            total_score, zuschlag_details = _zuschlag_weighted_score(
                bidder.id, zuschlag_top, criteria, scores_by_cell
            )
            if has_phase2:
                interim_score, interim_details = _zuschlag_weighted_score(
                    bidder.id, phase1_zuschlag, criteria, scores_by_cell
                )
                max_score, _ = _zuschlag_weighted_score(
                    bidder.id,
                    zuschlag_top,
                    criteria,
                    scores_by_cell,
                    fill_missing_phase2_at_max=True,
                )
            else:
                interim_score = total_score
                interim_details = zuschlag_details

        rows.append(
            {
                "bidder_id": bidder.id,
                "bidder_name": bidder.name,
                "ko": ko,
                "eignung": eignung_details,
                "zuschlag": zuschlag_details,
                "interim_zuschlag": interim_details,
                "total_score": total_score,
                "interim_score": interim_score,
                "max_score": max_score,
                "can_still_win": None,
                "has_phase2": has_phase2,
            }
        )

    leader_interim: Optional[float] = None
    if has_phase2:
        interim_vals = [
            r["interim_score"]
            for r in rows
            if not r["ko"] and r["interim_score"] is not None
        ]
        leader_interim = max(interim_vals) if interim_vals else None
        for r in rows:
            if r["ko"] or leader_interim is None or r["max_score"] is None:
                r["can_still_win"] = None
            else:
                r["can_still_win"] = r["max_score"] >= leader_interim - 0.01

    eligible = [r for r in rows if not r["ko"] and r["total_score"] is not None]
    ineligible = [r for r in rows if r["ko"] or r["total_score"] is None]
    eligible.sort(key=lambda r: r["total_score"], reverse=True)
    ranked = eligible + ineligible
    for idx, row in enumerate(ranked, start=1):
        row["rank"] = idx if not row["ko"] and row["total_score"] is not None else None

    if has_phase2:
        interim_eligible = [
            r for r in rows if not r["ko"] and r["interim_score"] is not None
        ]
        interim_ineligible = [
            r for r in rows if r["ko"] or r["interim_score"] is None
        ]
        interim_eligible.sort(key=lambda r: r["interim_score"], reverse=True)
        interim_ranked = interim_eligible + interim_ineligible
        interim_pos = {
            row["bidder_id"]: idx
            for idx, row in enumerate(interim_ranked, start=1)
            if not row["ko"] and row["interim_score"] is not None
        }
        for row in rows:
            row["interim_rank"] = interim_pos.get(row["bidder_id"])
            row["leader_interim_score"] = leader_interim
    else:
        for row in rows:
            row["interim_rank"] = row.get("rank")
            row["leader_interim_score"] = None

    return ranked


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _suggestion_json_keys_instruction(scale_max: int, kind: str) -> str:
    """LLM-JSON-Schema für rekursfähige Bewertungsvorschläge."""
    parts = [
        "Antwort NUR als JSON mit keys:",
        "value (Zahl 0 bis scale_max),",
        "strengths (konkret: was im Angebot gut belegt/erfüllt ist),",
    ]
    if kind == "zuschlag" and scale_max > 1:
        parts.append(
            f"deductions (PFLICHT wenn value < {scale_max}: jeder Punktabzug präzise — "
            "welche Vorgabe fehlt oder im Angebot unzureichend; bei voller Punktzahl leerer String),"
        )
        parts.append(
            "justification (zwei Absätze mit exakt diesen Präfixen: «Positiv: …» und "
            f"«Abzüge ({scale_max} − value P. unter Max.): …»; bei Maximalpunktzahl «Abzüge: keine»),"
        )
    elif kind == "eignung":
        parts.append(
            "deductions (PFLICHT bei Nicht-Erfüllung: konkreter Ausschlussgrund; bei erfüllt leer),"
        )
        parts.append(
            "justification (rekursfähig; bei Nein Abschnitt «Nicht erfüllt: …»),"
        )
    else:
        parts.append("justification (warum diese Punktzahl),")
    parts.append(
        "source_quote (ein wörtliches Angebotszitat als Beleg), "
        "source_chunk_id (Zahl oder null)."
    )
    return " ".join(parts)


def _compose_suggestion_justification(
    criterion: Criterion,
    value: float | None,
    parsed: dict[str, Any],
) -> str:
    """Baut strukturierte Begründung aus strengths/deductions oder Plain-Text."""
    scale_max = max(1, criterion.scale_max)
    strengths = str(parsed.get("strengths") or parsed.get("positiv") or "").strip()
    deductions = str(
        parsed.get("deductions") or parsed.get("abzuege") or parsed.get("abzüge") or ""
    ).strip()
    plain = str(parsed.get("justification") or "").strip()

    parts: list[str] = []
    if strengths:
        parts.append(f"Positiv: {strengths}")

    need_deductions = (
        value is not None
        and criterion.kind == "zuschlag"
        and scale_max > 1
        and float(value) < scale_max - 1e-6
    )
    need_eignung_fail = (
        value is not None
        and criterion.kind == "eignung"
        and not _eignung_pass(float(value), scale_max)
    )

    if deductions:
        if need_deductions:
            gap = scale_max - float(value)
            parts.append(f"Abzüge ({gap:g} P. unter Max. {scale_max:g}): {deductions}")
        elif need_eignung_fail:
            parts.append(f"Nicht erfüllt: {deductions}")
    elif need_deductions or need_eignung_fail:
        m = re.search(r"(?:abzüge|abzuege|nicht erfüllt)\s*:\s*(.+)", plain, re.I | re.S)
        if m and m.group(1).strip().lower() not in ("keine", "—", "-"):
            if need_deductions:
                gap = scale_max - float(value)
                parts.append(
                    f"Abzüge ({gap:g} P. unter Max. {scale_max:g}): {m.group(1).strip()}"
                )
            else:
                parts.append(f"Nicht erfüllt: {m.group(1).strip()}")

    if parts:
        return "\n\n".join(parts)
    return plain


def _suggestion_missing_deduction_rationale(
    criterion: Criterion,
    value: float | None,
    justification: str,
) -> bool:
    """True wenn bei Punktabzug/Eignungs-Nein keine nachvollziehbare Abzugsbegründung vorliegt."""
    if value is None:
        return False
    scale_max = max(1, criterion.scale_max)
    val = float(value)
    j = (justification or "").strip()
    if not j:
        return True

    if criterion.kind == "zuschlag" and scale_max > 1 and val < scale_max - 1e-6:
        m = re.search(r"abzüge[^:\n]*:\s*(.+)", j, re.I | re.S)
        if m:
            body = m.group(1).strip()
            if body.lower() in ("keine", "—", "-", ""):
                return True
            return len(body) < 12
        markers = (
            "abzug", "fehlt", "fehlen", "unzureichend", "nicht nachgewiesen",
            "nicht belegt", "lücke", "unklar", "punktabzug", "mangel",
            "unvollständig", "ohne nachweis", "nicht erfüllt",
        )
        return not any(m in j.lower() for m in markers)

    if criterion.kind == "eignung" and not _eignung_pass(val, scale_max):
        m = re.search(r"nicht erfüllt\s*:\s*(.+)", j, re.I | re.S)
        if m and len(m.group(1).strip()) >= 8:
            return False
        markers = ("nicht erfüllt", "ausgeschlossen", "ko-kriterium", "fehlt", "unzureichend")
        return not any(m in j.lower() for m in markers)

    return False


_DEDUCTION_MISSING_CLAIM_RE = re.compile(
    r"(fehlt|fehlen|keine[n]?\s+(?:ausführliche|detaillierte)?|nicht\s+"
    r"(?:erwähnt|belegt|nachgewiesen|beschrieben|genannt))",
    re.I,
)

_DEDUCTION_TOPIC_PHRASES = (
    "herausforderung",
    "gegenmaßnahme",
    "gegenmassnahme",
    "erfolgsfaktor",
    "mehrwert",
    "innovativ",
    "job-queue",
    "job queue",
    "retry",
    "asynchron",
    "integration",
    "escada",
)


def _positiv_text_from_justification(justification: str, strengths: str = "") -> str:
    positiv_m = re.search(
        r"positiv:\s*(.+?)(?=\n\nabzüge|\Z)",
        justification or "",
        re.I | re.S,
    )
    if positiv_m:
        return positiv_m.group(1).strip()
    return (strengths or "").strip()


def _deductions_text_from_justification(justification: str) -> str:
    ded_m = re.search(r"abzüge[^:]*:\s*(.+)", justification or "", re.I | re.S)
    return ded_m.group(1).strip() if ded_m else ""


def _suggestion_deduction_grounding_issues(
    justification: str,
    offer_context: str,
    strengths: str = "",
) -> list[str]:
    """Ticket 23: Abzugs-Claims gegen Positiv + Angebotskontext (Groundedness light)."""
    deductions = _deductions_text_from_justification(justification)
    if not deductions or not _DEDUCTION_MISSING_CLAIM_RE.search(deductions):
        return []

    positiv = _positiv_text_from_justification(justification, strengths)
    offer_lo = (offer_context or "").lower()
    positiv_lo = positiv.lower()
    deductions_lo = deductions.lower()
    issues: list[str] = []

    for phrase in _DEDUCTION_TOPIC_PHRASES:
        if phrase not in deductions_lo:
            continue
        in_offer = phrase in offer_lo or phrase.replace("-", " ") in offer_lo
        in_positiv = phrase in positiv_lo or phrase.replace("-", " ") in positiv_lo
        if in_offer or in_positiv:
            issues.append(
                f"«{phrase}» in Positiv/Angebot belegt, im Abzug aber als fehlend behauptet"
            )

    for tok in re.findall(r"[a-zäöüß]{6,}", positiv_lo):
        if tok in deductions_lo and tok in offer_lo:
            issues.append(f"Begriff «{tok}» in Positiv und Angebot, Abzug widerspricht")

    return list(dict.fromkeys(issues))[:5]


def _llm_deduction_contradiction_check(
    provider: str,
    model: str | None,
    justification: str,
    offer_context: str,
) -> bool:
    """Ticket 23 Stufe 3: günstiger Konsistenz-Check (nur bei Heuristik-Treffer)."""
    if not (justification or "").strip() or not (offer_context or "").strip():
        return False
    system = (
        "Prüfe, ob der Absatz «Abzüge» Fakten behauptet, die im «Positiv»-Teil oder im "
        "Angebotsauszug bereits belegt sind. Antwort NUR als JSON: "
        '{"contradiction": true/false, "reason": "kurz"}'
    )
    user = (
        f"Begründung:\n{justification}\n\n"
        f"Angebotsauszug:\n{(offer_context or '')[:3500]}\n\nJSON:"
    )
    raw = try_models_with_messages(
        provider,
        system,
        [{"role": "user", "content": user}],
        max_tokens=220,
        temperature=0.0,
        model=model,
    )
    parsed = _parse_suggestion_llm_json(raw)
    return bool(parsed.get("contradiction"))


def _parse_suggestion_llm_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    m = _JSON_BLOCK_RE.search(raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        log.warning("LLM JSON parse failed: %s", raw[:200])
        return {}
    return data if isinstance(data, dict) else {}


def _parse_suggestion_value(parsed: dict[str, Any], scale_max: int) -> Optional[float]:
    value = parsed.get("value")
    try:
        value_f = float(value) if value is not None else None
    except (TypeError, ValueError):
        value_f = None
    if value_f is not None:
        value_f = max(0.0, min(float(scale_max), value_f))
    return value_f


def _build_suggestion_chunk_ref(parsed: dict[str, Any]) -> Optional[str]:
    quote = parsed.get("source_quote") or ""
    chunk_id = parsed.get("source_chunk_id")
    if chunk_id:
        chunk_ref = f"chunk:{chunk_id}"
        if quote:
            chunk_ref += f" | {quote[:1200]}"
        return chunk_ref
    if quote:
        return str(quote)[:1200]
    return None


def suggest_score_with_rag(
    project_key: str,
    bidder_id: int,
    criterion: Criterion,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """RAG + LLM-Vorschlag für eine Bewertung (Mensch bestätigt). Dual-Kontext: Vorgabe + Angebot."""
    req_query = f"{criterion.description or criterion.name} — Anforderung Ausschreibung"
    offer_query = f"{criterion.description or criterion.name} — Nachweis im Angebot"

    cfg = get_evaluation_config(project_key)
    limit = cfg["rag_chunks_per_role"]
    roles = tender_roles_for_criterion(criterion)
    role_groups = [(roles, req_query)]
    tender_context_raw = _retrieve_tender_context_multi(
        project_key, role_groups, limit_per_role=limit
    )
    if tender_context_raw == "Keine passenden Vorgaben-Stellen gefunden.":
        tender_rag = retrieve_relevant_chunks_hybrid(
            req_query,
            project_key=project_key,
            limit=limit,
            threshold=0.35,
            exclude_classification=ANGEbot_CLASSIFICATION,
        )
        tender_context_raw = _format_rag_context(
            tender_rag.get("documents", []),
            empty_msg="Keine passenden Vorgaben-Stellen gefunden.",
            max_chunks=limit,
        )

    bidder_doc_ids = bidder_doc_ids_for_criterion(bidder_id, criterion)
    rag = retrieve_relevant_chunks_hybrid(
        offer_query,
        project_key=project_key,
        limit=limit,
        threshold=0.35,
        classification_filter=ANGEbot_CLASSIFICATION,
        bidder_id=bidder_id,
        document_ids=tuple(bidder_doc_ids) if bidder_doc_ids else None,
    )
    docs = rag.get("documents", [])
    if not docs:
        rag = retrieve_relevant_chunks_hybrid(
            offer_query,
            project_key=project_key,
            limit=limit,
            threshold=0.35,
            bidder_id=bidder_id,
        )
        docs = rag.get("documents", [])

    tender_context = tender_context_raw
    offer_context = _format_rag_context(
        docs, empty_msg="Keine passenden Angebotsstellen gefunden.", max_chunks=limit,
    )
    if is_cloud_llm_provider(provider):
        tender_context = sanitize_for_cloud_text(tender_context)
        offer_context = sanitize_for_cloud_text(offer_context)
    scale_max = max(1, criterion.scale_max)
    system = (
        f"{VERGABE_SYSTEM_RULES}\n"
        "Du bist Vergabesachverständiger. Vergleiche Ausschreibungs-Vorgaben mit dem Angebot "
        "des Bieters und bewerte ein Kriterium. Begründungen müssen rekursfähig sein (BöB/IVöB): "
        "bei jeder Punktzahl unter dem Maximum präzise benennen, welche Vorgaben im Angebot "
        "fehlen, unklar oder unzureichend belegt sind — nicht nur Lob wiederholen. "
        "Vor «Abzüge:» den «Positiv»-Text und den Angebotsauszug gegenprüfen: nichts als fehlend "
        "behaupten, was dort bereits belegt ist (kein Selbstwiderspruch).\n"
        + _suggestion_json_keys_instruction(scale_max, criterion.kind)
    )
    if cfg.get("vergabe_notes"):
        system += f"\nProjekt-Hinweise: {cfg['vergabe_notes']}"
    user = (
        f"Kriterium ({criterion.kind}): {criterion.name}\n"
        + (f"Anforderungstext: {criterion.description}\n" if criterion.description else "")
        + f"Skala: 0 bis {scale_max}\n\n"
        f"VORGABEN (Ausschreibung):\n{tender_context}\n\n"
        f"ANGEBOT (Bieter):\n{offer_context}\n\n"
        "JSON:"
    )
    messages = [{"role": "user", "content": user}]
    raw = try_models_with_messages(
        provider,
        system,
        messages,
        max_tokens=1500,
        temperature=0.2,
        model=model,
    )
    parsed = _parse_suggestion_llm_json(raw)
    value_f = _parse_suggestion_value(parsed, scale_max)
    justification = _compose_suggestion_justification(criterion, value_f, parsed)

    if value_f is not None and _suggestion_missing_deduction_rationale(
        criterion, value_f, justification
    ):
        gap = scale_max - value_f
        retry_system = (
            system
            + f"\n\nKORREKTUR: value={value_f} bei Maximum {scale_max} ({gap:g} P. Abzug). "
            "Das Feld deductions und der Absatz «Abzüge:» sind PFLICHT und müssen konkrete "
            "Lücken/Mängel im Angebot gegenüber der Vorgabe benennen — keine allgemeine Lobeshymne."
        )
        raw_retry = try_models_with_messages(
            provider,
            retry_system,
            messages,
            max_tokens=1500,
            temperature=0.15,
            model=model,
        )
        parsed_retry = _parse_suggestion_llm_json(raw_retry)
        value_retry = _parse_suggestion_value(parsed_retry, scale_max)
        justification_retry = _compose_suggestion_justification(
            criterion, value_retry, parsed_retry
        )
        if value_retry is not None and not _suggestion_missing_deduction_rationale(
            criterion, value_retry, justification_retry
        ):
            parsed, value_f, justification, raw = (
                parsed_retry, value_retry, justification_retry, raw_retry
            )
        elif parsed_retry:
            parsed = parsed_retry
            value_f = value_retry if value_retry is not None else value_f
            justification = justification_retry or justification
            raw = raw_retry

    justification_warning = None
    if value_f is not None and _suggestion_missing_deduction_rationale(
        criterion, value_f, justification
    ):
        justification_warning = (
            f"Abzugsbegründung unvollständig für {value_f}/{scale_max} — bitte manuell ergänzen "
            "oder KI-Vorschlag erneut anstossen."
        )

    grounding_issues = _suggestion_deduction_grounding_issues(
        justification,
        offer_context,
        str(parsed.get("strengths") or ""),
    )
    if (
        grounding_issues
        and value_f is not None
        and criterion.kind == "zuschlag"
        and float(value_f) < scale_max - 1e-6
    ):
        retry_ground = (
            system
            + "\n\nKORREKTUR (Abzugs-Beleg): Folgende Abzugsbehauptungen widersprechen Positiv "
            "oder Angebotsauszug — entfernen oder präzisieren: "
            + "; ".join(grounding_issues)
        )
        raw_ground = try_models_with_messages(
            provider,
            retry_ground,
            messages,
            max_tokens=1500,
            temperature=0.1,
            model=model,
        )
        parsed_ground = _parse_suggestion_llm_json(raw_ground)
        value_ground = _parse_suggestion_value(parsed_ground, scale_max)
        justification_ground = _compose_suggestion_justification(
            criterion, value_ground or value_f, parsed_ground or parsed
        )
        issues_ground = _suggestion_deduction_grounding_issues(
            justification_ground,
            offer_context,
            str((parsed_ground or parsed).get("strengths") or ""),
        )
        if not issues_ground:
            parsed = parsed_ground or parsed
            value_f = value_ground if value_ground is not None else value_f
            justification = justification_ground
            raw = raw_ground
            grounding_issues = []
        else:
            justification = justification_ground or justification
            grounding_issues = issues_ground

    if grounding_issues:
        if _llm_deduction_contradiction_check(provider, model, justification, offer_context):
            warn = (
                "Abzugsbegründung widerspricht Angebot/Positiv (KI-Check): "
                + "; ".join(grounding_issues[:3])
            )
            justification_warning = (
                f"{justification_warning} {warn}".strip()
                if justification_warning
                else warn
            )

    chunk_ref = _build_suggestion_chunk_ref(parsed)

    return {
        "value": value_f,
        "justification": justification,
        "justification_warning": justification_warning,
        "source_chunk_ref": chunk_ref,
        "rag_documents": docs,
        "raw_llm": raw,
    }


def _parse_llm_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    m = _JSON_BLOCK_RE.search(raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        log.warning("LLM JSON parse failed: %s", raw[:200])
        return {}
    return data if isinstance(data, dict) else {}


def validate_tender_cloud_gate(
    provider: str,
    project_key: str,
    cloud_confirm: bool,
) -> Optional[str]:
    """Cloud-LLM mit Vorgabe-Dokumenten nur nach Bestätigung."""
    if not is_cloud_llm_provider(provider):
        return None
    if get_tender_document_ids(project_key) and not cloud_confirm:
        return "cloud_confirm"
    return None


def import_criteria_payload(
    project_key: str,
    data: dict[str, Any],
    *,
    skip_existing: bool = True,
) -> dict[str, int]:
    """Kriterien aus JSON-Payload importieren (Format wie import_evaluation_criteria.py)."""
    existing_names = {c.name for c in list_criteria(project_key)}
    created = 0
    skipped = 0

    def _import_kind(kind: str, entries: list[dict]) -> None:
        nonlocal created, skipped
        for entry in entries or []:
            name = (entry.get("name") or "").strip()
            if not name:
                continue
            parent_id: Optional[int] = None
            if skip_existing and name in existing_names:
                skipped += 1
            else:
                parent = create_criterion(
                    project_key,
                    kind,
                    name,
                    weight_pct=float(entry.get("weight_pct") or 0),
                    scale_max=int(entry.get("scale_max") or 10),
                    auto_price=bool(entry.get("auto_price")),
                    description=entry.get("description"),
                    ranking_phase=(
                        int(entry["ranking_phase"])
                        if entry.get("ranking_phase") is not None
                        else None
                    ),
                    referenz=_entry_referenz(entry),
                )
                existing_names.add(name)
                parent_id = parent.id
                created += 1
            if parent_id is None:
                for c in list_criteria(project_key):
                    if c.name == name:
                        parent_id = c.id
                        break
            for child in entry.get("children", []) or []:
                cname = (child.get("name") or "").strip()
                if not cname:
                    continue
                if skip_existing and cname in existing_names:
                    skipped += 1
                    continue
                create_criterion(
                    project_key,
                    kind,
                    cname,
                    weight_pct=0,
                    scale_max=int(child.get("scale_max") or (1 if kind == "eignung" else 10)),
                    parent_id=parent_id,
                    description=child.get("description"),
                    referenz=_entry_referenz(child),
                )
                existing_names.add(cname)
                created += 1

    _import_kind("eignung", data.get("eignung") or [])
    _import_kind("zuschlag", data.get("zuschlag") or [])
    warnings = validate_criteria_payload(data)
    return {"created": created, "skipped": skipped, "warnings": warnings}


def _normalize_requirement_ref(raw: str) -> Optional[str]:
    """F-01 / F01 / EK2 / W-01 → kanonische Referenz für RAG/Suche."""
    s = (raw or "").strip()
    if not s:
        return None
    m = re.match(r"^(EK\d+)\b", s, re.I)
    if m:
        return m.group(1).upper()
    m = re.match(r"^([A-Za-z])-?0*(\d+)\b", s)
    if m:
        return f"{m.group(1).upper()}{int(m.group(2)):02d}"
    if re.match(r"^[A-Za-z]{1,4}\d+", s):
        return s.upper().replace("-", "")
    return None


def _normalize_line_ref(raw: str) -> Optional[str]:
    """Einzelzeile: F01-001, EK1-01, F-01-001 → kanonisch."""
    s = (raw or "").strip()
    if not s:
        return None
    m = re.match(r"^(EK\d+)-0*(\d+)\b", s, re.I)
    if m:
        return f"{m.group(1).upper()}-{int(m.group(2)):02d}"
    m = re.match(r"^([A-Za-z])-?0*(\d+)-0*(\d+)\b", s, re.I)
    if m:
        return f"{m.group(1).upper()}{int(m.group(2)):02d}-{int(m.group(3)):03d}"
    return None


def _store_referenz(raw: Optional[str]) -> Optional[str]:
    """Kanonicalisierter DB-Wert für Criterion.referenz."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    line = _normalize_line_ref(s)
    if line:
        return line
    norm = _normalize_requirement_ref(s)
    return norm or s.upper()


def format_requirement_ref_display(ref: Optional[str]) -> str:
    """Anzeige: F01 → F-01, EK1 bleibt EK1, F01-001 → F-01-001."""
    s = (ref or "").strip()
    if not s:
        return ""
    if re.match(r"^EK\d+$", s, re.I):
        return s.upper()
    m_ek_line = re.match(r"^(EK\d+)-(\d{2})$", s.upper())
    if m_ek_line:
        return f"{m_ek_line.group(1)}-{m_ek_line.group(2)}"
    line = _normalize_line_ref(s)
    if line:
        m = re.match(r"^([A-Z])(\d{2})-(\d{3})$", line)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        return line
    norm = _normalize_requirement_ref(s)
    if norm and re.match(r"^[A-Z]\d{2}$", norm):
        return f"{norm[0]}-{norm[1:]}"
    return s.upper()


def _entry_referenz(entry: dict[str, Any]) -> Optional[str]:
    """requirement_ref aus Payload oder Name/Beschreibung."""
    raw = (entry.get("requirement_ref") or "").strip()
    if raw:
        stored = _store_referenz(raw)
        if stored:
            return stored
    for text in (entry.get("name") or "", entry.get("description") or ""):
        line = _normalize_line_ref(text)
        if line:
            return line
        ref = _requirement_ref_from_text(text)
        if ref:
            return ref
    return None


def _ensure_criteria_refs(payload: dict[str, Any]) -> list[str]:
    """Fehlende requirement_ref ergänzen (EK1… für Eignung, Regex für Zuschlag)."""
    hints: list[str] = []
    for i, entry in enumerate(payload.get("eignung") or [], 1):
        ref = _entry_referenz(entry)
        if not ref:
            ref = f"EK{i}"
            hints.append(f"Eignung «{(entry.get('name') or '?').strip()}»: requirement_ref {ref} ergänzt")
        entry["requirement_ref"] = ref
    for entry in payload.get("zuschlag") or []:
        ref = _entry_referenz(entry)
        if ref:
            entry["requirement_ref"] = ref
        for child in entry.get("children") or []:
            cref = _entry_referenz(child)
            if cref:
                child["requirement_ref"] = cref
    return hints


def _criterion_ref_prefix(name: str) -> Optional[str]:
    """Legacy-Helfer: Referenz nur aus dem Namen (Start-Muster)."""
    return _normalize_requirement_ref(name)


def _requirement_ref_from_text(text: str) -> Optional[str]:
    """Regex-Fallback: Referenz aus Name oder Beschreibung (z. B. «vgl. F-02»)."""
    t = (text or "").strip()
    if not t:
        return None
    m = re.match(r"^(EK\d+)\b", t, re.I)
    if m:
        return m.group(1).upper()
    m = re.match(r"^([A-Za-z])-?0*(\d+)\b", t)
    if m:
        return f"{m.group(1).upper()}{int(m.group(2)):02d}"
    for pat in (r"\b(EK\d+)\b", r"\b([A-Za-z])-?0*(\d+)\b"):
        m = re.search(pat, t, re.I)
        if not m:
            continue
        if m.lastindex == 1:
            return m.group(1).upper()
        return f"{m.group(1).upper()}{int(m.group(2)):02d}"
    return None


def _resolve_requirement_search(entry: dict[str, Any]) -> tuple[Optional[str], str]:
    """
    requirement_ref (LLM) → Regex auf name/description → sonst name als Suchbegriff.
    Schritt 2 läuft immer; ohne Referenz nur mit reduzierter Trefferqualität.
    """
    name = (entry.get("name") or "").strip()
    desc = (entry.get("description") or "").strip()
    llm_ref = _normalize_requirement_ref(entry.get("requirement_ref") or "")
    if llm_ref:
        return llm_ref, llm_ref
    for text in (name, desc):
        ref = _requirement_ref_from_text(text)
        if ref:
            return ref, ref
    return None, name


def _flatten_eignung_payload(payload: dict[str, Any]) -> list[str]:
    """Eignung: scale_max=1 für Parent + Kinder (Unterfragen EK1-01… in Schritt 2)."""
    for entry in payload.get("eignung") or []:
        entry["scale_max"] = 1
        for ch in entry.get("children") or []:
            ch["scale_max"] = 1
    return []


def _is_ek_parent_ref(ref: Optional[str]) -> bool:
    return bool(re.match(r"^EK\d+$", (_normalize_requirement_ref(ref or "") or "").upper()))


def _child_ref_prefix_from_name(child_name: str) -> Optional[str]:
    """F01-001 → F01, T01-003 → T01."""
    m = re.match(r"^([A-Za-z])-?0*(\d+)", (child_name or "").strip())
    if not m:
        return None
    return f"{m.group(1).upper()}{int(m.group(2)):02d}"


def _child_belongs_to_parent_ref(child_name: str, parent_ref: Optional[str]) -> bool:
    if not parent_ref:
        return True
    parent_norm = (_normalize_requirement_ref(parent_ref) or "").upper()
    child_name = (child_name or "").strip()
    m_ek = re.match(r"^(EK\d+)-", child_name, re.I)
    if m_ek and parent_norm.startswith("EK"):
        return m_ek.group(1).upper() == parent_norm
    child_prefix = _child_ref_prefix_from_name(child_name)
    if not child_prefix:
        return False
    return child_prefix == parent_norm


def _extract_line_numbers_from_text(text: str, ref: Optional[str]) -> set[str]:
    """Zählt unterscheidbare Zeilennummern (F01-001, EK1-01) im RAG-Kontext."""
    ref_norm = (ref or "").upper().replace("-", "")
    found: set[str] = set()
    for m in re.finditer(r"\b(EK\d+)-0*(\d+)\b", text or "", re.I):
        label = f"{m.group(1).upper()}-{int(m.group(2)):02d}"
        if not ref_norm or m.group(1).upper() == ref_norm:
            found.add(label)
    for m in re.finditer(r"\b([A-Za-z])-?0*(\d+)-0*(\d+)\b", text or ""):
        prefix = f"{m.group(1).upper()}{int(m.group(2)):02d}"
        label = f"{prefix}-{int(m.group(3)):03d}"
        if not ref_norm or prefix.replace("-", "") == ref_norm:
            found.add(label)
    return found


def _line_label_for_ref(ref: str, line_suffix: int) -> str:
    """F01 + 3 → F01-003; EK1 + 3 → EK1-03 (kanonisches Label)."""
    norm = (_normalize_requirement_ref(ref) or ref or "").upper().replace("-", "")
    if re.match(r"^EK\d+$", norm):
        return f"{norm}-{line_suffix:02d}"
    m = re.match(r"^([A-Z])(\d{2})$", norm)
    if not m:
        return f"{norm}-{line_suffix:03d}"
    return f"{m.group(1)}{m.group(2)}-{line_suffix:03d}"


def _line_label_regex(label: str) -> str:
    """Regex-Fragment für F01-003 / EK1-03 im Fliesstext."""
    line = _normalize_line_ref(label) or (label or "").strip().upper()
    m_ek = re.match(r"^(EK\d+)-(\d{2})$", line)
    if m_ek:
        ek, num = m_ek.group(1), int(m_ek.group(2))
        return rf"\b{re.escape(ek)}-?0*{num}\b"
    m = re.match(r"^([A-Z])(\d{2})-(\d{3})$", line)
    if not m:
        return re.escape(label)
    letter, block, num = m.group(1), int(m.group(2)), int(m.group(3))
    return rf"\b{letter}-?0*{block}-?0*{num}\b"


def _parse_line_suffix_from_label(label: str, ref: Optional[str]) -> Optional[int]:
    """Zeilen-Suffix (3 aus F01-003), nur wenn Block zur Parent-Ref passt."""
    raw = (label or "").strip()
    if not raw:
        return None
    line = _normalize_line_ref(raw) or _store_referenz(raw)
    if not line or "-" not in line:
        return None
    m_ek = re.match(r"^(EK\d+)-(\d{2})$", line.upper())
    if m_ek:
        block = m_ek.group(1)
        ref_norm = (_normalize_requirement_ref(ref or "") or "").upper()
        if ref_norm and block != ref_norm:
            return None
        return int(m_ek.group(2))
    m = re.match(r"^([A-Z])(\d{2})-(\d{3})$", line.upper())
    if not m:
        return None
    block = f"{m.group(1)}{m.group(2)}"
    ref_norm = (_normalize_requirement_ref(ref or "") or "").upper()
    if ref_norm and block != ref_norm:
        return None
    return int(m.group(3))


def _parse_line_suffix_from_child(ch: dict[str, Any], ref: Optional[str]) -> Optional[int]:
    for field in (ch.get("requirement_ref"), ch.get("name")):
        n = _parse_line_suffix_from_label(str(field or ""), ref)
        if n is not None:
            return n
    return None


def _missing_line_suffixes(
    children: list[dict[str, Any]],
    ref: Optional[str],
    ctx: str,
    parent_entry: dict[str, Any],
) -> list[int]:
    """Lücken in fortlaufender Fragenr.-Folge (z. B. 001,002,004 → 003 fehlt)."""
    if not ref:
        return []
    found: set[int] = set()
    for ch in children:
        n = _parse_line_suffix_from_child(ch, ref)
        if n is not None:
            found.add(n)
    if len(found) < 2:
        return []

    in_ctx: set[int] = set()
    for label in _extract_line_numbers_from_text(ctx, ref):
        n = _parse_line_suffix_from_label(label, ref)
        if n is not None:
            in_ctx.add(n)

    universe = found | in_ctx
    if not universe:
        return []

    lo, hi = min(universe), max(universe)
    expected = parse_expected_child_count(
        (parent_entry.get("description") or ""),
        ref,
    )
    if expected and expected > hi - lo + 1:
        hi = lo + expected - 1

    if hi - lo + 1 <= len(found):
        return []

    missing: list[int] = []
    for n in range(lo, hi + 1):
        if n in found:
            continue
        label = _line_label_for_ref(ref, n)
        if not re.search(_line_label_regex(label), ctx or "", re.I):
            continue
        missing.append(n)
    return missing


def _extract_line_block_from_context(
    ctx: str,
    line_label: str,
    next_line_label: Optional[str],
) -> Optional[str]:
    """Anforderungstext zwischen zwei Fragennummern aus bereits abgerufenem Kontext."""
    if not (ctx or "").strip() or not line_label:
        return None
    m_start = re.search(_line_label_regex(line_label), ctx, re.I)
    if not m_start:
        return None
    start_pos = m_start.end()
    if next_line_label:
        m_end = re.search(_line_label_regex(next_line_label), ctx[start_pos:], re.I)
        end_pos = start_pos + m_end.start() if m_end else len(ctx)
    else:
        block_m = re.match(
            r"^([A-Z])(\d{2})-(\d{3})$",
            (_normalize_line_ref(line_label) or line_label).upper(),
        )
        end_pos = len(ctx)
        if block_m:
            letter, block = block_m.group(1), int(block_m.group(2))
            m_next = re.search(
                rf"\b{letter}-?0*{block}-?0*\d+\b",
                ctx[start_pos + 1:],
                re.I,
            )
            if m_next:
                end_pos = start_pos + 1 + m_next.start()
        else:
            ek_m = re.match(
                r"^(EK\d+)-(\d{2})$",
                (_normalize_line_ref(line_label) or line_label).upper(),
            )
            if ek_m:
                ek = ek_m.group(1)
                m_next = re.search(
                    rf"\b{re.escape(ek)}-0*\d+\b",
                    ctx[start_pos + 1:],
                    re.I,
                )
                if m_next:
                    end_pos = start_pos + 1 + m_next.start()
    text = re.sub(r"^[\s:;.\-|]+", "", ctx[start_pos:end_pos]).strip()
    if len(text) < 12:
        return None
    return text[:4000]


def _fill_missing_line_children(
    entry: dict[str, Any],
    ctx: str,
    ref: Optional[str],
) -> int:
    """Ergänzt fehlende Einzelzeilen deterministisch aus dem RAG-Kontext (Ticket 22)."""
    children = list(entry.get("children") or [])
    missing = _missing_line_suffixes(children, ref, ctx, entry)
    if not missing:
        return 0

    known_suffixes: set[int] = set()
    for ch in children:
        n = _parse_line_suffix_from_child(ch, ref)
        if n is not None:
            known_suffixes.add(n)
    for label in _extract_line_numbers_from_text(ctx, ref):
        n = _parse_line_suffix_from_label(label, ref)
        if n is not None:
            known_suffixes.add(n)

    filled = 0
    for n in sorted(missing):
        label = _line_label_for_ref(ref or "", n)
        next_suffix = min((s for s in known_suffixes if s > n), default=None)
        next_label = _line_label_for_ref(ref or "", next_suffix) if next_suffix else None
        desc = _extract_line_block_from_context(ctx, label, next_label)
        if not desc:
            continue
        children.append({
            "name": label,
            "description": desc,
            "scale_max": 10,
            "requirement_ref": _store_referenz(label),
        })
        known_suffixes.add(n)
        filled += 1

    if filled:
        children.sort(
            key=lambda ch: _parse_line_suffix_from_child(ch, ref) or 9999,
        )
        entry["children"] = children
        _stamp_child_requirement_refs(entry["children"])
    return filled


def _child_duplicates_parent(child: dict[str, Any], parent_desc: str) -> bool:
    cd = (child.get("description") or "").strip()
    pd = (parent_desc or "").strip()
    if not cd or not pd:
        return False
    if cd == pd:
        return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, cd[:400], pd[:400]).ratio() >= 0.88


def _text_grounded_in_context(needle: str, haystack: str) -> bool:
    needle = (needle or "").strip()
    haystack = haystack or ""
    if len(needle) < 8:
        return needle.lower() in haystack.lower()
    if needle.lower() in haystack.lower():
        return True
    words = needle.split()
    if len(words) >= 4:
        snippet = " ".join(words[:10])
        if snippet.lower() in haystack.lower():
            return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, needle.lower()[:240], haystack.lower()).ratio() >= 0.52


def _child_grounded_in_context(
    child: dict[str, Any],
    ctx: str,
    parent_ref: Optional[str],
    parent_desc: str,
) -> bool:
    name = (child.get("name") or "").strip()
    desc = (child.get("description") or "").strip()
    if not name:
        return False
    if _child_duplicates_parent(child, parent_desc):
        return False
    if parent_ref and not _child_belongs_to_parent_ref(name, parent_ref):
        return False
    if name.lower() in ctx.lower():
        return True
    if desc and _text_grounded_in_context(desc, ctx):
        return True
    return False


def _filter_grounded_children(
    children: list[dict[str, Any]],
    ctx: str,
    parent_ref: Optional[str],
    parent_desc: str,
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    dropped = 0
    for ch in children:
        if _child_grounded_in_context(ch, ctx, parent_ref, parent_desc):
            kept.append(ch)
        else:
            dropped += 1
    return kept, dropped


def _zuschlag_has_line_structure_evidence(
    structured: list[dict[str, Any]],
    ctx: str,
    ref: Optional[str],
) -> bool:
    if len(structured) >= 2:
        return True
    return len(_extract_line_numbers_from_text(ctx, ref)) >= 2


def _ref_matches_row_identifier(identifier: str, ref: Optional[str], search_term: str) -> bool:
    ident = re.sub(r"[\s_]+", "", (identifier or "").upper())
    if not ident:
        return False
    if ref:
        ref_norm = re.sub(r"[\s_-]+", "", ref.upper())
        if ident.startswith(ref_norm) or ref_norm in ident:
            return True
    term = re.sub(r"[\s_]+", "", (search_term or "").upper())
    if len(term) >= 4 and term in ident:
        return True
    return False


def _child_dict_from_structured_row(row: dict[str, Any], *, kind: str) -> Optional[dict[str, Any]]:
    name = (
        str(row.get("Nr") or row.get("Referenz") or row.get("referenz") or row.get("ID") or "")
    ).strip()
    desc = (
        str(
            row.get("Frage")
            or row.get("Anforderung")
            or row.get("Beschreibung")
            or row.get("Text")
            or ""
        )
    ).strip()
    if not desc:
        parts = [
            f"{k}: {v}" for k, v in row.items()
            if k not in ("Nr", "Referenz", "referenz", "ID", "Lieferant", "Antwort")
            and str(v).strip()
        ]
        desc = " | ".join(parts).strip()
    if not name and desc:
        name = desc[:60]
    if not name:
        return None
    child = {
        "name": name,
        "description": desc or name,
        "scale_max": 1 if kind == "eignung" else int(row.get("scale_max") or 10),
    }
    cref = _store_referenz(name) or _entry_referenz({"name": name, "description": desc})
    if cref:
        child["requirement_ref"] = cref
    return child


def _enrich_children_from_structured_tender_docs(
    project_key: str,
    entry: dict[str, Any],
    *,
    kind: str,
    ref: Optional[str],
    search_term: str,
) -> list[dict[str, Any]]:
    """CSV/XLSX-Vorgaben zeilenweise → deterministische Unterfragen (Ticket 16)."""
    from pathlib import Path

    from .m09_docs import (
        get_document_by_id,
        process_csv_to_chunks,
        process_generic_csv_rows,
        process_xlsx_to_rows,
    )

    roles = (
        ("eignungskriterien", "ausschreibungsunterlage", "bewertungsvorgaben")
        if kind == "eignung"
        else ("zuschlagskriterien", "ausschreibungsunterlage", "bewertungsvorgaben")
    )
    doc_ids = get_tender_document_ids(project_key, roles=roles)
    children: list[dict[str, Any]] = []
    seen: set[str] = set()

    for doc_id in doc_ids:
        doc = get_document_by_id(doc_id)
        if not doc or not doc.file_path:
            continue
        path = Path(doc.file_path)
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in (".csv", ".xlsx", ".xls"):
            continue
        fn = (doc.filename or path.name).lower()
        if ref:
            ref_lo = ref.lower().replace("-", "")
            if ref_lo not in fn.replace("-", "") and ref_lo not in path.name.lower().replace("-", ""):
                pass  # kein Dateiname-Match — Zeilenfilter kann trotzdem treffen

        rows: list[dict[str, Any]] = []
        if ext == ".csv":
            ok, _msg, data = process_csv_to_chunks(path)
            if not ok:
                ok, _msg, data = process_generic_csv_rows(path)
            if ok:
                rows = data
        else:
            ok, _msg, data = process_xlsx_to_rows(path)
            if ok:
                rows = data

        fn_norm = fn.replace("-", "").replace("_", "")
        file_matches = False
        if ref:
            file_matches = ref.lower().replace("-", "") in fn_norm
        elif search_term and len(search_term) >= 4:
            file_matches = search_term.lower().replace(" ", "")[:12] in fn_norm

        for row in rows:
            ident = str(
                row.get("Nr") or row.get("Referenz") or row.get("referenz") or row.get("ID") or ""
            )
            frage = str(row.get("Frage") or row.get("Anforderung") or "")
            row_match = _ref_matches_row_identifier(ident, ref, search_term) or _ref_matches_row_identifier(
                frage, ref, search_term
            )
            if not row_match and not file_matches:
                continue
            child = _child_dict_from_structured_row(row, kind=kind)
            if not child:
                continue
            cname = child["name"]
            if cname in seen:
                continue
            seen.add(cname)
            children.append(child)
    return children


def _stamp_child_requirement_refs(children: list[dict[str, Any]]) -> None:
    """Unterfragen: requirement_ref aus name (F01-001) falls leer."""
    for ch in children or []:
        if (ch.get("requirement_ref") or "").strip():
            continue
        cref = _entry_referenz(ch)
        if cref:
            ch["requirement_ref"] = cref


def _literal_chunks_for_requirement_ref(
    document_ids: tuple[int, ...],
    ref: Optional[str],
    *,
    max_chunks: int = 16,
) -> list[dict[str, Any]]:
    """Volltext-Scan aller Vorgaben-Chunks nach Zeilennummern der Ref-Gruppe (Ticket 21)."""
    from sqlmodel import select

    from .m03_db import Document, DocumentChunk, get_session

    if not ref or not document_ids:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    with get_session() as session:
        rows = session.exec(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.document_id.in_(list(document_ids)))
            .where(Document.is_deleted == False)  # noqa: E712
        ).all()
    for chunk, doc in rows:
        text = chunk.chunk_text or ""
        score = len(_extract_line_numbers_from_text(text, ref))
        if score <= 0:
            norm = (_normalize_requirement_ref(ref) or "").lower()
            flat = text.lower().replace("-", "").replace(" ", "")
            if norm and norm in flat:
                score = 1
        if score <= 0:
            continue
        scored.append((
            score,
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "filename": doc.filename or "",
                "text": text,
                "classification": doc.classification or "",
            },
        ))
    scored.sort(key=lambda x: (-x[0], x[1].get("chunk_id") or 0))
    return [row for _, row in scored[:max_chunks]]


def _enrichment_ref_diag(ctx: str, ref: Optional[str]) -> str:
    """Kurzdiagnose für KI-Hinweise: wie viele Zeilen, welche Beispiele."""
    lines = sorted(_extract_line_numbers_from_text(ctx, ref))
    if lines:
        sample = ", ".join(lines[:4])
        if len(lines) > 4:
            sample += f" (+{len(lines) - 4})"
        return f"{len(lines)} Zeilennummer(n); im Kontext: {sample}"
    norm = _normalize_requirement_ref(ref or "") or "?"
    return f"0 Zeilennummer(n); {norm}-001 nicht wörtlich im Kontext"


def _ref_enrichment_query(ref: Optional[str], name: str = "") -> str:
    """BM25-Query für Anforderungsblatt-Zeilen einer Ref-Gruppe (Ticket 20)."""
    if not ref:
        return (name or "").strip()
    norm = _normalize_requirement_ref(ref) or ref.upper().replace("-", "")
    parts = [norm, name.strip()]
    m = re.match(r"^([A-Z])(\d{2})$", norm)
    if m:
        letter, num = m.group(1), m.group(2)
        dashed = f"{letter}-{num}"
        parts.extend([
            dashed,
            f"{norm}-001",
            f"{norm}-002",
            f"{dashed}-001",
            f"{dashed}-002",
        ])
    parts.append("Einzelanforderungen Anforderungsblatt Referenz Fragenr Lieferant")
    return " ".join(p for p in parts if p)


def _retrieve_enrichment_context(
    project_key: str,
    *,
    query: str,
    ref: Optional[str],
    name: str,
    doc_ids: tuple[int, ...],
    limit: int,
    threshold: float,
) -> str:
    """Hybrid-RAG + ref-gezielter Zusatzpass, dedupliziert."""
    rag_docs: list[dict] = []
    rag = retrieve_relevant_chunks_hybrid(
        query,
        project_key=project_key,
        limit=limit,
        threshold=threshold,
        document_ids=doc_ids,
    )
    rag_docs = list(rag.get("documents", []))
    if ref:
        ref_q = _ref_enrichment_query(ref, name)
        ref_rag = retrieve_relevant_chunks_hybrid(
            ref_q,
            project_key=project_key,
            limit=limit,
            threshold=min(threshold, 0.12),
            document_ids=doc_ids,
        )
        rag_docs = _dedupe_rag_docs(rag_docs + list(ref_rag.get("documents", [])))
        literal = _literal_chunks_for_requirement_ref(doc_ids, ref, max_chunks=limit)
        if literal:
            rag_docs = _dedupe_rag_docs(rag_docs + literal)
            log.info(
                "[criteria-enrich] %s: +%d Literal-Chunk(s) für %s",
                name, len(literal), ref,
            )
    return _format_rag_context(
        rag_docs,
        empty_msg="",
        max_chunks=min(len(rag_docs), limit * 2),
    )


def _merge_criteria_children(
    existing: list[dict[str, Any]],
    new_children: list[dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    by_name = {(c.get("name") or "").strip(): c for c in existing if (c.get("name") or "").strip()}
    for ch in new_children:
        cname = (ch.get("name") or "").strip()
        if not cname:
            continue
        if cname not in by_name:
            scale = 1 if kind == "eignung" else int(ch.get("scale_max") or 10)
            row = {
                "name": cname,
                "description": (ch.get("description") or "").strip(),
                "scale_max": scale,
            }
            cref = (ch.get("requirement_ref") or "").strip() or _entry_referenz(ch)
            if cref:
                row["requirement_ref"] = cref
            by_name[cname] = row
        else:
            existing_row = by_name[cname]
            if not (existing_row.get("requirement_ref") or "").strip():
                cref = (ch.get("requirement_ref") or "").strip() or _entry_referenz(ch)
                if cref:
                    existing_row["requirement_ref"] = cref
    merged = list(by_name.values())
    _stamp_child_requirement_refs(merged)
    return merged


def _enrich_single_criteria_entry_children(
    project_key: str,
    entry: dict[str, Any],
    *,
    kind: str,
    provider: str,
    model: str | None,
    doc_ids: tuple[int, ...],
    enrich_limit: int,
    enrich_threshold: float,
) -> list[str]:
    """Schritt 2 für ein Top-Level-Kriterium (Eignung oder Zuschlag)."""
    hints: list[str] = []
    name = (entry.get("name") or "").strip()
    if not name:
        return hints
    ref, search_term = _resolve_requirement_search(entry)
    if ref and not entry.get("requirement_ref"):
        entry["requirement_ref"] = ref
    parent_desc = (entry.get("description") or "").strip()
    step1_removed = len(entry.get("children") or [])

    if kind == "zuschlag" and entry.get("auto_price"):
        if step1_removed:
            hints.append(f"{name}: Preis-Kriterium — {step1_removed} Unterfragen verworfen (auto_price)")
        entry["children"] = []
        return hints

    entry["children"] = []

    structured = _enrich_children_from_structured_tender_docs(
        project_key, entry, kind=kind, ref=ref, search_term=search_term,
    )
    if structured:
        entry["children"] = _merge_criteria_children([], structured, kind=kind)
        hints.append(f"{name}: {len(structured)} Unterfragen aus strukturierter Vorgabe")

    if kind == "eignung":
        query_parts = [search_term, ref or "", "Fragenkatalog Eignungskriterien Selbstdeklaration"]
        query_parts.append("Referenz Frage Antwort ja nein Kommentar EK1-01 EK2-06")
    else:
        query_parts = [search_term]
        if ref and ref != search_term:
            query_parts.append(ref)
        query_parts.append(
            "Einzelanforderungen Fragenr Referenz Anforderung Lieferant "
            "Ja Nein Teilweise Begründung Pflicht Kapitel Anforderungen"
        )
    query = " ".join(p for p in query_parts if p)
    ctx = _retrieve_enrichment_context(
        project_key,
        query=query,
        ref=ref,
        name=name,
        doc_ids=doc_ids,
        limit=enrich_limit,
        threshold=enrich_threshold,
    )
    if is_cloud_llm_provider(provider) and ctx.strip():
        ctx = sanitize_for_cloud_text(ctx)

    existing = list(entry.get("children") or [])
    if existing:
        filtered, dropped = _filter_grounded_children(existing, ctx, ref, parent_desc)
        if dropped:
            hints.append(f"{name}: {dropped} strukturierte Unterfragen nicht im Kontext belegt")
        entry["children"] = filtered

    if not _zuschlag_has_line_structure_evidence(list(entry.get("children") or []), ctx, ref):
        diag = _enrichment_ref_diag(ctx, ref)
        if step1_removed:
            hints.append(
                f"{name}: {step1_removed} Schritt-1-Unterfragen verworfen ({diag})"
            )
        elif not entry.get("children"):
            hints.append(f"{name}: keine Unterfragen — {diag}")
        log.info(
            "[criteria-enrich] Gate rot für «%s» (%s, %s): %s",
            name, kind, ref or search_term, diag,
        )
        entry["children"] = []
        return hints

    if len(entry.get("children") or []) >= 30:
        return hints

    if not ctx.strip():
        return hints

    ref_hint = f"Referenz-Gruppe: {ref}" if ref else f"Suchbegriff: {search_term}"
    if kind == "eignung":
        system = (
            "Extrahiere alle Fragen aus dem Eignungs-Fragenkatalog / Selbstdeklaration für "
            f"ein Eignungskriterium — NICHT nur die Kapitel-Einleitung. {ref_hint} "
            "(Fragen z. B. EK1-01, EK2-06). Antwort NUR als JSON mit key children (Liste). "
            "Jedes Kind: name (exakte Referenznummer), description (voller Fragetext). "
            "Nur Zeilen, die im Ausschreibungsauszug wörtlich vorkommen — nichts erfinden."
        )
        user = f"Top-Kriterium (eignung): {name}\n\nAusschreibungsauszug:\n{ctx}\n\nJSON:"
    else:
        system = (
            "Extrahiere alle Einzelanforderungen (Unterfragen) für ein Zuschlagskriterium aus "
            "dem Kapitel «Anforderungen» / Anforderungsblatt — NICHT nur die Kapitel-Einleitung. "
            f"{ref_hint} (Fragen z. B. F01-001, T01-003). "
            "Antwort NUR als JSON mit key children (Liste). "
            "Jedes Kind: name (kurz, exakte Fragennummer), description (voller Anforderungstext). "
            "Nur Zeilen, die im Ausschreibungsauszug wörtlich vorkommen — nichts erfinden."
        )
        user = f"Top-Kriterium (zuschlag): {name}\n\nAusschreibungsauszug:\n{ctx}\n\nJSON:"

    raw = try_models_with_messages(
        provider,
        system,
        [{"role": "user", "content": user}],
        max_tokens=4500,
        temperature=0.1,
        model=model,
    )
    sub = _parse_llm_json_object(raw)
    children = sub.get("children") if isinstance(sub, dict) else []
    if not children and isinstance(sub, list):
        children = sub

    default_scale = 1 if kind == "eignung" else 10
    llm_children: list[dict[str, Any]] = []
    for ch in children or []:
        cname = (ch.get("name") or "").strip()
        if not cname:
            continue
        llm_children.append({
            "name": cname,
            "description": (ch.get("description") or "").strip(),
            "scale_max": int(ch.get("scale_max") or default_scale),
            "requirement_ref": (ch.get("requirement_ref") or "").strip()
            or _entry_referenz({"name": cname, "description": ch.get("description")}),
        })

    before = len(entry.get("children") or [])
    merged = _merge_criteria_children(list(entry.get("children") or []), llm_children, kind=kind)
    filtered, dropped = _filter_grounded_children(merged, ctx, ref, parent_desc)
    entry["children"] = filtered
    added = len(filtered) - before
    if dropped:
        hints.append(f"{name}: {dropped} KI-Unterfragen verworfen (nicht im Vorgaben-Kontext)")
    if added > 0:
        hints.append(f"{name}: +{added} Unterfragen (KI/RAG, belegt)")
    gap_filled = _fill_missing_line_children(entry, ctx, ref)
    if gap_filled:
        hints.append(
            f"{name}: {gap_filled} fehlende Zeile(n) deterministisch aus Kontext ergänzt"
        )
    _stamp_child_requirement_refs(entry.get("children") or [])
    return hints


def _enrich_criteria_children_from_requirements(
    project_key: str,
    payload: dict[str, Any],
    provider: str,
    model: str | None,
    *,
    enrich_limit: int = ENRICH_CHILDREN_RAG_LIMIT,
    enrich_threshold: float = ENRICH_CHILDREN_RAG_THRESHOLD,
) -> list[str]:
    """Schritt 2: Unterfragen für Eignung + Zuschlag — mit Struktur-Nachweis und Groundedness."""
    hints: list[str] = []
    hints.extend(_flatten_eignung_payload(payload))

    kind_roles = (
        ("eignung", ("eignungskriterien", "ausschreibungsunterlage", "bewertungsvorgaben")),
        ("zuschlag", ("zuschlagskriterien", "ausschreibungsunterlage", "bewertungsvorgaben")),
    )
    for kind, roles in kind_roles:
        doc_ids = get_tender_document_ids(project_key, roles=roles)
        if not doc_ids:
            continue
        for entry in payload.get(kind) or []:
            hints.extend(
                _enrich_single_criteria_entry_children(
                    project_key,
                    entry,
                    kind=kind,
                    provider=provider,
                    model=model,
                    doc_ids=tuple(doc_ids),
                    enrich_limit=enrich_limit,
                    enrich_threshold=enrich_threshold,
                )
            )

    return hints


def _enrich_zuschlag_children_from_requirements(
    project_key: str,
    payload: dict[str, Any],
    provider: str,
    model: str | None,
    limit: int,
) -> list[str]:
    """Abwärtskompatibler Alias — delegiert an kind-agnostische Enrichment-Funktion."""
    return _enrich_criteria_children_from_requirements(
        project_key, payload, provider, model,
        enrich_limit=max(ENRICH_CHILDREN_RAG_LIMIT, min(48, limit * 2)),
    )


def extract_criteria_from_tender_docs(
    project_key: str,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """KI-Extraktion von Eignungs-/Zuschlagskriterien — rollenweise RAG-Pässe."""
    if not get_tender_document_ids(project_key):
        return {
            "error": "Keine Vorgaben verknüpft — zuerst Phase ① Dokumente mit Rolle markieren.",
            "payload": {},
            "warnings": [],
            "raw_llm": None,
        }

    cfg = get_evaluation_config(project_key)
    limit = cfg["rag_chunks_per_role"]
    extraction_limit = cfg.get("rag_chunks_extraction", DEFAULT_RAG_CHUNKS_EXTRACTION)
    role_queries = [
        (("eignungskriterien", "ausschreibungsunterlage"), "Eignungskriterien K.O. Mindestanforderungen Bieter"),
        (
            ("eignungskriterien", "ausschreibungsunterlage", "bewertungsvorgaben"),
            "Fragenkatalog Eignungskriterien Selbstdeklaration EK1-01 EK2-06 Referenz Frage Antwort",
        ),
        (("zuschlagskriterien", "bewertungsvorgaben"), "Zuschlagskriterien Gewichtung Punkte Bewertungsmatrix Übersicht"),
        (
            ("zuschlagskriterien", "ausschreibungsunterlage", "bewertungsvorgaben"),
            "Einzelanforderungen Fragenr Referenz Anforderung Lieferant Ja Nein Teilweise Begründung",
        ),
        (("bewertungsvorgaben", "ausschreibungsunterlage"), "Bewertungsvorgaben Verfahren Skala Rangfolge"),
    ]
    context = _retrieve_tender_context_multi(
        project_key,
        role_queries,
        limit_per_role=limit,
        max_format_chunks=extraction_limit,
    )
    if is_cloud_llm_provider(provider):
        context = sanitize_for_cloud_text(context)

    vergabe_extra = cfg.get("vergabe_notes") or ""
    system = (
        f"{VERGABE_SYSTEM_RULES}\n"
        "Extrahiere strukturierte Bewertungskriterien aus Ausschreibungsunterlagen. "
        "Antwort NUR als JSON mit keys eignung und zuschlag (Listen von Objekten). "
        "Jedes Objekt: name (kurz), description (Kapitel-Einleitung / Aufgabenstellung), "
        "requirement_ref (PFLICHT: Eignung EK1/EK2/EK3; Zuschlag F01, T01, W01, … — unabhängig vom name-Text). "
        "Eignung Schritt 1: nur Top-Level EK1/EK2/EK3 ohne children; Nachweistext in description "
        "(z. B. «Referenznummern EK2-01 bis EK2-06»). Unterfragen EK1-01… kommen in Schritt 2. "
        "Zuschlag: optional children nur in Schritt 2 (Anforderungsblätter F/T). "
        "Zuschlag: weight_pct, scale_max (default 10), auto_price true nur für reines Preis-Kriterium, "
        "ranking_phase 1 (ZK) oder 2 (Präsentation nach Einladung, z. B. A-01). "
        "Eignung: scale_max immer 1, kein weight_pct."
    )
    if vergabe_extra:
        system += f"\nProjekt-Hinweise: {vergabe_extra}"
    user = f"Ausschreibungsauszüge:\n{context}\n\nJSON:"
    raw = try_models_with_messages(
        provider, system, [{"role": "user", "content": user}],
        max_tokens=4000, temperature=0.1, model=model,
    )
    payload = _parse_llm_json_object(raw)
    ref_hints = _ensure_criteria_refs(payload)
    hints_pre = _flatten_eignung_payload(payload)
    child_hints = _enrich_criteria_children_from_requirements(
        project_key, payload, provider, model,
        enrich_limit=ENRICH_CHILDREN_RAG_LIMIT,
        enrich_threshold=ENRICH_CHILDREN_RAG_THRESHOLD,
    )
    warnings = validate_criteria_payload(payload)
    warnings = list(ref_hints) + list(hints_pre) + list(warnings) + child_hints
    warnings.extend(criteria_completeness_warnings(criteria_child_completeness(payload)))
    if not payload.get("eignung") and not payload.get("zuschlag"):
        return {
            "error": "KI konnte keine Kriterien extrahieren — Vorgaben prüfen oder manuell anlegen.",
            "payload": payload,
            "warnings": warnings,
            "raw_llm": raw,
        }
    return {"error": None, "payload": payload, "warnings": warnings, "raw_llm": raw}


def get_bidder_preisblatt_doc_ids(bidder_id: int) -> list[int]:
    ids: list[int] = []
    for doc_id in get_bidder_document_ids(bidder_id):
        subtypes = get_bidder_doc_subtypes(bidder_id, doc_id)
        doc = get_document_by_id(doc_id)
        if "Preisblatt" in subtypes:
            ids.append(doc_id)
        elif not subtypes and doc and (
            doc.doc_subtype == "Preisblatt"
            or "preisblatt" in (doc.filename or "").lower()
        ):
            ids.append(doc_id)
    return ids


def _price_rows_from_llm(data: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    einmalig = [r for r in (data.get("einmalig") or []) if (r.get("leistungsbeschreibung") or "").strip()]
    wiederkehrend = [
        r for r in (data.get("wiederkehrend") or []) if (r.get("leistungsbeschreibung") or "").strip()
    ]
    return einmalig, wiederkehrend


def extract_price_structure_from_tender(
    project_key: str,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """Leere Preisblatt-Struktur aus Vorlage (Rolle preisblatt_vorlage)."""
    tender_ids = get_tender_document_ids(project_key, roles=("preisblatt_vorlage",))
    if not tender_ids:
        return {"error": "Keine Preisblatt-Vorlage verknüpft (Phase ①, Rolle «Preisblatt-Vorlage»).", "structure": {}}

    cfg = get_evaluation_config(project_key)
    years_str = ", ".join(str(y) for y in cfg["price_years"])
    rag_limit = cfg["rag_chunks_per_role"]

    rag = retrieve_relevant_chunks_hybrid(
        "Preisblatt Positionen Leistungsbeschreibung Referenz Einheit Kosten",
        project_key=project_key,
        limit=rag_limit,
        threshold=0.28,
        document_ids=tuple(tender_ids),
    )
    context = _format_rag_context(
        rag.get("documents", []),
        empty_msg="Keine Preisblatt-Stellen gefunden.",
        max_chunks=rag_limit,
    )
    if is_cloud_llm_provider(provider):
        context = sanitize_for_cloud_text(context)

    system = (
        "Extrahiere die Zeilenstruktur eines Preisblatts aus einer Ausschreibungsvorlage. "
        "Antwort NUR als JSON: einmalig (Liste) und wiederkehrend (Liste). "
        "Felder: referenz (optional), leistungsbeschreibung, einheit, anzahl, kosten_pro_einheit, "
        f"bemerkung (optional). Bei leerer Vorlage: anzahl und kosten_pro_einheit = 0. "
        f"wiederkehrend zusätzlich year (erlaubte Jahre: {years_str})."
    )
    raw = try_models_with_messages(
        provider, system, [{"role": "user", "content": f"Preisblatt-Vorlage:\n{context}\n\nJSON:"}],
        max_tokens=3000, temperature=0.1, model=model,
    )
    structure = _parse_llm_json_object(raw)
    einmalig, wiederkehrend = _price_rows_from_llm(structure)
    if not einmalig and not wiederkehrend:
        return {"error": "Keine Preisblatt-Zeilen erkannt.", "structure": structure, "raw_llm": raw}
    return {"error": None, "structure": {"einmalig": einmalig, "wiederkehrend": wiederkehrend}, "raw_llm": raw}


def extract_price_from_bidder_doc(
    bidder_id: int,
    project_key: str,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """Ausgefülltes Preisblatt eines Bieters einlesen (Subtyp Preisblatt)."""
    doc_ids = get_bidder_preisblatt_doc_ids(bidder_id)
    if not doc_ids:
        return {"error": "Kein Preisblatt beim Bieter verknüpft (Subtyp «Preisblatt» oder Dateiname).", "structure": {}}

    cfg = get_evaluation_config(project_key)
    years_str = ", ".join(str(y) for y in cfg["price_years"])
    rag_limit = cfg["rag_chunks_per_role"]

    rag = retrieve_relevant_chunks_hybrid(
        "Preisblatt Kosten CHF Positionen Anzahl Einheit",
        project_key=project_key,
        limit=rag_limit,
        threshold=0.28,
        classification_filter=ANGEbot_CLASSIFICATION,
        bidder_id=bidder_id,
        document_ids=tuple(doc_ids),
    )
    context = _format_rag_context(
        rag.get("documents", []),
        empty_msg="Keine Preisblatt-Stellen gefunden.",
        max_chunks=rag_limit,
    )
    if is_cloud_llm_provider(provider):
        context = sanitize_for_cloud_text(context)

    system = (
        "Extrahiere alle Preispositionen aus dem Bieter-Preisblatt. "
        "Antwort NUR als JSON mit einmalig und wiederkehrend (Listen). "
        "Felder: referenz, leistungsbeschreibung, einheit, anzahl, kosten_pro_einheit, bemerkung; "
        f"wiederkehrend zusätzlich year ({years_str})."
    )
    raw = try_models_with_messages(
        provider, system, [{"role": "user", "content": f"Bieter-Preisblatt:\n{context}\n\nJSON:"}],
        max_tokens=3000, temperature=0.1, model=model,
    )
    structure = _parse_llm_json_object(raw)
    einmalig, wiederkehrend = _price_rows_from_llm(structure)
    if not einmalig and not wiederkehrend:
        return {"error": "Keine Preispositionen erkannt.", "structure": structure, "raw_llm": raw}
    return {"error": None, "structure": {"einmalig": einmalig, "wiederkehrend": wiederkehrend}, "raw_llm": raw}


def _price_row_key(category: str, year: Optional[int], referenz: Optional[str], desc: str) -> tuple:
    return (category, year, (referenz or "").strip(), desc.strip())


def seed_price_structure_for_bidder(
    bidder_id: int,
    structure: dict[str, Any],
    *,
    only_if_empty: bool = True,
) -> dict[str, int]:
    """Vorlagen-Struktur auf Bieter übertragen (Werte 0)."""
    existing = list_price_items(bidder_id)
    if existing and only_if_empty:
        return {"created": 0, "skipped": len(existing)}

    created = 0
    for row in structure.get("einmalig") or []:
        desc = (row.get("leistungsbeschreibung") or "").strip()
        if not desc:
            continue
        upsert_price_item(
            None, bidder_id, "einmalig", desc,
            anzahl=float(row.get("anzahl") or 0),
            kosten_pro_einheit=float(row.get("kosten_pro_einheit") or 0),
            referenz=row.get("referenz"),
            einheit=row.get("einheit"),
            bemerkung=row.get("bemerkung"),
        )
        created += 1
    for row in structure.get("wiederkehrend") or []:
        desc = (row.get("leistungsbeschreibung") or "").strip()
        year = row.get("year")
        if not desc or not year:
            continue
        upsert_price_item(
            None, bidder_id, "wiederkehrend", desc,
            anzahl=float(row.get("anzahl") or 0),
            kosten_pro_einheit=float(row.get("kosten_pro_einheit") or 0),
            year=int(year),
            einheit=row.get("einheit"),
            bemerkung=row.get("bemerkung"),
        )
        created += 1
    return {"created": created, "skipped": 0}


def merge_price_structure_for_bidder(bidder_id: int, structure: dict[str, Any]) -> dict[str, int]:
    """Bieter-Preisblatt-Werte einlesen — bestehende Zeilen per Referenz/Beschreibung aktualisieren."""
    existing = list_price_items(bidder_id)
    by_key = {
        _price_row_key(i.category, i.year, i.referenz, i.leistungsbeschreibung): i
        for i in existing
    }
    created = updated = 0
    for row in structure.get("einmalig") or []:
        desc = (row.get("leistungsbeschreibung") or "").strip()
        if not desc:
            continue
        key = _price_row_key("einmalig", None, row.get("referenz"), desc)
        if key in by_key:
            item = by_key[key]
            upsert_price_item(
                item.id, bidder_id, "einmalig", desc,
                anzahl=float(row.get("anzahl", item.anzahl)),
                kosten_pro_einheit=float(row.get("kosten_pro_einheit", item.kosten_pro_einheit)),
                referenz=row.get("referenz") or item.referenz,
                einheit=row.get("einheit") or item.einheit,
                bemerkung=row.get("bemerkung") or item.bemerkung,
            )
            updated += 1
        else:
            upsert_price_item(
                None, bidder_id, "einmalig", desc,
                anzahl=float(row.get("anzahl") or 0),
                kosten_pro_einheit=float(row.get("kosten_pro_einheit") or 0),
                referenz=row.get("referenz"),
                einheit=row.get("einheit"),
                bemerkung=row.get("bemerkung"),
            )
            created += 1
    for row in structure.get("wiederkehrend") or []:
        desc = (row.get("leistungsbeschreibung") or "").strip()
        year = row.get("year")
        if not desc or not year:
            continue
        key = _price_row_key("wiederkehrend", int(year), row.get("referenz"), desc)
        if key in by_key:
            item = by_key[key]
            upsert_price_item(
                item.id, bidder_id, "wiederkehrend", desc,
                anzahl=float(row.get("anzahl", item.anzahl)),
                kosten_pro_einheit=float(row.get("kosten_pro_einheit", item.kosten_pro_einheit)),
                year=int(year),
                einheit=row.get("einheit") or item.einheit,
                bemerkung=row.get("bemerkung") or item.bemerkung,
            )
            updated += 1
        else:
            upsert_price_item(
                None, bidder_id, "wiederkehrend", desc,
                anzahl=float(row.get("anzahl") or 0),
                kosten_pro_einheit=float(row.get("kosten_pro_einheit") or 0),
                year=int(year),
                einheit=row.get("einheit"),
                bemerkung=row.get("bemerkung"),
            )
            created += 1
    return {"created": created, "updated": updated}


def _export_score_columns(
    cell: list[Score],
    evaluator_ids: list[int],
) -> list[Any]:
    """KI + Bewerter-Wert/Begründung-Spalten für Export."""
    ai_row = next((s for s in cell if s.source_key == "ai"), None)
    sys_row = next((s for s in cell if s.source_key == "system"), None)
    by_uid = {s.evaluator_user_id: s for s in cell if s.source_key.startswith("user:")}
    cols: list[Any] = [
        ai_row.value if ai_row else "",
        (ai_row.justification or "") if ai_row else "",
        (ai_row.source_chunk_ref or "") if ai_row else "",
    ]
    for uid in evaluator_ids:
        sc = by_uid.get(uid)
        cols.extend([sc.value if sc else "", (sc.justification or "") if sc else ""])
    cols.extend([
        sys_row.value if sys_row else "",
        (sys_row.justification or "") if sys_row else "",
    ])
    return cols


def build_evaluation_export_sheets(
    project_key: str,
    *,
    project_title: str = "",
    may_see_evaluators: bool = True,
) -> dict[str, tuple[list[str], list[list]]]:
    """
    Export-Daten für CSV/XLSX: Top-Level «Bewertungen» + «Einzelanforderungen» (Unterfragen).
    Begründungen spaltenweise pro Bewerter (analog Werte), plus KI und System (Preis).
    """
    from .m14_auth import get_username_by_id

    bidders = list_bidders(project_key)
    all_criteria = list_criteria(project_key)
    top_criteria = [c for c in all_criteria if c.parent_id is None]
    child_criteria = [c for c in all_criteria if c.parent_id is not None]
    parent_names = {c.id: c.name for c in all_criteria}
    scores = list_scores_for_project(project_key)
    scores_by_cell: dict[tuple[int, int], list[Score]] = {}
    for s in scores:
        scores_by_cell.setdefault((s.bidder_id, s.criterion_id), []).append(s)
    rankings = {r["bidder_id"]: r for r in compute_rankings(project_key)}

    evaluator_ids: list[int] = []
    if may_see_evaluators:
        seen: set[int] = set()
        for s in scores:
            if s.source_key.startswith("user:") and s.evaluator_user_id not in seen:
                seen.add(s.evaluator_user_id)
                evaluator_ids.append(s.evaluator_user_id)
        evaluator_ids.sort()
    evaluator_names = {uid: (get_username_by_id(uid) or f"User {uid}") for uid in evaluator_ids}

    main_headers = [
        "Projekt", "Bieter", "Kriterium", "Art", "Gewicht %", "Skala",
        "KI-Wert", "KI-Begründung", "KI-Quelle",
    ]
    for uid in evaluator_ids:
        name = evaluator_names[uid]
        main_headers.extend([f"Bewerter: {name}", f"Begründung: {name}"])
    main_headers += ["System-Wert", "System-Begründung", "Ø / Offiziell", "Rang", "Gesamt %"]

    main_rows: list[list] = []
    for crit in top_criteria:
        for bidder in bidders:
            cell = scores_by_cell.get((bidder.id, crit.id), [])
            rank_row = rankings.get(bidder.id, {})
            row = [
                project_title,
                bidder.name,
                crit.name,
                crit.kind,
                crit.weight_pct if crit.kind == "zuschlag" else "",
                crit.scale_max,
            ]
            row.extend(_export_score_columns(cell, evaluator_ids))
            row += [
                official_score(bidder.id, crit, cell),
                rank_row.get("rank"),
                rank_row.get("total_score"),
            ]
            main_rows.append(row)

    detail_headers = [
        "Projekt", "Bieter", "Übergeordnetes Kriterium", "Anforderung", "Art", "Skala",
        "KI-Wert", "KI-Begründung", "KI-Quelle",
    ]
    for uid in evaluator_ids:
        name = evaluator_names[uid]
        detail_headers.extend([f"Bewerter: {name}", f"Begründung: {name}"])
    detail_headers += ["System-Wert", "System-Begründung", "Offiziell"]

    detail_rows: list[list] = []
    for crit in child_criteria:
        parent_name = parent_names.get(crit.parent_id or 0, "")
        for bidder in bidders:
            cell = scores_by_cell.get((bidder.id, crit.id), [])
            row = [
                project_title,
                bidder.name,
                parent_name,
                crit.name,
                crit.kind,
                crit.scale_max,
            ]
            row.extend(_export_score_columns(cell, evaluator_ids))
            row.append(official_score(bidder.id, crit, cell))
            detail_rows.append(row)

    return {
        "Bewertungen": (main_headers, main_rows),
        "Einzelanforderungen": (detail_headers, detail_rows),
    }


def migrate_evaluation_db() -> None:
    """Leichte SQLite-Migrationen für Evaluation-Tabellen."""
    with engine.begin() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "bidder" not in tables:
            return

        crit_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(criterion)")).fetchall()}
        if "auto_price" not in crit_cols:
            conn.execute(text("ALTER TABLE criterion ADD COLUMN auto_price BOOLEAN NOT NULL DEFAULT 0"))
        if "description" not in crit_cols:
            conn.execute(text("ALTER TABLE criterion ADD COLUMN description VARCHAR"))
        if "ranking_phase" not in crit_cols:
            conn.execute(text("ALTER TABLE criterion ADD COLUMN ranking_phase INTEGER NOT NULL DEFAULT 1"))
        if "referenz" not in crit_cols:
            conn.execute(text("ALTER TABLE criterion ADD COLUMN referenz VARCHAR(16)"))

        if "price_item" in tables:
            price_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(price_item)")).fetchall()}
            if "bemerkung" not in price_cols:
                conn.execute(text("ALTER TABLE price_item ADD COLUMN bemerkung VARCHAR(500)"))

        score_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(score)")).fetchall()}
        if "source_key" not in score_cols:
            # Alte Score-Tabelle: 1 Zeile pro (bidder, criterion). Neue: 1 Zeile pro
            # (bidder, criterion, source_key) - Rebuild, SQLite kann Unique-Constraints
            # nicht per ALTER TABLE aendern. Bestehende Bewertungen werden zu "user:<id>".
            conn.execute(text("""
                CREATE TABLE score_new (
                    id INTEGER PRIMARY KEY,
                    bidder_id INTEGER NOT NULL,
                    criterion_id INTEGER NOT NULL,
                    source_key VARCHAR(32) NOT NULL,
                    evaluator_user_id INTEGER,
                    value FLOAT NOT NULL,
                    justification VARCHAR,
                    source_chunk_ref VARCHAR,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    UNIQUE (bidder_id, criterion_id, source_key)
                )
            """))
            conn.execute(text("""
                INSERT INTO score_new
                    (id, bidder_id, criterion_id, source_key, evaluator_user_id,
                     value, justification, source_chunk_ref, created_at, updated_at)
                SELECT
                    id, bidder_id, criterion_id, 'user:' || evaluator_user_id, evaluator_user_id,
                    value, justification, source_chunk_ref, created_at, updated_at
                FROM score
            """))
            conn.execute(text("DROP TABLE score"))
            conn.execute(text("ALTER TABLE score_new RENAME TO score"))
            conn.execute(text("CREATE INDEX ix_score_bidder_id ON score (bidder_id)"))
            conn.execute(text("CREATE INDEX ix_score_criterion_id ON score (criterion_id)"))
            conn.execute(text("CREATE INDEX ix_score_source_key ON score (source_key)"))
            conn.execute(text("CREATE INDEX ix_score_evaluator_user_id ON score (evaluator_user_id)"))

        if "evaluation_project_config" in tables:
            cfg_cols = {
                r[1] for r in conn.execute(text("PRAGMA table_info(evaluation_project_config)")).fetchall()
            }
            if "price_formula" not in cfg_cols:
                conn.execute(text(
                    "ALTER TABLE evaluation_project_config "
                    f"ADD COLUMN price_formula VARCHAR(20) NOT NULL DEFAULT '{DEFAULT_PRICE_FORMULA}'"
                ))
            if "vorgaben_ki_provider" not in cfg_cols:
                conn.execute(text(
                    "ALTER TABLE evaluation_project_config ADD COLUMN vorgaben_ki_provider VARCHAR(40)"
                ))
            if "vorgaben_ki_model" not in cfg_cols:
                conn.execute(text(
                    "ALTER TABLE evaluation_project_config ADD COLUMN vorgaben_ki_model VARCHAR(80)"
                ))
            if "rag_chunks_extraction" not in cfg_cols:
                conn.execute(text(
                    f"ALTER TABLE evaluation_project_config "
                    f"ADD COLUMN rag_chunks_extraction INTEGER NOT NULL DEFAULT {DEFAULT_RAG_CHUNKS_EXTRACTION}"
                ))
            if "bewertung_ki_provider" not in cfg_cols:
                conn.execute(text(
                    "ALTER TABLE evaluation_project_config ADD COLUMN bewertung_ki_provider VARCHAR(40)"
                ))
            if "bewertung_ki_model" not in cfg_cols:
                conn.execute(text(
                    "ALTER TABLE evaluation_project_config ADD COLUMN bewertung_ki_model VARCHAR(80)"
                ))

        if "evaluation_tender_doc" in tables:
            idx_rows = conn.execute(text("PRAGMA index_list('evaluation_tender_doc')")).fetchall()
            needs_role_uc = False
            for idx in idx_rows:
                if not idx[2]:
                    continue
                cols = [
                    r[2]
                    for r in conn.execute(text(f"PRAGMA index_info('{idx[1]}')")).fetchall()
                ]
                if cols == ["project_key", "document_id"]:
                    needs_role_uc = True
                    break
            if needs_role_uc:
                conn.execute(text("""
                    CREATE TABLE evaluation_tender_doc_new (
                        id INTEGER PRIMARY KEY,
                        project_key VARCHAR(80) NOT NULL,
                        document_id INTEGER NOT NULL,
                        tender_role VARCHAR(40) NOT NULL,
                        added_at DATETIME NOT NULL,
                        UNIQUE (project_key, document_id, tender_role)
                    )
                """))
                conn.execute(text("""
                    INSERT INTO evaluation_tender_doc_new
                        (id, project_key, document_id, tender_role, added_at)
                    SELECT id, project_key, document_id, tender_role, added_at
                    FROM evaluation_tender_doc
                """))
                conn.execute(text("DROP TABLE evaluation_tender_doc"))
                conn.execute(text("ALTER TABLE evaluation_tender_doc_new RENAME TO evaluation_tender_doc"))
                conn.execute(text(
                    "CREATE INDEX ix_evaluation_tender_doc_project_key "
                    "ON evaluation_tender_doc (project_key)"
                ))
                conn.execute(text(
                    "CREATE INDEX ix_evaluation_tender_doc_document_id "
                    "ON evaluation_tender_doc (document_id)"
                ))

        if "bidder_document_subtype" not in tables:
            conn.execute(text("""
                CREATE TABLE bidder_document_subtype (
                    id INTEGER PRIMARY KEY,
                    bidder_id INTEGER NOT NULL,
                    document_id INTEGER NOT NULL,
                    doc_subtype VARCHAR(80) NOT NULL,
                    added_at DATETIME NOT NULL,
                    UNIQUE (bidder_id, document_id, doc_subtype)
                )
            """))
            conn.execute(text(
                "CREATE INDEX ix_bidder_document_subtype_bidder_id "
                "ON bidder_document_subtype (bidder_id)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_bidder_document_subtype_document_id "
                "ON bidder_document_subtype (document_id)"
            ))
            if "bidder_document_link" in tables and "document" in tables:
                conn.execute(text("""
                    INSERT INTO bidder_document_subtype
                        (bidder_id, document_id, doc_subtype, added_at)
                    SELECT bdl.bidder_id, bdl.document_id, TRIM(d.doc_subtype), datetime('now')
                    FROM bidder_document_link bdl
                    JOIN document d ON d.id = bdl.document_id
                    WHERE d.doc_subtype IS NOT NULL AND TRIM(d.doc_subtype) != ''
                """))
        # zukünftige Spalten hier ergänzen
