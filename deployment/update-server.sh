#!/bin/bash
# SlitProjektHub — Update auf dem Server (git pull, deps, pip, Service-Restart)
# PDF-Export (Offertbeurteilung) benötigt WeasyPrint + System-Libs — siehe docs/SERVER_SETUP.md §9
#
# Verwendung (als root auf dem LXC/Server):
#   sudo APP_ROOT=/opt/slitprojekthub ./deployment/update-server.sh
#
# Service-Namen anpassen, falls abweichend (Homelab nutzt teils projekthub-* statt slitproj-*):
#   sudo BACKEND_SERVICE=projekthub-backend FRONTEND_SERVICE=projekthub-frontend ./deployment/update-server.sh

set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/slitprojekthub}"
BACKEND_SERVICE="${BACKEND_SERVICE:-slitproj-backend}"
FRONTEND_SERVICE="${FRONTEND_SERVICE:-slitproj-frontend}"
SERVICE_USER="${SERVICE_USER:-root}"
INSTALL_WEASYPRINT_APT="${INSTALL_WEASYPRINT_APT:-1}"
RUN_TESTS="${RUN_TESTS:-0}"

if [ "$EUID" -ne 0 ]; then
    echo "Bitte als root ausführen: sudo $0"
    exit 1
fi

if [ ! -d "$APP_ROOT/.git" ]; then
    echo "Kein Git-Repo unter $APP_ROOT — APP_ROOT prüfen."
    exit 1
fi

echo "=== SlitProjektHub Update ==="
echo "APP_ROOT=$APP_ROOT"
echo "BACKEND_SERVICE=$BACKEND_SERVICE"
echo "FRONTEND_SERVICE=$FRONTEND_SERVICE"
echo ""

echo ">>> apt update"
apt-get update

if [ "$INSTALL_WEASYPRINT_APT" = "1" ]; then
    echo ">>> WeasyPrint System-Abhängigkeiten (PDF-Export)"
    apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-dejavu-core
fi

cd "$APP_ROOT"

echo ">>> git pull"
if id "$SERVICE_USER" &>/dev/null && [ "$SERVICE_USER" != "root" ]; then
    sudo -u "$SERVICE_USER" git pull --ff-only
else
    git pull --ff-only
fi

if [ ! -x ".venv/bin/pip" ]; then
    echo "Kein venv unter $APP_ROOT/.venv — zuerst docs/SERVER_SETUP.md §2 ausführen."
    exit 1
fi

echo ">>> pip install -r requirements.txt"
if id "$SERVICE_USER" &>/dev/null && [ "$SERVICE_USER" != "root" ]; then
    sudo -u "$SERVICE_USER" .venv/bin/pip install -r requirements.txt
else
    .venv/bin/pip install -r requirements.txt
fi

echo ">>> WeasyPrint Smoke-Test"
if .venv/bin/python -c "
from weasyprint import HTML
pdf = HTML(string='<html><body><p>SlitProjektHub PDF OK</p></body></html>').write_pdf()
assert pdf[:4] == b'%PDF', pdf[:20]
print('WeasyPrint OK,', len(pdf), 'bytes')
"; then
    echo "PDF-Export bereit."
else
    echo "WARNUNG: WeasyPrint-Test fehlgeschlagen — PDF-Export (/evaluation/export.pdf) wird 500 liefern."
    echo "Siehe docs/SERVER_SETUP.md Abschnitt 9 (Troubleshooting)."
fi

if [ "$RUN_TESTS" = "1" ]; then
    echo ">>> Tests (optional)"
    .venv/bin/python scripts/testing/test_evaluation.py
fi

echo ">>> systemd restart"
systemctl daemon-reload
if systemctl is-enabled --quiet "$BACKEND_SERVICE" 2>/dev/null; then
    systemctl restart "$BACKEND_SERVICE"
    echo "  restarted $BACKEND_SERVICE"
else
    echo "  übersprungen (nicht aktiv): $BACKEND_SERVICE"
fi
if systemctl is-enabled --quiet "$FRONTEND_SERVICE" 2>/dev/null; then
    systemctl restart "$FRONTEND_SERVICE"
    echo "  restarted $FRONTEND_SERVICE"
else
    echo "  übersprungen (nicht aktiv): $FRONTEND_SERVICE"
fi

echo ""
echo "=== Fertig ==="
echo "Status:"
systemctl is-active "$BACKEND_SERVICE" 2>/dev/null || true
systemctl is-active "$FRONTEND_SERVICE" 2>/dev/null || true
echo ""
echo "Logs Backend: journalctl -u $BACKEND_SERVICE -n 40 --no-pager"
