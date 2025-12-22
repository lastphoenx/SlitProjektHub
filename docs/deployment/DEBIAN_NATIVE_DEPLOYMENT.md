# Native Debian Deployment Guide (ohne Docker)

> Für SlitProjektHub auf Debian/Ubuntu Server oder WSL

## 🎯 Übersicht

Dieses Projekt läuft **nativ auf Debian** mit:
- Python 3.11+ Virtual Environment
- Systemd Services (Backend + Frontend)
- nginx als Reverse Proxy
- Optional: Let's Encrypt SSL

**Keine Docker-Container** erforderlich!

## 📋 Voraussetzungen

```bash
# System: Debian 11/12 oder Ubuntu 22.04+
# Python: 3.11+
# RAM: min. 2GB
# Disk: min. 10GB
```

## 🚀 Quick Start (Automatisch)

### 1. Auf Server einloggen

```bash
ssh user@dein-server.de
sudo su
```

### 2. Deployment-Script herunterladen & ausführen

```bash
# Repository clonen (oder nur Script laden)
wget https://raw.githubusercontent.com/USER/SlitProjektHub/main/deployment/deploy-debian.sh
chmod +x deploy-debian.sh

# Ausführen
./deploy-debian.sh
```

### 3. Konfiguration anpassen

```bash
# .env erstellen
sudo -u projekthub nano <PROJECT_ROOT>/.env

# Inhalt:
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
MISTRAL_API_KEY=xxx
```

### 4. Datenbank importieren (bei Migration)

```bash
# Von Windows/WSL übertragen
scp data/db/slitproj.db user@server:/tmp/

# Auf Server kopieren
sudo -u projekthub cp /tmp/slitproj.db <PROJECT_ROOT>/data/db/
```

### 5. Services starten

```bash
systemctl start projekthub-backend
systemctl start projekthub-frontend
systemctl status projekthub-backend projekthub-frontend
```

### 6. Nginx & SSL konfigurieren

```bash
# Domain in nginx Config anpassen
nano /etc/nginx/sites-available/projekthub
# Ersetze: projekthub.deine-domain.de

# SSL-Zertifikat erstellen
certbot --nginx -d projekthub.deine-domain.de

# Nginx neustarten
systemctl restart nginx
```

**Fertig!** → `https://projekthub.deine-domain.de`

---

## 🔧 Manuelles Setup (Schritt für Schritt)

### 1. System vorbereiten

```bash
# Als root
sudo su

# System aktualisieren
apt-get update && apt-get upgrade -y

# Python & Dependencies
apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    sqlite3 \
    nginx \
    git \
    certbot \
    python3-certbot-nginx
```

### 2. Service-User erstellen

```bash
useradd -r -s /bin/bash -d <PROJECT_ROOT> projekthub
mkdir -p <PROJECT_ROOT>
chown projekthub:projekthub <PROJECT_ROOT>
```

### 3. Code klonen

```bash
cd <PROJECT_ROOT>
sudo -u projekthub git clone https://github.com/USER/SlitProjektHub.git .
```

### 4. Python Environment

```bash
# Als projekthub user
sudo -u projekthub python3.11 -m venv .venv
sudo -u projekthub .venv/bin/pip install --upgrade pip
sudo -u projekthub .venv/bin/pip install -r requirements.txt
```

### 5. Verzeichnisse erstellen

```bash
sudo -u projekthub mkdir -p data/db data/rag config logs backups
```

### 6. .env konfigurieren

```bash
sudo -u projekthub nano <PROJECT_ROOT>/.env
```

Inhalt:
```bash
# API Keys
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
MISTRAL_API_KEY=xxx

# Optional
DEEPSEEK_API_KEY=xxx
GROQ_API_KEY=xxx
```

### 7. Datenbank initialisieren

```bash
# Neu initialisieren
sudo -u projekthub <PROJECT_ROOT>/.venv/bin/python << 'EOF'
from src.m03_db import init_db
init_db()
print("✅ Datenbank initialisiert")
EOF

# ODER: Bestehende DB importieren
sudo -u projekthub cp /tmp/backup.db <PROJECT_ROOT>/data/db/slitproj.db
```

### 8. Systemd Services erstellen

**Backend Service:** `/etc/systemd/system/projekthub-backend.service`

