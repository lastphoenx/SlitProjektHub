#!/usr/bin/env python
"""Test: Decision-Creation und RAG-Suche"""

from src.m10_chat import save_message
from src.m09_rag import retrieve_relevant_chunks, build_rag_context_from_search
from src.m03_db import get_session, Decision
from sqlmodel import select
import json

print("=" * 80)
print("TEST: Decision Auto-Creation und RAG-Suche")
print("=" * 80)

# 1. Simuliere einen Chat mit Entscheidung
project_key = "entwicklung-einer-modularen-verwaltungssoftware-f-r-service-weiterbildung"
session_id = "test-session-123"

print("\n[1] Speichere Chat-Messages...")

# User sagt etwas
save_message(
    provider="openai",
    session_id=session_id,
    role="user",
    content="Welche Authentifizierungslösung sollten wir nehmen?",
    project_key=project_key,
    message_type="idea",
    message_status="ungeprüft"
)
print("  - User-Message (idea) gespeichert")

# KI antwortet
save_message(
    provider="openai",
    session_id=session_id,
    role="assistant",
    content="OAuth2 bietet gute Sicherheit und Standards-Compliance.",
    project_key=project_key,
    message_type="info",
    message_status="ungeprüft"
)
print("  - KI-Message (info) gespeichert")

# User markiert als Decision + bestätigt
save_message(
    provider="openai",
    session_id=session_id,
    role="user",
    content="Wir nehmen OAuth2 für die Authentifizierung. Das bietet gute Sicherheit und ist Standard im Enterprise-Bereich.",
    project_key=project_key,
    message_type="decision",
    message_status="bestätigt"
)
print("  - User-Message (decision, bestätigt) gespeichert")
print("    -> AUTO-DECISION sollte erstellt worden sein!")

# 2. Prüfe ob Decision in der DB ist
print("\n[2] Prüfe Decisions in der DB...")
with get_session() as ses:
    decisions = ses.exec(select(Decision).where(Decision.project_key == project_key)).all()
    print(f"  - Decisions gefunden: {len(decisions)}")
    for dec in decisions:
        has_emb = "JA" if dec.embedding else "NEIN"
        print(f"    {dec.id}: {dec.title} (embedding: {has_emb})")

# 3. Teste RAG-Suche nach Decisions
print("\n[3] Teste RAG-Suche nach 'Authentifizierung'...")
results = retrieve_relevant_chunks("Authentifizierung OAuth2", limit=5, threshold=0.3)

if results.get("decisions"):
    print(f"  - Decisions gefunden: {len(results['decisions'])}")
    for dec in results["decisions"]:
        print(f"    {dec['title']} (Relevanz: {dec['similarity']})")
else:
    print("  - KEINE Decisions gefunden (möglicherweise threshold zu hoch oder embedding-Fehler)")

# 4. Teste build_rag_context_from_search
print("\n[4] Teste RAG-Context-Formatierung...")
context_text = build_rag_context_from_search(results)
if context_text:
    print("  - Generierter Kontext:")
    for line in context_text.split("\n"):
        print(f"    {line}")
else:
    print("  - Kein Kontext generiert")

print("\n" + "=" * 80)
print("TEST COMPLETE")
print("=" * 80)
