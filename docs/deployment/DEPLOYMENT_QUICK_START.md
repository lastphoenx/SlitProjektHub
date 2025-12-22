# ProjektHub - Production Deployment Quick Start

## **Voraussetzungen**
- Debian/Ubuntu Server mit Docker & Docker Compose
- Domain mit Zugriff auf DNS (für SSL-Zertifikat)
- Reverse Proxy (Traefik oder nginx)

---

## **Option A: Mit Traefik (einfachste Lösung)**

### **Step 1: Secrets generieren**

```bash
cd <PROJECT_ROOT>

# Generate JWT & Session Secrets
JWT_SECRET=$(openssl rand -base64 32)
SESSION_SECRET=$(openssl rand -base64 32)

# Copy template
cp .env.production.template .env.production

# Edit with your API keys
nano .env.production
```

### **Step 2: Authelia Setup**

```bash
# Create directories
mkdir -p authelia/db
touch authelia/db.sqlite3
chmod 600 authelia/db.sqlite3

# Generate password hashes
docker run authelia/authelia:latest authelia crypto hash generate argon2
# Enter your passwords when prompted
# Copy the hashes to authelia/users_database.yml
```

**Beispiel:** Wenn du "admin123" eingibst, erhältst du:
```
Output: $argon2id$v=19$m=65536,t=3,p=4$BpLnH2c99yU1NRBWJpzKGA$Xxssfg2+gzwFPEqsNFTBVRPUOW0pCBILBQVMZ9KG9Ys
```

Setze diesen Hash in `authelia/users_database.yml`:

```yaml
users:
  admin:
    displayname: "Administrator"
    password: "$argon2id$v=19$m=65536,t=3,p=4$BpLnH2c99yU1NRBWJpzKGA$..." # ← hier einfügen
    email: admin@<YOUR_DOMAIN>
    groups:
      - admins
```

### **Step 3: Secrets in Config eintragen**

```bash
# Update configuration.yml with secrets
sed -i "s|your-very-secure-random-jwt-secret-here-min-32-chars|$JWT_SECRET|g" authelia/configuration.yml
sed -i "s|another-very-secure-random-session-secret-min-32-chars|$SESSION_SECRET|g" authelia/configuration.yml
```

### **Step 4: Docker starten**

```bash
# Build & start containers
docker-compose -f docker-compose.production.yml up -d

# Check logs
docker-compose -f docker-compose.production.yml logs -f

# Check health
docker-compose -f docker-compose.production.yml ps
```

### **Step 5: Traefik-Netzwerk aktivieren** (falls extern)

Wenn Traefik bereits läuft:

```bash
# Get Traefik network
docker network ls | grep traefik

# Update docker-compose.production.yml:
# networks:
#   projekthub-net:
#     external: true
#     name: traefik
```

---

## **Option B: Mit existing nginx Reverse Proxy**

Falls du bereits einen nginx Reverse Proxy hast:

### **Step 1: Authelia Container starten (ohne Traefik)**

Modifizierte `docker-compose.production.yml` (ohne Traefik Labels):

```yaml
version: '3.8'

services:
  projekthub:
    build: .
    container_name: slit-projekthub
    ports:
      - "127.0.0.1:8501:8501"
      - "127.0.0.1:8000:8000"
    environment:
      - STREAMLIT_SERVER_HEADLESS=true
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    restart: unless-stopped

  authelia:
    image: authelia/authelia:latest
    container_name: authelia
    ports:
      - "127.0.0.1:9091:9091"
    volumes:
      - ./authelia:/config
    environment:
      - TZ=Europe/Berlin
    restart: unless-stopped

networks:
  default:
    name: custom-network
```

### **Step 2: nginx konfigurieren**

```bash
# Copy nginx config
sudo cp nginx.conf.example /etc/nginx/sites-available/projekthub

# Edit domain
sudo nano /etc/nginx/sites-available/projekthub

# Enable site
sudo ln -s /etc/nginx/sites-available/projekthub /etc/nginx/sites-enabled/

# Test config
sudo nginx -t

# Reload
sudo systemctl reload nginx
```

### **Step 3: SSL-Zertifikat (Let's Encrypt)**

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificate
sudo certbot certonly --nginx -d <YOUR_DOMAIN> -d *.<YOUR_DOMAIN>

