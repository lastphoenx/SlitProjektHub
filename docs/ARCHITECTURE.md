# 🏗️ ProjektHub - Architektur-Übersicht

## 📐 Deployment-Architektur

```
┌─────────────────────────────────────────────────────────────┐
│                        INTERNET                              │
│                    (User Browser)                            │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        │ HTTPS (443)
                        ▼
┌─────────────────────────────────────────────────────────────┐
│              REVERSE PROXY (Traefik/Nginx)                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  projekthub.<YOUR_DOMAIN>  →  Container:8501      │  │
│  │  auth.<YOUR_DOMAIN>        →  Authelia:9091       │  │
│  └──────────────────────────────────────────────────────┘  │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   Authelia   │ │ ProjektHub   │ │  Backend     │
│   (2FA)      │ │  (Streamlit) │ │  (FastAPI)   │
│   Port 9091  │ │  Port 8501   │ │  Port 8000   │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       │                │                │
       ▼                ▼                ▼
┌─────────────────────────────────────────────────┐
│           PERSISTENT VOLUMES                    │
│  ┌─────────────────────────────────────────┐   │
│  │ authelia/db.sqlite3  - User Sessions    │   │
│  │ data/db/slitproj.db  - App Database     │   │
│  │ data/rag/            - Documents        │   │
│  │ config/              - Configuration    │   │
│  └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

---

## 🔄 Request Flow (mit 2FA)

```
1. User ruft auf: https://projekthub.<YOUR_DOMAIN>
                            │
                            ▼
2. Reverse Proxy prüft: Authenticated?
                            │
                            ├─ Nein → Redirect zu Authelia
                            │         │
                            │         ▼
                            │    3. Authelia Login-Page
                            │         │
                            │         ├─ Username/Password
                            │         ├─ 2FA Code (TOTP)
                            │         │
                            │         ▼
                            │    4. Session erstellt
                            │         │
                            ▼         ▼
5. Request wird weitergeleitet an Streamlit (8501)
                            │
                            ▼
6. Streamlit lädt Daten von Backend (8000)
                            │
                            ▼
7. Response → User Browser
```

---

## 🗂️ Datei-Flow beim Deployment

```
Windows PC (Entwicklung)
    │
    │ WinSCP/Git
    ▼
Server (<PROJECT_ROOT>/)
    │
    │ bash setup-production.sh
    ▼
┌─────────────────────────────────────┐
│  1. .env.production erstellt        │
│     - JWT_SECRET generiert          │
│     - SESSION_SECRET generiert      │
└─────────────────────────────────────┘
    │
    │ docker run authelia/... (Hash-Generator)
    ▼
┌─────────────────────────────────────┐
│  2. authelia/users_database.yml     │
│     - Passwort-Hashes eingefügt     │
└─────────────────────────────────────┘
    │
    │ docker-compose -f docker-compose.production.yml up
    ▼
┌─────────────────────────────────────┐
│  3. Container starten               │
│     - ProjektHub (8501)             │
│     - Authelia (9091)               │
│     - Backend (8000)                │
└─────────────────────────────────────┘
    │
    │ DNS: projekthub.<YOUR_DOMAIN> → Server-IP
    ▼
┌─────────────────────────────────────┐
│  4. Reverse Proxy                   │
│     - Let's Encrypt SSL             │
│     - Authelia Middleware           │
└─────────────────────────────────────┘
    │
    ▼
  ONLINE! ✅
