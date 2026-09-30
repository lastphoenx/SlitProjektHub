# SlitProjektHub — Hinweise für KI-Assistenten

## Versions-Wahrheit

Kanonisch auf `main`: `requirements.txt` (Root), `backend/requirements.txt` (FastAPI-Zusatz). Pins nicht aus Chat rekonstruieren — Dateien lesen.

## Dependabot

Security updates in GitHub-Repo-Einstellungen aktivieren. Version-PRs: `.github/dependabot.yml` (weekly, gruppiert, keine semver-major per Bot). **SQLAlchemy:** nur `>=2.0.x,<2.1.0` solange `sqlmodel` `<2.1.0` verlangt (siehe `requirements.txt`).

## Git

**`main`** für normale Arbeit. Commit/Push nur auf Nutzeranweisung.
