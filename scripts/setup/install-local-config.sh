#!/usr/bin/env bash
# Einmalig nach clone/pull: lokale Config aus Vorlagen (gitignored Ziele).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

copy_if_missing() {
  local src="$1" dst="$2"
  if [[ -f "$dst" ]]; then
    echo "OK  $dst (existiert)"
  else
    cp "$src" "$dst"
    echo "cp  $src -> $dst"
  fi
}

copy_if_missing config/config.example.yaml config/config.yaml
copy_if_missing config/auth.example.yaml config/auth.yaml
copy_if_missing config/user_settings.example.yaml config/user_settings.yaml
copy_if_missing .env.example .env

echo "Fertig. Bitte anpassen: config/config.yaml (trusted_proxy_ips), config/auth.yaml, .env"
