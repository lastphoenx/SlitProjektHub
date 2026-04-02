# SlitProjektHub

KI-gestütztes Tool zur Verwaltung von Ausschreibungsprojekten mit automatischer Fragenbeantwortung (RAG) und KI-Erkennung.

## Was es tut

- **Projekte & Stammdaten**: Projekte, Rollen und Aufgaben verwalten
- **Dokumente**: PDF, Word, CSV hochladen und durchsuchbar machen (ChromaDB, Embeddings)
- **Fragen-Batch**: CSV mit Anbieter-Fragen einlesen → automatisch mit KI + RAG beantworten → als Excel/CSV exportieren
- **KI-Erkennung**: Analysiert Anbieter-Fragen auf KI-typische Merkmale (Heuristik + optionale AI-Tiefenanalyse)
- **Chat**: Projektbezogener Chat mit Kontext aus hochgeladenen Dokumenten
- **Multi-Provider LLM**: OpenAI (GPT-5.x, GPT-4o), Anthropic (Claude), Mistral — umschaltbar im UI

## Technologie

- **Frontend**: Streamlit (Python)
- **Backend**: FastAPI (REST API, läuft lokal auf Port 8000)
- **Datenbank**: SQLite via SQLModel
- **Vektorsuche**: ChromaDB
- **Betrieb**: Lokal unter Windows, Start via `start_app.ps1`

## Voraussetzungen

- Python 3.11+
- API-Key von OpenAI, Anthropic oder Mistral

## Installation

```powershell
git clone https://github.com/lastphoenx/SlitProjektHub.git
cd SlitProjektHub
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# .env öffnen und API-Keys eintragen
```

## Starten

```powershell
.\start_app.ps1
```

Das Skript beendet automatisch laufende Prozesse auf Port 8000/8501 und öffnet Backend und Frontend in separaten Fenstern.

- **Frontend**: http://localhost:8501
- **Backend API / Docs**: http://localhost:8000/docs

## Projektstruktur

```
app/
  streamlit_app.py       # Einstiegspunkt Streamlit
  pages/                 # Einzelne App-Seiten (01–08)
src/
  m01_config.py          # Einstellungen
  m03_db.py              # Datenbank-Modelle (SQLModel)
  m07_*.py               # Domänenlogik (Projekte, Rollen, Tasks)
  m08_llm.py             # LLM Provider Abstraction (OpenAI/Anthropic/Mistral)
  m09_rag.py             # RAG (Retrieval Augmented Generation)
  m10_chat.py            # Chat-Logik
  m13_ki_detector.py     # KI-Erkennung für Ausschreibungsfragen
backend/
  main.py                # FastAPI REST API
data/
  db/                    # SQLite Datenbank (nicht in Git)
  rag/                   # ChromaDB Vektoren (nicht in Git)
config/
  config.yaml            # App-Einstellungen
scripts/
  maintenance/           # Hilfsskripte (check_*, fix_*, debug_*)
docs/
  ARCHITECTURE.md        # Technische Architektur
  PROJECT_STRUCTURE.md   # Dateistruktur & Feature-Übersicht
```

## Konfiguration

`.env`-Datei im Root-Verzeichnis (Vorlage: `.env.example`):

```env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
MISTRAL_API_KEY=...
```

## KI-Erkennung

Analysiert Fragen aus eingelesenen CSV-Dateien darauf, ob sie KI-generiert wirken:

- **Heuristik** (kostenlos, 10 Signale): Strukturreferenzen, KI-Floskeln, Übergangsphrasing, Einstiege, Bullets, erschöpfende Aufzählungen, Burstiness, Längenuniformität, Volumen, Informal-Malus
- **AI-Tiefenanalyse** (optional, OpenAI/Anthropic): Einzelner Anbieter oder alle Anbieter auf einmal, kalibrierter Prompt mit Gegenmerkmalen

## Bekannte Einschränkungen

- Läuft ausschliesslich lokal (kein Cloud-Deployment vorgesehen)
- Keine Benutzerauthentifizierung (single-user)
- Embedding-Modell: lokal via `sentence-transformers`
