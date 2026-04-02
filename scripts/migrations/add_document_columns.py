"""
Migration: Add chunk_size_used and linked_role_keys columns to document table
"""
import sqlite3
from pathlib import Path

# Pfad zur Datenbank
db_path = Path(__file__).parent.parent.parent / "data" / "db" / "slitproj.db"

def run_migration():
    """Führt die Migration durch"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Prüfen ob Spalten bereits existieren
        cursor.execute("PRAGMA table_info(document)")
        columns = [col[1] for col in cursor.fetchall()]
        
        # chunk_size_used hinzufügen
        if 'chunk_size_used' not in columns:
            print("Adding column: chunk_size_used")
            cursor.execute("ALTER TABLE document ADD COLUMN chunk_size_used INTEGER")
            print("✅ chunk_size_used added")
        else:
            print("⏭️  chunk_size_used already exists")
        
        # linked_role_keys hinzufügen
        if 'linked_role_keys' not in columns:
            print("Adding column: linked_role_keys")
            cursor.execute("ALTER TABLE document ADD COLUMN linked_role_keys TEXT")
            print("✅ linked_role_keys added")
        else:
            print("⏭️  linked_role_keys already exists")
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    run_migration()
