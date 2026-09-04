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
    EvaluationProjectConfig,
    EvaluationTenderDoc,
    PriceItem,
    Score,
    compute_rankings,
    create_bidder,
    create_criterion,
    get_tender_document_ids,
    import_criteria_payload,
    link_tender_doc,
    list_criteria,
    list_price_items,
    merge_price_structure_for_bidder,
    seed_price_structure_for_bidder,
    tender_roles_for_criterion,
    unlink_tender_doc,
    upsert_score,
    validate_tender_cloud_gate,
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


def test_compute_rankings_phase2_interim():
    engine, evaluator_id = _setup_db()
    project_key = "p-phase2"

    with Session(engine) as session:
        b1 = Bidder(project_key=project_key, name="Leader")
        b2 = Bidder(project_key=project_key, name="Chaser")
        b3 = Bidder(project_key=project_key, name="Out")
        session.add(b1)
        session.add(b2)
        session.add(b3)
        session.commit()
        session.refresh(b1)
        session.refresh(b2)
        session.refresh(b3)

        zk = Criterion(
            project_key=project_key, kind="zuschlag", name="ZK1", weight_pct=70,
            scale_max=10, ranking_phase=1,
        )
        a01 = Criterion(
            project_key=project_key, kind="zuschlag", name="A-01 Präsentation", weight_pct=30,
            scale_max=10, ranking_phase=2,
        )
        session.add(zk)
        session.add(a01)
        session.commit()
        session.refresh(zk)
        session.refresh(a01)

        def score(bid, crit, val):
            session.add(
                Score(
                    bidder_id=bid,
                    criterion_id=crit,
                    source_key=f"user:{evaluator_id}",
                    evaluator_user_id=evaluator_id,
                    value=val,
                )
            )

        score(b1.id, zk.id, 9.0)
        score(b2.id, zk.id, 9.0)
        score(b3.id, zk.id, 6.5)
        session.commit()

    import src.m15_evaluation as ev
    import src.m03_db as db

    old_engine = db.engine
    old_get = ev.get_session
    db.engine = engine
    ev.engine = engine
    ev.get_session = lambda: Session(engine)

    rankings = {r["bidder_name"]: r for r in compute_rankings(project_key)}

    assert rankings["Leader"]["interim_score"] == 90.0
    assert rankings["Chaser"]["interim_score"] == 90.0
    assert rankings["Leader"]["interim_rank"] in (1, 2)
    assert rankings["Chaser"]["interim_rank"] in (1, 2)
    assert rankings["Chaser"]["can_still_win"] is True
    assert rankings["Out"]["can_still_win"] is False
    assert rankings["Leader"]["has_phase2"] is True

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


