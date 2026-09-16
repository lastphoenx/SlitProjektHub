"""
Migration: Add task generation metadata fields to Task table
"""
import sqlite3
from pathlib import Path

# Database path
db_path = Path(__file__).parent.parent / "data" / "db" / "slitproj.db"

def migrate():
    """Add task generation metadata columns to task table"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(task)")
        columns = [col[1] for col in cursor.fetchall()]
        
        new_columns = {
            "source_role_key": "TEXT",
            "source_responsibility": "TEXT",
            "generation_batch_id": "TEXT",
            "generated_at": "DATETIME"
        }
        
        for col_name, col_type in new_columns.items():
            if col_name not in columns:
                print(f"Adding column: {col_name} ({col_type})")
                cursor.execute(f"ALTER TABLE task ADD COLUMN {col_name} {col_type}")
                conn.commit()
            else:
                print(f"Column already exists: {col_name}")
        
        print("\n✅ Migration completed successfully")
        
        # Show updated schema
        cursor.execute("PRAGMA table_info(task)")
        print("\nUpdated Task table schema:")
        for col in cursor.fetchall():
            print(f"  {col[1]}: {col[2]}")
        
    except Exception as e:
        print(f"❌ Migration failed: {str(e)}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
