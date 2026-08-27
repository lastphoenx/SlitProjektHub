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
                source_key=f"user:{evaluator_id}",
                evaluator_user_id=evaluator_id,
                value=9.0,
            )
        )
        session.add(
            Score(
                bidder_id=b1.id,
                criterion_id=z1.id,
                source_key=f"user:{evaluator_id}",
                evaluator_user_id=evaluator_id,
                value=8.0,
            )
        )
        session.add(
            Score(
                bidder_id=b1.id,
                criterion_id=z2.id,
                source_key=f"user:{evaluator_id}",
                evaluator_user_id=evaluator_id,
                value=6.0,
            )
        )
        session.add(
            Score(
                bidder_id=b2.id,
                criterion_id=eign.id,
                source_key=f"user:{evaluator_id}",
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


def test_chunk_meta_prefix_subtype():
    from src.m09_docs import chunk_meta_prefix

    assert chunk_meta_prefix("Angebot (Bieter)", "x.pdf") == "[Angebot (Bieter) | x.pdf]\n"
    assert chunk_meta_prefix("Angebot (Bieter)", "x.pdf", "Preisblatt") == (
        "[Angebot (Bieter) · Preisblatt | x.pdf]\n"
    )


def test_validate_evaluation_cloud_gate():
    from unittest.mock import patch
    from src.m15_evaluation import validate_evaluation_cloud_gate

    with patch("src.m15_evaluation.get_bidder_document_ids", return_value=[11, 12]):
        assert validate_evaluation_cloud_gate("openai", 1, False) == "cloud_confirm"
        assert validate_evaluation_cloud_gate("openai", 1, True) is None
        assert validate_evaluation_cloud_gate("ollama", 1, False) is None
    with patch("src.m15_evaluation.get_bidder_document_ids", return_value=[]):
        assert validate_evaluation_cloud_gate("openai", 1, False) is None


def test_suggest_score_sanitizes_cloud_context():
    from unittest.mock import patch
    from src.m15_evaluation import Criterion, suggest_score_with_rag

    crit = Criterion(id=1, project_key="p", kind="zuschlag", name="Lösung", scale_max=10)
    rag = {
        "documents": [
            {
                "text": "Kontakt Maria Muster, AHV 756.1234.5678.97, Budget CHF 1.2 Mio.",
                "filename": "angebot.pdf",
                "chunk_id": 7,
            }
        ]
    }
    captured: dict = {}

    def fake_try(provider, system, messages, **kwargs):
        captured["user"] = messages[0]["content"]
        return '{"value": 8, "justification": "ok", "source_quote": "x", "source_chunk_id": 7}'

    def fake_sanitize(text: str) -> str:
        return (text or "").replace("Maria Muster", "[Name entfernt]").replace(
            "756.1234.5678.97", "[AHV]"
        )

    with patch("src.m15_evaluation.retrieve_relevant_chunks_hybrid", return_value=rag):
        with patch("src.m15_evaluation.try_models_with_messages", side_effect=fake_try):
            with patch("src.m15_evaluation.sanitize_for_cloud_text", side_effect=fake_sanitize) as mock_s:
                suggest_score_with_rag("p", 1, crit, provider="openai", model="gpt-4o-mini")

    mock_s.assert_called()
    user = captured["user"]
    assert "Maria Muster" not in user
    assert "756.1234.5678.97" not in user
    assert "[Name entfernt]" in user


def test_suggest_score_skips_sanitize_for_local_provider():
    from unittest.mock import patch
    from src.m15_evaluation import Criterion, suggest_score_with_rag

    crit = Criterion(id=1, project_key="p", kind="zuschlag", name="Lösung", scale_max=10)
    rag = {
        "documents": [
            {"text": "Kontakt Maria Muster intern.", "filename": "angebot.pdf", "chunk_id": 1}
        ]
    }
    captured: dict = {}

    def fake_try(provider, system, messages, **kwargs):
        captured["user"] = messages[0]["content"]
        return '{"value": 5, "justification": "ok"}'

    with patch("src.m15_evaluation.retrieve_relevant_chunks_hybrid", return_value=rag):
        with patch("src.m15_evaluation.try_models_with_messages", side_effect=fake_try):
            with patch("src.m15_evaluation.sanitize_for_cloud_text") as mock_s:
                suggest_score_with_rag("p", 1, crit, provider="ollama", model="qwen3:32b")

    mock_s.assert_not_called()
    assert "Maria Muster" in captured["user"]


if __name__ == "__main__":
    test_create_bidder_and_criterion()
    test_ranking_ko_and_weighted_sum()
    test_chunk_meta_prefix_subtype()
    test_validate_evaluation_cloud_gate()
    test_suggest_score_sanitizes_cloud_context()
    test_suggest_score_skips_sanitize_for_local_provider()
    print("OK")
