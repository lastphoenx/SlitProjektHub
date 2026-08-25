# Nacharbeitungsplan: `fd0e8a6` → DB-Tabellen + AppRollen

> **Umgesetzt 2026-08-25** in `src/m14_auth.py` (Tabellen `app_user` / `app_role`).
> Abweichungen zum Rohplan: keine Default-Rolle `projektleiter_intern` (unassigned);
> 2FA = User-Override → App-Rolle → `two_factor_mode`; `session_epoch` in der DB;
> Super-User-Rolle `totp_required=true`. YAML-Import läuft in `init_db()`.
>
> Ausgangslage: `fd0e8a6` (auf `main`, von Cursor) trennt Login-User sauber von der
> Stammdaten-`Role`, speichert aber weiterhin alles in `config/auth.yaml` und kennt
> nur `is_admin: bool` statt der vier AppRollen. Dieser Plan beschreibt den Weg von
> dort zu dem Zielbild aus `docs/EXPANSION_CONCEPT.md` (DB-Tabellen `app_user` /
> `app_role`, vier Perspektiven), **ohne** die guten Teile aus `fd0e8a6` zu verlieren.

## Was aus `fd0e8a6` übernommen wird (nicht neu erfinden)

- **Case-insensitive Username-Vergleich** beim Duplikat-Check (`"admin"` und `"Admin"`
  dürfen nicht beide existieren) — in meiner ursprünglichen DB-Variante fehlte das.
- **`validate_username()`-Regex**, die rollenartige Namen zurückweist (schützt genau
  vor der Verwechslung Login-User ↔ Stammdaten-Rolle).
- **Legacy-Erkennung** für ein `auth.yaml` im alten Single-Admin-Format.
- **Tests** aus `scripts/testing/test_auth_users.py` (Duplikat-Check, per-User-Logout-
  Isolation) — Testfälle 1:1 übernehmen, nur auf DB-Funktionen umschreiben.

## Warum trotzdem migrieren, nicht so belassen

`docs/EXPANSION_CONCEPT.md` setzt für Phase C (Offertbeurteilung) `Score.evaluator_user_id`
als **Fremdschlüssel** auf eine User-Tabelle voraus — das lässt sich gegen eine YAML-Datei
nicht bauen. Ausserdem: `is_admin: bool` kann die vier AppRollen (Super-User, Projektleiter
intern, Product Owner, Auftraggeber) nicht abbilden, und paralleles Schreiben mehrerer
gleichzeitig aktiver Personen in dieselbe YAML-Datei ist ohne Locking eine latente
Race-Condition — genau das Szenario, für das die Rollen gedacht sind.

## Schritt-für-Schritt

### 1. Neue Tabellen ergänzen (kein Bruch, additiv)

`AppRole` (`app_role`) + `User` (`app_user`) in `src/m14_auth.py`, wie in meinem
vorbereiteten Stand — vier Default-Rollen seeden: `super_user`,
`projektleiter_intern`, `product_owner`, `auftraggeber`.

### 2. Einmaliges Migrationsscript (`scripts/maintenance/migrate_auth_yaml_to_db.py`)

- Liest `config/auth.yaml` im **aktuellen** `fd0e8a6`-Format (`users:`-Map).
- Für jeden Eintrag: neue `app_user`-Zeile (Username, Passwort-Hash, TOTP-Secret
  1:1 übernehmen — kein Passwort-Reset nötig).
- `is_admin: true` → AppRole `super_user`.
- `is_admin: false` → **kein automatisches Mapping auf eine der drei übrigen Rollen**
  (dafür reicht die Information nicht). Script setzt einen Platzhalter (z. B.
  `projektleiter_intern`) und druckt eine Liste "bitte manuell zuordnen" —
  Zuordnung danach über `/admin/users` oder `scripts/maintenance/set_user_role.py`.
- Nach erfolgreichem Import: `auth.yaml` auf nur noch `session_secret` reduzieren
  (Verhalten wie in meinem ursprünglichen Stand).
- Script ist idempotent (zweimal laufen lassen = keine Doppel-Accounts, via
  case-insensitive Check wie in `fd0e8a6`).

