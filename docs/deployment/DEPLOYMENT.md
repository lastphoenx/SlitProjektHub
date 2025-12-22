# SlitProjektHub - Deployment-Anleitung

## **A) Windows - Desktop Start (Doppelklick)**

1. **Doppelklick auf `start_app.bat`**
   - VirtualEnv wird automatisch erstellt
   - Dependencies werden installiert
   - App startet auf `http://localhost:8501`

---

## **B) Server-Deployment (Debian/Proxmox - Multi-User)**

### **Option B1: Docker (Empfohlen)**

**Auf dem Server (Debian):**

```bash
# 1. Docker installieren
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# 2. Projekt klonen/hochladen
git clone <repo-url> <PROJECT_ROOT>
cd <PROJECT_ROOT>

# 3. Environment vorbereiten
cp .env.docker .env.docker.prod
nano .env.docker.prod  # API-Keys eintragen

# 4. Container starten
docker-compose up -d

# 5. Logs prüfen
docker-compose logs -f

# 6. Im Browser öffnen
# http://<server-ip>:8501
```

**Alle Haushalts-Nutzer können dann zugreifen:**
- Lokal: `http://192.168.x.x:8501`
- Remote (mit Port-Freigabe): `http://<public-ip>:8501`

---

### **Option B2: Systemd Service (Debian)**

Wenn Docker nicht gewünscht ist:

**`/etc/systemd/system/projekthub.service`:**
```ini
[Unit]
Description=SlitProjektHub
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=<PROJECT_ROOT>
Environment="PATH=<PROJECT_ROOT>/.venv/bin"
ExecStart=<PROJECT_ROOT>/.venv/bin/streamlit run app/streamlit_app.py --server.port=8501 --server.address=0.0.0.0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Aktivieren:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable projekthub
sudo systemctl start projekthub
```

---

## **C) Reverse Proxy + Auth + 2FA (Domain)**

### **Setup: nginx + Keycloak/Authelia + Reverse Proxy**

**1. Reverse Proxy konfigurieren (nginx)**

`/etc/nginx/sites-available/projekthub`:
```nginx
upstream streamlit {
    server localhost:8501;
}

server {
    listen 443 ssl http2;
    server_name projekthub.<YOUR_DOMAIN>;

    ssl_certificate /etc/letsencrypt/live/<YOUR_DOMAIN>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<YOUR_DOMAIN>/privkey.pem;

    # Authelia (2FA) prüfen
    location / {
        auth_request /authelia;
        auth_request_set $user $upstream_http_remote_user;
        proxy_set_header Remote-User $user;

        proxy_pass http://streamlit;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Streamlit WebSocket
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Authelia Endpoint
    location /authelia {
        internal;
        proxy_pass http://localhost:9091/api/verify;
        proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
    }

    # Authelia Public Routes
    location /authelia/api {
        proxy_pass http://localhost:9091;
    }
}

# HTTP redirect
server {
    listen 80;
    server_name projekthub.<YOUR_DOMAIN>;
    return 301 https://$server_name$request_uri;
}
```

**2. Authelia installieren (2FA + User-Verwaltung)**

`docker-compose.yml` (erweitert):
```yaml
version: '3.8'

services:
  projekthub:
    image: slit-projekthub
    ports:
      - "8501:8501"
    environment:
      - STREAMLIT_SERVER_HEADLESS=true
    networks:
      - projekthub-net

  authelia:
    image: authelia/authelia:latest
    container_name: authelia
    ports:
      - "9091:9091"
    volumes:
      - ./authelia/configuration.yml:/config/configuration.yml
      - ./authelia/users_database.yml:/config/users_database.yml
      - ./authelia/db:/var/lib/authelia/db
    environment:
      - TZ=Europe/Berlin
    networks:
      - projekthub-net
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: redis-authelia
    ports:
      - "6379:6379"
    networks:
      - projekthub-net
    restart: unless-stopped

networks:
  projekthub-net:
```

**3. Authelia Configuration**

`authelia/configuration.yml`:
```yaml
theme: dark
default_redirection_url: https://projekthub.<YOUR_DOMAIN>

server:
  host: 0.0.0.0
  port: 9091
  asset_path: /config/assets/

log:
  level: info

totp:
  issuer: SlitProjektHub
  period: 30
  skew: 1

session:
  domain: <YOUR_DOMAIN>
  name: authelia_session
  same_site: lax
  secret: <generate-random-32-char>
  expiration: 3600
  inactivity: 300
  remember_me: 1800

authentication_backend:
  file:
    path: /config/users_database.yml

access_control:
  default_policy: deny
  rules:
    - domain: projekthub.<YOUR_DOMAIN>
      policy: two_factor
      networks:
      - 0.0.0.0/0

storage:
  encryption_key: <generate-random-32-char>
  local:
    path: /var/lib/authelia/db/db.sqlite3

notifier:
  disable_startup_check: false
  smtp:
    host: smtp.gmail.com
    port: 587
    username: noreply@<YOUR_DOMAIN>
    password: <app-password>
    sender: noreply@<YOUR_DOMAIN>
    subject: "[ProjektHub] {title}"
    startup_check_address: test@<YOUR_DOMAIN>
```

**4. Benutzer definieren**

`authelia/users_database.yml`:
```yaml
users:
  <username>:
    displayname: "Your Name"
    password: "$argon2id$..." # bcrypt/argon2 hash
    email: <username>@<YOUR_DOMAIN>
    groups:
      - admins

  partner:
    displayname: "Partner Name"
    password: "$argon2id$..."
    email: partner@<YOUR_DOMAIN>
    groups:
      - users
```

---

## **Schnell-Übersicht:**

| Szenario | Lösung | URL | Auth |
|----------|--------|-----|------|
| **Lokal (Windows)** | `start_app.bat` | `localhost:8501` | Keine |
| **Haushalt (LAN)** | Docker/Systemd | `192.168.x.x:8501` | Keine |
| **Domain + Sicherheit** | Docker + nginx + Authelia | `https://projekthub.<YOUR_DOMAIN>` | 2FA ✅ |

---

## **Fragen?**

Welches Szenario interessiert dich am meisten? Ich helfe beim Setup! 🚀