```ini
[Unit]
Description=ProjektHub FastAPI Backend
After=network.target

[Service]
Type=simple
User=projekthub
WorkingDirectory=<PROJECT_ROOT>/backend
Environment="PATH=<PROJECT_ROOT>/.venv/bin"
EnvironmentFile=<PROJECT_ROOT>/.env
ExecStart=<PROJECT_ROOT>/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Frontend Service:** `/etc/systemd/system/projekthub-frontend.service`

```ini
[Unit]
Description=ProjektHub Streamlit Frontend
After=network.target projekthub-backend.service

[Service]
Type=simple
User=projekthub
WorkingDirectory=<PROJECT_ROOT>
Environment="PATH=<PROJECT_ROOT>/.venv/bin"
EnvironmentFile=<PROJECT_ROOT>/.env
ExecStart=<PROJECT_ROOT>/.venv/bin/streamlit run app/streamlit_app.py \
    --server.port=8501 \
    --server.address=127.0.0.1 \
    --server.headless=true \
    --server.maxUploadSize=200
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# Services aktivieren
systemctl daemon-reload
systemctl enable projekthub-backend projekthub-frontend
systemctl start projekthub-backend projekthub-frontend
```

### 9. Nginx konfigurieren

`/etc/nginx/sites-available/projekthub`:

```nginx
server {
    listen 80;
    server_name projekthub.deine-domain.de;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name projekthub.deine-domain.de;

    # SSL (nach certbot)
    ssl_certificate /etc/letsencrypt/live/projekthub.deine-domain.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/projekthub.deine-domain.de/privkey.pem;

    # Frontend
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }

    # Backend API
    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /docs {
        proxy_pass http://127.0.0.1:8000/docs;
        proxy_set_header Host $host;
    }
}
```

```bash
# Aktivieren
ln -s /etc/nginx/sites-available/projekthub /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 10. SSL-Zertifikat

```bash
certbot --nginx -d projekthub.deine-domain.de
```

---

## 🔄 Updates & Wartung

### Code aktualisieren

```bash
cd <PROJECT_ROOT>
sudo -u projekthub git pull
sudo -u projekthub .venv/bin/pip install -r requirements.txt
systemctl restart projekthub-backend projekthub-frontend
```

### Logs anzeigen

```bash
# Backend
journalctl -u projekthub-backend -f

# Frontend
journalctl -u projekthub-frontend -f

# Nginx
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

### Backup erstellen

```bash
sudo -u projekthub <PROJECT_ROOT>/scripts/maintenance/01_create_backup.ps1
# Oder manuell:
cp <PROJECT_ROOT>/data/db/slitproj.db <PROJECT_ROOT>/backups/backup-$(date +%Y%m%d).db
```

### Datenbank prüfen

```bash
sudo -u projekthub <PROJECT_ROOT>/.venv/bin/python <PROJECT_ROOT>/scripts/maintenance/check_db.py
```

---

## 🐛 Troubleshooting

### Services starten nicht

```bash
# Status prüfen
systemctl status projekthub-backend
systemctl status projekthub-frontend

# Logs prüfen
journalctl -u projekthub-backend -n 50
journalctl -u projekthub-frontend -n 50

# Manuell testen
sudo -u projekthub <PROJECT_ROOT>/.venv/bin/python <PROJECT_ROOT>/backend/main.py
```

### Port bereits belegt

```bash
# Prüfe was auf Port 8501/8000 läuft
ss -tulpn | grep :8501
ss -tulpn | grep :8000

# Prozess beenden
kill <PID>
```

### Nginx 502 Bad Gateway

```bash
# Backend läuft?
curl http://127.0.0.1:8000/docs

# Frontend läuft?
curl http://127.0.0.1:8501

# Services neustarten
systemctl restart projekthub-backend projekthub-frontend
```

### Permissions-Probleme

```bash
# Alles projekthub user zuweisen
chown -R projekthub:projekthub <PROJECT_ROOT>

# Logs-Ordner
chmod 755 <PROJECT_ROOT>/logs
```

---

## 🔒 Sicherheit

### Firewall (UFW)

```bash
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw enable
```

### Fail2Ban

```bash
apt-get install fail2ban
systemctl enable fail2ban
```

### Automatische Updates

```bash
apt-get install unattended-upgrades
dpkg-reconfigure --priority=low unattended-upgrades
```

---

## 📊 Monitoring

### Ressourcen überwachen

```bash
# Disk Space
df -h <PROJECT_ROOT>

# Memory
free -h

# CPU
top -u projekthub
```

### Systemd Timer für Backups

```bash
# /etc/systemd/system/projekthub-backup.timer
[Unit]
Description=Daily ProjektHub Backup

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

---

**Viel Erfolg beim Deployment! 🚀**
