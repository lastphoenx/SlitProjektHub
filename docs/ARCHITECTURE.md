# Architektur (Stand 2026)

## Überblick

```
Internet → nginx (TLS) → FastAPI :8000 (Haupt-UI)
                      → optional Streamlit :8501 (intern)
         SQLite (data/db/) + Chroma (data/rag/)
```

- **Auth:** In-App (Login, TOTP, Rate-Limit) — `docs/AUTH.md`, `src/m14_auth.py`
- **Öffentliche UI:** FastAPI + Jinja2 (`backend/`)
- **Legacy UI:** Streamlit (`app/nav_pages/`) — gleicher Auth-Kern
- **Konfiguration:** `config/*.example.yaml` → lokal kopieren; Secrets in `.env` (nicht im Git)

## Request-Flow (Login)

1. `GET /auth/login` — Benutzername + Passwort
2. Falls 2FA noch nicht aktiv: Enrollment (QR + Secret + Code)
3. Falls 2FA aktiv: TOTP-Code
4. Session-Cookie `_auth_token` → Middleware schützt alle anderen Routen

## Reverse Proxy

- nginx vHost-Vorlagen: `deployment/nginx/`
- `config/config.yaml` → `auth.trusted_proxy_ips` muss die nginx-LAN-IP enthalten
- uvicorn mit `--proxy-headers`; Client-IP aus `X-Forwarded-For` nur von vertrauenswürdigen Peers

## Phase C (Offertbeurteilung)

- FastAPI `/evaluation` — Schreibzugriff über `can_evaluate()`
- KI-Vorschläge nur als Vorschlag, Speichern durch Mensch

## Weitere Docs

- `docs/AUTH.md` — Rollen, 2FA, IP-Sperre
- `deployment/README.md` — systemd, nginx
- `docs/RAG_ARCHITECTURE.md` — RAG-Pipeline
