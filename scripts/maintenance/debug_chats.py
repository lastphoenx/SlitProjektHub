#!/usr/bin/env python3
from src.m03_db import get_session, ChatMessage
from sqlmodel import select

with get_session() as ses:
    # Alle ChatMessages für openai
    stmt = select(ChatMessage).where(ChatMessage.provider == "openai").order_by(ChatMessage.timestamp.desc())
    all_openai = ses.exec(stmt).all()
    
    print(f"Gesamte OpenAI Nachrichten: {len(all_openai)}\n")
    
    if all_openai:
        print("ALLE OpenAI Nachrichten (letzte 10):")
        for msg in all_openai[:10]:
            print(f"  ID: {msg.id}")
            print(f"  Provider: {msg.provider}")
            print(f"  Session: {msg.session_id}")
            print(f"  Project: {msg.project_key}")
            print(f"  Role: {msg.role}")
            print(f"  Timestamp: {msg.timestamp}")
            print(f"  Content: {msg.content[:50]}...")
            print(f"  is_deleted: {msg.is_deleted}")
            print()
    
    # Nach projekt_key filtern
    print("\n" + "="*80)
    print("Nach Projekt gefiltert:")
    stmt2 = select(ChatMessage).where(
        ChatMessage.provider == "openai",
        ChatMessage.project_key == "entwicklung-einer-modularen-verwaltungssoftware-f-r-service-weiterbildung"
    ).order_by(ChatMessage.timestamp.desc())
    project_msgs = ses.exec(stmt2).all()
    
    print(f"Nachrichten für das Projekt: {len(project_msgs)}\n")
    if project_msgs:
        for msg in project_msgs[:5]:
            print(f"  Session: {msg.session_id} | Role: {msg.role} | is_deleted: {msg.is_deleted}")
    
    # Alle unterschiedlichen Sessions für openai
    print("\n" + "="*80)
    print("Alle verschiedenen Sessions für openai:")
    sessions_stmt = select(ChatMessage.session_id).where(
        ChatMessage.provider == "openai"
    ).distinct()
    sessions = ses.exec(sessions_stmt).all()
    print(f"Gefundene Session-IDs: {len(sessions)}")
    for sess_id in sessions:
        print(f"  {sess_id}")
