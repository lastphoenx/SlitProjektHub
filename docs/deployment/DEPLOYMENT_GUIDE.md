# ============================================
# DEPLOYMENT ANLEITUNG - SlitProjektHub
# ============================================

[Vollständiger Inhalt in separater Datei - siehe oben]

## Zusammenfassung

✅ **a) Desktop-Start**: `start_app.bat` ist fertig (startet jetzt auch Backend!)
✅ **b) Lokales Netzwerk**: `docker-compose.yml` ready
✅ **c) Production mit 2FA**: `docker-compose.production.yml` + Authelia-Config

## Schnellstart

### Lokal (Proxmox/Debian)
```bash
docker-compose up -d
# → http://192.168.x.x:8501
```

### Production mit Domain & 2FA
```bash
# 1. Secrets generieren
openssl rand -base64 32  # jwt_secret
openssl rand -base64 32  # session_secret

# 2. In authelia/configuration.yml eintragen

# 3. Passwörter hashen
docker run authelia/authelia:latest authelia crypto hash generate argon2 --password 'DeinPasswort'

# 4. In authelia/users_database.yml eintragen

# 5. Starten
docker-compose -f docker-compose.production.yml up -d

# 6. DNS konfigurieren
# projekthub.<YOUR_DOMAIN> → Server-IP
# auth.<YOUR_DOMAIN> → Server-IP
```

Fertig! 🚀
