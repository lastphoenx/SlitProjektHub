# 🚀 SlitProjektHub - Deployment Übersicht

## 📁 Wichtige Dateien

```
SlitProjektHub/
│
├── 🖥️ LOKALER START (Windows)
│   └── start_app.bat              ← Doppelklick zum Starten
│
├── 🐳 DOCKER DEPLOYMENT
│   ├── Dockerfile                 ← Container Image
│   ├── docker-compose.yml         ← Lokal/LAN (Port 8501)
│   ├── docker-compose.production.yml  ← Production mit 2FA
│   └── docker-entrypoint.sh       ← Container Startup
│
├── 🔧 SETUP SCRIPTS
│   ├── setup-production.sh        ← Automatisches Setup (START HIER!)
│   └── scripts/
│       └── generate-authelia-hash.sh  ← Password-Hash Generator
│
├── 🔐 KONFIGURATION
│   ├── .env.production.template   ← Secrets-Vorlage
│   ├── .streamlit/config.production.toml
│   ├── authelia/
│   │   ├── configuration.yml      ← 2FA Konfiguration
│   │   └── users_database.yml     ← Benutzer + Passwörter
│   └── nginx.conf.example         ← Nginx Reverse Proxy
│
└── 📚 DOKUMENTATION
    ├── DEPLOYMENT_QUICK_START.md  ← ⭐ START HIER! ⭐
    ├── PROXMOX_SETUP.md           ← Proxmox/Debian Guide
    ├── DEPLOYMENT_GUIDE.md        ← Detaillierte Erklärungen
    └── README.md                  ← Diese Datei
```

---

## ⚡ Schnellstart

### Windows (Lokal)
```cmd
:: Einfach Doppelklick auf:
start_app.bat
```

### Server (Production)
```bash
# 1. Setup
bash setup-production.sh

# 2. API-Keys eintragen
nano .env.production

# 3. Passwörter generieren
bash scripts/generate-authelia-hash.sh

# 4. Starten
docker-compose -f docker-compose.production.yml up -d
```

---

## 📖 Dokumentation

Lies **[DEPLOYMENT_QUICK_START.md](DEPLOYMENT_QUICK_START.md)** für die komplette Anleitung!

**Andere Guides:**
- **Proxmox/Debian:** [PROXMOX_SETUP.md](PROXMOX_SETUP.md)
- **Detailliert:** [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
- **Datei-Übersicht:** [DEPLOYMENT_FILES_OVERVIEW.md](DEPLOYMENT_FILES_OVERVIEW.md)

---

## 🎯 3 Deployment-Varianten

| Variante | Dateien | Aufruf | Zugriff |
|----------|---------|--------|---------|
| **A) Windows Lokal** | `start_app.bat` | Doppelklick | `localhost:8501` |
| **B) Server LAN** | `docker-compose.yml` | `docker-compose up -d` | `http://192.168.x.x:8501` |
| **C) Production** | `docker-compose.production.yml` + Authelia | `docker-compose -f docker-compose.production.yml up -d` | `https://projekthub.mainedomain.com` |

---

## ✅ Was ist enthalten?

- 🔐 **2FA-Authentifizierung** mit Authelia (TOTP)
- 🌐 **Reverse Proxy** Support (Traefik & Nginx)
- 📦 **Docker Container** für einfaches Deployment
- 💾 **Persistent Storage** (Datenbank, Config, Uploads)
- 🔒 **SSL/HTTPS** via Let's Encrypt
- 👥 **Multi-User** Support
- 📊 **API Backend** (FastAPI)
- 🎨 **Frontend** (Streamlit)

---

## 🆘 Hilfe benötigt?

**Häufige Probleme:**
- Container startet nicht → `docker-compose logs`
- 2FA funktioniert nicht → Zeit synchronisieren: `ntpdate pool.ntp.org`
- WebSocket-Fehler → Nginx: `proxy_http_version 1.1` + `Upgrade` Header

**Siehe:** [DEPLOYMENT_QUICK_START.md](DEPLOYMENT_QUICK_START.md) → Troubleshooting

---

## 📞 Support

Bei Problemen:
1. Logs sammeln: `docker-compose logs > logs.txt`
2. Config: `cat authelia/configuration.yml >> logs.txt`
3. Status: `docker-compose ps >> logs.txt`

---

## 🔄 Updates

```bash
cd /opt/projekthub
docker-compose pull
docker-compose up -d
```

---

Viel Erfolg! 🚀
