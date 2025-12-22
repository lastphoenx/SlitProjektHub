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
│  │  projekthub.mainedomain.com  →  Container:8501      │  │
│  │  auth.mainedomain.com        →  Authelia:9091       │  │
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
1. User ruft auf: https://projekthub.mainedomain.com
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
Server (/opt/projekthub/)
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
    │ DNS: projekthub.mainedomain.com → Server-IP
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
│     cd /opt/projekthub                          │
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
