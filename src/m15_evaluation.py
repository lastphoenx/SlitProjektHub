"""
Phase C — Offertbeurteilung: Bieter, Kriterien, Scores, Rangfolge.

AppRole-Gating über m14_auth (can_evaluate, can_view_evaluator_details).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Boolean, Column, Float, Integer, String, UniqueConstraint, text
from sqlmodel import Field, Session, SQLModel, select

from .m03_db import engine, get_session
from .m08_llm import try_models_with_messages
from .m09_docs import get_document_by_id
from .m09_rag import retrieve_relevant_chunks_hybrid
from .m16_idea_visual import is_cloud_llm_provider, sanitize_for_cloud_text

log = logging.getLogger(__name__)

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


class EvaluationTenderDoc(SQLModel, table=True):
    """Projekt-Vorgaben/Frameworks für die Offertbeurteilung (verweist auf bestehende Document-Zeilen)."""
    __tablename__ = "evaluation_tender_doc"
    __table_args__ = (
        UniqueConstraint("project_key", "document_id", name="uq_eval_tender_doc"),
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


def create_criterion(
    project_key: str,
    kind: str,
    name: str,
    weight_pct: float = 0.0,
    scale_max: int = 10,
    parent_id: Optional[int] = None,
    auto_price: bool = False,
    description: Optional[str] = None,
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
    with get_session() as session:
        criteria = session.exec(
            select(Criterion).where(Criterion.project_key == project_key, Criterion.is_deleted == False)
        ).all()
        crit = Criterion(
            project_key=project_key,
            kind=kind,
            name=name,
            description=(description or "").strip() or None,
            weight_pct=float(weight_pct) if kind == "zuschlag" else 0.0,
            parent_id=parent_id,
            scale_max=scale_max,
            auto_price=bool(auto_price),
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
    return [r.document_id for r in rows]


def link_tender_doc(project_key: str, document_id: int, tender_role: str) -> bool:
    role = (tender_role or "").strip().lower()
    if role not in TENDER_ROLES:
        raise ValueError(f"tender_role muss einer von {TENDER_ROLES} sein")
    with get_session() as session:
        existing = session.exec(
            select(EvaluationTenderDoc).where(
                EvaluationTenderDoc.project_key == project_key,
                EvaluationTenderDoc.document_id == document_id,
            )
        ).first()
        if existing:
            existing.tender_role = role
            session.add(existing)
            session.commit()
            return True
        link = EvaluationTenderDoc(
            project_key=project_key,
            document_id=document_id,
            tender_role=role,
        )
        session.add(link)
        session.commit()
        return True


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
        }
    try:
        years = json.loads(row.price_years_json or "[]")
        years = [int(y) for y in years if y]
    except (TypeError, ValueError, json.JSONDecodeError):
        years = list(DEFAULT_PRICE_YEARS)
    if not years:
        years = list(DEFAULT_PRICE_YEARS)
    return {
        "price_years": years,
        "vergabe_notes": (row.vergabe_notes or "").strip(),
        "rag_chunks_per_role": max(4, min(24, int(row.rag_chunks_per_role or 12))),
    }


def save_evaluation_config(
    project_key: str,
    *,
    price_years: list[int] | None = None,
    vergabe_notes: str | None = None,
    rag_chunks_per_role: int | None = None,
) -> None:
    cfg = get_evaluation_config(project_key)
    if price_years is not None:
        cfg["price_years"] = [int(y) for y in price_years if y]
    if vergabe_notes is not None:
        cfg["vergabe_notes"] = vergabe_notes.strip()
    if rag_chunks_per_role is not None:
        cfg["rag_chunks_per_role"] = max(4, min(24, int(rag_chunks_per_role)))
    with get_session() as session:
        row = session.get(EvaluationProjectConfig, project_key)
        if not row:
            row = EvaluationProjectConfig(project_key=project_key)
        row.price_years_json = json.dumps(cfg["price_years"])
        row.vergabe_notes = cfg["vergabe_notes"] or None
        row.rag_chunks_per_role = cfg["rag_chunks_per_role"]
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
            for ch in e.get("children") or []:
                if kind == "eignung" and int(ch.get("scale_max") or 10) != 1:
                    warnings.append(f"Eignungs-Unterkriterium «{ch.get('name')}»: scale_max sollte 1 sein.")
    return warnings


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
) -> str:
    """Mehrere RAG-Pässe nach tender_role, dedupliziert."""
    all_docs: list[dict] = []
    for roles, query in role_queries:
        ids = get_tender_document_ids(project_key, roles=roles)
        if not ids:
            continue
        rag = retrieve_relevant_chunks_hybrid(
            query,
            project_key=project_key,
            limit=limit_per_role,
            threshold=0.28,
            document_ids=tuple(ids),
        )
        all_docs.extend(rag.get("documents", []))
    return _format_rag_context(
        _dedupe_rag_docs(all_docs)[: limit_per_role * 2],
        empty_msg="Keine passenden Vorgaben-Stellen gefunden.",
    )


def bidder_doc_ids_for_criterion(bidder_id: int, criterion: Criterion) -> list[int]:
    """Bevorzugte Bieter-Dokument-IDs passend zum Kriterium (Subtyp-Heuristik)."""
    all_ids = get_bidder_document_ids(bidder_id)
    if not all_ids:
        return []
    by_subtype: dict[str, list[int]] = {}
    for doc_id in all_ids:
        doc = get_document_by_id(doc_id)
        if not doc:
            continue
        st = (doc.doc_subtype or "").strip()
        if st:
            by_subtype.setdefault(st, []).append(doc_id)
    preferred: tuple[str, ...]
    if criterion.auto_price or "preis" in (criterion.name or "").lower():
        preferred = ("Preisblatt",)
    elif criterion.kind == "eignung":
        preferred = EIGNUNG_BIDDER_SUBTYPES
    else:
        preferred = ZUSCHLAG_BIDDER_SUBTYPES
    matched: list[int] = []
    for st in preferred:
        matched.extend(by_subtype.get(st, []))
    return matched or all_ids


def tender_roles_for_criterion(criterion: Criterion) -> tuple[str, ...]:
    """Welche Vorgabe-Rollen für RAG zu einem Kriterium passen."""
    common = ("bewertungsvorgaben", "ausschreibungsunterlage", "interne_richtlinie")
    if criterion.kind == "eignung":
        return ("eignungskriterien",) + common
    return ("zuschlagskriterien",) + common


def _format_rag_context(docs: list[dict], *, empty_msg: str) -> str:
    parts: list[str] = []
    for i, d in enumerate(docs[:5], 1):
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


def sync_price_criterion_scores(project_key: str) -> None:
    """
    Schreibt für jedes auto_price-Kriterium den "system"-Score neu: linear zum
    günstigsten Angebot (günstigstes TCO = volle Punktzahl). Aufrufen, wann immer
    sich ein Preisblatt geändert hat.
    """
    criteria = [c for c in list_criteria(project_key) if c.auto_price]
    if not criteria:
        return
    bidders = list_bidders(project_key)
    if not bidders:
        return
    totals = {b.id: compute_bidder_tco(b.id)["total_inkl_mwst"] for b in bidders}
    priced = {bid: t for bid, t in totals.items() if t and t > 0}
    if not priced:
        return
    cheapest = min(priced.values())
    for crit in criteria:
        scale_max = max(1, crit.scale_max)
        for bidder in bidders:
            total = totals.get(bidder.id) or 0.0
            if total <= 0:
                continue
            value = round(scale_max * (cheapest / total), 3)
            value = max(0.0, min(float(scale_max), value))
            upsert_score(
                bidder.id,
                crit.id,
                evaluator_user_id=0,
                value=value,
                justification=f"Automatisch: günstigstes TCO CHF {cheapest:,.2f} / dieses TCO CHF {total:,.2f}",
                as_source="system",
            )


# ── Rangfolge ───────────────────────────────────────────────────────────────


def _eignung_pass(value: float, scale_max: int) -> bool:
    """Unterhalb der Hälfte der Skala = nicht geeignet (K.O.). Für Ja/Nein-Fragen (scale_max=1): 1=Ja."""
    return value >= (scale_max / 2.0)


def compute_rankings(project_key: str) -> list[dict[str, Any]]:
    """
    Rangfolge: erst Eignung (K.O.), dann gewichtete Zuschlagskriterien.
    Nur TOP-LEVEL-Kriterien (parent_id is None) fliessen in die Gewichtung ein -
    Unterfragen (z.B. F01-001 unter F-01) sind Beleg-/KI-Hilfsebene, kein eigenes
    Gewicht. Eignungs-Unterfragen lösen K.O. aus, sobald irgendeine mit "Nein" (0)
    beantwortet ist.
    """
    bidders = list_bidders(project_key)
    criteria = list_criteria(project_key)
    scores = list_scores_for_project(project_key)

    scores_by_cell: dict[tuple[int, int], list[Score]] = {}
    for s in scores:
        scores_by_cell.setdefault((s.bidder_id, s.criterion_id), []).append(s)

    by_id = {c.id: c for c in criteria}
    eignung_top = [c for c in criteria if c.kind == "eignung" and c.parent_id is None]
    eignung_children: dict[int, list] = {}
    for c in criteria:
        if c.kind == "eignung" and c.parent_id is not None:
            eignung_children.setdefault(c.parent_id, []).append(c)
    zuschlag_top = [c for c in criteria if c.kind == "zuschlag" and c.parent_id is None]
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

        weighted_sum = 0.0
        zuschlag_details: list[dict[str, Any]] = []
        if not ko and total_weight > 0:
            for crit in zuschlag_top:
                if crit.weight_pct <= 0:
                    continue
                val, _answered, _total = rolled_up_score(bidder.id, crit, criteria, scores_by_cell)
                if val is None:
                    continue
                normalized = val / max(1, crit.scale_max)
                contrib = normalized * crit.weight_pct
                weighted_sum += contrib
                zuschlag_details.append(
                    {
                        "criterion_id": crit.id,
                        "name": crit.name,
                        "value": val,
                        "weight_pct": crit.weight_pct,
                        "normalized": round(normalized, 4),
                        "contrib": round(contrib, 4),
                    }
                )
            total_score = round((weighted_sum / total_weight) * 100.0, 2) if total_weight else None
        else:
            total_score = None

        rows.append(
            {
                "bidder_id": bidder.id,
                "bidder_name": bidder.name,
                "ko": ko,
                "eignung": eignung_details,
                "zuschlag": zuschlag_details,
                "total_score": total_score,
            }
        )

    eligible = [r for r in rows if not r["ko"] and r["total_score"] is not None]
    ineligible = [r for r in rows if r["ko"] or r["total_score"] is None]
    eligible.sort(key=lambda r: r["total_score"], reverse=True)
    ranked = eligible + ineligible
    for idx, row in enumerate(ranked, start=1):
        row["rank"] = idx if not row["ko"] and row["total_score"] is not None else None
    return ranked


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


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
        docs, empty_msg="Keine passenden Angebotsstellen gefunden."
    )
    if is_cloud_llm_provider(provider):
        tender_context = sanitize_for_cloud_text(tender_context)
        offer_context = sanitize_for_cloud_text(offer_context)
    scale_max = max(1, criterion.scale_max)
    system = (
        f"{VERGABE_SYSTEM_RULES}\n"
        "Du bist Vergabesachverständiger. Vergleiche Ausschreibungs-Vorgaben mit dem Angebot "
        "des Bieters und bewerte ein Kriterium. "
        "Antwort NUR als JSON mit keys: value (Zahl 0 bis scale_max), justification (kurz), "
        "source_quote (Zitat aus dem Angebot), source_chunk_id (Zahl oder null)."
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
        max_tokens=800,
        temperature=0.2,
        model=model,
    )
    parsed: dict[str, Any] = {}
    if raw:
        m = _JSON_BLOCK_RE.search(raw)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                log.warning("LLM JSON parse failed: %s", raw[:200])

    value = parsed.get("value")
    try:
        value_f = float(value) if value is not None else None
    except (TypeError, ValueError):
        value_f = None
    if value_f is not None:
        value_f = max(0.0, min(float(scale_max), value_f))

    chunk_ref = None
    quote = parsed.get("source_quote") or ""
    chunk_id = parsed.get("source_chunk_id")
    if chunk_id:
        chunk_ref = f"chunk:{chunk_id}"
        if quote:
            chunk_ref += f" | {quote[:500]}"
    elif quote:
        chunk_ref = quote[:500]

    return {
        "value": value_f,
        "justification": (parsed.get("justification") or "").strip(),
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
                    scale_max=int(child.get("scale_max") or 10),
                    parent_id=parent_id,
                    description=child.get("description"),
                )
                existing_names.add(cname)
                created += 1

    _import_kind("eignung", data.get("eignung") or [])
    _import_kind("zuschlag", data.get("zuschlag") or [])
    warnings = validate_criteria_payload(data)
    return {"created": created, "skipped": skipped, "warnings": warnings}


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
    role_queries = [
        (("eignungskriterien", "ausschreibungsunterlage"), "Eignungskriterien K.O. Mindestanforderungen Bieter"),
        (("zuschlagskriterien", "bewertungsvorgaben"), "Zuschlagskriterien Gewichtung Punkte Bewertungsmatrix"),
        (("bewertungsvorgaben", "ausschreibungsunterlage"), "Bewertungsvorgaben Verfahren Skala Rangfolge"),
    ]
    context = _retrieve_tender_context_multi(project_key, role_queries, limit_per_role=limit)
    if is_cloud_llm_provider(provider):
        context = sanitize_for_cloud_text(context)

    vergabe_extra = cfg.get("vergabe_notes") or ""
    system = (
        f"{VERGABE_SYSTEM_RULES}\n"
        "Extrahiere strukturierte Bewertungskriterien aus Ausschreibungsunterlagen. "
        "Antwort NUR als JSON mit keys eignung und zuschlag (Listen von Objekten). "
        "Jedes Objekt: name (kurz), description (voller Anforderungstext), optional children. "
        "Zuschlag: weight_pct, scale_max (default 10), auto_price true nur für reines Preis-Kriterium. "
        "Eignung: scale_max immer 1, kein weight_pct, keine Teilpunkte."
    )
    if vergabe_extra:
        system += f"\nProjekt-Hinweise: {vergabe_extra}"
    user = f"Ausschreibungsauszüge:\n{context}\n\nJSON:"
    raw = try_models_with_messages(
        provider, system, [{"role": "user", "content": user}],
        max_tokens=4000, temperature=0.1, model=model,
    )
    payload = _parse_llm_json_object(raw)
    warnings = validate_criteria_payload(payload)
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
        doc = get_document_by_id(doc_id)
        if not doc:
            continue
        if doc.doc_subtype == "Preisblatt" or "preisblatt" in (doc.filename or "").lower():
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

    rag = retrieve_relevant_chunks_hybrid(
        "Preisblatt Positionen Leistungsbeschreibung Referenz Einheit Kosten",
        project_key=project_key,
        limit=cfg["rag_chunks_per_role"],
        threshold=0.28,
        document_ids=tuple(tender_ids),
    )
    context = _format_rag_context(rag.get("documents", []), empty_msg="Keine Preisblatt-Stellen gefunden.")
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

    rag = retrieve_relevant_chunks_hybrid(
        "Preisblatt Kosten CHF Positionen Anzahl Einheit",
        project_key=project_key,
        limit=cfg["rag_chunks_per_role"],
        threshold=0.28,
        classification_filter=ANGEbot_CLASSIFICATION,
        bidder_id=bidder_id,
        document_ids=tuple(doc_ids),
    )
    context = _format_rag_context(rag.get("documents", []), empty_msg="Keine Preisblatt-Stellen gefunden.")
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
        # zukünftige Spalten hier ergänzen
