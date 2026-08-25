#!/usr/bin/env python
# Check Batch-QA answers in database
from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.m03_db import get_session, DocumentChunk
from sqlmodel import select

def main():
    with get_session() as session:
        # Get chunks from document 2 (CSV)
        chunks = session.exec(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == 2)
            .order_by(DocumentChunk.chunk_index)
            .limit(15)
        ).all()
        
        print(f"=== Erste 15 CSV-Chunks (von {len(chunks)} geladen) ===\n")
        
        for chunk in chunks:
            try:
                data = json.loads(chunk.chunk_text)
                nr = data.get('Nr', '?')
                lieferant = data.get('Lieferant', '?')
                frage = data.get('Frage', '')[:60]
                antwort = data.get('Antwort', '')
                
                has_answer = len(antwort.strip()) > 0
                answer_preview = antwort[:100] + '...' if len(antwort) > 100 else antwort
                
                status = "✅" if has_answer else "⏳"
                print(f"{status} Nr. {nr} | {lieferant}")
                print(f"   Frage: {frage}...")
                if has_answer:
                    print(f"   Antwort: {answer_preview}")
                print()
                
            except json.JSONDecodeError:
                print(f"❌ Chunk {chunk.chunk_index}: Kein gültiges JSON")
                print()

if __name__ == "__main__":
    main()
