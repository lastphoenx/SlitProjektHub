#!/bin/bash
# SlitProjektHub — Update auf dem Server (git pull, deps, pip, Service-Restart)
# PDF-Export (Offertbeurteilung) benötigt WeasyPrint + System-Libs — siehe docs/SERVER_SETUP.md §9
#
# Verwendung (als root auf dem LXC/Server):
#   sudo /opt/slitprojekthub/deployment/update-server.sh
#
# Optional überschreiben:
#   sudo BACKEND_SERVICE=… FRONTEND_SERVICE=… SERVICE_USER=projekthub ./deployment/update-server.sh

set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/slitprojekthub}"
BACKEND_SERVICE="${BACKEND_SERVICE:-}"
FRONTEND_SERVICE="${FRONTEND_SERVICE:-}"
SERVICE_USER="${SERVICE_USER:-}"
INSTALL_WEASYPRINT_APT="${INSTALL_WEASYPRINT_APT:-1}"
RUN_TESTS="${RUN_TESTS:-0}"

unit_exists() {
    systemctl cat "${1}.service" &>/dev/null
}

detect_service() {
    local role="$1"
    shift
    local candidate
    for candidate in "$@"; do
        if unit_exists "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

resolve_services() {
    if [ -z "$BACKEND_SERVICE" ]; then
        BACKEND_SERVICE="$(detect_service backend projekthub-backend slitproj-backend)" || {
            echo "FEHLER: Keine Backend-Unit gefunden (projekthub-backend / slitproj-backend)."
            exit 1
        }
    elif ! unit_exists "$BACKEND_SERVICE"; then
        echo "FEHLER: BACKEND_SERVICE=$BACKEND_SERVICE existiert nicht."
        exit 1
    fi

    if [ -z "$FRONTEND_SERVICE" ]; then
        FRONTEND_SERVICE="$(detect_service frontend projekthub-frontend slitproj-frontend)" || {
            echo "FEHLER: Keine Frontend-Unit gefunden (projekthub-frontend / slitproj-frontend)."
            exit 1
        }
    elif ! unit_exists "$FRONTEND_SERVICE"; then
        echo "FEHLER: FRONTEND_SERVICE=$FRONTEND_SERVICE existiert nicht."
        exit 1
    fi
}

resolve_service_user() {
    if [ -n "$SERVICE_USER" ]; then
        return
    fi
    if id projekthub &>/dev/null; then
        SERVICE_USER=projekthub
    else
        SERVICE_USER=root
    fi
}

restart_service() {
    local unit="$1"
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

resolve_services
resolve_service_user

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
if [ "$SERVICE_USER" != "root" ] && id "$SERVICE_USER" &>/dev/null; then
    sudo -u "$SERVICE_USER" git pull --ff-only
else
    git pull --ff-only
fi

if [ ! -x ".venv/bin/pip" ]; then
    echo "Kein venv unter $APP_ROOT/.venv — zuerst docs/SERVER_SETUP.md §2 ausführen."
    exit 1
fi

echo ">>> pip install -r requirements.txt"
if [ "$SERVICE_USER" != "root" ] && id "$SERVICE_USER" &>/dev/null; then
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
