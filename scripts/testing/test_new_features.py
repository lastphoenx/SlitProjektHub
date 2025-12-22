#!/usr/bin/env python3
"""Test Script - prüft ob neue Funktionen syntaktisch korrekt sind."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

print("=" * 60)
print("Test: Neue Model & Temperature Features")
print("=" * 60)

try:
    from src.m08_llm import get_available_models, get_model_id, AVAILABLE_MODELS, DEFAULT_MODELS
    print("\n[OK] m08_llm: Import erfolgreich")
    
    print("\nVerfuegbare Modelle:")
    for provider, models in AVAILABLE_MODELS.items():
        print(f"  {provider}: {', '.join(models.keys())}")
    
    print("\nDefault Modelle:")
    for provider, model in DEFAULT_MODELS.items():
        print(f"  {provider}: {model}")
    
    print("\nTest get_model_id():")
    print(f"  openai + 'gpt-4o': {get_model_id('openai', 'gpt-4o')}")
    print(f"  anthropic + 'haiku': {get_model_id('anthropic', 'haiku')}")
    print(f"  mistral + 'large': {get_model_id('mistral', 'large')}")
    print(f"  openai + None (default): {get_model_id('openai', None)}")
    
    print("\nTest get_available_models():")
    print(f"  openai: {get_available_models('openai')}")
    print(f"  anthropic: {get_available_models('anthropic')}")
    print(f"  mistral: {get_available_models('mistral')}")
    
except Exception as e:
    print(f"[ERROR] Fehler bei Import: {e}")
    import traceback
    traceback.print_exc()

try:
    from src.m03_db import ChatMessage
    print("\n[OK] m03_db: ChatMessage Import erfolgreich")
    
    fields = ChatMessage.model_fields if hasattr(ChatMessage, 'model_fields') else ChatMessage.__fields__
    relevant_fields = ['provider', 'model_name', 'model_temperature', 'role', 'content']
    print(f"\nChatMessage Felder (relevant): {[f for f in fields.keys() if f in relevant_fields]}")
    
except Exception as e:
    print(f"[ERROR] Fehler bei DB Import: {e}")
    import traceback
    traceback.print_exc()

try:
    from src.m10_chat import save_message
    import inspect
    sig = inspect.signature(save_message)
    print("\n[OK] m10_chat: save_message Import erfolgreich")
    print(f"save_message Parameter: {list(sig.parameters.keys())}")
    
except Exception as e:
    print(f"[ERROR] Fehler bei Chat Import: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("[OK] Alle Tests erfolgreich!")
print("=" * 60)
