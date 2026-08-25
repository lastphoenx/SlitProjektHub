"""
m14_auth.py – In-App-Auth, TOTP, IP-Rate-Limit.

Schichten:
  app_user / app_role  – Login + Vergabe-Perspektive (nicht Stammdaten)
  role (m03_db)        – Fachpersona für RAG/Pflichtenheft, kein Login
  config/auth.yaml     – nur noch session_secret (+ Legacy-users bis Migration)

Ohne User in der DB und ohne Legacy-YAML: Setup-Modus.
"""
from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import yaml
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from sqlalchemy import Boolean, Column, Integer, String, func
from sqlmodel import Field, Session, SQLModel, select

from .m01_config import BASE_DIR, get_settings
from .m03_db import engine

log = logging.getLogger(__name__)

AUTH_CONFIG_PATH = BASE_DIR / "config" / "auth.yaml"
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")

BAN_DURATIONS = {
    1: timedelta(hours=1),
    2: timedelta(hours=24),
    3: None,
}
WRONG_PW_LOCKOUT = {
    5: timedelta(minutes=15),
    10: timedelta(hours=24),
}

# Phase-C-Bewertung. Unassigned und Auftraggeber: einloggen ja, bewerten nein.
EVALUATOR_ROLE_KEYS = frozenset({"super_user", "projektleiter_intern", "product_owner"})

DEFAULT_APP_ROLES: tuple[tuple[str, str, Optional[bool], int], ...] = (
    ("super_user", "Super-User", True, 10),
    ("projektleiter_intern", "Projektleiter intern", None, 20),
    ("product_owner", "Product Owner", None, 30),
    ("auftraggeber", "Auftraggeber", None, 40),
)


class IpBlocklist(SQLModel, table=True):
    __tablename__ = "ip_blocklist"
    ip: str = Field(primary_key=True)
    level: int = Field(default=1)
    attempt_count: int = Field(default=1)
    blocked_until: Optional[datetime] = None
    blocked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = Field(default="unknown_user")
    admin_note: Optional[str] = None


class LoginAttempt(SQLModel, table=True):
    __tablename__ = "login_attempts"
    id: Optional[int] = Field(default=None, primary_key=True)
    ip: str = Field(index=True)
    username: str
    success: bool
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = Field(default="")


class AppRole(SQLModel, table=True):
    __tablename__ = "app_role"
    key: str = Field(primary_key=True, max_length=64)
    title: str
    totp_required: Optional[bool] = Field(
        default=None,
        sa_column=Column(Boolean, nullable=True),
    )
    sort_order: int = 0


class AppUser(SQLModel, table=True):
    __tablename__ = "app_user"
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(
        sa_column=Column(String(32, collation="NOCASE"), nullable=False, unique=True)
    )
    password_hash: str
    totp_secret: Optional[str] = None
    totp_enabled: bool = False
    totp_required: Optional[bool] = Field(
        default=None,
        sa_column=Column(Boolean, nullable=True),
    )
    app_role_key: Optional[str] = Field(default=None, foreign_key="app_role.key")
    session_epoch: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1))
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_auth_config() -> dict:
    if not AUTH_CONFIG_PATH.exists():
        return {}
    with open(AUTH_CONFIG_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, Mapping):
        log.warning("config/auth.yaml: erwartet YAML-Mapping, ignoriert (%s)", type(raw).__name__)
        return {}
    return raw


def _save_auth_config(cfg: dict) -> None:
    AUTH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTH_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)


def normalize_username(username: str) -> str:
    return (username or "").strip()


def validate_username(username: str) -> str:
    name = normalize_username(username)
    if not _USERNAME_RE.fullmatch(name):
        raise ValueError(
            "Benutzername: 2–32 Zeichen, nur Buchstaben, Ziffern, Punkt, Unterstrich, Bindestrich."
        )
    return name


