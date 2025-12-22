# SlitProjektHub - KI-gestütztes Projektmanagement

> Intelligentes Projektmanagement-Tool mit RAG (Retrieval Augmented Generation), Multi-Provider LLM-Integration und strukturiertem Aufgaben-/Rollenmanagement.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.28+-red.svg)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)

## 🎯 Features

- **📊 Projektmanagement**: Projekte, Rollen, Aufgaben und Kontexte strukturiert verwalten
- **🤖 Multi-LLM Integration**: OpenAI, Anthropic, Mistral, DeepSeek mit einheitlicher API
- **💬 KI-Chat**: Projekt-spezifische Chats mit RAG-basierter Dokumenten-Integration
- **📚 Dokumenten-Management**: Upload, Klassifizierung und intelligente Suche
- **🔍 Vector Search**: Semantische Suche über alle Inhalte (ChromaDB)
- **🎨 Moderne UI**: Streamlit-basiertes Interface mit responsive Design
- **🚀 Production-Ready**: Docker + Authelia 2FA + Traefik/nginx Support

## 📁 Projektstruktur

```
SlitProjektHub/
├── 📱 app/                    # Streamlit Frontend
│   ├── streamlit_app.py       # Main App
│   └── pages/                 # Multi-Page App
├── 🔌 backend/                # FastAPI Backend
│   └── main.py                # REST API
├── 📦 src/                    # Core Business Logic
│   ├── m03_db.py              # Database Models (SQLModel)
│   ├── m07_*.py               # Domain Logic (Roles, Tasks, Projects)
│   ├── m08_llm.py             # LLM Provider Abstraction
│   ├── m09_rag.py             # RAG Implementation
│   └── m10_chat.py            # Chat Logic
├── 🐳 deployment/             # Docker, nginx, Production Configs
├── 📚 docs/                   # Dokumentation
│   ├── ARCHITECTURE.md        # System-Architektur
│   └── deployment/            # Deployment-Guides
├── 🔧 scripts/                # Utility Scripts
│   ├── maintenance/           # check_*, fix_*, debug_*
│   ├── testing/               # test_*
│   └── migrations/            # DB Migrations
├── 💾 data/                   # Daten (nicht in Git!)
│   ├── db/                    # SQLite Datenbank
│   └── rag/                   # ChromaDB Vector Store
└── ⚙️ config/                 # Konfigurationen

```

## 🚀 Schnellstart (Lokal)

### Voraussetzungen
- Python 3.11+
- Git
- API Keys: OpenAI, Anthropic, oder Mistral

### Installation

```bash
# Repository clonen
git clone https://github.com/YOUR_USERNAME/SlitProjektHub.git
cd SlitProjektHub

# Virtual Environment erstellen
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Dependencies installieren
pip install -r requirements.txt

# .env erstellen
cp .env.example .env
# .env bearbeiten und API Keys eintragen
```

### Starten

**Windows:**
```powershell
.\start_app.ps1
```

**Linux/Mac:**
```bash
# Backend starten
cd backend
python main.py &

# Frontend starten
streamlit run app/streamlit_app.py
```

**Zugriff:**
- Frontend: http://localhost:8501
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

## 🐳 Production Deployment

Siehe [Deployment Quick Start](docs/deployment/DEPLOYMENT_QUICK_START.md) für vollständige Anleitung.

**Kurz-Version:**

```bash
# .env.production konfigurieren
cp .env.production.template .env.production
nano .env.production

# Docker Container starten
docker-compose -f deployment/docker-compose.production.yml up -d
```

Unterstützt:
- ✅ Traefik (Auto-SSL via Let's Encrypt)
- ✅ nginx Reverse Proxy
- ✅ Authelia 2FA Authentication
- ✅ Proxmox/Debian Server

## 📚 Dokumentation

| Dokument | Beschreibung |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System-Architektur & Technologie-Stack |
| [Deployment Quick Start](docs/deployment/DEPLOYMENT_QUICK_START.md) | Schnelle Production-Deployment Anleitung |
| [Deployment Guide](docs/deployment/DEPLOYMENT_GUIDE.md) | Vollständiger Deployment-Guide |
| [Proxmox Setup](docs/deployment/PROXMOX_SETUP.md) | Proxmox-spezifische Konfiguration |

## 🛠️ Entwicklung

### Projekt-Struktur Setup

```bash
# Datenbank initialisieren
python -c "from src.m03_db import init_db; init_db()"

# Entwicklungs-Server starten
streamlit run app/streamlit_app.py --server.runOnSave=true
```

### Hilfreiche Skripte

```bash
# Datenbank prüfen
python scripts/maintenance/check_db.py

# Embeddings anzeigen
python scripts/maintenance/show_embeddings.py

# Tests ausführen
python scripts/testing/test_*.py
```

### Code-Qualität

```bash
# Type Checking
mypy src/

# Linting
ruff check src/

# Formatting
black src/
```

## 🔧 Konfiguration

### Environment Variables

```bash
# LLM Provider API Keys
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
MISTRAL_API_KEY=...

# Optional
DEEPSEEK_API_KEY=...
GROQ_API_KEY=...
```

### Datenbank

SQLite (Development):
```
data/db/slitproj.db
```

Automatische Schema-Migration beim Start via `init_db()`.

### Vector Store

ChromaDB (RAG):
```
data/rag/chroma/
```

Automatische Initialisierung beim ersten RAG-Zugriff.

## 🤝 Migration zu neuem Server

Siehe [Migration Guide](docs/deployment/DEPLOYMENT_GUIDE.md#migration).

**Quick:**
```bash
# 1. Windows: Datenbanken exportieren
.\scripts\export_databases.ps1

# 2. Code zu GitHub pushen
git push origin main

# 3. Server: Clonen & Setup
./scripts/clone_and_setup.sh --repo github.com/user/SlitProjektHub

# 4. Datenbanken importieren
scp backup.db user@server:~/projects/SlitProjektHub/data/db/
```

## 📊 Tech Stack

| Layer | Technologie |
|-------|-------------|
| **Frontend** | Streamlit 1.28+ |
| **Backend** | FastAPI 0.104+ |
| **Database** | SQLite + SQLModel |
| **Vector Store** | ChromaDB |
| **LLM Providers** | OpenAI, Anthropic, Mistral, DeepSeek |
| **Embeddings** | OpenAI text-embedding-3-small |
| **Deployment** | Docker, Traefik, Authelia |

## 📝 Lizenz

[Deine Lizenz hier einfügen]

## 🙏 Credits

Entwickelt für strukturiertes KI-gestütztes Projektmanagement.

---

**Letzte Aktualisierung:** Dezember 2025
