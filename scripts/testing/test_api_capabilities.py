#!/usr/bin/env python3
"""
Test-Skript: Prüft welche Modelle & Features mit deinen API-Keys verfügbar sind.
Führe aus: python test_api_capabilities.py
"""

import os
import sys

def test_anthropic():
    """Testet Claude API - verfügbare Modelle und Extended Thinking."""
    print("\n" + "="*60)
    print("CLAUDE (Anthropic)")
    print("="*60)
    
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY nicht gesetzt")
        return
    
    try:
        import anthropic
        client = anthropic.Anthropic()
        
        models_to_test = [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ]
        
        for model in models_to_test:
            try:
                # Basic test
                msg = client.messages.create(
                    model=model,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=10,
                )
                print(f"✅ {model}")
                
                # Test Extended Thinking
                try:
                    msg_thinking = client.messages.create(
                        model=model,
                        messages=[{"role": "user", "content": "test"}],
                        max_tokens=100,
                        thinking={
                            "type": "enabled",
                            "budget_tokens": 1000,
                        }
                    )
                    print(f"   ✅ Extended Thinking (budget_tokens=1000)")
                except Exception as e:
                    print(f"   ❌ Extended Thinking: {str(e)[:60]}")
                    
            except Exception as e:
                print(f"❌ {model}: {str(e)[:60]}")
                
    except Exception as e:
        print(f"❌ Import fehler: {e}")


def test_openai():
    """Testet OpenAI API - verfügbare Modelle und Extended Thinking."""
    print("\n" + "="*60)
    print("OPENAI")
    print("="*60)
    
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY nicht gesetzt")
        return
    
    try:
        from openai import OpenAI
        client = OpenAI()
        
        models_to_test = [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
            "o1",
            "o3",
        ]
        
        for model in models_to_test:
            try:
                msg = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=10,
                )
                print(f"✅ {model}")
                
                # o1/o3 haben automatisch thinking
                if model in ["o1", "o3"]:
                    print(f"   ℹ️  {model} hat integriertes Extended Thinking")
                    
            except Exception as e:
                error_msg = str(e)[:80]
                if "not found" in error_msg.lower() or "not found" in error_msg:
                    print(f"❌ {model}: nicht verfügbar mit deinem Key")
                else:
                    print(f"❌ {model}: {error_msg}")
                    
    except Exception as e:
        print(f"❌ Import fehler: {e}")


def test_mistral():
    """Testet Mistral API - verfügbare Modelle."""
    print("\n" + "="*60)
    print("MISTRAL")
    print("="*60)
    
    if not os.getenv("MISTRAL_API_KEY"):
        print("❌ MISTRAL_API_KEY nicht gesetzt")
        return
    
    try:
        from mistralai import Mistral
        client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
        
        models_to_test = [
            "mistral-large-latest",
            "mistral-small-latest",
            "mistral-nemo-latest",
        ]
        
        for model in models_to_test:
            try:
                msg = client.chat.complete(
                    model=model,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=10,
                )
                print(f"✅ {model}")
                print(f"   ℹ️  Extended Thinking: nicht nativ unterstützt")
                
            except Exception as e:
                error_msg = str(e)[:80]
                print(f"❌ {model}: {error_msg}")
                
    except Exception as e:
        print(f"❌ Import fehler: {e}")


def summary():
    """Zeigt Zusammenfassung der Erkenntnisse."""
    print("\n" + "="*60)
    print("ZUSAMMENFASSUNG")
    print("="*60)
    print("""
Nächste Schritte basierend auf den Ergebnissen:

1. Für m08_llm.py erweitern um:
   - model_name Parameter (statt hardcoded)
   - thinking Parameter (für Claude budget_tokens, für o1/o3 automatisch)
   - temperature Parameter (schon da)

2. ChatMessage DB-Schema um Feld 'model_name' erweitern (optional)

3. UI-Formulare: Dropdown für verfügbare Modelle pro Provider

Siehe Dokumentation:
  - Claude Extended Thinking: https://platform.claude.com/docs/en/build-with-claude/extended-thinking
  - OpenAI o1/o3: https://platform.openai.com/docs/guides/reasoning
""")


if __name__ == "__main__":
    print("\n🔍 API-Capabilities Test\n")
    print("Teste welche Modelle & Features verfügbar sind...")
    
    test_anthropic()
    test_openai()
    test_mistral()
    
    summary()
