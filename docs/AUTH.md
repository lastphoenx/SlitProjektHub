# Auth (In-App)

Drei Schichten, nicht vermischen:

| Schicht | Zweck | Speicher |
|---------|--------|----------|
| Login (`app_user`) | Wer die App öffnen darf | SQLite `app_user` |
| App-Rolle (`app_role`) | Vergabe-Perspektive | `super_user`, `projektleiter_intern`, `product_owner`, `auftraggeber` |
| Projektpersona (`role`) | CEO, QA, RAG, Pflichtenheft | SQLite `role` + Markdown |

`config/auth.example.yaml` → `config/auth.yaml` hält `session_secret`. Alte `users:`-Maps werden beim `init_db()` nach `app_user` übernommen (`is_admin` → `super_user`, sonst **unassigned**). `config.yaml` / `auth.yaml` sind gitignored — nur `.example` im Repo.

## Rechte (jetzt)

- **Login + heutige App** (Stammdaten, Chat, Batch-QA / Phase B): jeder aktive Login, auch ohne App-Rolle.
- **Phase C Bewertung** (`can_evaluate`): nur `super_user`, `projektleiter_intern`, `product_owner`. Unassigned und `auftraggeber`: nein.
- **Einzelbewertungen / Bewerter** (`can_view_evaluator_details`): wie Bewertung, aber **nicht** `auftraggeber` (nur Aggregat/Rangfolge).
- UI: Seite **Offertbeurteilung** (`app/pages/09_Offert_Beurteilung.py`) — Bieter, Kriterien, Matrix, KI-Vorschlag, Export.

## 2FA

`config/config.yaml`:

```yaml
auth:
  two_factor_mode: off   # off | optional | required
```

Effektive Pflicht: User-Override → App-Rolle → globaler Modus. Seed: `super_user.totp_required = true` (Super-User braucht TOTP auch bei global `off`).

## CLI

```bash
python scripts/maintenance/setup_admin.py
python scripts/maintenance/create_user.py
python scripts/maintenance/create_user.py --role product_owner
python scripts/maintenance/set_user_role.py --list
python scripts/maintenance/set_user_role.py anna projektleiter_intern
python scripts/maintenance/rename_login.py
python scripts/maintenance/migrate_auth_yaml_to_db.py
python scripts/maintenance/unblock_ip.py --list
```

Web (Super-User, FastAPI): `/admin/users`

Produktion hinter Reverse-Proxy (nginx):

```yaml
auth:
  trusted_proxy_ips: ["<nginx-reverse-proxy-lan-ip>"]
```

Ohne `trusted_proxy_ips` werden `X-Forwarded-For` / `X-Real-IP` ignoriert.

## Streamlit

`require_auth()` einmal im Entrypoint plus in jeder Page. Logout erhöht nur die `session_epoch` dieses Users.

Kein Default-`session_secret` im Quellcode.
