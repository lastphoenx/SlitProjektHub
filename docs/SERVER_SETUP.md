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
| RAM | 2 GB | 4 GB (8 GB mit Cloud-PII Stufe 2) |
| Disk | 10 GB | 25 GB (Torch/PII-Modelle) |
| vCPU | 1 | 2 |

> **RAM-Hinweis**: spaCy `de_core_news_sm` belegt beim ersten BM25-Aufruf ~200 MB RAM (Singleton, bleibt danach geladen). Minimum daher 2 GB.

---

## 2. Python + App installieren

```bash
apt update && apt install -y python3.11 python3.11-venv git

# Optional, aber empfohlen für PDF-Export Offertbeurteilung (WeasyPrint) — Details §9
# apt install -y libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 libcairo2 \
#   libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info fonts-dejavu-core

git clone https://github.com/lastphoenx/SlitProjektHub.git /opt/slitprojekthub
cd /opt/slitprojekthub

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download de_core_news_sm
# Optional Cloud-PII Stufe 2 — siehe docs/SERVER_SETUP.md Abschnitt 7
# export HF_HOME=/opt/slitprojekthub/.hf_cache
# .venv/bin/python scripts/maintenance/prefetch_pii_models.py

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

## 7. Cloud-PII Stufe 2 (Presidio + Flair)

Optional: anonymisiert Cloud-Prompts vor OpenAI/Anthropic (ergänzt Regex-Stufe 1 in
`sanitize_for_cloud_text()`). Paket: `swiss-pii-anonymizer` (in `requirements.txt`).
Stufe-1-Regex zentral in `src/m20_pii_stage1.py` (alle Cloud-Pfade: Offerte, Ideen, Visual-Lab, `/sanitize`).

### Was wird geladen?

| Komponente | Zweck | Größe (ca.) |
|------------|--------|-------------|
| spaCy `de_core_news_lg` | Presidio NLP | ~570 MB |
| `flair/ner-german-large` | Personen-NER (Flair-Gewichte) | ~2.1 GB |
| `FacebookAI/xlm-roberta-large` | Basis-Transformer für `ner-german-large` (Tokenizer/Config) | ~15 MB + Blobs im Cache |
| PyTorch (via Flair) | Laufzeit | bereits in venv |

**Wichtig:** Nur der Flair-NER-Checkpoint reicht nicht — `ner-german-large` (FLERT) braucht
`xlm-roberta-large` aus dem HuggingFace-Hub-Cache. Ohne dieses Basis-Modell schlägt Offline-Laden fehl.

Alternative **`flair/ner-german`**: kleiner, kein `xlm-roberta`, weniger RAM — etwas schwächer bei Namen.

### RAM & Disk

| Modell | RAM-Spitze (laden) | HF-Cache (ca.) |
|--------|-------------------|----------------|
| `ner-german-large` | oft **>6 GB** | ~2.2 GB unter `HF_HOME/hub/` |
| `ner-german` | oft **>4 GB** | deutlich kleiner |

Symptom OOM: Prozess endet mit `Getötet`. Abhilfe: CT-RAM 8 GB, Swap 2–4 GB, oder `FLAIR_NER_MODEL=flair/ner-german`.

Disk: zusätzlich ~3 GB für venv (Torch/CUDA-Pakete) — CT-Disk **≥20 GB** empfohlen.

### systemd & Cache-Pfade

Der Backend-Service (`ProtectHome=true`, `User=projekthub`) kann **nicht** lesen:

- `/root/.flair`
- `/root/.cache/huggingface`

Caches müssen unter **`APP_ROOT`** liegen, mit Rechten für den Service-User:

```text
/opt/slitprojekthub/.hf_cache/hub/models--flair--ner-german-large/
/opt/slitprojekthub/.hf_cache/hub/models--FacebookAI--xlm-roberta-large/
```

### Installation (einmalig, mit Internet)

```bash
cd /opt/slitprojekthub
free -h

pip install -r requirements.txt   # falls noch nicht

export HF_HOME=/opt/slitprojekthub/.hf_cache
# Optional kleines Modell:
# export FLAIR_NER_MODEL=flair/ner-german

.venv/bin/python scripts/maintenance/prefetch_pii_models.py

