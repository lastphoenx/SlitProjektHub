# ProjektHub - Deployment Dateien Übersicht

## **Datei-Struktur & Zweck**

```
/
├── 🖥️ start_app.bat                    ← Windows: Doppelklick zum Starten (local dev)
├── 🐳 Dockerfile                       ← Container Image
├── 🐳 docker-compose.yml               ← Local/LAN Deployment (Port 8501)
├── 🐳 docker-compose.production.yml    ← Production mit Traefik + Authelia
├── 📄 docker-entrypoint.sh             ← Container Startup Script (Backend + Frontend)
│
├── 🔐 .env.production.template         ← Secrets Template (KOPIEREN & EDITIEREN)
├── 📝 .streamlit/config.production.toml← Streamlit Production Config
│
├── 🔑 authelia/
│   ├── configuration.yml               ← Authelia (2FA) Konfiguration
│   ├── users_database.yml              ← Benutzer + Passwort-Hashes
│   └── db.sqlite3                      ← Authelia Datenbank (auto-created)
│
├── 🌐 nginx.conf.example               ← Nginx Reverse Proxy Config
├── 🔧 scripts/
│   └── generate-authelia-hash.sh       ← Password-Hash Generator
│
├── 📚 DEPLOYMENT_QUICK_START.md        ← ← START HIER! ← Step-by-Step Anleitung
├── 📚 DEPLOYMENT_GUIDE.md              ← Detaillierte Erklärungen
└── 📚 DEPLOYMENT_FILES_OVERVIEW.md     ← Diese Datei
```

---

## **🚀 Schnell-Start (3 Varianten)**

### **A) Windows - Lokal (Doppelklick)**
```
1. Doppelklick auf: start_app.bat
2. Browser öffnet: http://localhost:8501
✅ Fertig!
```
**Dateien:** `start_app.bat`

---

### **B) Server - Lokal Netzwerk (Haushalt)**
```
1. Server: docker-compose up -d
2. Browser: http://192.168.x.x:8501
✅ Alle im Haushalt haben Zugriff
```
**Dateien:** `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`

---

### **C) Server - Domain + 2FA Sicherheit**
```
1. Secrets generieren:   openssl rand -base64 32
2. Passwörter hashen:    scripts/generate-authelia-hash.sh
3. Container starten:    docker-compose -f docker-compose.production.yml up -d
4. Browser:              https://projekthub.mainedomain.com
5. Login mit 2FA ✅
```
**Dateien:** Alle!

---

## **📋 Setup Checklist**

### **Vor dem Start (Production)**

- [ ] **Secrets vorbereiten**
  ```bash
  cp .env.production.template .env.production
  # Editiere: API-Keys, Domain, SMTP
  ```

- [ ] **Authelia konfigurieren**
  ```bash
  mkdir -p authelia/db
  chmod 700 authelia/db
  # Passwort-Hashes generieren
  bash scripts/generate-authelia-hash.sh
  # In users_database.yml eintragen
  ```

- [ ] **SSL-Zertifikat**
  - Let's Encrypt via Traefik (auto)
  - Oder: `sudo certbot certonly --nginx`

- [ ] **Domain DNS**
  - `projekthub.mainedomain.com` → Server IP
  - `auth.mainedomain.com` → Server IP (optional)

### **Container starten**

```bash
# Development
docker-compose up -d

# Production (mit Traefik)
docker-compose -f docker-compose.production.yml up -d

# Production (mit nginx)
docker-compose -f docker-compose.production.yml -f docker-compose.nginx.yml up -d
```

### **Nach dem Start**

- [ ] Check: `docker-compose ps`
- [ ] Logs: `docker-compose logs -f`
- [ ] Health: `curl http://localhost:8501/_stcore/health`
- [ ] Browser: https://projekthub.mainedomain.com

---

## **📁 Wichtige Dateien Editieren**

### **1. .env.production** (MUST EDIT)
```bash
cp .env.production.template .env.production
nano .env.production
# Eintragen:
# - OPENAI_API_KEY
# - ANTHROPIC_API_KEY
# - MISTRAL_API_KEY
# - SMTP Details (für Email)
```

### **2. authelia/configuration.yml** (REVIEW)
```yaml
server:
  port: 9091
theme: light  # oder 'dark'
default_redirection_url: https://projekthub.mainedomain.com
# ... Rest ist OK
```

### **3. authelia/users_database.yml** (MUST EDIT)
```yaml
users:
  admin:
    displayname: "Your Name"
    password: "$argon2id$..."  # ← Generiert via script
    email: admin@mainedomain.com
```

### **4. docker-compose.production.yml** (Optional)
Wenn du **nicht Traefik** verwendest:
```yaml
# Kommentiere Traefik-Labels aus:
# labels:
#   - "traefik.enable=true"
#   ...
```

---

## **🔐 Sicherheit Checkliste**

- [ ] **Passwörter gehashed** (nicht plaintext)
- [ ] **.env.production** in `.gitignore`
- [ ] **Firewall**: nur Port 80, 443 offen
- [ ] **2FA**: Alle Benutzer aktivieren
- [ ] **SSL-Zertifikat**: gültig & auto-renew
- [ ] **CORS**: Deaktiviert (nur reverse proxy Zugriff)
- [ ] **Secrets**: 32+ Zeichen, random generiert
- [ ] **Regelmäßige Backups** einrichten

---

## **🔄 Update & Maintenance**

### **Container Update**
```bash
docker-compose pull
docker-compose up -d
```

### **Database Backup**
```bash
# ProjektHub DB
docker exec slit-projekthub sqlite3 /app/data/db/sph.db .dump > backup_app.sql

# Authelia DB
docker exec authelia sqlite3 /config/db.sqlite3 .dump > backup_auth.sql
```

### **Logs anschauen**
```bash
# Real-time
docker-compose logs -f

# Spezifisches Service
docker-compose logs -f authelia
docker-compose logs -f projekthub
```

---

## **🛠️ Troubleshooting**

| Problem | Lösung |
|---------|--------|
| **503 Service Unavailable** | `docker-compose logs` prüfen |
| **2FA funktioniert nicht** | Authelia-Config validieren: `docker run authelia/authelia:latest authelia validate-config /config/configuration.yml` |
| **WebSocket Fehler** | Reverse Proxy Check: `proxy_http_version 1.1`, `Upgrade`, `Connection` Header |
| **Passwort-Reset nicht möglich** | SMTP in `configuration.yml` konfigurieren |
| **SSL-Fehler** | `sudo certbot renew --dry-run` |

---

## **📞 Support Files**

Wenn du Hilfe brauchst:

1. **Logs sammeln**: `docker-compose logs > logs.txt`
2. **Config dumpen**: `cat authelia/configuration.yml >> logs.txt`
3. **Status**: `docker-compose ps >> logs.txt`
4. **Systeminfo**: `docker --version && docker-compose --version >> logs.txt`

Dann alle 3 Info-Dateien bereithalten für Support!

---

## **✅ Fertig?**

Wenn du alle Schritte aus **DEPLOYMENT_QUICK_START.md** gemacht hast:

```bash
✅ ProjektHub läuft
✅ 2FA aktiv
✅ Domain erreichbar
✅ Benutzer können sich anmelden
✅ Haushalt hat Zugriff
```

Viel Erfolg! 🚀
