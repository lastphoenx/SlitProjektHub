#!/usr/bin/env python
"""Tests für Ollama-Belegung, Job-Meldungen und KI-Reset der Einschätzung."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m08_llm import ollama_runtime_status
from src.m16_idea import ProjectIdea, ai_defaults_from_idea, effective_assessment
from src.m16_idea_jobs import _friendly_err, job_public, parse_job


def test_ollama_runtime_other_model_loaded():
    with patch("src.m08_llm._ollama_root_url", return_value="http://127.0.0.1:11434"), patch(
        "src.m08_llm._fetch_ollama_ps",
        return_value=[{"name": "qwen2.5vl:7b"}],
    ):
        st = ollama_runtime_status("qwen3:32b")
    assert st["ok"] is True
    assert st["switching"] is True
    assert "qwen2.5vl:7b" in st["other_loaded"]
    assert "qwen2.5vl" in st["message"]
    assert "qwen3:32b" in st["message"]
    assert "Warteschlange" in st["message"]


def test_ollama_runtime_same_model_loaded():
    with patch("src.m08_llm._ollama_root_url", return_value="http://127.0.0.1:11434"), patch(
        "src.m08_llm._fetch_ollama_ps",
        return_value=[{"name": "qwen3:32b"}],
    ):
        st = ollama_runtime_status("qwen3:32b")
    assert st["switching"] is False
    assert st["other_loaded"] == []
    assert "bereits geladen" in st["message"]


def test_ollama_runtime_free():
    with patch("src.m08_llm._ollama_root_url", return_value="http://127.0.0.1:11434"), patch(
        "src.m08_llm._fetch_ollama_ps", return_value=[]
    ):
        st = ollama_runtime_status("qwen3:32b")
    assert st["switching"] is False
    assert "frei" in st["message"]


def test_job_public_and_parse():
    assert parse_job(None) is None
    assert parse_job("{") is None
    job = parse_job('{"status":"queued","message":"wartet","kind":"assess"}')
    pub = job_public(job)
    assert pub["status"] == "queued"
    assert pub["kind"] == "assess"
    assert job_public(None)["status"] == "idle"


def test_friendly_timeout():
    msg = _friendly_err(TimeoutError("timed out"))
    assert "nicht rechtzeitig" in msg


def test_reset_payload_matches_ai_defaults():
    idea = ProjectIdea(
        id=9,
        idea_text="x",
        ai_summary="KI-Text",
        ai_internal_pt=40,
        ai_internal_pt_reasoning="Schätzung",
        ai_external_cost=12000,
        ai_external_cost_reasoning="Extern",
        ai_challenges_json='[{"title":"Risiko","description":"d","severity":"hoch","likelihood":"mittel"}]',
        ai_phases_json='[{"name":"Analyse","description":"Ist","duration_estimate":"4 Wochen","internal_pt":10}]',
        ai_recommendation="Go",
        ai_assessed_at=datetime.now(timezone.utc),
        user_summary="User-Text",
        user_internal_pt=10,
        user_assessed_at=datetime.now(timezone.utc),
    )
    d = ai_defaults_from_idea(idea)
    assert d["summary"] == "KI-Text"
    assert d["internal_pt"] == 40
    assert d["challenges"][0]["title"] == "Risiko"
    assert d["phases"][0]["name"] == "Analyse"
    before = effective_assessment(idea)
    assert before["summary"] == "User-Text"
    idea.user_summary = d["summary"]
    idea.user_internal_pt = d["internal_pt"]
    after = effective_assessment(idea)
    assert after["summary"] == "KI-Text"
    assert after["internal_pt"] == 40


def test_resolve_sqlite_url_ignores_cwd():
    from src.m01_config import resolve_sqlite_url

    base = Path("/opt/slitprojekthub")
    got = resolve_sqlite_url("sqlite:///data/db/slitproj.db", base)
    assert got.replace("\\", "/").endswith("data/db/slitproj.db")
    assert got.startswith("sqlite:///")
    assert resolve_sqlite_url("sqlite:///:memory:", base) == "sqlite:///:memory:"


if __name__ == "__main__":
    test_ollama_runtime_other_model_loaded()
    test_ollama_runtime_same_model_loaded()
    test_ollama_runtime_free()
    test_job_public_and_parse()
    test_friendly_timeout()
    test_reset_payload_matches_ai_defaults()
    test_resolve_sqlite_url_ignores_cwd()
    print("ok")
