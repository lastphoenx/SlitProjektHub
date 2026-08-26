# Server-Deployment (Proxmox / LXC)

Anleitung für den Betrieb auf einem Heimserver mit bestehendem Proxmox + nginx Reverse Proxy + Authentik.

## Voraussetzungen

- Proxmox mit laufendem nginx Reverse Proxy und Authentik
- Domain mit DNS-Eintrag auf die Server-IP
- SSL via nginx (Let's Encrypt oder eigenes Zertifikat)

---

## 1. LXC erstellen

In Proxmox: Ubuntu 24.04 LXC, empfohlene Ressourcen:

| Ressource | Minimum | Empfohlen |
|-----------|---------|-----------|
| RAM | 2 GB | 4 GB |
| Disk | 10 GB | 20 GB |
| vCPU | 1 | 2 |

> **RAM-Hinweis**: spaCy `de_core_news_sm` belegt beim ersten BM25-Aufruf ~200 MB RAM (Singleton, bleibt danach geladen). Minimum daher 2 GB.

---

## 2. Python + App installieren

```bash
apt update && apt install -y python3.11 python3.11-venv git

git clone https://github.com/lastphoenx/SlitProjektHub.git /opt/slitprojekthub
cd /opt/slitprojekthub

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download de_core_news_sm
# PII Stufe 2 (Cloud-Prompts) — spaCy lg + Flair (~400 MB, Internet nötig)
.venv/bin/python scripts/maintenance/prefetch_pii_models.py

cp .env.example .env
nano .env   # API Keys eintragen
```

---

## 3. Systemd-Services

### Backend (FastAPI auf Port 8000)

Datei: `/etc/systemd/system/slitproj-backend.service`

```ini
[Unit]
Description=SlitProjektHub Backend
After=network.target

[Service]
User=root
WorkingDirectory=/opt/slitprojekthub
ExecStart=/opt/slitprojekthub/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Frontend (Streamlit auf Port 8501)

Datei: `/etc/systemd/system/slitproj-frontend.service`

```ini
[Unit]
Description=SlitProjektHub Frontend
After=network.target slitproj-backend.service

[Service]
User=root
WorkingDirectory=/opt/slitprojekthub
ExecStart=/opt/slitprojekthub/.venv/bin/streamlit run app/streamlit_app.py --server.port 8501 --server.headless true --server.address 127.0.0.1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Aktivieren

```bash
systemctl daemon-reload
systemctl enable --now slitproj-backend slitproj-frontend
systemctl status slitproj-backend slitproj-frontend
```

---

## 4. nginx Proxy Provider in Authentik

1. In Authentik: **Providers → Proxy Provider erstellen**
   - Typ: **Forward Auth (Single Application)**
   - External Host: `https://slitproj.deine-domain.ch`
   - Internal Host: `http://<LXC-IP>:8501`

2. **Application erstellen** → Provider zuweisen

3. **Outpost** → Proxy Provider hinzufügen, Outpost neu starten

---

## 5. nginx Location-Block

Im nginx-Config für die Domain (Beispiel für Authentik Forward Auth):

```nginx
location / {
    auth_request     /outpost.goauthentik.io/auth/nginx;
    error_page 401 = @goauthentik_proxy_signin;
    auth_request_set $auth_cookie $upstream_http_set_cookie;
    add_header       Set-Cookie $auth_cookie;

    proxy_pass http://<LXC-IP>:8501;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 86400;
}

# Streamlit WebSocket (zwingend)
location /_stcore/stream {
    proxy_pass http://<LXC-IP>:8501/_stcore/stream;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
}
```

> **Wichtig:** Streamlit benötigt WebSocket-Support (`Upgrade`-Header). Ohne den zweiten `location`-Block friert die App nach dem Login ein.

---

## 7. Cloud-PII Stufe 2 (optional testen)

Nach `prefetch_pii_models.py` und Backend-Neustart:

```bash
cd /opt/slitprojekthub
source .venv/bin/activate

# Nur Stufe 1+2 Pipeline (ohne LLM)
.venv/bin/python -c "
from src.m16_idea_visual import sanitize_for_cloud_text
t = 'Kontakt Maria Muster, herr.schmidt@beispiel.ch, AHV 756.1234.5678.97'
print(sanitize_for_cloud_text(t))
"

# Direkt swiss-pii-anonymizer
.venv/bin/python -c "
from swiss_pii_anonymizer import anonymize
r = anonymize('Maria Muster plant den Digitalen Sportpass.')
print(r.text)
for f in r.findings:
    print(f.entity_type, f.text, f.score)
"

# Unit-Tests (mock, kein Flair nötig)
.venv/bin/python scripts/testing/test_cloud_pii.py
```

`SWISS_PII_ANONYMIZER=0` in `.env` schaltet Stufe 2 ab (nur Regex-Stufe 1).

---

## 6. SQLite WAL-Modus

Für mehrere gleichzeitige Benutzer ist SQLite WAL-Modus aktiv (Standard in `config/config.yaml`):

```yaml
database:
  wal_mode: true
```

Nebeneffekt: Neben `slitproj.db` entstehen `slitproj.db-wal` und `slitproj.db-shm`. Das ist normal.  
Bei Backups immer alle drei Dateien gemeinsam kopieren.  
Zum Deaktivieren: `wal_mode: false`.

---

## 8. Updates einspielen

```bash
cd /opt/slitprojekthub
source .venv/bin/activate
git pull
pip install -r requirements.txt   # nur nötig wenn requirements geändert
systemctl restart slitproj-backend slitproj-frontend
```
