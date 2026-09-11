#!/bin/bash
# SlitProjektHub — Update auf dem Server (git pull, deps, pip, Service-Restart)
# Produktion CT: /opt/slitprojekthub, projekthub-backend, projekthub-frontend
# PDF-Export: WeasyPrint — siehe docs/SERVER_SETUP.md §9
#
#   sudo /opt/slitprojekthub/deployment/update-server.sh

set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/slitprojekthub}"
BACKEND_SERVICE="${BACKEND_SERVICE:-projekthub-backend}"
FRONTEND_SERVICE="${FRONTEND_SERVICE:-projekthub-frontend}"
SERVICE_USER="${SERVICE_USER:-projekthub}"
INSTALL_WEASYPRINT_APT="${INSTALL_WEASYPRINT_APT:-1}"
RUN_TESTS="${RUN_TESTS:-0}"

unit_exists() {
    systemctl cat "${1}.service" &>/dev/null
}

restart_service() {
    local unit="$1"
    if ! unit_exists "$unit"; then
        echo "FEHLER: systemd unit ${unit}.service nicht gefunden."
        exit 1
    fi
    systemctl restart "$unit"
    local state
    state="$(systemctl is-active "$unit" 2>/dev/null || echo failed)"
    if [ "$state" != "active" ]; then
        echo "FEHLER: $unit nach Restart nicht active (Status: $state)."
        echo "  journalctl -u $unit -n 30 --no-pager"
        exit 1
    fi
    echo "  restarted $unit ($state)"
}

if [ "$EUID" -ne 0 ]; then
    echo "Bitte als root ausführen: sudo $0"
    exit 1
fi

if [ ! -d "$APP_ROOT/.git" ]; then
    echo "Kein Git-Repo unter $APP_ROOT — APP_ROOT prüfen."
    exit 1
fi

if ! id "$SERVICE_USER" &>/dev/null; then
    echo "WARNUNG: User $SERVICE_USER fehlt — git/pip als root."
    SERVICE_USER=root
fi

echo "=== SlitProjektHub Update ==="
echo "APP_ROOT=$APP_ROOT"
echo "BACKEND_SERVICE=$BACKEND_SERVICE"
echo "FRONTEND_SERVICE=$FRONTEND_SERVICE"
echo "SERVICE_USER=$SERVICE_USER"
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
if [ "$SERVICE_USER" != "root" ]; then
    sudo -u "$SERVICE_USER" git pull --ff-only
else
    git pull --ff-only
fi

if [ ! -x ".venv/bin/pip" ]; then
    echo "Kein venv unter $APP_ROOT/.venv — zuerst docs/SERVER_SETUP.md §2 ausführen."
    exit 1
fi

echo ">>> pip install -r requirements.txt"
if [ "$SERVICE_USER" != "root" ]; then
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
restart_service "$BACKEND_SERVICE"
restart_service "$FRONTEND_SERVICE"

echo ""
echo "=== Fertig ==="
echo "Status: $(systemctl is-active "$BACKEND_SERVICE") / $(systemctl is-active "$FRONTEND_SERVICE")"
echo "Logs Backend: journalctl -u $BACKEND_SERVICE -n 40 --no-pager"
