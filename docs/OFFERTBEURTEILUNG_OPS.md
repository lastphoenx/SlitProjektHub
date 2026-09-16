# Offertbeurteilung — Betrieb, Reports, CLI

> Stand: 2026-09-16  
> UI: FastAPI `/evaluation` (Haupt-UI in Produktion)  
> Code: `backend/app/routes/evaluation.py`, `src/m15_evaluation.py`

Betrieb auf dem App-CT (typisch `/opt/slitprojekthub`), Python aus der venv:

```bash
cd /opt/slitprojekthub
.venv/bin/python scripts/maintenance/…
```

Deploy/Update: `deployment/update-server.sh` oder `docs/SERVER_SETUP.md` §8.  
Auth/Rollen: `docs/AUTH.md`.

---

## 1. UI — Matrix, KI, Speichern

| Aktion | Route | Hinweis |
|--------|-------|---------|
| Matrix / Bieter / Kriterien | `GET /evaluation` | Projekt wählen, Phase ① `tender_role` setzen |
| Zelle öffnen | `GET /evaluation/cell` | Bieter-Spalte klicken (nicht Kriterienname) |
| Eigene Bewertung speichern | `POST /evaluation/score` | HTMX → Modal |
| KI-Vorschlag (RAG, einzeln) | `POST /evaluation/suggest` | JavaScript `fetch`, kann Minuten dauern (Vision-Modelle) |
| Nur KI-Spalte speichern | `POST /evaluation/score/ai` | `source=ai`, keine Bewerter-Zuordnung |
| KI-Stapel (Parent + Kinder) | `POST /evaluation/suggest-batch-step` | Schrittweise im Browser; protokolliert in DB |
| Kriterien-Editor | `GET/POST /evaluation/criteria-manage` | Tabellen-Editor für Eignung/Zuschlag |
| Export | `GET /evaluation/export.*` | siehe §3 |

**KI-Einstellungen pro Projekt:** `bewertung_ki_provider` / `bewertung_ki_model` in `evaluation_project_config` (Picker in der Zelle überschreibt einmalig).

**Cloud-KI mit Bieter-Dokumenten:** Checkbox «Cloud bestätigt» + PII-Sanitizer (Stufe 1/2) — siehe `docs/SERVER_SETUP.md` §7.

---

## 2. Session — warum «Speichern» plötzlich scheitert

Die Login-Session ist **absolut** ab Login-Zeit gültig (`auth.session_timeout_minutes`, Default **60** in `config/config.example.yaml`). Aktivität verlängert sie **nicht**.

Typischer Ablauf:

1. Matrix-Zelle öffnen, **KI-Vorschlag (RAG)** starten (lange Laufzeit bei lokalem Vision-LLM).
2. Session läuft ab, während das Modal offen bleibt.
3. **Speichern** → Middleware lehnt ab.

**Seit Fix `f78c52d`:** HTMX-Anfragen ohne gültige Session → `401` + Header `HX-Redirect: /auth/login` (Weiterleitung zur Login-Seite, kein JSON-Fehler «username/password required»).

**Workaround vor Deploy:** Seite neu laden, einloggen, Zelle erneut öffnen.  
**Langfristig optional:** `session_timeout_minutes` erhöhen oder Sliding-Session (Feature-Idee).

---

## 3. Reports & Export

### 3.1 UI (Browser)

Offertbeurteilung → Bereich **Export**:

- **Projektweit:** CSV/XLSX aller Bewertungen.
- **Pro Bieter:** Quelle `ai` (🤖 KI-Vorschlag) oder `user` (Bewerter), optional ein Bewerter.
- **Formate:** CSV, XLSX, HTML, DOCX, **PDF**, ZIP (alle Kombinationen).

PDF/ZIP benötigen WeasyPrint + System-Libs — `docs/SERVER_SETUP.md` §9.

### 3.2 Direkt-URLs (nach Login, gleiche Query-Parameter für alle Formate)

| Format | Pfad |
|--------|------|
| CSV | `/evaluation/export.csv` |
| XLSX | `/evaluation/export.xlsx` |
| HTML | `/evaluation/export.html` |
| DOCX | `/evaluation/export.docx` |
| PDF | `/evaluation/export.pdf` |
| ZIP | `/evaluation/export.zip` |

**Projektweit:**

