import sqlite3
import json
from pathlib import Path

db_path = Path('data/db/slitproj.db')

if db_path.exists():
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    print("=== ROLE 'cash' ===")
    cursor.execute("SELECT key, title, embedding FROM role WHERE key='cash'")
    row = cursor.fetchone()
    if row:
        key, title, embedding = row
        has_emb = embedding is not None and embedding != ''
        if has_emb:
            emb_list = json.loads(embedding)
            print(f"[OK] {key}: HAS embedding ({len(emb_list)} dimensions)")
        else:
            print(f"[NO] {key}: NO embedding")
    else:
        print("[NO] Role 'cash' not found")

    print("\n=== ALL ROLES ===")
    cursor.execute("SELECT key, title, embedding FROM role")
    for row in cursor.fetchall():
        key, title, embedding = row
        has_emb = embedding is not None and embedding != ''
        status = "[OK]" if has_emb else "[NO]"
        dims = len(json.loads(embedding)) if has_emb else 0
        print(f"  {status} {key}: {dims} dims")

    print("\n=== CONTEXTS ===")
    cursor.execute("SELECT key, title, embedding FROM context")
    for row in cursor.fetchall():
        key, title, embedding = row
        has_emb = embedding is not None and embedding != ''
        status = "[OK]" if has_emb else "[NO]"
        dims = len(json.loads(embedding)) if has_emb else 0
        print(f"  {status} {key}: {dims} dims")

    print("\n=== PROJECTS ===")
    cursor.execute("SELECT key, title, embedding FROM project")
    for row in cursor.fetchall():
        key, title, embedding = row
        has_emb = embedding is not None and embedding != ''
        status = "[OK]" if has_emb else "[NO]"
        dims = len(json.loads(embedding)) if has_emb else 0
        print(f"  {status} {key}: {dims} dims")

    conn.close()
else:
    print("Database file not found!")
