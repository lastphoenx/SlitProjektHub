import sqlite3
import json
from pathlib import Path

db_path = Path('data/db/slitproj.db')

if db_path.exists():
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    print("=" * 80)
    print("RAG EMBEDDINGS - DETAIL VIEW")
    print("=" * 80)

    print("\n### ROLLE: leiter-cash ###")
    cursor.execute("SELECT key, title, embedding FROM role WHERE key='leiter-cash'")
    row = cursor.fetchone()
    if row:
        key, title, embedding = row
        if embedding:
            emb = json.loads(embedding)
            print(f"Key: {key}")
            print(f"Title: {title}")
            print(f"Dimensions: {len(emb)}")
            print(f"First 10 values: {emb[:10]}")
            print(f"Min: {min(emb):.6f}, Max: {max(emb):.6f}, Mean: {sum(emb)/len(emb):.6f}")

    print("\n### KONTEXT: entwicklung-einer-verwaltungssoftware-... ###")
    cursor.execute("SELECT key, title, embedding FROM context LIMIT 1")
    row = cursor.fetchone()
    if row:
        key, title, embedding = row
        if embedding:
            emb = json.loads(embedding)
            print(f"Key: {key}")
            print(f"Title: {title}")
            print(f"Dimensions: {len(emb)}")
            print(f"First 10 values: {emb[:10]}")
            print(f"Min: {min(emb):.6f}, Max: {max(emb):.6f}, Mean: {sum(emb)/len(emb):.6f}")

    print("\n### PROJEKT: entwicklung-einer-modularen-verwaltungssoftware-... ###")
    cursor.execute("SELECT key, title, embedding FROM project LIMIT 1")
    row = cursor.fetchone()
    if row:
        key, title, embedding = row
        if embedding:
            emb = json.loads(embedding)
            print(f"Key: {key}")
            print(f"Title: {title}")
            print(f"Dimensions: {len(emb)}")
            print(f"First 10 values: {emb[:10]}")
            print(f"Min: {min(emb):.6f}, Max: {max(emb):.6f}, Mean: {sum(emb)/len(emb):.6f}")

    print("\n" + "=" * 80)
    print("Full embedding example (first 50 dims):")
    print("=" * 80)
    cursor.execute("SELECT embedding FROM role WHERE key='leiter-cash'")
    row = cursor.fetchone()
    if row and row[0]:
        emb = json.loads(row[0])
        print(f"[{', '.join(f'{v:.4f}' for v in emb[:50])}]")

    conn.close()
