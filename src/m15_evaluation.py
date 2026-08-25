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

from sqlalchemy import Column, Float, Integer, String, UniqueConstraint, text
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
    weight_pct: float = Field(default=0.0, sa_column=Column(Float, nullable=False, default=0.0))
    parent_id: Optional[int] = Field(default=None, foreign_key="criterion.id")
    scale_max: int = Field(default=10, sa_column=Column(Integer, nullable=False, default=10))
    sort_order: int = 0
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Score(SQLModel, table=True):
    __tablename__ = "score"
    __table_args__ = (UniqueConstraint("bidder_id", "criterion_id", name="uq_score_bidder_criterion"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    bidder_id: int = Field(foreign_key="bidder.id", index=True)
    criterion_id: int = Field(foreign_key="criterion.id", index=True)
    evaluator_user_id: int = Field(foreign_key="app_user.id", index=True)
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
) -> Criterion:
    kind = (kind or "").strip().lower()
    if kind not in CRITERION_KINDS:
        raise ValueError(f"kind muss {' oder '.join(CRITERION_KINDS)} sein")
    name = (name or "").strip()
    if not name:
        raise ValueError("Kriteriumname erforderlich")
    scale_max = max(1, int(scale_max))
    if kind == "zuschlag" and weight_pct < 0:
        raise ValueError("Gewicht muss >= 0 sein")
    with get_session() as session:
        criteria = session.exec(
            select(Criterion).where(Criterion.project_key == project_key, Criterion.is_deleted == False)
        ).all()
        crit = Criterion(
            project_key=project_key,
            kind=kind,
            name=name,
            weight_pct=float(weight_pct) if kind == "zuschlag" else 0.0,
            parent_id=parent_id,
            scale_max=scale_max,
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


def get_score(bidder_id: int, criterion_id: int) -> Optional[Score]:
    with get_session() as session:
        return session.exec(
            select(Score).where(Score.bidder_id == bidder_id, Score.criterion_id == criterion_id)
        ).first()


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


def upsert_score(
    bidder_id: int,
    criterion_id: int,
    evaluator_user_id: int,
    value: float,
    justification: str | None = None,
    source_chunk_ref: str | None = None,
    allow_override: bool = False,
) -> Score:
    with get_session() as session:
        crit = session.get(Criterion, criterion_id)
        if not crit or crit.is_deleted:
            raise ValueError("Kriterium nicht gefunden")
        scale_max = max(1, crit.scale_max)
        value = float(value)
        if value < 0 or value > scale_max:
            raise ValueError(f"Wert muss zwischen 0 und {scale_max} liegen")

        existing = session.exec(
            select(Score).where(Score.bidder_id == bidder_id, Score.criterion_id == criterion_id)
        ).first()

        if existing and not allow_override and existing.evaluator_user_id != evaluator_user_id:
            raise PermissionError("Bewertung gehört einem anderen Bewerter — nur Super-User kann ändern")

        now = _now()
        if existing:
            existing.value = value
            existing.justification = justification
            existing.source_chunk_ref = source_chunk_ref
            existing.evaluator_user_id = evaluator_user_id
            existing.updated_at = now
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        score = Score(
            bidder_id=bidder_id,
            criterion_id=criterion_id,
            evaluator_user_id=evaluator_user_id,
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


def _eignung_pass(value: float, scale_max: int) -> bool:
    """Unterhalb der Hälfte der Skala = nicht geeignet (K.O.)."""
    return value >= (scale_max / 2.0)


def compute_rankings(project_key: str) -> list[dict[str, Any]]:
    """
    Rangfolge: erst Eignung (K.O.), dann gewichtete Zuschlagskriterien.
    Returns list of dicts sorted by rank (1 = best).
    """
    bidders = list_bidders(project_key)
    criteria = list_criteria(project_key)
    scores = list_scores_for_project(project_key)

    score_map: dict[tuple[int, int], Score] = {
        (s.bidder_id, s.criterion_id): s for s in scores
    }
    eignung = [c for c in criteria if c.kind == "eignung"]
    zuschlag = [c for c in criteria if c.kind == "zuschlag"]
    total_weight = sum(c.weight_pct for c in zuschlag if c.weight_pct > 0)

    rows: list[dict[str, Any]] = []
    for bidder in bidders:
        ko = False
        eignung_details: list[dict[str, Any]] = []
        for crit in eignung:
            sc = score_map.get((bidder.id, crit.id))
            val = sc.value if sc else None
            passed = _eignung_pass(val, crit.scale_max) if val is not None else False
            if val is not None and not passed:
                ko = True
            eignung_details.append(
                {"criterion_id": crit.id, "name": crit.name, "value": val, "passed": passed}
            )

        weighted_sum = 0.0
        zuschlag_details: list[dict[str, Any]] = []
        if not ko and total_weight > 0:
            for crit in zuschlag:
                if crit.weight_pct <= 0:
                    continue
                sc = score_map.get((bidder.id, crit.id))
                if sc is None:
                    continue
                normalized = sc.value / max(1, crit.scale_max)
                contrib = normalized * crit.weight_pct
                weighted_sum += contrib
                zuschlag_details.append(
                    {
                        "criterion_id": crit.id,
                        "name": crit.name,
                        "value": sc.value,
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
    query = f"{criterion.name} — Nachweis im Angebot"
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
        f"Skala: 0 bis {scale_max}\n\n"
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
        # zukünftige Spalten hier ergänzen
