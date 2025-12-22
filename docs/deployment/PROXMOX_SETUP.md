# 🚀 SCHRITT-FÜR-SCHRITT: Production auf Proxmox Debian VM

## 📋 Übersicht
Wir erstellen:
1. Debian VM in Proxmox
2. Docker installieren
3. SlitProjektHub deployen
4. Reverse Proxy konfigurieren (dein bestehender)
5. 2FA mit Authelia einrichten

---

## SCHRITT 1: Debian VM in Proxmox erstellen

### In Proxmox Web-UI:

1. **VM erstellen** (oder bestehende nutzen)
   - Rechtsklick auf Node → "Create VM"
   - VM ID: z.B. 150
   - Name: `projekthub`
   - OS: Debian 12 (Bookworm) ISO
   - Disk: 20 GB
   - CPU: 2 Cores
   - RAM: 4 GB
   - Netzwerk: vmbr0 (Bridge)

2. **VM starten und Debian installieren**
   - Standard-Installation
   - Hostname: `projekthub`
   - Benutzer anlegen: z.B. `admin`
   - SSH Server installieren: ✅ Ja

3. **IP-Adresse notieren**
   ```bash
   # In der VM-Konsole:
   ip addr show
   # Notiere die IP, z.B. 192.168.1.50
   ```

---

## SCHRITT 2: Von deinem Windows-PC auf die VM verbinden

```powershell
# SSH-Verbindung zur VM
ssh admin@192.168.1.50
```

**Ab jetzt alles in der VM ausführen!**

---

## SCHRITT 3: System vorbereiten

```bash
# Als root arbeiten
sudo su -

# System aktualisieren
apt update && apt upgrade -y

# Notwendige Pakete installieren
apt install -y \
    docker.io \
    docker-compose \
    git \
    curl \
    nano \
    net-tools

# Docker aktivieren
systemctl enable docker
systemctl start docker

# Docker-Status prüfen
systemctl status docker

# User zur Docker-Gruppe hinzufügen (optional)
usermod -aG docker admin
# Neu einloggen damit es wirkt!
```

---

## SCHRITT 4: Projekt auf VM übertragen

### Option A: Von GitHub/Git (wenn du ein Repo hast)
```bash
cd /opt
git clone https://dein-repo/SlitProjektHub.git projekthub
cd projekthub
```

### Option B: Von Windows per SCP übertragen
```powershell
# Auf deinem Windows-PC (PowerShell):
cd C:\Users\santinel\Documents\Apps\SlitProjektHub

# Mit SCP auf VM kopieren
scp -r * admin@192.168.1.50:/home/admin/projekthub/

# Dann in der VM nach /opt verschieben:
# ssh admin@192.168.1.50
# sudo mv /home/admin/projekthub /opt/
# sudo chown -R root:root /opt/projekthub
```

### Option C: Manuell mit WinSCP (einfachste Methode!)
1. WinSCP installieren: https://winscp.net/
2. Verbinden zu 192.168.1.50 (User: admin)
3. Lokales Verzeichnis: `C:\Users\santinel\Documents\Apps\SlitProjektHub`
4. Remote: `/opt/projekthub` erstellen und alles hochladen

---

## SCHRITT 5: Environment-Variablen einrichten

```bash
cd /opt/projekthub

# .env Datei erstellen
nano .env
```

**Inhalt von .env:**
```bash
# API Keys (DEINE echten Keys eintragen!)
OPENAI_API_KEY=sk-proj-...dein-key...
ANTHROPIC_API_KEY=sk-ant-...dein-key...
MISTRAL_API_KEY=...dein-key...

# Optionale Settings
PYTHONUNBUFFERED=1
```

Speichern: `Ctrl+O`, Enter, `Ctrl+X`

---

## SCHRITT 6: Authelia konfigurieren (2FA)

### 6.1 Secrets generieren
```bash
# JWT Secret generieren
openssl rand -base64 32
# Kopiere die Ausgabe, z.B.: kJ8n3K9mP2qR5sT7vW8xY1zA2bC3dE4fG5hI6jK7lM8=

# Session Secret generieren
openssl rand -base64 32
# Kopiere die Ausgabe, z.B.: nO9pQ1rS2tU3vW4xY5zA6bC7dE8fG9hI0jK1lM2nO3p=
```

### 6.2 Authelia-Config anpassen
```bash
nano authelia/configuration.yml
```

