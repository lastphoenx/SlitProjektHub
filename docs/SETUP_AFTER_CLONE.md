# Setup nach Git Clone

> Was passiert beim frischen Clone und wie die Datenbank initialisiert wird

## 🎯 Workflow nach `git clone`

### 1. Repository clonen

```bash
git clone https://github.com/lastphoenx/SlitProjektHub.git
cd SlitProjektHub
```

**Was ist im Repository:**
- ✅ Gesamter Code (`app/`, `backend/`, `src/`)
- ✅ Dependencies (`requirements.txt`)
- ✅ Konfiguration (`config/*.yaml`)
- ✅ Dokumentation (`docs/`)
- ✅ Scripts (`scripts/`)
- ✅ Environment Templates (`.env.example`)

**Was ist NICHT im Repository:**
- ❌ Datenbanken (`data/db/*.db`)
- ❌ Vector Store (`data/db/chroma/`)
- ❌ RAG Dokumente (`data/rag/docs/*.pdf`)
- ❌ Secrets (`.env` mit API Keys)
- ❌ Virtual Environment (`.venv/`)

### 2. Python Environment einrichten

```bash
# Virtual Environment erstellen
python -m venv .venv

# Aktivieren
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1

# Windows CMD:
.venv\Scripts\activate.bat

# Linux/Mac:
source .venv/bin/activate

# Dependencies installieren
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Environment Variables konfigurieren

```bash
# Template kopieren
cp .env.example .env

# Bearbeiten
nano .env  # oder: code .env
```

Inhalt von `.env`:
```bash
# LLM Provider API Keys
OPENAI_API_KEY=sk-xxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxx
MISTRAL_API_KEY=xxxxx

# Optional
DEEPSEEK_API_KEY=xxxxx
GROQ_API_KEY=gsk-xxxxx
```

### 4. Datenbank automatisch initialisieren

**Die Datenbank wird automatisch erstellt beim ersten Start!**

#### Wie funktioniert das?

In `src/m03_db.py`:

```python
def init_db() -> None:
    """
    Erstellt automatisch:
    - Alle SQLite Tabellen (role, task, context, project, etc.)
    - Alle Indizes
    - Führt Migrationen durch (fehlende Spalten)
    """
    SQLModel.metadata.create_all(engine)
    migrate_db()
```

Diese Funktion wird aufgerufen von:
- `app/streamlit_app.py` beim App-Start
- `backend/main.py` beim Backend-Start

**Du musst NICHTS manuell machen!**

#### Was wird erstellt:

```
data/
├── db/
│   └── slitproj.db          ← Automatisch erstellt (leer)
└── rag/
    ├── contexts/            ← Automatisch erstellt
    ├── projects/            ← Automatisch erstellt
    ├── roles/               ← Automatisch erstellt
    ├── tasks/               ← Automatisch erstellt
    └── docs/                ← Für RAG-Dokumente (manuell hochladen)
```

### 5. App starten

#### Windows:
```powershell
.\start_app.ps1
```

#### Linux/Mac:
```bash
# Backend
cd backend
python main.py &

# Frontend
streamlit run app/streamlit_app.py
```

#### Manuell (einzelne Komponenten):
```bash
# Nur Backend
cd backend
python main.py

# Nur Frontend (anderes Terminal)
streamlit run app/streamlit_app.py
```

### 6. Erste Verwendung

**Beim ersten Öffnen:**
1. Frontend öffnet: `http://localhost:8501`
2. Datenbank wird automatisch erstellt
3. Alle Tabellen werden angelegt
4. App ist einsatzbereit!

**Keine Daten vorhanden?**
- Rollen, Tasks, Projekte sind leer (neu anfangen)
- RAG-System ist leer (keine Dokumente)

---

## 🔄 Datenbank aus Backup importieren

Falls du eine bestehende Datenbank migrieren möchtest:

### Option 1: SQLite DB direkt kopieren