```

---

## 🔐 Security-Layer

```
┌─────────────────────────────────────────────────┐
│  Layer 1: Firewall                              │
│  - Nur Port 80, 443 offen                       │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  Layer 2: SSL/TLS (HTTPS)                       │
│  - Let's Encrypt Zertifikat                     │
│  - TLS 1.2/1.3                                  │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  Layer 3: Authelia (2FA)                        │
│  - Username/Password (Argon2id Hash)            │
│  - TOTP (6-stelliger Code)                      │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  Layer 4: Application                           │
│  - API-Keys in .env (nicht in Git)              │
│  - SQLite mit File-Permissions                  │
└─────────────────────────────────────────────────┘
```

---

## 📦 Container-Struktur

```
┌─────────────────────────────────────────────────┐
│  Docker Container: slit-projekthub              │
│  ┌───────────────────────────────────────────┐ │
│  │  /app/                                    │ │
│  │  ├── backend/                             │ │
│  │  │   ├── main.py         (FastAPI)       │ │
│  │  │   └── app/                             │ │
│  │  ├── app/                                 │ │
│  │  │   └── streamlit_app.py (Streamlit)    │ │
│  │  ├── src/                                 │ │
│  │  │   ├── m01_config.py                    │ │
│  │  │   ├── m03_db.py                        │ │
│  │  │   ├── m08_llm.py                       │ │
│  │  │   └── ...                              │ │
│  │  └── data/ (Volume gemountet)             │ │
│  │      ├── db/slitproj.db                   │ │
│  │      └── rag/                             │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
│  Ports:                                         │
│  - 8501 → Streamlit Frontend                   │
│  - 8000 → FastAPI Backend                      │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  Docker Container: authelia                     │
│  ┌───────────────────────────────────────────┐ │
│  │  /config/                                 │ │
│  │  ├── configuration.yml                    │ │
│  │  ├── users_database.yml                   │ │
│  │  └── db.sqlite3 (Volume gemountet)        │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
│  Port: 9091                                     │
└─────────────────────────────────────────────────┘
```

---

## 🔄 Update-Prozess

```
┌─────────────────────────────────────────────────┐
│  1. Code-Update (Windows PC)                    │
│     - Änderungen in src/                        │
│     - Commit & Push zu Git                      │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  2. Server-Update                               │
│     cd <PROJECT_ROOT>                          │
│     git pull                                    │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  3. Container neu bauen                         │
│     docker-compose build                        │
│     docker-compose up -d                        │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  4. Zero-Downtime (optional)                    │
│     docker-compose up -d --no-deps --build      │
└─────────────────────────────────────────────────┘
```

---

## 📊 Monitoring Points

```
Health Checks:
┌──────────────────────────────────────┐
│  http://localhost:8501/_stcore/health│  ← Streamlit
│  http://localhost:8000/docs          │  ← FastAPI
│  http://localhost:9091/api/health    │  ← Authelia
└──────────────────────────────────────┘

Logs:
┌──────────────────────────────────────┐
│  docker-compose logs -f projekthub   │
│  docker-compose logs -f authelia     │
│  /tmp/backend.log   (in Container)   │
│  /tmp/frontend.log  (in Container)   │
└──────────────────────────────────────┘

Metrics:
┌──────────────────────────────────────┐
│  docker stats                        │
│  docker-compose ps                   │
└──────────────────────────────────────┘
```

---

Diese Architektur bietet:
- ✅ Hohe Sicherheit (Multi-Layer)
- ✅ Einfaches Deployment (Docker)
- ✅ Skalierbar (Container-basiert)
- ✅ Wartbar (klare Trennung)

Siehe [DEPLOYMENT_QUICK_START.md](DEPLOYMENT_QUICK_START.md) für Setup-Anleitung! 🚀

---

## 🧠 KI-Retrieval Pipeline (RAG)

### Architektur-Überblick

Das System verwendet eine **vierstufige Query-Intelligence-Pipeline**, die einfache Keyword-Suche weit hinter sich lässt:

```
Nutzer-Eingabe
     │
     ▼