**Wichtige Zeilen ändern:**
```yaml
# Zeile 9: JWT Secret eintragen
jwt_secret: kJ8n3K9mP2qR5sT7vW8xY1zA2bC3dE4fG5hI6jK7lM8=

# Zeile 12: Deine Domain
default_redirection_url: https://projekthub.mainedomain.com

# Zeile 38-40: Deine Domain
access_control:
  default_policy: deny
  rules:
    - domain: projekthub.mainedomain.com
      policy: two_factor

# Zeile 45: Session Secret
session:
  secret: nO9pQ1rS2tU3vW4xY5zA6bC7dE8fG9hI0jK1lM2nO3p=
  domain: mainedomain.com  # Deine Hauptdomain!
```

### 6.3 Benutzer anlegen
```bash
# Passwort-Hash generieren
docker run authelia/authelia:latest authelia crypto hash generate argon2 --password 'DeinSicheresPasswort123!'

# Ausgabe kopieren, z.B.:
# $argon2id$v=19$m=65536,t=3,p=4$xyz...
```

```bash
# User-Datei bearbeiten
nano authelia/users_database.yml
```

**Deine User eintragen:**
```yaml
users:
  admin:
    displayname: "Administrator"
    password: "$argon2id$v=19$m=65536,t=3,p=4$xyz..." # DEIN Hash hier!
    email: admin@mainedomain.com
    groups:
      - admins

  benutzer1:
    displayname: "Max Mustermann"
    password: "$argon2id$v=19$m=65536,t=3,p=4$abc..." # Anderer Hash!
    email: max@mainedomain.com
    groups:
      - users

  benutzer2:
    displayname: "Maria Musterfrau"
    password: "$argon2id$v=19$m=65536,t=3,p=4$def..." # Anderer Hash!
    email: maria@mainedomain.com
    groups:
      - users
```

---

## SCHRITT 7: Container starten (ERSTE TEST ohne 2FA!)

```bash
cd /opt/projekthub

# Erstmal ohne Production testen
docker-compose up -d

# Status prüfen
docker-compose ps

# Sollte zeigen:
# NAME               STATUS    PORTS
# slit-projekthub    Up        0.0.0.0:8501->8501/tcp, 0.0.0.0:8000->8000/tcp

# Logs anschauen
docker-compose logs -f

# Wenn alles läuft (Ctrl+C zum Beenden)
```

**Browser-Test:**
- http://192.168.1.50:8501 → Sollte die App zeigen! ✅

**Wenn es funktioniert, Container stoppen:**
```bash
docker-compose down
```

---

## SCHRITT 8: Reverse Proxy einrichten

### Option A: Traefik (automatisch, empfohlen)

Wenn du bereits Traefik auf Proxmox hast:

```bash
# docker-compose.production.yml verwenden
nano docker-compose.production.yml
```

**Wichtig: Domain anpassen:**
```yaml
# Zeile 28-31: Deine Domain eintragen
labels:
  - "traefik.http.routers.projekthub.rule=Host(`projekthub.mainedomain.com`)"
  - "traefik.http.routers.authelia.rule=Host(`auth.mainedomain.com`)"
```

**Container starten:**
```bash
docker-compose -f docker-compose.production.yml up -d
```

### Option B: Nginx (dein bestehender Reverse Proxy)

Falls du Nginx Proxy Manager oder Nginx hast:

**In deinem Reverse Proxy:**
1. **Proxy Host erstellen**
   - Domain: `projekthub.mainedomain.com`
   - Forward to: `192.168.1.50:8501`
   - WebSocket Support: ✅ AN
   - SSL: Let's Encrypt