# Auto-renew
sudo systemctl enable certbot.timer
```

### **Step 4: Docker starten**

```bash
docker-compose up -d

# Prüfe Logs
docker-compose logs -f authelia
docker-compose logs -f projekthub
```

---

## **Benutzer hinzufügen/ändern**

### **Passwort-Hash generieren**

```bash
docker run authelia/authelia:latest authelia crypto hash generate argon2

# Prompt erscheint:
# Enter password: your_password
# Confirm password: your_password
# Output: $argon2id$v=19$...
```

### **Benutzer in `authelia/users_database.yml` hinzufügen**

```yaml
users:
  <username>:
    displayname: "Your Name"
    password: "$argon2id$v=19$..." # ← generierter Hash
    email: <username>@<YOUR_DOMAIN>
    groups:
      - admins
  
  partner:
    displayname: "Partner"
    password: "$argon2id$v=19$..."
    email: partner@<YOUR_DOMAIN>
    groups:
      - users
```

### **Container neu starten**

```bash
docker-compose -f docker-compose.production.yml restart authelia
```

---

## **2FA Setup (TOTP)**

Nach dem Login wird dem Benutzer ein QR-Code angezeigt für:
- **Google Authenticator**
- **Authy**
- **Microsoft Authenticator**
- Jeder anderen TOTP-App

---

## **Troubleshooting**

### **"503 Service Unavailable"**
```bash
# Check if containers are running
docker-compose ps

# Check logs
docker-compose logs projekthub
docker-compose logs authelia
```

### **Authelia zeigt "unauthorized"**
```bash
# Verify users_database.yml is valid YAML
docker run authelia/authelia:latest authelia validate-config /config/configuration.yml

# Check password hash format
docker run authelia/authelia:latest authelia crypto hash validate argon2 '$argon2id$...'
```

### **WebSocket-Fehler in Streamlit**
```bash
# Prüfe nginx proxy_read_timeout (mindestens 60s)
# Prüfe Upgrade-Header werden weitergeleitet
```

### **SSL-Fehler**
```bash
# Test cert
sudo openssl x509 -in /etc/letsencrypt/live/<YOUR_DOMAIN>/fullchain.pem -text -noout

# Renew manuell
sudo certbot renew --dry-run
```

---

## **URLs nach Setup**

| Service | URL | Authentifizierung |
|---------|-----|-------------------|
| **ProjektHub** | `https://projekthub.<YOUR_DOMAIN>` | 2FA ✅ |
| **Authelia Dashboard** | `https://auth.<YOUR_DOMAIN>` | 2FA ✅ |
| **Backend API** | `https://projekthub.<YOUR_DOMAIN>/api/docs` | 2FA ✅ |

---

## **Monitoring & Logs**

```bash
# Real-time logs
docker-compose logs -f

# Spezifisches Service
docker-compose logs -f authelia

# Prüfe System-Ressourcen
docker stats

# Backup Database
docker exec authelia sqlite3 /config/db.sqlite3 .dump > backup.sql
```

---

## **Sicherheits-Checkliste**

- [ ] `.env.production` hat echte API-Keys
- [ ] Passwort-Hashes sind aktualisiert
- [ ] JWT_SECRET & SESSION_SECRET sind eindeutig generiert
- [ ] SSL-Zertifikat ist gültig
- [ ] SMTP-Settings für Email-Benachrichtigungen (optional)
- [ ] Firewall: Nur Port 80/443 offen
- [ ] Regelmäßige Backups der Datenbanken
- [ ] 2FA für alle Benutzer aktiviert

---

## **Backup & Recovery**

```bash
# Backup Datenbanken
docker exec slit-projekthub sqlite3 /app/data/db/sph.db .dump > backup_app.sql
docker exec authelia sqlite3 /config/db.sqlite3 .dump > backup_auth.sql

# Backup Konfiguration
tar czf config_backup.tar.gz ./authelia ./config

# Restore (nach Container-Neustart)
docker cp backup_auth.sql authelia:/tmp/
docker exec authelia sqlite3 /config/db.sqlite3 < /tmp/backup_auth.sql
```

---

## **Support & Updates**

```bash
# Update Container Images
docker-compose -f docker-compose.production.yml pull
docker-compose -f docker-compose.production.yml up -d

# Prüfe Authelia-Updates
docker pull authelia/authelia:latest
```

Viel Erfolg! 🚀