def test_tender_doc_link_and_roles():
    engine, _ = _setup_db()
    import src.m15_evaluation as ev
    import src.m03_db as db
    from src.m03_db import Document

    old_engine = db.engine
    old_get = ev.get_session
    db.engine = engine
    ev.engine = engine
    ev.get_session = lambda: Session(engine)

    with Session(engine) as session:
        doc = Document(
            filename="pflichtenheft.pdf",
            sha256_hash="abc123",
            classification="Pflichtenheft (Projekt)",
            file_path="/tmp/x.pdf",
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        doc_id = doc.id

    link_tender_doc("p1", doc_id, "zuschlagskriterien")
    assert get_tender_document_ids("p1") == [doc_id]
    assert get_tender_document_ids("p1", roles=("eignungskriterien",)) == []

    crit = Criterion(project_key="p1", kind="zuschlag", name="Z1")
    assert "zuschlagskriterien" in tender_roles_for_criterion(crit)

    unlink_tender_doc("p1", doc_id)
    assert get_tender_document_ids("p1") == []

    db.engine = old_engine
    ev.engine = old_engine
    ev.get_session = old_get


def test_import_criteria_payload_skip_existing():
    engine, _ = _setup_db()
    import src.m15_evaluation as ev
    import src.m03_db as db

    old_engine = db.engine
    db.engine = engine
    ev.engine = engine
    ev.get_session = lambda: Session(engine)

    create_criterion("p1", "zuschlag", "Preis", weight_pct=30, auto_price=True)
    stats = import_criteria_payload(
        "p1",
        {
            "zuschlag": [
                {"name": "Preis", "weight_pct": 30, "auto_price": True},
                {"name": "Qualität", "weight_pct": 70, "description": "Lösung"},
            ]
        },
    )
    assert stats["skipped"] >= 1
    assert stats["created"] >= 1
    names = {c.name for c in list_criteria("p1")}
    assert "Qualität" in names

    db.engine = old_engine
    ev.engine = old_engine


def test_seed_and_merge_price_structure():
    engine, _ = _setup_db()
    import src.m15_evaluation as ev
    import src.m03_db as db

    old_engine = db.engine
    db.engine = engine
    ev.engine = engine
    ev.get_session = lambda: Session(engine)

    bidder = create_bidder("p1", "Bieter A")
    seeded = seed_price_structure_for_bidder(
        bidder.id,
        {"einmalig": [{"referenz": "F-01", "leistungsbeschreibung": "Konzept", "anzahl": 0, "kosten_pro_einheit": 0}]},
    )
    assert seeded["created"] == 1
    merged = merge_price_structure_for_bidder(
        bidder.id,
        {"einmalig": [{"referenz": "F-01", "leistungsbeschreibung": "Konzept", "anzahl": 5, "kosten_pro_einheit": 100}]},
    )
    assert merged["updated"] == 1
    items = list_price_items(bidder.id)
    assert items[0].chf == 500.0

    db.engine = old_engine
    ev.engine = old_engine


def test_validate_tender_cloud_gate():
    from unittest.mock import patch
    from src.m15_evaluation import validate_tender_cloud_gate

    with patch("src.m15_evaluation.get_tender_document_ids", return_value=[1]):
        assert validate_tender_cloud_gate("openai", "p1", False) == "cloud_confirm"
        assert validate_tender_cloud_gate("openai", "p1", True) is None
        assert validate_tender_cloud_gate("ollama", "p1", False) is None


def test_suggest_tender_role_and_validate_criteria():
    from src.m15_evaluation import (
        normalize_chunk_size,
        infer_ranking_phase,
        suggest_tender_role,
        validate_criteria_payload,
    )

    assert suggest_tender_role("Anforderung/Feature", "Anhang2_Preisblatt_Unisport.pdf") == "preisblatt_vorlage"
    assert suggest_tender_role("Pflichtenheft (Projekt)", "Pflichtenheft.docx") == "ausschreibungsunterlage"
    assert infer_ranking_phase("A-01 Angebotspräsentation") == 2
    assert infer_ranking_phase("ZK3 Lösung") == 1
    assert normalize_chunk_size(0) == 0
    assert normalize_chunk_size(150) == 200
    assert normalize_chunk_size(5000) == 4000
    warnings = validate_criteria_payload({
        "zuschlag": [{"name": "A", "weight_pct": 30}, {"name": "B", "weight_pct": 30}],
    })
    assert any("100" in w for w in warnings)


def test_evaluation_config_roundtrip():
    engine, _ = _setup_db()
    import src.m15_evaluation as ev
    import src.m03_db as db

    old_engine = db.engine
    db.engine = engine
    ev.engine = engine
    ev.get_session = lambda: Session(engine)

    from src.m15_evaluation import EvaluationProjectConfig, get_evaluation_config, save_evaluation_config
    SQLModel.metadata.create_all(engine)

    save_evaluation_config("p1", price_years=[2026, 2027], vergabe_notes="Test", rag_chunks_per_role=14)
    cfg = get_evaluation_config("p1")
    assert cfg["price_years"] == [2026, 2027]
    assert cfg["rag_chunks_per_role"] == 14

    save_evaluation_config("p1", vorgaben_ki_provider="ollama", vorgaben_ki_model="llama3.3:70b")
    cfg2 = get_evaluation_config("p1")
    assert cfg2["vorgaben_ki_provider"] == "ollama"
    assert cfg2["vorgaben_ki_model"] == "llama3.3:70b"

    from unittest.mock import patch
    from src.m15_evaluation import resolve_vorgaben_ki

    p, m = resolve_vorgaben_ki("p1", "", "", global_provider="openai", global_model="gpt-4o-mini")
    assert p == "ollama"
    assert m == "llama3.3:70b"
    # resolve_visual_llm() prueft have_key(provider) - ohne konfigurierten Key wuerde die
    # explizite Picker-Wahl "openai" sonst still auf den Projekt-Default zurueckfallen und
    # den eigentlichen Test (Picker > Projekt-Default) unbemerkt umgehen.
    with patch("src.m16_idea_visual.have_key", return_value=True):
        p2, m2 = resolve_vorgaben_ki("p1", "openai", "gpt-4o", global_provider="openai", global_model="gpt-4o-mini")
    assert p2 == "openai"
    assert m2 == "gpt-4o"

    db.engine = old_engine
    ev.engine = old_engine


def test_criteria_preview_meta():
    from src.m15_evaluation import criteria_apply_requires_confirm, criteria_preview_meta

    data = {
        "eignung": [],
        "zuschlag": [{"name": "A", "weight_pct": 30, "description": "x"}, {"name": "B", "weight_pct": 30}],
    }
    meta = criteria_preview_meta(data)
    assert meta["missing_eignung"] is True
    assert meta["weight_ok"] is False
    assert meta["requires_confirm"] is True
    assert criteria_apply_requires_confirm(data) is True

    ok = {
        "eignung": [{"name": "K1", "description": "ok"}],
        "zuschlag": [{"name": "Z", "weight_pct": 100, "description": "z"}],
    }
    meta2 = criteria_preview_meta(ok)
    assert meta2["weight_ok"] is True
    assert meta2["requires_confirm"] is False


def test_ki_busy_hint():
    from unittest.mock import patch

    from src.m15_evaluation import ki_busy_hint

    with patch("src.m08_llm.ollama_runtime_status", return_value={"message": "Ollama ist frei."}):
        with patch("src.m16_idea_jobs.idea_ki_queue_size", return_value=0):
            h = ki_busy_hint("openai", "gpt-4o")
    assert "KI läuft" in h["message"]

    with patch("src.m08_llm.have_key", return_value=True), patch(
        "src.m08_llm.ollama_runtime_status",
        return_value={"message": "Modellwechsel nötig."},
    ), patch("src.m16_idea_jobs.idea_ki_queue_size", return_value=2):
        h2 = ki_busy_hint("ollama", "llama3.3:70b")
    assert "Modellwechsel" in h2["message"]
    assert "Warteschlange" in h2["message"]


def test_score_justification_required():
    from src.m15_evaluation import (
        Criterion,
        score_requires_justification,
        validate_score_justification,
    )

    eign = Criterion(id=1, project_key="p", kind="eignung", name="E1", scale_max=1)
    assert score_requires_justification(eign, 0) is True
    assert score_requires_justification(eign, 1) is False
    zus = Criterion(id=2, project_key="p", kind="zuschlag", name="Z1", scale_max=10)
    assert score_requires_justification(zus, 8) is True
    assert score_requires_justification(zus, 10) is False
    try:
        validate_score_justification(zus, 7, "")
    except ValueError as exc:
        assert "Begründung" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_sync_price_criterion_scores_reciprocal_gate():
    engine, evaluator_id = _setup_db()
    project_key = "p-price"
    with Session(engine) as session:
        b1 = Bidder(project_key=project_key, name="Günstig")
        b2 = Bidder(project_key=project_key, name="Teuer")
        session.add(b1)
        session.add(b2)
        session.commit()
        session.refresh(b1)
        session.refresh(b2)
        crit = Criterion(
            project_key=project_key, kind="zuschlag", name="Preis", weight_pct=30,
            scale_max=10, auto_price=True,
        )
        session.add(crit)
        session.add(PriceItem(bidder_id=b1.id, category="einmalig", leistungsbeschreibung="A", anzahl=1, kosten_pro_einheit=100))
        session.commit()
        session.refresh(crit)
        b1_id, b2_id, crit_id = b1.id, b2.id, crit.id

    import src.m15_evaluation as ev
    import src.m03_db as db

    old_engine = db.engine
    db.engine = engine
    ev.engine = engine
    ev.get_session = lambda: Session(engine)

    from src.m15_evaluation import get_score, sync_price_criterion_scores, price_offers_status

    status = price_offers_status(project_key)
    assert status["ready"] is False
    result = sync_price_criterion_scores(project_key)
    assert result["synced"] is False

    with Session(engine) as session:
        session.add(PriceItem(bidder_id=b2_id, category="einmalig", leistungsbeschreibung="B", anzahl=1, kosten_pro_einheit=200))
        session.commit()

    result = sync_price_criterion_scores(project_key)
    assert result["synced"] is True
    cheap_score = get_score(b1_id, crit_id, "system")
    dear_score = get_score(b2_id, crit_id, "system")
    assert cheap_score and cheap_score.value == 10.0
    assert dear_score and dear_score.value == 5.0

    db.engine = old_engine
    ev.engine = old_engine


def test_build_evaluation_export_includes_justifications():
    engine, evaluator_id = _setup_db()
    project_key = "p-export"
    with Session(engine) as session:
        b = Bidder(project_key=project_key, name="Bieter A")
        session.add(b)
        session.commit()
        session.refresh(b)
        crit = Criterion(project_key=project_key, kind="zuschlag", name="Qualität", scale_max=10, weight_pct=50)
        session.add(crit)
        session.commit()
        session.refresh(crit)
        session.add(
            Score(
                bidder_id=b.id,
                criterion_id=crit.id,
                source_key=f"user:{evaluator_id}",
                evaluator_user_id=evaluator_id,
                value=7.0,
                justification="Referenz unvollständig",
            )
        )
        session.commit()

    import src.m15_evaluation as ev
    import src.m03_db as db
    import src.m14_auth as auth14

    old_engine = db.engine
    old_auth_engine = auth14.engine
    db.engine = engine
    ev.engine = engine
    auth14.engine = engine
    ev.get_session = lambda: Session(engine)

    from src.m15_evaluation import build_evaluation_export_sheets

    sheets = build_evaluation_export_sheets(project_key, project_title="Test", may_see_evaluators=True)
    headers, rows = sheets["Bewertungen"]
    assert "KI-Begründung" in headers
    assert any("Begründung:" in h for h in headers)
    assert rows and "Referenz unvollständig" in rows[0]

    db.engine = old_engine
    ev.engine = old_engine
    auth14.engine = old_auth_engine


def test_compute_price_reciprocal():
    from src.m15_evaluation import compute_price_criterion_value

    assert compute_price_criterion_value(10, 120_000, 120_000) == 10.0
    assert compute_price_criterion_value(10, 120_000, 150_000) == 8.0
    assert compute_price_criterion_value(10, 120_000, 200_000) == 6.0


if __name__ == "__main__":
    test_create_bidder_and_criterion()
    test_ranking_ko_and_weighted_sum()
    test_compute_rankings_phase2_interim()
    test_chunk_meta_prefix_subtype()
    test_validate_evaluation_cloud_gate()
    test_suggest_score_sanitizes_cloud_context()
    test_suggest_score_skips_sanitize_for_local_provider()
    test_tender_doc_link_and_roles()
    test_import_criteria_payload_skip_existing()
    test_seed_and_merge_price_structure()
    test_validate_tender_cloud_gate()
    test_suggest_tender_role_and_validate_criteria()
    test_evaluation_config_roundtrip()
    test_criteria_preview_meta()
    test_ki_busy_hint()
    test_score_justification_required()
    test_sync_price_criterion_scores_reciprocal_gate()
    test_build_evaluation_export_includes_justifications()
    test_compute_price_reciprocal()
    print("OK")
