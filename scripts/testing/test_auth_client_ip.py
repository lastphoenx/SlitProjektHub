from src.m14_auth import resolve_client_ip


def test_ignores_forwarded_headers_without_trusted_proxy(monkeypatch):
    monkeypatch.setattr(
        "src.m14_auth.get_settings",
        lambda: type("S", (), {"auth_trusted_proxy_ips": ()})(),
    )
    ip = resolve_client_ip(
        "10.0.0.5",
        {"X-Forwarded-For": "203.0.113.9", "X-Real-IP": "203.0.113.9"},
    )
    assert ip == "10.0.0.5"


def test_uses_xff_only_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(
        "src.m14_auth.get_settings",
        lambda: type("S", (), {"auth_trusted_proxy_ips": ("10.0.0.1",)})(),
    )
    ip = resolve_client_ip(
        "10.0.0.1",
        {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
    )
    assert ip == "203.0.113.9"
