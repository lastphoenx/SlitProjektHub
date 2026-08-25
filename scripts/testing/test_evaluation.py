#!/usr/bin/env python
"""Tests für Phase C Offertbeurteilung."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlmodel import Session, create_engine, SQLModel

from src.m14_auth import AppRole, AppUser
from src.m15_evaluation import (
    Bidder,
    BidderDocumentLink,
    Criterion,
    Score,
    compute_rankings,
    create_bidder,
    create_criterion,
    upsert_score,
)


def _setup_db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            AppRole(key="projektleiter_intern", title="PL intern", sort_order=20)
        )
        user = AppUser(
            username="evaluator",
            password_hash="x",
            app_role_key="projektleiter_intern",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id
    return engine, user_id


def test_ranking_ko_and_weighted_sum():
    engine, evaluator_id = _setup_db()
    project_key = "test-project"

    with Session(engine) as session:
        b1 = Bidder(project_key=project_key, name="A")
        b2 = Bidder(project_key=project_key, name="B")
        session.add(b1)
        session.add(b2)
        session.commit()
        session.refresh(b1)
        session.refresh(b2)

        eign = Criterion(project_key=project_key, kind="eignung", name="E1", scale_max=10)
        z1 = Criterion(
            project_key=project_key, kind="zuschlag", name="Z1", weight_pct=60, scale_max=10
        )
        z2 = Criterion(
            project_key=project_key, kind="zuschlag", name="Z2", weight_pct=40, scale_max=10
        )
        session.add(eign)
        session.add(z1)
        session.add(z2)
        session.commit()
        session.refresh(eign)
        session.refresh(z1)
        session.refresh(z2)

        session.add(
            Score(
                bidder_id=b1.id,
                criterion_id=eign.id,
                evaluator_user_id=evaluator_id,
                value=9.0,
            )
        )
        session.add(
            Score(
                bidder_id=b1.id,
                criterion_id=z1.id,
                evaluator_user_id=evaluator_id,
                value=8.0,
            )
        )
        session.add(
            Score(
                bidder_id=b1.id,
                criterion_id=z2.id,
                evaluator_user_id=evaluator_id,
                value=6.0,
            )
        )
        session.add(
            Score(
                bidder_id=b2.id,
                criterion_id=eign.id,
                evaluator_user_id=evaluator_id,
                value=2.0,
            )
        )
        session.commit()

    # Patch engine for compute_rankings helpers
    import src.m15_evaluation as ev
    import src.m03_db as db

    old_engine = db.engine
    old_get = ev.get_session
    db.engine = engine
    ev.engine = engine
    ev.get_session = lambda: Session(engine)

    rankings = compute_rankings(project_key)
    by_name = {r["bidder_name"]: r for r in rankings}

    assert by_name["B"]["ko"] is True
    assert by_name["B"]["total_score"] is None
    assert by_name["A"]["total_score"] == 72.0
    assert by_name["A"]["rank"] == 1

    db.engine = old_engine
    ev.engine = old_engine
    ev.get_session = old_get


def test_create_bidder_and_criterion():
    engine, _ = _setup_db()
    import src.m15_evaluation as ev
    import src.m03_db as db

    old_engine = db.engine
    db.engine = engine
    ev.engine = engine
    ev.get_session = lambda: Session(engine)

    b = create_bidder("p1", "Firma X")
    c = create_criterion("p1", "zuschlag", "Preis", weight_pct=50.0)
    assert b.name == "Firma X"
    assert c.kind == "zuschlag"
    assert c.weight_pct == 50.0

    db.engine = old_engine
    ev.engine = old_engine


if __name__ == "__main__":
    test_create_bidder_and_criterion()
    test_ranking_ko_and_weighted_sum()
    print("OK")
