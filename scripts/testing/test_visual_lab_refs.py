#!/usr/bin/env python
"""Tests Visual-Lab Referenz-Uploads."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m08_llm import model_supports_vision
from src.m17_visual_lab_refs import (
    LabReferenceBundle,
    build_prompt_with_references,
    merge_bundles,
    process_upload_bytes,
)


def test_model_supports_vision_ollama_vl():
    assert model_supports_vision("ollama", "qwen2.5vl:7b")
    assert not model_supports_vision("ollama", "qwen3:32b")


def test_text_file_in_bundle(tmp_path=None):
    store = Path(__file__).resolve().parents[2] / "data" / "visual_lab_test_att"
    store.mkdir(parents=True, exist_ok=True)
    data = "Phase 1: Init\nPhase 2: Plan".encode("utf-8")
    err, bundle = process_upload_bytes("plan.txt", data, store)
    assert err is None
    assert bundle and bundle.text_blocks
    assert "Init" in bundle.merged_text()


def test_build_prompt_with_reference_text():
    b = LabReferenceBundle(text_blocks=["[PDF test.pdf]\nInhalt Änderungen"])
    out = build_prompt_with_references("Mein Prompt", b)
    assert "Mein Prompt" in out
    assert "Referenzmaterial" in out
    assert "Änderungen" in out


if __name__ == "__main__":
    test_model_supports_vision_ollama_vl()
    test_text_file_in_bundle()
    test_build_prompt_with_reference_text()
    print("ok")