chown -R projekthub:projekthub /opt/slitprojekthub/.hf_cache
du -sh /opt/slitprojekthub/.hf_cache/hub/
```

Das Prefetch-Skript lädt spaCy lg, bei `ner-german-large` auch `xlm-roberta-large`, dann Flair + Smoke-Test.

### `.env` (Produktion, nach Prefetch)

```bash
SWISS_PII_ANONYMIZER=1
HF_HUB_OFFLINE=1
HF_HOME=/opt/slitprojekthub/.hf_cache
# Optional kleines Flair-Modell:
# FLAIR_NER_MODEL=flair/ner-german
# Optional Circuit-Breaker nach Fehler (Sekunden, Default 300):
# PII_CIRCUIT_BREAKER_SECONDS=300
```

`SWISS_PII_ANONYMIZER=0` schaltet Stufe 2 ab (nur Regex-Stufe 1).

### systemd (`/etc/systemd/system/projekthub-backend.service`)

Zusätzlich zu `EnvironmentFile=APP_ROOT/.env` (empfohlen, explizit):

```ini
Environment=APP_ROOT=/opt/slitprojekthub
Environment=HF_HOME=/opt/slitprojekthub/.hf_cache
Environment=HF_HUB_OFFLINE=1
```

Vorlage: `deployment/systemd/projekthub-backend.service.example`

```bash
systemctl daemon-reload
systemctl restart projekthub-backend
sleep 30
journalctl -u projekthub-backend -n 30 --no-pager | grep -i pii
```

Erwartung: `PII-Stufe 2 (Presidio+Flair) bereit`

Das Backend lädt Modelle beim Start im Hintergrund (`warmup_pii_analyzer`).

### Verifizieren

```bash
# Als Service-User (offline)
sudo -u projekthub env \
  HF_HOME=/opt/slitprojekthub/.hf_cache \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  PYTHONPATH=/opt/slitprojekthub \
  /opt/slitprojekthub/.venv/bin/python -c "
from src.m16_idea_visual import sanitize_for_cloud_text
print(sanitize_for_cloud_text('Kontakt Maria Muster, AHV 756.1234.5678.97'))
"

.venv/bin/python scripts/testing/test_cloud_pii.py
.venv/bin/python scripts/testing/test_evaluation.py
```

Erwartung: `[PERSON]`, `[CH_AHV_NR]` (nicht nur Regex-Stufe 1).

Web-UI: unter **PII-Sanitizer** (`/sanitize`) Text oder PDF hochladen — gleiche Pipeline, mit Entitäten-Vorschau. Bei langen PDFs **Seiten von/bis** wählen (z. B. 1–20, dann 21–40); `SANITIZE_MAX_PDF_PAGES` begrenzt die Spanne pro Lauf.

**Offertbeurteilung** (`/evaluation`): Phase ① `tender_role` setzen; Chunk ↻ re-indexieren.
Zweistufiges Ranking: Zuschlagskriterium «A-01» o. ä. als **Phase 2 (Präsentation)** markieren →
Zwischenrangliste vor Einladung. Cloud-KI mit Bieter-Docs: PII-Checkbox + Sanitizer (Stufe 1+2).

Optional in `.env` (Schutz vor OOM bei grossen PDFs):

```bash
SANITIZE_MAX_CHARS=50000
SANITIZE_MAX_PDF_PAGES=20
SANITIZE_MAX_FILE_BYTES=15728640
```

Bei **502 Bad Gateway** auf `/sanitize`: meist OOM-Kill (`journalctl -u projekthub-backend | grep -i kill`) — RAM/Swap erhöhen (CT: `pct set <id> -memory 12288 -swap 4096`) oder Limits senken.

### Troubleshooting

| Symptom | Ursache | Fix |
|---------|---------|-----|
| `No such file ... .flair/models/ner-german-large` | Cache nicht unter APP_ROOT | Prefetch mit `HF_HOME` oder kopieren + `chown projekthub` |
| HF-Fehler offline, nur `pytorch_model.bin` im Cache | `xlm-roberta-large` fehlt | Prefetch erneut mit Internet oder `AutoTokenizer.from_pretrained('FacebookAI/xlm-roberta-large')` |
| `Getötet` beim Prefetch | OOM | RAM 8 GB / Swap / `FLAIR_NER_MODEL=flair/ner-german` |
| 502 auf `/sanitize` | PDF zu gross / Flair OOM | `SANITIZE_MAX_CHARS`/`SANITIZE_MAX_PDF_PAGES` senken; CT RAM 12 GB + Swap 4 GB |
| 48s pro Request, HF-Retries | `HF_HUB_OFFLINE` fehlt | In `.env` + systemd; Circuit-Breaker in `m18_cloud_pii` |
| Log: nur Stufe 1 | Warmup fehlgeschlagen | `journalctl -u projekthub-backend`, Rechte auf `.hf_cache` prüfen |

### Swap (optional, 4 GB CT)

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# dauerhaft: /etc/fstab → /swapfile none swap sw 0 0
```

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

### Schnell (empfohlen): Update-Skript

Die Skripte unter `deployment/` sind im Repo **ausführbar** (`git pull` setzt +x mit).

Standard-Update (pull, apt-Libs für PDF, pip, Service-Restart):

```bash
sudo /opt/slitprojekthub/deployment/update-server.sh
```

Homelab mit anderen Pfaden/Service-Namen (Beispiel CT mit `projekthub-*`):

```bash
sudo APP_ROOT=/opt/projekthub \
     BACKEND_SERVICE=projekthub-backend \
     FRONTEND_SERVICE=projekthub-frontend \
     SERVICE_USER=projekthub \
     /opt/projekthub/deployment/update-server.sh
```

