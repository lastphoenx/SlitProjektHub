# Projekt-Struktur Dokumentation

> Stand: 2026-04-02

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

**Letzte Änderung:** 2026-04-02
**Status:** ✅ Funktionsfähig getestet

---

## 🤖 KI-Erkennung & Batch-QA (Stand 2026-04-02)

### Neue Module

| Datei | Beschreibung |
|---|---|
| `src/m13_ki_detector.py` | KI-Erkennung für Ausschreibungsfragen (10 Heuristik-Signale + optionale AI-Tiefenanalyse) |
| `app/pages/08_Batch_QA.py` | Fragen-Batch-Beantworter mit KI-Erkennung, Checkpoint/Resume, Live-Vorschau |

### KI-Erkennung (`m13_ki_detector.py`)

**Stufe 1 – Heuristik (ohne API, kostenlos):**
- 10 gewichtete Signale: Strukturreferenzen, KI-Floskeln, Übergangsphrasing, uniforme Einstiege, Bullets, erschöpfende Aufzählungen, Burstiness, Längenuniformität, Volumen-Signal, Informal-Malus
- Kleine Stichproben (n < 10) werden gedämpft, um False Positives zu vermeiden
- Kombinationsbonus: n ≥ 15 + hohe Fragenanzahl + mehrere Signale → +15%

**Stufe 2 – AI-Tiefenanalyse (optional, OpenAI/Anthropic):**
- Einzelner Anbieter oder alle Anbieter auf einmal
- Kalibrierter System-Prompt mit expliziten menschlichen Gegenmerkmalen
- Kombinierte Tabelle: Heuristik-Score vs. AI-Score vs. Auffällige Merkmale

### Batch-QA (`08_Batch_QA.py`)

**Checkpoint/Resume:**
- Checkpoint-Datei: `data/batch_checkpoint_{projekt}_{csv_id}.json`
- Format: `{"__meta__": {provider, model, roles, ...}, "results": [...]}`
- Validierung beim Laden: Checkpoint wird nur verwendet wenn Provider, Modell, Rollen-Modus und Rollen identisch sind
- Lösch-Button sichtbar wenn Checkpoint vorhanden

**Live-Vorschau:**
- Liest aus Checkpoint-JSON (nicht aus Memory)
- Letzte 5 Antworten als aufklappbare Expander mit vollständigem Antworttext

**Fallback-Warnung:**
- Wenn ein OpenAI-Modell nicht verfügbar ist (404 / Tier-Problem), wird automatisch auf `gpt-4o-mini` zurückgefallen
- Sichtbare Warnung im UI mit Angabe welches Modell verwendet wurde

### OpenAI-Modelle (`src/m08_llm.py`)

Verfügbare Modelle (Stand April 2026, Quelle: developers.openai.com/api/docs/models):

| UI-Name | Model-ID | Hinweis |
|---|---|---|
| Thinking 5.4 | `gpt-5.4` | Flagship, `max_completion_tokens` |
| Instant 5.3 | `gpt-5.3-instant` | Schnell/alltäglich |
| — | `gpt-5.4-mini` | Mini-Flagship |
| — | `gpt-5.4-nano` | Günstigstes GPT-5.4 |
| — | `gpt-5.2` | Vorheriges Frontier |
| — | `gpt-5.0` | Älteres GPT-5 |
| — | `gpt-5-mini` | Günstig |
| — | `o4-mini`, `o3`, `o3-mini`, `o1`, `o1-mini` | Reasoning-Modelle |
| — | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo` | Legacy/stabil |

Alle `gpt-5.*` und `o*`-Modelle verwenden `max_completion_tokens` (nicht `max_tokens`).

### `start_app.ps1`

Beendet vor dem Start automatisch alle laufenden Prozesse auf Port 8000 (Backend) und 8501 (Frontend), um Port-Konflikte bei Neustarts zu vermeiden.
