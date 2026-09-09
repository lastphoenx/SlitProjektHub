#!/usr/bin/env python
"""Tests Ollama-Modell-Konstanten (qwen3.8:27b Migration)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.m08_llm import (
    DEFAULT_MODELS,
    OLLAMA_DEFAULT_MODEL,
    _ollama_chat_extra_body,
    normalize_ollama_model,
)
from src.m16_idea_visual import VISUAL_TEXT_DEFAULT_MODELS


def test_ollama_default_model():
    assert OLLAMA_DEFAULT_MODEL == "qwen3.8:27b"
    assert DEFAULT_MODELS["ollama"] == OLLAMA_DEFAULT_MODEL
    assert VISUAL_TEXT_DEFAULT_MODELS["ollama"] == OLLAMA_DEFAULT_MODEL


def test_normalize_legacy_ollama_models():
    assert normalize_ollama_model("qwen3:32b") == OLLAMA_DEFAULT_MODEL
    assert normalize_ollama_model("qwen3.6:27b") == OLLAMA_DEFAULT_MODEL
    assert normalize_ollama_model("qwen3.8:27b") == OLLAMA_DEFAULT_MODEL


def test_ollama_think_disabled_for_qwen38():
    extra = _ollama_chat_extra_body("qwen3.8:27b")
    assert extra == {"think": False}


def test_strip_llm_reasoning_wrappers():
    from src.m08_llm import strip_llm_reasoning_wrappers

    raw = '<think>plan</think>\n{"value": 7}'
    assert strip_llm_reasoning_wrappers(raw) == '{"value": 7}'


if __name__ == "__main__":
    test_ollama_default_model()
    test_normalize_legacy_ollama_models()
    test_ollama_think_disabled_for_qwen38()
    test_strip_llm_reasoning_wrappers()
    print("OK")
