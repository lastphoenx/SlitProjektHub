"""Login-User in der DB; App-Rolle; YAML-Migration; Sessions pro Benutzer."""
from sqlmodel import SQLModel, create_engine

from src import m03_db, m14_auth
from src.m14_auth import (
    _normalize_auth_config,
    can_evaluate,
    create_session_token,
    create_user,
    list_login_usernames,
    migrate_yaml_users_to_db,
    revoke_sessions,
    setup_admin,
    totp_is_required,
    validate_session_token,
    validate_username,
)


def _install(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    monkeypatch.setattr(m03_db, "engine", eng)
    monkeypatch.setattr(m14_auth, "engine", eng)
    monkeypatch.setattr(m14_auth, "AUTH_CONFIG_PATH", tmp_path / "auth.yaml")
    monkeypatch.setattr(
        m14_auth,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "auth_two_factor_mode": "off",
                "auth_two_factor_enabled": False,
                "auth_trusted_proxy_ips": (),
            },
        )(),
    )
    SQLModel.metadata.create_all(eng)
    m14_auth.seed_app_roles()


def test_normalize_legacy_single_admin():
    cfg = _normalize_auth_config(
        {
            "username": "bob",
            "password_hash": "x",
            "totp_secret": "t",
            "session_secret": "s",
            "session_epoch": 3,
        }
    )
    assert cfg["users"]["bob"]["is_admin"] is True
    assert cfg["users"]["bob"]["session_epoch"] == 3


def test_validate_username_rejects_role_like_titles():
    try:
        validate_username("Projektleiter:in")
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert validate_username("anna.m") == "anna.m"


def test_create_user_unassigned_and_logout(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    setup_admin("admin", "supersecret1")
    create_user("anna", "anderespass1")
    names = {u["username"]: u["app_role_key"] for u in list_login_usernames()}
    assert names["admin"] == "super_user"
    assert names["anna"] is None
    assert can_evaluate("admin")
    assert not can_evaluate("anna")
    assert totp_is_required("admin") is True
    assert totp_is_required("anna") is False

    t_admin = create_session_token("admin")
    t_anna = create_session_token("anna")
    assert validate_session_token(t_admin)
    assert validate_session_token(t_anna)
    revoke_sessions("anna")
    assert validate_session_token(t_admin)
    assert not validate_session_token(t_anna)

    try:
        create_user("Admin", "nochlaenger1")
        assert False, "expected duplicate"
    except ValueError:
        pass


def test_migrate_yaml_admin_and_plain(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    yaml_path = tmp_path / "auth.yaml"
    yaml_path.write_text(
        "session_secret: abcdef0123456789abcdef0123456789abcdef0123456789abcdef01234567\n"
        "users:\n"
        "  admin:\n"
        "    password_hash: dummyhashnotbcrypt\n"
        "    is_admin: true\n"
        "    session_epoch: 2\n"
        "  gast:\n"
        "    password_hash: dummyhash2\n"
        "    is_admin: false\n",
        encoding="utf-8",
    )
    pending = migrate_yaml_users_to_db()
    assert pending == ["gast"]
    by_name = {u["username"]: u["app_role_key"] for u in list_login_usernames()}
    assert by_name["admin"] == "super_user"
    assert by_name["gast"] is None
    leftover = yaml_path.read_text(encoding="utf-8")
    assert "users:" not in leftover
    assert "session_secret:" in leftover
    assert migrate_yaml_users_to_db() == []
