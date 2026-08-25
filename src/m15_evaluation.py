"""
Phase C — Offertbeurteilung: Bieter, Kriterien, Scores, Rangfolge.

AppRole-Gating über m14_auth (can_evaluate, can_view_evaluator_details).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, Column, Float, Integer, String, UniqueConstraint, text
from sqlmodel import Field, Session, SQLModel, select

from .m03_db import engine, get_session
from .m08_llm import try_models_with_messages
from .m09_rag import retrieve_relevant_chunks_hybrid

log = logging.getLogger(__name__)

CRITERION_KINDS = ("eignung", "zuschlag")
ANGEbot_CLASSIFICATION = "Angebot (Bieter)"


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
    """RAG + LLM-Vorschlag für eine Bewertung (Mensch bestätigt)."""
    query = f"{criterion.description or criterion.name} — Nachweis im Angebot"
    rag = retrieve_relevant_chunks_hybrid(
        query,
        project_key=project_key,
        limit=5,
        threshold=0.35,
        classification_filter=ANGEbot_CLASSIFICATION,
        bidder_id=bidder_id,
    )
    docs = rag.get("documents", [])
    if not docs:
        rag = retrieve_relevant_chunks_hybrid(
            query,
            project_key=project_key,
            limit=5,
            threshold=0.35,
            bidder_id=bidder_id,
        )
        docs = rag.get("documents", [])

    context_parts: list[str] = []
    for i, d in enumerate(docs[:5], 1):
        text = (d.get("text") or "")[:1200]
        fname = d.get("filename", "?")
        chunk_id = d.get("chunk_id")
        context_parts.append(f"[{i}] Datei: {fname}, Chunk {chunk_id}\n{text}")

    context = "\n\n".join(context_parts) or "Keine passenden Angebotsstellen gefunden."
    scale_max = max(1, criterion.scale_max)
    system = (
        "Du bist Vergabesachverständiger. Bewerte ein Angebot zu einem Kriterium. "
        "Antwort NUR als JSON mit keys: value (Zahl 0 bis scale_max), justification (kurz), "
        "source_quote (Zitat aus dem Angebot), source_chunk_id (Zahl oder null)."
    )
    user = (
        f"Kriterium ({criterion.kind}): {criterion.name}\n"
        + (f"Anforderungstext: {criterion.description}\n" if criterion.description else "")
        + f"Skala: 0 bis {scale_max}\n\n"
        f"Angebotsstellen:\n{context}\n\n"
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
