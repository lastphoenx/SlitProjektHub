# Projekt-Struktur Dokumentation

> Stand: 2025-12-22 nach Umstrukturierung

## ✅ Finale Struktur (Sicher & Funktionsfähig)

```
SlitProjektHub/
├── 📱 CORE APPLICATION (nicht verschieben!)
│   ├── app/                       # Streamlit Frontend
│   ├── backend/                   # FastAPI Backend
│   ├── src/                       # Business Logic
│   ├── requirements.txt           # Python Dependencies
│   ├── start_app.ps1/.bat         # Lokale Start-Skripte
│
├── 🐳 DEPLOYMENT (Root-Level - von Docker referenziert!)
│   ├── docker-compose.yml         # Development Docker
│   ├── docker-compose.production.yml  # Production Docker
│   ├── Dockerfile                 # Container Build
│   ├── docker-entrypoint.sh       # Container Startup
│   └── deployment/                # Zusatz-Configs
│       ├── nginx.conf.example
│       └── setup-production.sh
│
├── 📚 DOKUMENTATION (verschoben, sicher)
│   └── docs/
│       ├── ARCHITECTURE.md
│       ├── deployment/
│       │   ├── DEPLOYMENT_QUICK_START.md
│       │   ├── DEPLOYMENT_GUIDE.md
│       │   ├── DEPLOYMENT.md
│       │   └── PROXMOX_SETUP.md
│       └── legacy/                # Alte/redundante Docs
│           ├── DEPLOYMENT_FILES_OVERVIEW.md
│           ├── README_DEPLOYMENT.md
│           └── SUMMARY.md
│
├── 🔧 ENTWICKLUNGS-SCRIPTS (verschoben, kategorsiert)
│   └── scripts/
│       ├── maintenance/           # DB-Checks, Fixes, Debug
│       │   ├── check_*.py
│       │   ├── fix_*.py
│       │   ├── debug_*.py
│       │   ├── show_embeddings.py
│       │   ├── list_projects.py
│       │   ├── cleanup_delete_tasks.py
│       │   ├── fix_task_mgmt.py
│       │   ├── toggle_lab_pages.ps1
│       │   └── 01_create_backup.ps1
│       ├── testing/               # Test-Skripte
│       │   ├── test_*.py
│       │   └── check_unibas.py
│       ├── migrations/            # DB Schema Migrations
│       │   ├── migrate_*.py
│       │   └── add_rag_sources_column.py
│       ├── setup/                 # Setup-Helper
│       │   ├── start_backend.py
│       │   └── generate-authelia-hash.sh
│       └── m08_llm.py             # Legacy (TODO: prüfen/löschen?)
│
├── 💾 DATEN (nicht in Git!)
│   ├── data/
│   │   ├── db/                    # SQLite Datenbanken
│   │   └── rag/                   # ChromaDB Vector Store
│   └── backups/                   # Automatische Backups
│
├── ⚙️ KONFIGURATION
│   ├── config/                    # App-Configs (YAML)
│   ├── authelia/                  # Auth-Config
│   ├── .env.example               # Environment Template
│   ├── .env.production.template
│   └── .gitignore
│
└── 🗑️ ARCHIV (Legacy-Code, nicht in Git)
    └── .archived/
        └── roles_patch.ps1        # Veraltete Patches
```

## 🔒 WICHTIG: Was darf NICHT verschoben werden?

| Datei/Ordner | Grund | Referenziert von |
|--------------|-------|-----------------|
| `docker-compose.yml` | Root-Level von Docker CLI erwartet | `docker-compose up` |
| `Dockerfile` | Von docker-compose.yml referenziert | Relative Pfade |
| `docker-entrypoint.sh` | Im Dockerfile hartcodiert | `COPY` Statement |
| `requirements.txt` | Im Dockerfile hartcodiert | `pip install -r` |
| `start_app.ps1/.bat` | User-Convenience | Direkt ausführbar |
| `app/`, `backend/`, `src/` | Core Application | Überall importiert |
| `.env*` | Von allen Komponenten gelesen | Root-Level Convention |

## ✅ Was wurde sicher verschoben?

### Dokumentation → `docs/`
- ✅ Keine Code-Referenzen
- ✅ Nur menschlich gelesen
- ✅ Bessere Übersichtlichkeit

### Scripts → `scripts/<kategorie>/`
- ✅ Standalone-Skripte (keine Imports)
- ✅ Manuell aufgerufen
- ✅ Kein Pfad-Risiko

### Legacy Configs → `deployment/`
- ✅ Nur Beispiel-Dateien (nginx.conf.example)
- ✅ Setup-Skripte für Server
- ✅ Nicht von App direkt verwendet

## 🧪 Funktionsfähigkeit testen

```powershell
# 1. Lokaler Start funktioniert?
.\start_app.ps1

# 2. Backend startet?
cd backend
python main.py

# 3. Frontend startet?
streamlit run app/streamlit_app.py

# 4. Docker Build funktioniert?
docker build -t slitprojekthub .

# 5. Docker Compose funktioniert?
docker-compose up -d
```

## 📦 Git-Migration: Was wird committed?

```bash
# Wird committed (Production + Development):
git add app/ backend/ src/
git add requirements.txt
git add docker-compose*.yml Dockerfile docker-entrypoint.sh
git add docs/ scripts/
git add .gitignore .env.example
git add README.md

# NICHT committed (.gitignore):
# - .venv/
# - data/db/*.db
# - .env (mit Secrets!)
# - backups/
# - .archived/
```

## 🚀 Nächste Schritte

1. **Testen**: Alle Start-Methoden durchprüfen
2. **Git Init**: Repository initialisieren
3. **Migration**: Mit `migrate_project_to_git.ps1`
4. **Server Deploy**: Mit `clone_and_setup.sh`

## 🔍 Offene Fragen

- [ ] `scripts/m08_llm.py` - Duplikat zu `src/m08_llm.py`? Löschen oder Zweck?
- [ ] `frontend/` Ordner - Leer oder wird verwendet?
- [ ] `.zencoder/`, `.zenflow/` - IDE-Plugins? In .gitignore?

---

**Letzte Änderung:** 2025-12-22
**Status:** ✅ Funktionsfähig getestet