```text
/evaluation/export.csv?project_key=MEIN_PROJEKT
/evaluation/export.zip?project_key=MEIN_PROJEKT&format=pdf
```

**Pro Bieter + Quelle:**

```text
/evaluation/export.pdf?project_key=MEIN_PROJEKT&bidder_id=1&source=ai
/evaluation/export.docx?project_key=MEIN_PROJEKT&bidder_id=1&source=user&evaluator_id=3
```

`source`: `ai` oder `user`. `evaluator_id` nur bei `source=user` und wenn der Login Bewerter-Namen sehen darf.

Export-Inhalt: Bewertungsmatrix, Begründungen, Zitate, **RAG-Nachweis** (`rag_basis`), Seitenvorschauen (PDF-Thumbnails).

Ticket-Historie: `docs/tickets/offertbeurteilung-export-pro-bieter.md`.

---

## 4. KI-Stapel-Protokoll (`evaluation_batch_log`)

Persistiert jeden Schritt der **KI-Stapelverarbeitung** (Parent-Kriterium → alle Unterfragen nacheinander).

| Feld | Bedeutung |
|------|-----------|
| `run_id` | UUID eines Stapel-Laufs |
| `step_index` | Schritt 1…n innerhalb des Laufs |
| `ok` / `value` / `error_message` | Erfolg, KI-Score oder Fehlertext |
| `triggered_by` | Login-Name des Auslösers |
| `bidder_id`, `parent_criterion_id`, `criterion_id` | Zuordnung |

**In der UI:** Parent-Zelle → aufklappbar «KI-Stapel-Protokoll» (`backend/templates/evaluation/_eval_batch_log.html`).

### 4.1 CLI — `check_evaluation_batch_log.py`

```bash
cd /opt/slitprojekthub

# Projekte mit Log-Einträgen
.venv/bin/python scripts/maintenance/check_evaluation_batch_log.py --list-projects

# Letzte 7 Tage, nach Run gruppiert (empfohlen)
.venv/bin/python scripts/maintenance/check_evaluation_batch_log.py \
  --project-key '<project_key>' --days 7 --group-runs

# Nur heute, ein Bieter, ein Parent
.venv/bin/python scripts/maintenance/check_evaluation_batch_log.py \
  --project-key '<project_key>' --bidder-id 5 --parent-id 17 --today

# Einzelner Lauf
.venv/bin/python scripts/maintenance/check_evaluation_batch_log.py \
  --project-key '<project_key>' --run-id '<uuid>' --json

# project_key alternativ per Umgebung
PK='<project_key>' .venv/bin/python scripts/maintenance/check_evaluation_batch_log.py --group-runs
```

Optionen: `--limit` (default 200), `--json`, `--days` (default 14).

**Tabelle fehlt:** Backend nach Deploy neu starten (`init_db` legt Tabelle an).

---

## 5. Hilfsskripte (`scripts/maintenance/`)

Alle Skripte vom **Repo-Root** ausführen (`PYTHONPATH` setzen die Skripte selbst).

### 5.1 Offertbeurteilung

| Skript | Zweck |
|--------|--------|
| `check_evaluation_batch_log.py` | KI-Stapel-Protokoll aus DB (§4) |
| `import_evaluation_criteria.py` | Kriterien aus lokaler JSON (`--project-key`, `--file`) — idempotent |
| `list_projects.py` | `project_key` aller aktiven Projekte (für CLI-Argumente) |
| `backfill_document_page_images.py` | Fehlende PDF-Seitenvorschauen (WebP) nachziehen — poppler nötig |
| `backfill_document_chunk_page_numbers.py` | `page_number` an Chunks — Voraussetzung für Thumbnails in RAG-Nachweis |

Beispiel Kriterien-Import (JSON **nicht** committen — nur lokal):

```bash
.venv/bin/python scripts/maintenance/import_evaluation_criteria.py \
  --project-key '<project_key>' --file /pfad/kriterien.json
```

Format: Docstring in `import_evaluation_criteria.py` (lokale JSON-Datei, nicht committen).

Beispiel PDF-Thumbnails:

```bash
sudo -u projekthub .venv/bin/python scripts/maintenance/backfill_document_page_images.py --dry-run
sudo -u projekthub .venv/bin/python scripts/maintenance/backfill_document_chunk_page_numbers.py
```

