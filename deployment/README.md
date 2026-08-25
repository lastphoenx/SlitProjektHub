# Deployment (Proxmox / Debian CT)

**Kein Docker.** Produktion: Python venv + **systemd** auf dem App-CT, **nginx** auf dem Reverse-Proxy-CT.

## Artefakte

| Was | Pfad |
|-----|------|
| systemd Backend | `systemd/projekthub-backend.service.example` |
| systemd Streamlit (optional, intern) | `systemd/projekthub-frontend.service.example` |
| nginx vHost FastAPI (öffentlich) | `nginx/fastapi-vhost.conf.example` |
| nginx vHost Streamlit (legacy) | `nginx/streamlit-vhost.conf.example` |
| Erstinstallation auf Debian | `deploy-debian.sh` (optional, generisch) |

## Kurzablauf (wie bei euch)

1. App-CT: `git clone` → `/opt/slitprojekthub`, venv, `config/*.yaml` aus `.example` kopieren
2. `.env` mit API-Keys (nicht im Git)
3. `config/config.yaml` → `auth.trusted_proxy_ips: ["<nginx-lan-ip>"]`
4. systemd-Units aus `.example` nach `/etc/systemd/system/`, `systemctl enable --now projekthub-backend`
5. nginx-CT: vHost aus `fastapi-vhost.conf.example`, TLS, `proxy_pass` → App-CT `:8000`

Auth: In-App (Login + 2FA) — `docs/AUTH.md`. Kein Authelia/Traefik.

## Git-History (optional)

`config/config.yaml` mit LAN-IPs war kurz im Repo (bis Commit `557eca8`). **HEAD ist sauber** — alte Commits auf GitHub können die Datei noch enthalten. Purge nur bei Bedarf, **nicht auf dem App-Server**, sondern auf einem Rechner mit vollem Clone + `git push --force` (siehe Team-Doku / Audit).

## Lokal entwickeln

- Windows: `start_app.ps1` oder `uvicorn` + Streamlit
- Linux: `deployment/systemd/*.example` als Vorlage
