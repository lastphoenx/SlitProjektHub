#!/bin/bash
# Native Debian Deployment Script (ohne Docker)
# Für SlitProjektHub auf Debian/Ubuntu Server

set -e

INSTALL_DIR="/opt/projekthub"
SERVICE_USER="projekthub"
PYTHON_VERSION="3.11"

echo "╔═══════════════════════════════════════════════════════╗"
echo "║  🚀 SlitProjektHub - Native Debian Deployment        ║"
echo "╚═══════════════════════════════════════════════════════╝"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Bitte als root ausführen: sudo ./deploy-debian.sh"
    exit 1
fi

echo "📦 System Update & Dependencies installieren..."
apt-get update
apt-get install -y python3.11 python3.11-venv python3-pip sqlite3 nginx certbot python3-certbot-nginx git

echo "👤 Service-User erstellen..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd -r -s /bin/bash -d $INSTALL_DIR $SERVICE_USER
fi

echo "📁 Installationsverzeichnis vorbereiten..."
mkdir -p $INSTALL_DIR
chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR

echo "📥 Repository clonen..."
cd $INSTALL_DIR
if [ -d ".git" ]; then
    sudo -u $SERVICE_USER git pull
else
    # Hier deine GitHub URL eintragen
    sudo -u $SERVICE_USER git clone https://github.com/USER/SlitProjektHub.git .
fi

echo "🐍 Python Virtual Environment erstellen..."
sudo -u $SERVICE_USER python3.11 -m venv .venv
sudo -u $SERVICE_USER .venv/bin/pip install --upgrade pip
sudo -u $SERVICE_USER .venv/bin/pip install -r requirements.txt

echo "📂 Verzeichnisse erstellen..."
sudo -u $SERVICE_USER mkdir -p data/db data/rag config logs backups

echo "⚙️ Systemd Services erstellen..."

# Backend Service
cat > /etc/systemd/system/projekthub-backend.service << 'EOF'
[Unit]
Description=ProjektHub FastAPI Backend
After=network.target

[Service]
Type=simple
User=projekthub
WorkingDirectory=/opt/projekthub/backend
Environment="PATH=/opt/projekthub/.venv/bin"
EnvironmentFile=/opt/projekthub/.env
ExecStart=/opt/projekthub/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Frontend Service
cat > /etc/systemd/system/projekthub-frontend.service << 'EOF'
[Unit]
Description=ProjektHub Streamlit Frontend
After=network.target projekthub-backend.service

[Service]
Type=simple
User=projekthub
WorkingDirectory=/opt/projekthub
Environment="PATH=/opt/projekthub/.venv/bin"
EnvironmentFile=/opt/projekthub/.env
ExecStart=/opt/projekthub/.venv/bin/streamlit run app/streamlit_app.py --server.port=8501 --server.address=127.0.0.1 --server.headless=true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "🔄 Systemd Services aktivieren..."
systemctl daemon-reload
systemctl enable projekthub-backend projekthub-frontend

echo "🌐 Nginx konfigurieren..."
cat > /etc/nginx/sites-available/projekthub << 'EOF'
server {
    listen 80;
    server_name projekthub.deine-domain.de;

    # Redirect to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name projekthub.deine-domain.de;

    # SSL certificates (nach certbot Setup)
    ssl_certificate /etc/letsencrypt/live/projekthub.deine-domain.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/projekthub.deine-domain.de/privkey.pem;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    # Streamlit Frontend
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Streamlit WebSocket
        proxy_read_timeout 86400;
    }

    # FastAPI Backend
    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Backend Docs
    location /docs {
        proxy_pass http://127.0.0.1:8000/docs;
        proxy_set_header Host $host;
    }
}
EOF

# Nginx aktivieren
ln -sf /etc/nginx/sites-available/projekthub /etc/nginx/sites-enabled/
nginx -t

echo ""
echo "╔═══════════════════════════════════════════════════════╗"
echo "║  ✅ Installation abgeschlossen!                       ║"
echo "╚═══════════════════════════════════════════════════════╝"
echo ""
echo "📋 Nächste Schritte:"
echo ""
echo "1. .env Datei erstellen und konfigurieren:"
echo "   sudo -u projekthub nano $INSTALL_DIR/.env"
echo "   # API Keys eintragen: OPENAI_API_KEY, ANTHROPIC_API_KEY, etc."
echo ""
echo "2. Datenbank importieren (falls Migration):"
echo "   sudo -u projekthub cp /tmp/backup.db $INSTALL_DIR/data/db/"
echo ""
echo "3. Datenbank initialisieren:"
echo "   sudo -u projekthub $INSTALL_DIR/.venv/bin/python -c 'from src.m03_db import init_db; init_db()'"
echo ""
echo "4. Services starten:"
echo "   systemctl start projekthub-backend"
echo "   systemctl start projekthub-frontend"
echo ""
echo "5. Status prüfen:"
echo "   systemctl status projekthub-backend"
echo "   systemctl status projekthub-frontend"
echo "   journalctl -u projekthub-frontend -f"
echo ""
echo "6. SSL-Zertifikat erstellen:"
echo "   certbot --nginx -d projekthub.deine-domain.de"
echo ""
echo "7. Nginx neustarten:"
echo "   systemctl restart nginx"
echo ""
echo "🌐 Zugriff: https://projekthub.deine-domain.de"
echo ""
