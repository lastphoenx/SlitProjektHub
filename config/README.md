# Lokale Konfiguration (nicht committen)

Öffentliches Repo enthält nur **`.example`**-Dateien. Auf jedem Host (PC, Server):

```bash
cp config/config.example.yaml config/config.yaml
cp config/auth.example.yaml config/auth.yaml
cp config/user_settings.example.yaml config/user_settings.yaml
cp .env.example .env
```

Dann **lokal anpassen** (API-Keys, `trusted_proxy_ips`, `session_secret`).  
Diese Zieldateien sind in `.gitignore` — nie ins Git pushen.

| Datei | Inhalt |
|-------|--------|
| `config.yaml` | Auth-Modus, DB, `trusted_proxy_ips` (Proxy-LAN-IP) |
| `auth.yaml` | `session_secret` (+ optional Legacy-`users` bis Migration) |
| `user_settings.yaml` | Standard-LLM im UI |
| `.env` | API-Keys (`OPENAI_API_KEY`, …) |

Weitere Vorlagen: `deployment/systemd/*.example`, `deployment/nginx/*.example`.