def _normalize_auth_config(cfg: dict) -> dict:
    """Legacy-YAML (Single-Admin oder users-Map) lesen — nur für Migration."""
    if not cfg:
        return {"session_secret": None, "session_epoch": 1, "users": {}}
    users = cfg.get("users")
    if isinstance(users, dict):
        return {
            "session_secret": cfg.get("session_secret"),
            "session_epoch": int(cfg.get("session_epoch") or 1),
            "users": users,
        }
    username = normalize_username(str(cfg.get("username") or "admin")) or "admin"
    users_out: dict = {}
    if cfg.get("password_hash"):
        users_out[username] = {
            "password_hash": cfg["password_hash"],
            "totp_secret": cfg.get("totp_secret"),
            "is_admin": True,
            "session_epoch": int(cfg.get("session_epoch") or 1),
        }
    return {
        "session_secret": cfg.get("session_secret"),
        "session_epoch": int(cfg.get("session_epoch") or 1),
        "users": users_out,
    }


def two_factor_mode() -> str:
    """off | optional | required. Legacy two_factor_enabled: true → required."""
    try:
        s = get_settings()
    except Exception:
        return "off"
    mode = getattr(s, "auth_two_factor_mode", None)
    if mode in ("off", "optional", "required"):
        return mode
    if getattr(s, "auth_two_factor_enabled", False):
        return "required"
    return "off"


def seed_app_roles() -> None:
    with Session(engine) as session:
        for key, title, totp_required, order in DEFAULT_APP_ROLES:
            existing = session.get(AppRole, key)
            if existing:
                continue
            session.add(
                AppRole(key=key, title=title, totp_required=totp_required, sort_order=order)
            )
        session.commit()


def list_app_roles() -> list[dict]:
    seed_app_roles()
    with Session(engine) as session:
        rows = session.exec(select(AppRole).order_by(AppRole.sort_order)).all()
        return [
            {"key": r.key, "title": r.title, "totp_required": r.totp_required}
            for r in rows
        ]


def _get_user(username: str) -> AppUser | None:
    name = normalize_username(username)
    if not name:
        return None
    with Session(engine) as session:
        user = session.exec(
            select(AppUser).where(func.lower(AppUser.username) == name.casefold())
        ).first()
        if user:
            session.expunge(user)
        return user


def _find_user(username: str) -> tuple[str, dict] | tuple[None, None]:
    """Kompatibilität: (canonical_name, rec-dict) wie YAML-Stand."""
    user = _get_user(username)
    if not user:
        return None, None
    rec = {
        "password_hash": user.password_hash,
        "totp_secret": user.totp_secret,
        "is_admin": user.app_role_key == "super_user",
        "session_epoch": user.session_epoch,
        "app_role_key": user.app_role_key,
        "totp_enabled": user.totp_enabled,
        "totp_required": user.totp_required,
    }
    return user.username, rec


def list_login_usernames() -> list[dict]:
    with Session(engine) as session:
        rows = session.exec(select(AppUser)).all()
        out = [
            {
                "username": u.username,
                "is_admin": u.app_role_key == "super_user",
                "app_role_key": u.app_role_key,
                "totp_enabled": u.totp_enabled,
            }
            for u in rows
        ]
    return sorted(
        out,
        key=lambda r: (r["app_role_key"] or "zzz", r["username"].casefold()),
    )


def is_setup_required() -> bool:
    yaml_users = _normalize_auth_config(_load_auth_config()).get("users") or {}
    if any(isinstance(r, dict) and r.get("password_hash") for r in yaml_users.values()):
        return False
    with Session(engine) as session:
        return session.exec(select(AppUser)).first() is None


