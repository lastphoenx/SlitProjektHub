"""Migration: Fügt rag_sources Spalte zu chat_message Tabelle hinzu"""
from src.m03_db import get_session, engine
from sqlalchemy import text

def migrate():
    with get_session() as ses:
        try:
            # Prüfe ob Spalte schon existiert
            result = ses.exec(text("PRAGMA table_info(chat_message)")).all()
            columns = [row[1] for row in result]
            
            if 'rag_sources' in columns:
                print("✅ Spalte rag_sources existiert bereits")
                return
            
            # Füge Spalte hinzu
            ses.exec(text("ALTER TABLE chat_message ADD COLUMN rag_sources TEXT"))
            ses.commit()
            print("✅ Spalte rag_sources erfolgreich hinzugefügt")
        except Exception as e:
            print(f"❌ Fehler: {e}")
            ses.rollback()

if __name__ == "__main__":
    migrate()
