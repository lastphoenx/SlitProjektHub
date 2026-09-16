from src.m03_db import get_session, Role, Context, Project
from sqlmodel import select
import json

with get_session() as ses:
    print("=== ROLE CHECK ===")
    role = ses.exec(select(Role).where(Role.key == "cash")).first()
    if role:
        has_embedding = bool(role.embedding)
        embedding_len = len(json.loads(role.embedding)) if role.embedding else 0
        print(f"✓ Role 'cash': embedding={has_embedding}, dimensions={embedding_len}")
    else:
        print("✗ Role 'cash' not found")
    
    print("\n=== CONTEXTS CHECK ===")
    contexts = ses.exec(select(Context)).all()
    for ctx in contexts:
        has_embedding = bool(ctx.embedding)
        embedding_len = len(json.loads(ctx.embedding)) if ctx.embedding else 0
        print(f"  {ctx.key}: embedding={has_embedding}, dims={embedding_len}")
    
    print("\n=== PROJECTS CHECK ===")
    projects = ses.exec(select(Project)).all()
    for proj in projects:
        has_embedding = bool(proj.embedding)
        embedding_len = len(json.loads(proj.embedding)) if proj.embedding else 0
        print(f"  {proj.key}: embedding={has_embedding}, dims={embedding_len}")