┌────────────────────────────────────────────────────┐
│  STUFE 1: Query Distillation                       │
│  src/m08_llm.py → rewrite_query_for_retrieval()    │
│                                                    │
│  LLM bereinigt UI-Noise, extrahiert Fachbegriffe   │
│  "Was ist eigentlich mit dem Subunternehmer?"      │
│       → "Subunternehmer Vertrag Anforderungen"     │
│                                                    │
│  Konfigurierbar: config/retrieval.yaml             │
│  query.enable_distillation: true/false             │
└────────────────────┬───────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────────┐
│  STUFE 2: Parallele Hybrid-Retrieval               │
│  src/m09_rag.py → retrieve_relevant_chunks_hybrid()│
│                                                    │
│  ┌─────────────────┐   ┌─────────────────────────┐│
│  │ BM25 / Keyword  │   │  Semantic / Embedding   ││
│  │ rank_bm25       │   │  text-embedding-3-small  ││
│  │ DE-Stemming     │   │  ChromaDB Vector Store   ││
│  │ IDF-Gewichtung  │   │  Cosine Similarity       ││
│  │ Priority Boost  │   │  Discovery Threshold     ││
│  └────────┬────────┘   └────────────┬────────────┘│
│           │                         │              │
└───────────┼─────────────────────────┼──────────────┘
            │                         │
            ▼                         ▼
┌────────────────────────────────────────────────────┐
│  STUFE 3: Reciprocal Rank Fusion (RRF)             │
│  src/m09_rag.py → reciprocal_rank_fusion()         │
│                                                    │
│  Score = Σ ( 1 / (k + rank_i) )  mit k=60         │
│                                                    │
│  Vorteile gegenüber Magic-Number Weights:           │
│  • Robust gegen Score-Skalierung                   │
│  • Mathematisch fundiert (IR-Literatur)            │
│  • Keine manuellen Gewichtungen nötig              │
│  • k konfigurierbar: config/retrieval.yaml         │
└────────────────────┬───────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────────┐
│  STUFE 4: Multi-Hypothesis (Optional)              │
│  src/m08_llm.py → generate_query_hypotheses()      │
│                                                    │
│  Generiert N Suchstrategien:                       │
│  1. KEYWORD  – kompakte Fachbegriffe               │
│  2. SEMANTIC – Umformulierung + Synonyme           │
│  3. CONTEXT  – erweiterte Beschreibung             │
│                                                    │
│  Alle Hypothesen-Rankings fliessen in RRF          │
│  Default: OFF (Performance-Trade-off)              │
│  Toggle: config/retrieval.yaml → enable_multi_hypothesis│
└────────────────────┬───────────────────────────────┘
                     │
                     ▼
              Gefilterte, gewichtete
              Dokument-Chunks + Scores
                     │
                     ▼
           LLM-Antwort mit Kontext
```

### Konfiguration

Alle Retrieval-Parameter sind **ausschliesslich** in `config/retrieval.yaml` definiert:

| Parameter | Default | Beschreibung |
|-----------|---------|-------------|
| `bm25.priority_terms` | Domain-Begriffe | BM25 Score-Boost für wichtige Fachbegriffe |
| `hybrid.rrf_k` | 60 | RRF Fusion-Parameter (IR-Standard) |
| `query.enable_distillation` | `true` | LLM-basierte Query-Bereinigung |
| `query.enable_multi_hypothesis` | `false` | Parallele Query-Varianten |
| `query.hypothesis_count` | 3 | Anzahl Multi-Hypothesis Varianten |
| `semantic.discovery_threshold_multiplier` | 0.35 | Breite der semantischen Suche |
| `filename_boost.boost_amount` | 0.10 | +10% Score bei Filename-Match |

### Module

| Modul | Funktion |
|-------|----------|
| `src/m01_retrieval_config.py` | YAML → Python Dataclasses (typsicher) |
| `src/m08_llm.py` | `rewrite_query_for_retrieval()`, `generate_query_hypotheses()` |
| `src/m09_rag.py` | `retrieve_relevant_chunks_hybrid()`, `reciprocal_rank_fusion()` |
| `data/db/chroma/` | ChromaDB Vektor-Store (Embeddings) |
| `data/rag/` | Originaldokumente (PDF, TXT, MD) |