### 5.2 Auth & Benutzer

| Skript | Zweck |
|--------|--------|
| `setup_admin.py` | Ersten Super-User anlegen |
| `create_user.py` | Weitere Logins (`--role product_owner`, …) |
| `set_user_role.py` | Rolle setzen / `--list` |
| `rename_login.py` | Benutzername umbenennen |
| `migrate_auth_yaml_to_db.py` | Legacy `auth.yaml` users → `app_user` |
| `unblock_ip.py` | IP-Sperre nach Fehl-Logins (`--list`) |

Details: `docs/AUTH.md`.

### 5.3 RAG / Dokumente / DB

| Skript | Zweck |
|--------|--------|
| `check_db.py` | SQLite-Grundcheck |
| `check_chroma.py` | ChromaDB-Erreichbarkeit |
| `check_embeddings.py` | Embedding-Modell / Dimensionen |
| `check_extraction.py` | Chunk-Sample zu Dokument (`--contains Substring`) |
| `check_doc_by_substring.py` | Dokument in DB finden |
| `fix_rag_embeddings.py` | Embeddings reparieren/neu anstossen |
| `seed_retrieval_keywords.py` | BM25-Keywords pflegen |
| `prefetch_pii_models.py` | Cloud-PII Stufe 2 — Modell-Download |

### 5.4 Sonstiges

| Skript | Zweck |
|--------|--------|
| `check_idea_jobs.py` | KI-Job-Queue (Idea-Lab) |
| `check_project.py` / `check_project_status.py` | Projekt-Stammdaten |
| `check_tasks.py` / `check_all_tasks.py` | Aufgaben-Integrität |

Entwickler-Tests: `scripts/testing/test_evaluation.py` (Export, Batch-Log, RAG, Sanitizer).

---

## 6. Wichtige Server-Befehle

```bash
# Update (Git pull, pip, optional Tests, Service-Restart)
sudo /opt/slitprojekthub/deployment/update-server.sh

# Mit Tests
sudo RUN_TESTS=1 /opt/slitprojekthub/deployment/update-server.sh

# Services
sudo systemctl restart projekthub-backend projekthub-frontend
sudo journalctl -u projekthub-backend -n 80 --no-pager

# Evaluation Smoke-Test
cd /opt/slitprojekthub
.venv/bin/python scripts/testing/test_evaluation.py
```

Nach **Python- oder Template-Änderungen** Backend neu starten (kein Hot-Reload in Prod).

---

## 7. Troubleshooting

| Symptom | Wahrscheinliche Ursache | Massnahme |
|---------|-------------------------|-----------|
| JSON `username`/`password` required beim Speichern | Session abgelaufen + alter HTMX-Redirect | Neu einloggen; Fix ≥ `f78c52d` deployen |
| KI-Vorschlag hängt / sehr langsam | Lokales Vision-LLM (z. B. qwen) + RAG | Warten oder schnelleres Modell wählen |
| Keine Seitenvorschau im RAG-Nachweis | DOCX statt PDF, oder fehlende `page_number` | PDF hochladen; Backfill-Skripte §5.1 |
| PDF-Export 500 | WeasyPrint/System-Libs fehlen | `docs/SERVER_SETUP.md` §9 |
| `evaluation_batch_log fehlt` | DB-Migration nicht gelaufen | `systemctl restart projekthub-backend` |
| Cloud-KI blockiert | Gate ohne Bestätigung | Checkbox in Zelle; PII prüfen unter `/sanitize` |
| 403 «Keine Berechtigung» | Rolle `auftraggeber` / unassigned | `set_user_role.py` — nur `super_user`, `projektleiter_intern`, `product_owner` dürfen bewerten |

---

## 8. Weitere Docs

| Thema | Datei |
|-------|--------|
| Feature-Tickets (Dokumente, RAG, Export) | `docs/tickets/offertbeurteilung-dokumente.md` |
| Export pro Bieter | `docs/tickets/offertbeurteilung-export-pro-bieter.md` |
| PDF-OCR / Ingest | `docs/tickets/pdf-ocr-ingest.md` |
| Server-Setup, PII, WeasyPrint | `docs/SERVER_SETUP.md` |
| Auth & CLI User | `docs/AUTH.md` |
| Architektur Phase C | `docs/ARCHITECTURE.md` |