Optional Tests nach dem Update:

```bash
sudo RUN_TESTS=1 /opt/slitprojekthub/deployment/update-server.sh
```

Ohne apt-Pakete (nur Git + pip — PDF bleibt ggf. kaputt):

```bash
sudo INSTALL_WEASYPRINT_APT=0 /opt/slitprojekthub/deployment/update-server.sh
```

### Manuell (Einzelbefehle)

```bash
cd /opt/slitprojekthub
source .venv/bin/activate

# System-Libs für PDF (einmalig oder nach OS-Upgrade)
sudo apt update
sudo apt install -y --no-install-recommends \
  libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 libcairo2 \
  libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info fonts-dejavu-core

git pull
pip install -r requirements.txt

# Smoke-Test PDF
python -c "from weasyprint import HTML; assert HTML(string='<p>t</p>').write_pdf()[:4]==b'%PDF'; print('OK')"

systemctl restart slitproj-backend slitproj-frontend
# bzw. projekthub-backend projekthub-frontend — je nach Installation
```

Siehe **Abschnitt 9** für PDF-Export-Funktion und Troubleshooting.

---

## 9. PDF-Export Offertbeurteilung (WeasyPrint)

Ab Ticket 3+4 liefert `/evaluation` pro Bieter und Quelle (KI / Bewerter) auch **PDF**
und **ZIP** (`/evaluation/export.pdf`, `/evaluation/export.zip?format=pdf`).

Technik: HTML-Protokoll (`export_report.html`) → **WeasyPrint** → PDF-Bytes.
Python-Paket: `weasyprint` (in `requirements.txt`). Zusätzlich **native Libraries**
(Pango/Cairo) — reines `pip install` reicht auf Linux nicht.

### System-Pakete (Debian / Ubuntu LXC)

```bash
sudo apt update
sudo apt install -y --no-install-recommends \
  libpango-1.0-0 \
  libpangocairo-1.0-0 \
  libpangoft2-1.0-0 \
  libcairo2 \
  libgdk-pixbuf-2.0-0 \
  libffi-dev \
  shared-mime-info \
  fonts-dejavu-core
```

`fonts-dejavu-core`: Umlaute/Layout in PDFs; ohne Font-Paket oft Fallback-Glyphen.

### Python (venv)

```bash
cd /opt/slitprojekthub   # oder APP_ROOT
source .venv/bin/activate
pip install -r requirements.txt   # enthält weasyprint>=62.0
```

### Verifizieren

```bash
cd /opt/slitprojekthub
.venv/bin/python -c "
from weasyprint import HTML
pdf = HTML(string='<html><body><p>Test äöü</p></body></html>').write_pdf()
assert pdf[:4] == b'%PDF'
print('WeasyPrint OK,', len(pdf), 'bytes')
"

# Optional: Evaluation-Unit-Tests (ZIP/PDF-Logik ohne Browser)
.venv/bin/python scripts/testing/test_evaluation.py
```

**Im Browser** (nach Login): Offertbeurteilung → Export → Bieter + Quelle wählen → **PDF**,
oder **ZIP herunterladen** mit Format «PDF».

Direkt-URL (Beispiel):

```text
/evaluation/export.pdf?project_key=MEIN_PROJEKT&bidder_id=1&source=ai
/evaluation/export.zip?project_key=MEIN_PROJEKT&format=pdf
```

> **Hinweis:** PDF/ZIP-Export läuft über das **FastAPI-Backend** (Port 8000). nginx muss
> `/evaluation/*` zum Backend proxen, wenn die UI nicht nur Streamlit nutzt. Bei reiner
> Streamlit-Installation ggf. Backend-Route über bestehenden API-Proxy prüfen.

### Deployment-Ablauf (Checkliste)

1. Auf dem LXC einloggen
2. `sudo /opt/slitprojekthub/deployment/update-server.sh` (oder manuelle Befehle oben)
3. Smoke-Test WeasyPrint (CLI)
4. Im UI einen PDF-Export testen
5. Bei Fehler: `journalctl -u slitproj-backend -n 50` (Service-Name anpassen)

### Troubleshooting

| Symptom | Ursache | Fix |
|---------|---------|-----|
| HTTP 500 «WeasyPrint nicht installiert» | `pip install` fehlte / falsches venv | `pip install -r requirements.txt` im Service-venv |
| `OSError: cannot load library 'libpango…'` | apt-Pakete fehlen | Abschnitt 9 apt-Zeile + `update-server.sh` |
| PDF leer / kaputte Zeichen | Fonts fehlen | `fonts-dejavu-core` installieren |
| «Keine exportierbaren Bewertungen» (ZIP) | Keine Scores gespeichert | Mindestens eine KI- oder Bewerter-Zeile pro Bieter |
| Export 403 Person | Rolle `auftraggeber` | Nur Aggregate — kein Einzel-Bewerter-Export |

WeasyPrint-Doku: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation
