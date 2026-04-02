import os
import anthropic

# Test Anthropic API
try:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    print(f"Key gefunden: {len(key) > 0}")
    print(f"Key Länge: {len(key)}")
    print(f"Key Prefix: {key[:7]}..." if len(key) > 7 else "Zu kurz")
    
    client = anthropic.Anthropic(api_key=key)
    print("\nClient erstellt. Teste API-Call...")
    
    msg = client.messages.create(
        model="claude-3-5-haiku-20241022",
        messages=[{"role": "user", "content": "ok"}],
        max_tokens=5,
    )
    print("✓ Erfolg!")
    print(f"Antwort: {msg.content}")
    
except Exception as e:
    print(f"\n✗ Fehler: {type(e).__name__}")
    print(f"Details: {str(e)}")
    import traceback
    traceback.print_exc()