```bash
# Auf Windows (vor Git Push):
# Datenbank exportieren
copy data\db\slitproj.db C:\Backup\

# Auf Debian (nach Git Clone):
# Datenbank importieren
scp user@windows:/Backup/slitproj.db data/db/

# ODER: Direkt via USB-Stick, Cloud, etc.
```

### Option 2: Via Export-Script (Windows)

```powershell
# Auf Windows
.\export_databases.ps1 -ProjectPath "."

# Erstellt: exports_for_migration/<timestamp>/
# - databases/*.db
# - databases/*.sql (SQL Dump)
# - vector_stores/ (ChromaDB)
# - rag/ (Dokumente)
```

Dann auf Server:
```bash
# DBs übertragen
scp -r exports_for_migration/<timestamp>/databases/*.db user@server:~/

# Auf Server kopieren
cp ~/slitproj.db /opt/projekthub/data/db/
```

### Option 3: Via SQL Dump

```bash
# Auf Windows
sqlite3 data/db/slitproj.db .dump > backup.sql

# Auf Debian
sqlite3 data/db/slitproj.db < backup.sql
```

---

## 🧪 Testen ob alles funktioniert

```bash
# 1. DB-Check (optional)
python scripts/maintenance/check_db.py

# 2. Backend testen
curl http://localhost:8000/docs
# Sollte: FastAPI Swagger UI zeigen

# 3. Frontend testen
curl http://localhost:8501
# Sollte: Streamlit App zeigen

# 4. LLM Provider testen
python -c "from src.m08_llm import providers_available; print(providers_available())"
# Sollte: Liste mit verfügbaren Providern zeigen
```

---

## 📊 Schema-Übersicht

Nach `init_db()` existieren diese Tabellen:

```sql
-- Core Entities
role                 -- Rollen (CEO, CTO, etc.)
task                 -- Aufgaben
context              -- Kontexte
project              -- Projekte

-- Document Management
document             -- Hochgeladene Dokumente
document_chunk       -- Text-Chunks für RAG
project_document_link -- Many-to-Many: Project ↔ Document

-- Chat & KI
chat_message         -- Chat-Historie
decision             -- Projektentscheidungen
rag_feedback         -- Feedback zu RAG-Quellen
```

Alle Tabellen haben automatisch:
- `id` Primary Key
- Zeitstempel (`created_at`, `updated_at`)
- Soft-Delete (`is_deleted`)
- Indizes für Performance

---

## 🔧 Maintenance

### DB neu initialisieren (reset)

```bash
# ACHTUNG: Löscht alle Daten!
rm data/db/slitproj.db

# Neu starten → automatisch neu erstellt
streamlit run app/streamlit_app.py
```

### DB Schema manuell erstellen

```python
# Falls automatische Initialisierung nicht klappt:
python -c "from src.m03_db import init_db; init_db(); print('✅ DB initialized')"
```

### Migrationen manuell anwenden

```bash
# Falls neue Spalten hinzugefügt wurden:
python scripts/migrations/migrate_*.py
```

---

## ❓ FAQ

**Q: Muss ich die Datenbank manuell erstellen?**
A: **NEIN!** Wird automatisch beim ersten Start erstellt.

**Q: Was wenn ich bestehende Daten habe?**
A: Kopiere die alte `slitproj.db` nach `data/db/` BEVOR du die App startest.

**Q: Werden meine lokalen Datenbanken in Git gepusht?**
A: **NEIN!** `.gitignore` verhindert das.

**Q: Was ist mit ChromaDB/Vector Store?**
A: Wird auch automatisch initialisiert beim ersten RAG-Zugriff.

**Q: Muss ich etwas für RAG vorbereiten?**
A: Nein, aber du musst Dokumente manuell hochladen (in der App: "Dokumente" Tab).

---

✅ **Du bist fertig!** Die App erstellt alles Notwendige automatisch beim ersten Start.