### 3. Verhalten aus `fd0e8a6` in die DB-Variante übernehmen

- `validate_username()` unverändert weiterverwenden, vor jedem `User`-Insert aufrufen.
- Case-insensitive Duplikat-Check: entweder `func.lower(User.username)` beim Lookup,
  oder DB-seitig `UNIQUE` mit `COLLATE NOCASE` (SQLite) auf der Spalte — Letzteres ist
  robuster, weil es nicht vom Aufrufer abhängt.
- Race-Condition-Problem entfällt automatisch (SQLite-Transaktionen pro `Session`).

### 4. AppRole-Gating ergänzen

- `/admin/users` (bereits vorbereitet) auf `app_role_key == "super_user"` prüfen
  statt `is_admin`.
- Rechte-Matrix aus `EXPANSION_CONCEPT.md` Abschnitt 3.6 als Grundlage für die
  Freigaben in Fragerunde/Offertbeurteilung verwenden, sobald diese gebaut werden.

### 5. 2FA pro User optional machen

`fd0e8a6` generiert zwar für jeden User ein TOTP-Secret, aber `is_2fa_enabled()`
bleibt ein reiner globaler Schalter — kein Pro-User-Opt-in. Beim Rework: pro `User`
ein `totp_enabled`-Flag ergänzen, effektive Pflicht = globaler Schalter **oder**
`user.totp_enabled` (löst die ursprüngliche Anforderung "Pflicht oder optional").

### 6. Tests migrieren

`scripts/testing/test_auth_users.py` überarbeiten: gleiche Testfälle (Duplikat-Check
case-insensitive, per-User-Logout-Isolation, Legacy-Erkennung), aber gegen die
DB-Funktionen statt YAML. Migrationsscript selbst braucht einen eigenen Test
(YAML rein → korrekte `app_user`-Zeilen raus, inkl. Passwort-Hash-Erhalt).

### 7. Web-UI ergänzen

`backend/templates/auth/users.html` + `/admin/users`-Routen aus meinem Stand
übernehmen, aber `is_admin`-Checkbox durch AppRolle-Dropdown ersetzen (Rollenliste
aus `list_app_roles()`).

## Reihenfolge / Aufwand

| Schritt | Aufwand | Blockierend für |
|---|---|---|
| 1. Tabellen + Seed | klein | alles Weitere |
| 2. Migrationsscript | klein–mittel | Produktivbetrieb ohne Datenverlust |
| 3. Verhalten übernehmen | klein | Qualität/Robustheit |
| 4. AppRole-Gating | klein | Phase C (Rechte-Matrix) |
| 5. 2FA pro User | klein | ursprüngliche 2FA-Anforderung |
| 6. Tests migrieren | klein | Regressionsschutz |
| 7. Web-UI | mittel (schon fast fertig) | Self-Service ohne SSH/CLI |

Kein Schritt ist einzeln gross — in Summe realistisch **1–2 Tage**, wenn man von
meinem vorbereiteten Stand (Tabellen, `/admin/users`-UI existieren bereits) ausgeht
und nur Schritt 2 (Migrationsscript) und Schritt 3/5 (Verhalten aus `fd0e8a6`
integrieren) neu dazukommen.

## Konkreter Git-Fahrplan

1. Von `main` (enthält `fd0e8a6`) einen neuen Branch abzweigen.
2. Tabellen + Migrationsscript + AppRole-Gating + Pro-User-2FA + Web-UI aufsetzen
   (Schritte 1–7 oben).
3. **Nicht** einfach meinen alten Branch (`claude/eager-thompson-dlcj58`, Commit
   `b63fdb7`) über `main` drüberpushen — der hat weder den Case-insensitive-Check
   noch `validate_username()` noch die Legacy-Migration noch die Tests aus `fd0e8a6`.
   Der Rework baut auf `fd0e8a6` auf, nicht umgekehrt.
4. Migrationsscript einmalig gegen die echte `config/auth.yaml` laufen lassen,
   danach die Nicht-Admin-User manuell den richtigen AppRollen zuordnen.