def _new_totp_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def _password_hash(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Passwort muss mindestens 10 Zeichen haben.")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _ensure_session_secret() -> str:
    cfg = _load_auth_config()
    secret = cfg.get("session_secret")
    if secret:
        return str(secret)
    secret = secrets.token_hex(32)
    keep_users = _normalize_auth_config(cfg).get("users") or {}
    out = {"session_secret": secret, "session_epoch": int(cfg.get("session_epoch") or 1)}
    if keep_users:
        out["users"] = keep_users
        if cfg.get("username"):
            out["username"] = cfg.get("username")
            out["password_hash"] = cfg.get("password_hash")
            out["totp_secret"] = cfg.get("totp_secret")
    _save_auth_config(out if keep_users else {"session_secret": secret, "session_epoch": 1})
    return secret


def setup_admin(username: str, password: str) -> str:
    seed_app_roles()
    if not is_setup_required():
        raise ValueError("Admin existiert bereits. Weitere Accounts: create_user().")
    name = validate_username(username)
    totp_secret = _new_totp_secret()
    _ensure_session_secret()
    with Session(engine) as session:
        session.add(
            AppUser(
                username=name,
                password_hash=_password_hash(password),
                totp_secret=totp_secret,
                totp_enabled=False,
                app_role_key="super_user",
                session_epoch=1,
            )
        )
        session.commit()
    return totp_secret


def create_user(
    username: str,
    password: str,
    *,
    is_admin: bool = False,
    app_role_key: str | None = None,
) -> str:
    seed_app_roles()
    if is_setup_required():
        raise ValueError("Zuerst setup_admin() / setup_admin.py.")
    name = validate_username(username)
    if _get_user(name):
        raise ValueError(f"Login-Benutzer '{name}' existiert bereits.")
    role = "super_user" if is_admin else app_role_key
    if role:
        with Session(engine) as session:
            if session.get(AppRole, role) is None:
                raise ValueError(f"Unbekannte App-Rolle: {role}")
    totp_secret = _new_totp_secret()
    _ensure_session_secret()
    with Session(engine) as session:
        session.add(
            AppUser(
                username=name,
                password_hash=_password_hash(password),
                totp_secret=totp_secret,
                totp_enabled=False,
                app_role_key=role,
                session_epoch=1,
            )
        )
        session.commit()
    return totp_secret


def set_user_role(username: str, app_role_key: str | None) -> None:
    seed_app_roles()
    user = _get_user(username)
    if not user:
        raise ValueError("Unbekannter Login-Benutzer.")
    if app_role_key:
        with Session(engine) as session:
            if session.get(AppRole, app_role_key) is None:
                raise ValueError(f"Unbekannte App-Rolle: {app_role_key}")
    with Session(engine) as session:
        db_user = session.get(AppUser, user.id)
        if not db_user:
            raise ValueError("Unbekannter Login-Benutzer.")
        db_user.app_role_key = app_role_key
        session.add(db_user)
        session.commit()


def rename_login_user(old_username: str, new_username: str) -> str:
    """Login-Namen ändern. Passwort, TOTP und App-Rolle bleiben."""
    new_name = validate_username(new_username)
    user = _get_user(old_username)
    if not user:
        raise ValueError("Unbekannter Login-Benutzer.")
    if _get_user(new_name) and user.username.casefold() != new_name.casefold():
        raise ValueError(f"Login-Benutzer '{new_name}' existiert bereits.")
    with Session(engine) as session:
        db_user = session.get(AppUser, user.id)
        if not db_user:
            raise ValueError("Unbekannter Login-Benutzer.")
        db_user.username = new_name
        session.add(db_user)
        session.commit()
    return new_name


def migrate_yaml_users_to_db() -> list[str]:
    """YAML-users → app_user. is_admin→super_user, sonst unassigned. Gibt offene Zuordnungen zurück."""
    seed_app_roles()
    state = _normalize_auth_config(_load_auth_config())
    users = state.get("users") or {}
    pending: list[str] = []
    imported = 0
    for name, rec in users.items():
        if not isinstance(rec, dict) or not rec.get("password_hash"):
            continue
        try:
            canonical = validate_username(str(name))
        except ValueError:
            log.warning("YAML-User %r übersprungen (ungültiger Name)", name)
            continue
        if _get_user(canonical):
            continue
        is_admin = bool(rec.get("is_admin"))
        role = "super_user" if is_admin else None
        try:
            epoch = int(rec.get("session_epoch") or 1)
        except (TypeError, ValueError):
            epoch = 1
        with Session(engine) as session:
            session.add(
                AppUser(
                    username=canonical,
                    password_hash=str(rec["password_hash"]),
                    totp_secret=rec.get("totp_secret"),
                    totp_enabled=False,
                    app_role_key=role,
                    session_epoch=epoch,
                )
            )
            session.commit()
        imported += 1
        if role is None:
            pending.append(canonical)
    if imported:
        secret = state.get("session_secret") or _ensure_session_secret()
        _save_auth_config({"session_secret": secret, "session_epoch": int(state.get("session_epoch") or 1)})
        log.info("Auth-YAML nach DB: %s User, unassigned=%s", imported, pending)
    elif state.get("session_secret") and users:
        _save_auth_config(
            {
                "session_secret": state["session_secret"],
                "session_epoch": int(state.get("session_epoch") or 1),
            }
        )
    return pending


def get_totp_uri(username: str) -> str:
    import pyotp

    user = _get_user(username)
    if not user:
        raise ValueError("Unbekannter Login-Benutzer.")
    secret = user.totp_secret or ""
    if not secret:
        raise ValueError("Kein TOTP-Secret für diesen Benutzer.")
    return pyotp.TOTP(secret).provisioning_uri(
        name=user.username, issuer_name="SlitProjektHub"
    )


def get_totp_secret_plain(username: str) -> str:
    user = _get_user(username)
    if not user or not user.totp_secret:
        raise ValueError("Kein TOTP-Secret für diesen Benutzer.")
    return user.totp_secret


def parse_pre_token_username(pre_token: str, max_age_seconds: int = 600) -> str | None:
    if not pre_token:
        return None
    try:
        raw = _get_signer().unsign(pre_token, max_age=max_age_seconds)
        payload = raw.decode() if isinstance(raw, bytes) else str(raw)
    except (SignatureExpired, BadSignature):
        return None
    parts = payload.split(":")
    if len(parts) >= 3 and parts[0] == "pre":
        return parts[1]
    return None


def verify_password(password: str, username: str | None = None) -> bool:
    user = _get_user(username) if username else _get_user(get_admin_username())
    pw_hash = (user.password_hash if user else "") or ""
    if not pw_hash:
        return False
    return bcrypt.checkpw(password.encode(), pw_hash.encode())


def change_password(username: str, current_password: str, new_password: str) -> None:
    """Passwort ändern; erhöht session_epoch (andere Sessions ungültig)."""
    user = _get_user(username)
    if not user or not user.is_active:
        raise ValueError("Unbekannter Benutzer.")
    if not verify_password(current_password, username):
        raise ValueError("Aktuelles Passwort ist falsch.")
    new_pw = (new_password or "").strip()
    if len(new_pw) < 10:
        raise ValueError("Passwort muss mindestens 10 Zeichen haben.")
    if current_password == new_pw:
        raise ValueError("Neues Passwort muss sich vom alten unterscheiden.")
    with Session(engine) as session:
        db_user = session.get(AppUser, user.id)
        if not db_user:
            raise ValueError("Unbekannter Benutzer.")
        db_user.password_hash = _password_hash(new_pw)
        db_user.session_epoch = int(db_user.session_epoch or 1) + 1
        session.add(db_user)
        session.commit()


def verify_totp(code: str, username: str | None = None) -> bool:
    import pyotp

    user = _get_user(username) if username else _get_user(get_admin_username())
    secret = (user.totp_secret if user else "") or ""
    if not secret:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def get_admin_username() -> str:
    with Session(engine) as session:
        admin = session.exec(
            select(AppUser).where(AppUser.app_role_key == "super_user")
        ).first()
        if admin:
            return admin.username
        any_user = session.exec(select(AppUser)).first()
        return any_user.username if any_user else "admin"


def is_super_user(username: str) -> bool:
    user = _get_user(username)
    return bool(user and user.app_role_key == "super_user")


def can_evaluate(username: str) -> bool:
    """Phase C: Bewertung. Unassigned und Auftraggeber: False."""
    user = _get_user(username)
    if not user or not user.is_active:
        return False
    return user.app_role_key in EVALUATOR_ROLE_KEYS


def get_user_id(username: str) -> int | None:
    user = _get_user(username)
    return user.id if user else None


def get_username_by_id(user_id: int) -> str | None:
    with Session(engine) as session:
        user = session.get(AppUser, user_id)
        return user.username if user else None


def can_view_evaluator_details(username: str) -> bool:
    """Einzelbewertungen/Bewerter-Namen: nicht für Auftraggeber."""
    user = _get_user(username)
    if not user or not user.is_active:
        return False
    if user.app_role_key == "auftraggeber":
        return False
    return user.app_role_key in EVALUATOR_ROLE_KEYS or user.app_role_key == "super_user"


def totp_is_required(username: str) -> bool:
    """User-Override > App-Rolle > globaler Modus."""
    user = _get_user(username)
    if not user:
        return two_factor_mode() == "required"
    if user.totp_required is True:
        return True
    if user.totp_required is False:
        return False
    if user.app_role_key:
        with Session(engine) as session:
            role = session.get(AppRole, user.app_role_key)
            if role is not None:
                if role.totp_required is True:
                    return True
                if role.totp_required is False:
                    return False
    return two_factor_mode() == "required"


def totp_should_challenge(username: str) -> bool:
    if totp_is_required(username):
        return True
    user = _get_user(username)
    if two_factor_mode() == "optional" and user and user.totp_enabled:
        return True
    return False


def is_2fa_enabled() -> bool:
    """Legacy: global required. Login nutzt totp_should_challenge()."""
    return two_factor_mode() == "required"


def _header(headers: Mapping[str, str] | None, *names: str) -> str:
    if not headers:
        return ""
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    for name in names:
        val = lower.get(name.lower(), "")
        if val:
            return val
    return ""


def resolve_client_ip(
    direct_ip: str | None,
    headers: Mapping[str, str] | None = None,
) -> str:
    peer = (direct_ip or "").strip() or "127.0.0.1"
    try:
        trusted = tuple(get_settings().auth_trusted_proxy_ips or ())
    except Exception:
        trusted = ()
    if not trusted or peer not in trusted:
        return peer
    xff = _header(headers, "x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip() or peer
    real = _header(headers, "x-real-ip", "cf-connecting-ip")
    return real.strip() or peer


def _session_secret() -> str:
    secret = _load_auth_config().get("session_secret") or _ensure_session_secret()
    if not secret:
        raise RuntimeError(
            "config/auth.yaml hat kein session_secret. "
            "Setup ausführen (scripts/maintenance/setup_admin.py)."
        )
    return str(secret)


def _session_epoch() -> int:
    try:
        return int(_load_auth_config().get("session_epoch", 1))
    except (TypeError, ValueError):
        return 1


def _user_session_epoch(username: str) -> int:
    user = _get_user(username)
    if not user:
        return -1
    return int(user.session_epoch or 1)


def _get_signer() -> TimestampSigner:
    return TimestampSigner(_session_secret())


def _unsigned_payload(token: str, max_age_seconds: int) -> str | None:
    if not token:
        return None
    try:
        raw = _get_signer().unsign(token, max_age=max_age_seconds)
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except (SignatureExpired, BadSignature, ValueError, TypeError, RuntimeError):
        return None


def session_username(token: str, max_age_seconds: int = 86400) -> str | None:
    payload = _unsigned_payload(token, max_age_seconds)
    if not payload:
        return None
    parts = payload.split(":")
    if len(parts) >= 3:
        user = _get_user(parts[1])
        if user and int(parts[0]) == int(user.session_epoch or 1):
            return user.username
    return None


def revoke_sessions(username: str | None = None) -> None:
    with Session(engine) as session:
        if username:
            user = session.exec(
                select(AppUser).where(
                    func.lower(AppUser.username) == normalize_username(username).casefold()
                )
            ).first()
            if not user:
                return
            user.session_epoch = int(user.session_epoch or 1) + 1
            session.add(user)
        else:
            cfg = _load_auth_config()
            if cfg:
                cfg["session_epoch"] = _session_epoch() + 1
                _save_auth_config(cfg)
            for user in session.exec(select(AppUser)).all():
                user.session_epoch = int(user.session_epoch or 1) + 1
                session.add(user)
        session.commit()


def create_session_token(username: str | None = None) -> str:
    name = username or get_admin_username()
    user = _get_user(name)
    who = user.username if user else name
    payload = f"{_user_session_epoch(who)}:{who}:{secrets.token_hex(16)}"
    return _get_signer().sign(payload.encode()).decode()


def validate_session_token(token: str, max_age_seconds: int = 3600) -> bool:
    payload = _unsigned_payload(token, max_age_seconds)
    if not payload:
        return False
    parts = payload.split(":")
    try:
        if len(parts) >= 3:
            user = _get_user(parts[1])
            if not user or not user.is_active:
                return False
            return int(parts[0]) == int(user.session_epoch or 1)
        return int(parts[0]) == _session_epoch()
    except (ValueError, TypeError, IndexError):
        return False


def is_ip_blocked(ip: str) -> tuple[bool, str]:
    with Session(engine) as session:
        entry = session.get(IpBlocklist, ip)
        if not entry:
            return False, ""
        until = entry.blocked_until
        if until is not None and until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if entry.level == 3:
            return True, "permanent"
        if until and until > _now():
            minutes = int((until - _now()).total_seconds() / 60)
            return True, f"blocked for {minutes} more minutes"
        return False, ""


def record_unknown_user_attempt(ip: str) -> tuple[int, Optional[datetime]]:
    with Session(engine) as session:
        entry = session.get(IpBlocklist, ip)
        if not entry:
            entry = IpBlocklist(ip=ip, level=0, attempt_count=1)
            entry.blocked_until = None
        else:
            entry.attempt_count += 1
            entry.level = min(max(entry.attempt_count - 1, 0), 3)
            if entry.level > 0:
                duration = BAN_DURATIONS[entry.level]
                entry.blocked_until = (_now() + duration) if duration else None
            else:
                entry.blocked_until = None
        entry.blocked_at = _now()
        entry.reason = "unknown_user"
        session.merge(entry)
        session.commit()
        return entry.level, entry.blocked_until


def record_wrong_password(ip: str, username: str) -> Optional[timedelta]:
    cutoff = _now() - timedelta(hours=1)
    with Session(engine) as session:
        recent = session.exec(
            select(LoginAttempt).where(
                LoginAttempt.ip == ip,
                LoginAttempt.username == username,
                LoginAttempt.success == False,  # noqa: E712
                LoginAttempt.timestamp >= cutoff,
            )
        ).all()
        count = len(recent) + 1
        for threshold, duration in sorted(WRONG_PW_LOCKOUT.items()):
            if count >= threshold:
                return duration
        return None


def log_attempt(ip: str, username: str, success: bool, reason: str = "") -> None:
    with Session(engine) as session:
        session.add(LoginAttempt(ip=ip, username=username, success=success, reason=reason))
        session.commit()


def admin_unblock_ip(ip: str, note: str = "") -> bool:
    with Session(engine) as session:
        entry = session.get(IpBlocklist, ip)
        if not entry:
            return False
        entry.blocked_until = _now() - timedelta(seconds=1)
        entry.level = 0
        entry.admin_note = note or "admin reset"
        session.merge(entry)
        session.commit()
        return True


def list_blocked_ips() -> list[dict]:
    with Session(engine) as session:
        entries = session.exec(select(IpBlocklist)).all()
        now = _now()
        result = []
        for e in entries:
            until = e.blocked_until
            if until is not None and until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if e.level == 3 or (until and until > now):
                result.append({
                    "ip": e.ip,
                    "level": e.level,
                    "attempt_count": e.attempt_count,
                    "blocked_until": until.isoformat() if until else "permanent",
                    "reason": e.reason,
                    "admin_note": e.admin_note,
                })
        return result


def authenticate(ip: str, username: str, password: str) -> dict:
    """Login gegen app_user. Projektrollen (SQLite role) sind kein Account."""
    stored, rec = _find_user(username)
    blocked, reason = is_ip_blocked(ip)
    known = bool(stored and rec and verify_password(password, stored))

    if blocked and not known:
        return {"ok": False, "error": f"IP gesperrt: {reason}"}

    if not stored:
        level, blocked_until = record_unknown_user_attempt(ip)
        log_attempt(ip, username, False, "unknown_user")
        msg = {
            0: "Unbekannter Benutzername.",
            1: "Unbekannter Benutzername. IP für 1 Stunde gesperrt.",
            2: "Wiederholter Versuch. IP für 24 Stunden gesperrt.",
            3: "IP permanent gesperrt. Kontaktiere den Administrator.",
        }.get(level, "Gesperrt.")
        return {"ok": False, "error": msg, "blocked_until": blocked_until}

    user = _get_user(stored)
    if user and not user.is_active:
        log_attempt(ip, stored, False, "inactive")
        return {"ok": False, "error": "Konto deaktiviert."}

    if not known:
        lockout = record_wrong_password(ip, stored)
        log_attempt(ip, stored, False, "wrong_password")
        if lockout:
            with Session(engine) as session:
                entry = IpBlocklist(
                    ip=ip,
                    level=1,
                    blocked_until=_now() + lockout,
                    reason="too_many_wrong_passwords",
                )
                session.merge(entry)
                session.commit()
            return {
                "ok": False,
                "error": (
                    f"Zu viele Fehlversuche. Gesperrt für "
                    f"{int(lockout.total_seconds() / 60)} Minuten."
                ),
            }
        return {"ok": False, "error": "Falsches Passwort."}

    if blocked:
        admin_unblock_ip(ip, note="auto-unblock after successful login")
        log.info("IP %s nach erfolgreichem Login entsperrt", ip)

    log_attempt(ip, stored, True)

    user = _get_user(stored)
    pre_token = _get_signer().sign(
        f"pre:{stored}:{secrets.token_hex(8)}".encode()
    ).decode()

    # Noch kein 2FA: nach Passwort → Einrichtung (QR/Secret) + Code
    if user and not user.totp_enabled:
        if not user.totp_secret:
            secret = _new_totp_secret()
            with Session(engine) as session:
                db_user = session.get(AppUser, user.id)
                if db_user:
                    db_user.totp_secret = secret
                    session.add(db_user)
                    session.commit()
        return {
            "ok": True,
            "needs_enrollment": True,
            "pre_token": pre_token,
            "username": stored,
        }

    # 2FA bereits aktiv: Code eingeben
    if user and user.totp_enabled:
        return {"ok": True, "needs_2fa": True, "pre_token": pre_token}

    return {"ok": True, "needs_2fa": False, "token": create_session_token(stored)}


def authenticate_totp(pre_token: str, code: str) -> dict:
    try:
        raw = _get_signer().unsign(pre_token, max_age=600)
        payload = raw.decode() if isinstance(raw, bytes) else str(raw)
    except (SignatureExpired, BadSignature):
        return {"ok": False, "error": "Session abgelaufen. Bitte neu einloggen."}
    parts = payload.split(":")
    username = parts[1] if len(parts) >= 3 and parts[0] == "pre" else get_admin_username()
    if not verify_totp(code, username):
        return {"ok": False, "error": "Ungültiger 2FA-Code."}
    user = _get_user(username)
    if user and not user.totp_enabled:
        with Session(engine) as session:
            db_user = session.get(AppUser, user.id)
            if db_user:
                db_user.totp_enabled = True
                session.add(db_user)
                session.commit()
    stored = user.username if user else username
    return {"ok": True, "token": create_session_token(stored)}