2. **Custom Nginx Config hinzufügen:**
```nginx
# WebSocket Support für Streamlit
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 86400;

# Headers
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

3. **Für Authelia (2FA):**
   - Domain: `auth.mainedomain.com`
   - Forward to: `192.168.1.50:9091`
   - SSL: Let's Encrypt

---

## SCHRITT 9: DNS konfigurieren

**Bei deinem Domain-Provider (z.B. Strato, IONOS, Cloudflare):**

Erstelle **A-Records**:
```
projekthub.mainedomain.com  →  DEINE-PUBLIC-IP
auth.mainedomain.com        →  DEINE-PUBLIC-IP
```

**Oder Subdomain bei Cloudflare:**
```
projekthub  →  DEINE-PUBLIC-IP  (Proxy: ☁️ Proxied)
auth        →  DEINE-PUBLIC-IP  (Proxy: ☁️ Proxied)
```

**DNS-Test (nach 5-10 Minuten):**
```bash
# Auf Windows-PC:
nslookup projekthub.mainedomain.com
# Sollte deine IP zeigen
```

---

## SCHRITT 10: Port-Forwarding im Router

**In deinem Router (z.B. Fritzbox, UniFi):**

Erstelle Port-Weiterleitung:
```
Extern Port 80  → 192.168.1.50:80   (HTTP)
Extern Port 443 → 192.168.1.50:443  (HTTPS)
```

**Oder wenn Reverse Proxy auf anderem Server läuft:**
```
Extern Port 80/443 → IP-von-Reverse-Proxy
```

---

## SCHRITT 11: Testen & 2FA einrichten

1. **Browser öffnen:**
   ```
   https://projekthub.mainedomain.com
   ```

2. **Umleitung zu Authelia:**
   - Du wirst zu `https://auth.mainedomain.com` umgeleitet
   - Login mit User/Passwort (aus users_database.yml)

3. **2FA einrichten:**
   - QR-Code wird angezeigt
   - Mit Google Authenticator/Authy scannen
   - 6-stelligen Code eingeben

4. **Fertig!** Du bist eingeloggt und siehst SlitProjektHub ✅

---

## 🔧 Troubleshooting

### Container startet nicht
```bash
docker-compose logs projekthub
# Häufig: API-Keys fehlen in .env
```

### "Connection refused" beim Zugriff
```bash
# Firewall prüfen
ufw status
ufw allow 8501
ufw allow 8000
ufw allow 9091

# Container läuft?
docker ps

# Port lauscht?
netstat -tulpn | grep 8501
```

### DNS funktioniert nicht
```bash
# Warte 10-15 Minuten
# DNS-Cache leeren (Windows):
ipconfig /flushdns

# Testen:
ping projekthub.mainedomain.com
```

### 2FA-Code wird nicht akzeptiert
```bash
# Zeit auf VM synchronisieren (wichtig!)
apt install ntpdate
ntpdate pool.ntp.org

# Authelia neustarten
docker-compose restart authelia
```

### WebSocket-Fehler (Streamlit lädt nicht)
```nginx
# In Nginx Proxy Config ergänzen:
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 86400;
```

---

## 📝 Nützliche Befehle

```bash
# Container-Status
docker-compose ps

# Logs anschauen
docker-compose logs -f projekthub
docker-compose logs -f authelia

# Container neustarten
docker-compose restart

# Container stoppen
docker-compose down

# Container + Neu bauen
docker-compose up -d --build

# Alle Container löschen (VORSICHT!)
docker-compose down -v

# In Container reingehen (Debug)
docker exec -it slit-projekthub bash
```

---

## 🔐 Nach dem Setup

### Backup einrichten
```bash
# Backup-Script erstellen
nano /opt/backup-projekthub.sh
```

```bash
#!/bin/bash
BACKUP_DIR="/backup/projekthub"
DATE=$(date +%Y%m%d_%H%M%S)
cd /opt/projekthub

mkdir -p $BACKUP_DIR
tar -czf $BACKUP_DIR/data_$DATE.tar.gz data/
tar -czf $BACKUP_DIR/config_$DATE.tar.gz config/
tar -czf $BACKUP_DIR/authelia_$DATE.tar.gz authelia/

# Alte Backups löschen (älter als 30 Tage)
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete

echo "Backup erstellt: $DATE"
```

```bash
# Ausführbar machen
chmod +x /opt/backup-projekthub.sh

# Cronjob (täglich um 2 Uhr)
crontab -e
# Eintragen:
0 2 * * * /opt/backup-projekthub.sh
```

### Updates durchführen
```bash
cd /opt/projekthub
git pull  # Falls Git-Repo
docker-compose down
docker-compose up -d --build
```

---

## ✅ Fertig!

Deine App läuft jetzt auf:
- 🌐 https://projekthub.mainedomain.com
- 🔒 Mit 2FA-Login über https://auth.mainedomain.com
- 👥 Mehrere Benutzer können sich einloggen
- 💾 Daten werden persistent gespeichert

**Viel Erfolg!** 🚀
