"""
Migration Script: Role Schema Optimization
==========================================

Changes:
1. KORREKTUR: short_code ← group_name (Kürzel war im falschen Feld)
2. ENTFERNEN: group_name, rag_path, short_title (nicht genutzt)
3. UMBENENNEN: text_path → markdown_content_path (klarere Benennung)
4. HINZUFÜGEN: attached_docs, created_at, updated_at, rag_indexed_at, rag_chunk_count

WICHTIG: Erstellt Backup vor Migration!
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
import shutil

# Konfiguration
BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "db" / "slitproj.db"
BACKUP_DIR = BASE_DIR / "backups" / "migrations"

def create_backup():
    """Erstelle Backup der Datenbank"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"slitproj_before_migration_{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    print(f"✅ Backup erstellt: {backup_path}")
    return backup_path

def migrate_role_schema(conn: sqlite3.Connection):
    """Migriere Role-Schema"""
    cursor = conn.cursor()
    
    print("\n" + "="*80)
    print("MIGRATION: Role Schema Optimization")
    print("="*80)
    
    # 1. Prüfe aktuelle Struktur
    cursor.execute("PRAGMA table_info(role)")
    existing_columns = {col[1] for col in cursor.fetchall()}
    print(f"\n📋 Vorhandene Spalten: {existing_columns}")
    
    # 2. Erstelle neue Tabelle mit optimiertem Schema
    print("\n🔄 Erstelle neue Tabelle 'role_new'...")
    cursor.execute("""
        CREATE TABLE role_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key VARCHAR NOT NULL,
            title VARCHAR NOT NULL,
            short_code VARCHAR,
            description VARCHAR,
            responsibilities TEXT,
            qualifications TEXT,
            expertise TEXT,
            markdown_content_path VARCHAR NOT NULL,
            attached_docs TEXT,
            rag_indexed_at TIMESTAMP,
            rag_chunk_count INTEGER,
            rag_status VARCHAR,
            is_deleted BOOLEAN DEFAULT 0,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
    """)
    
    # 3. Migriere Daten mit Korrektur
    print("\n📦 Migriere Daten...")
    
    # Korrigiere: short_code ← group_name (wo short_code NULL ist)
    cursor.execute("""
        INSERT INTO role_new (
            id, key, title, short_code, description, 
            responsibilities, qualifications, expertise,
            markdown_content_path, attached_docs,
            rag_indexed_at, rag_chunk_count, rag_status,
            is_deleted, created_at, updated_at
        )
        SELECT 
            id,
            key,
            title,
            CASE 
                WHEN short_code IS NOT NULL THEN short_code
                WHEN group_name IS NOT NULL THEN group_name
                ELSE NULL
            END as short_code,
            description,
            responsibilities,
            qualifications,
            expertise,
            text_path as markdown_content_path,
            NULL as attached_docs,
            NULL as rag_indexed_at,
            NULL as rag_chunk_count,
            NULL as rag_status,
            is_deleted,
            CURRENT_TIMESTAMP as created_at,
            CURRENT_TIMESTAMP as updated_at
        FROM role
    """)
    
    rows_migrated = cursor.rowcount
    print(f"   ✅ {rows_migrated} Datensätze migriert")
    
    # 4. Zeige Korrektur-Statistik
    cursor.execute("""
        SELECT COUNT(*) 
        FROM role 
        WHERE short_code IS NULL AND group_name IS NOT NULL
    """)
    corrected = cursor.fetchone()[0]
    print(f"   ✅ {corrected} Datensätze korrigiert (short_code ← group_name)")
    
    # 5. Ersetze alte Tabelle
    print("\n🔄 Ersetze alte Tabelle...")
    cursor.execute("DROP TABLE role")
    cursor.execute("ALTER TABLE role_new RENAME TO role")
    
    # 6. Erstelle Indizes
    print("\n📊 Erstelle Indizes...")
    cursor.execute("CREATE INDEX idx_role_key ON role(key)")
    cursor.execute("CREATE INDEX idx_role_title ON role(title)")
    cursor.execute("CREATE INDEX idx_role_is_deleted ON role(is_deleted)")
    
    conn.commit()
    print("\n✅ Migration abgeschlossen!")

def verify_migration(conn: sqlite3.Connection):
    """Verifiziere Migration"""
    cursor = conn.cursor()
    
    print("\n" + "="*80)
    print("VERIFIKATION")
    print("="*80)
    
    # Prüfe neue Struktur
    cursor.execute("PRAGMA table_info(role)")
    new_columns = cursor.fetchall()
    
    print("\n📋 Neue Spalten:")
    for col in new_columns:
        print(f"   {col[1]:<25} {col[2]:<15}")
    
    # Prüfe Daten
    cursor.execute("SELECT COUNT(*) FROM role")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM role WHERE short_code IS NOT NULL")
    with_short_code = cursor.fetchone()[0]
    
    print(f"\n📊 Statistik:")
    print(f"   Gesamt: {total} Rollen")
    print(f"   Mit Kürzel: {with_short_code} Rollen")
    print(f"   Ohne Kürzel: {total - with_short_code} Rollen")
    
    # Zeige Beispiel-Datensatz
    cursor.execute("""
        SELECT key, title, short_code, markdown_content_path, created_at
        FROM role 
        WHERE short_code IS NOT NULL
        LIMIT 1
    """)
    example = cursor.fetchone()
    
    if example:
        print(f"\n📄 Beispiel-Datensatz:")
        print(f"   Key: {example[0]}")
        print(f"   Title: {example[1]}")
        print(f"   Short Code: {example[2]}")
        print(f"   Content Path: {example[3]}")
        print(f"   Created At: {example[4]}")

def main():
    """Hauptfunktion"""
    print("\n" + "="*80)
    print("ROLE SCHEMA MIGRATION")
    print("="*80)
    
    if not DB_PATH.exists():
        print(f"\n❌ Datenbank nicht gefunden: {DB_PATH}")
        return
    
    # Backup erstellen
    backup_path = create_backup()
    
    try:
        # Migration durchführen
        conn = sqlite3.connect(DB_PATH)
        migrate_role_schema(conn)
        verify_migration(conn)
        conn.close()
        
        print("\n" + "="*80)
        print("✅ MIGRATION ERFOLGREICH ABGESCHLOSSEN!")
        print("="*80)
        print(f"\n💾 Backup gespeichert: {backup_path}")
        print("\n⚠️  NÄCHSTE SCHRITTE:")
        print("   1. m03_db.py: Role-Model aktualisieren")
        print("   2. m07_roles.py: upsert_role() anpassen")
        print("   3. UI-Code testen (Lab Forms, 03_Roles.py)")
        
    except Exception as e:
        print(f"\n❌ FEHLER bei Migration: {e}")
        print(f"\n🔄 Stelle Backup wieder her:")
        print(f"   copy \"{backup_path}\" \"{DB_PATH}\"")
        raise

if __name__ == "__main__":
    main()
