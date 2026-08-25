"""
backend/app/routes/auth.py - FastAPI Auth-Routen (Login, Logout, 2FA, Admin).
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from backend.app.jinja_env import templates

from src.m01_config import get_settings
from src.m14_auth import (
    _get_user,
    authenticate,
    authenticate_totp,
    change_password,
    create_session_token,
    get_totp_secret_plain,
    get_totp_uri,
    is_setup_required,
    is_super_user,
    list_app_roles,
    list_blocked_ips,
    list_login_usernames,
    admin_unblock_ip,
    parse_pre_token_username,
    resolve_client_ip,
    revoke_sessions,
    session_username,
    set_user_role,
    setup_admin,
    validate_session_token,
)

router = APIRouter()


def _get_client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "127.0.0.1"
    return resolve_client_ip(direct, request.headers)


def _cookie_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def _set_auth_cookie(resp, name: str, value: str, max_age: int, request: Request) -> None:
    resp.set_cookie(
        name,
        value,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        max_age=max_age,
        path="/",
    )


def _session_timeout_seconds() -> int:
    return get_settings().auth_session_timeout_minutes * 60


def _current_username(request: Request) -> str | None:
    token = request.cookies.get("_auth_token", "")
    return session_username(token, max_age_seconds=_session_timeout_seconds())


def _totp_enroll_response(
    request: Request,
    username: str,
    pre_token: str,
    error: str | None = None,
    status_code: int = 200,
):
    resp = templates.TemplateResponse(
        "auth/totp_enroll.html",
        {
            "request": request,
            "error": error,
            "username": username,
            "totp_uri": get_totp_uri(username),
            "totp_secret": get_totp_secret_plain(username),
        },
        status_code=status_code,
    )
    _set_auth_cookie(resp, "_pre_token", pre_token, 600, request)
    return resp


# ── Setup ──────────────────────────────────────────────────────────────────

@router.get("/auth/setup", response_class=HTMLResponse)
async def setup_get(request: Request):
    if not is_setup_required():
        return RedirectResponse(url="/auth/login")
    return templates.TemplateResponse("auth/setup.html", {"request": request, "error": None})


@router.post("/auth/setup", response_class=HTMLResponse)
async def setup_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
):
    if not is_setup_required():
        return RedirectResponse(url="/auth/login")
    error = None
    if password != password2:
        error = "Passwörter stimmen nicht überein."
    elif len(password) < 10:
        error = "Passwort muss mindestens 10 Zeichen haben."
    if error:
        return templates.TemplateResponse("auth/setup.html", {"request": request, "error": error})

    setup_admin(username, password)
    return RedirectResponse(url="/auth/login", status_code=303)


# ── Login ──────────────────────────────────────────────────────────────────

@router.get("/auth/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if is_setup_required():
        return RedirectResponse(url="/auth/setup")
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None})


@router.post("/auth/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    ip = _get_client_ip(request)
    result = authenticate(ip, username, password)

    if not result["ok"]:
        return templates.TemplateResponse(
            "auth/login.html", {"request": request, "error": result["error"]}, status_code=401
        )

    if result.get("needs_enrollment"):
        return _totp_enroll_response(
            request,
            result["username"],
            result["pre_token"],
        )

    if result.get("needs_2fa"):
        resp = templates.TemplateResponse(
            "auth/totp.html", {"request": request, "error": None}
        )
        _set_auth_cookie(resp, "_pre_token", result["pre_token"], 600, request)
        return resp

    resp = RedirectResponse(url="/dashboard", status_code=303)
    s = get_settings()
    _set_auth_cookie(
        resp, "_auth_token", result["token"], s.auth_session_timeout_minutes * 60, request
    )
    return resp


# ── 2FA ───────────────────────────────────────────────────────────────────

@router.post("/auth/totp", response_class=HTMLResponse)
async def totp_post(request: Request, code: str = Form(...)):
    pre_token = request.cookies.get("_pre_token", "")
    username = parse_pre_token_username(pre_token) or ""

    result = authenticate_totp(pre_token, code)

    if not result["ok"]:
        if username:
            user = _get_user(username)
            if user and not user.totp_enabled:
                return _totp_enroll_response(
                    request, username, pre_token, error=result["error"], status_code=401
                )
        return templates.TemplateResponse(
            "auth/totp.html", {"request": request, "error": result["error"]}, status_code=401
        )

    s = get_settings()
    resp = RedirectResponse(url="/dashboard", status_code=303)
    _set_auth_cookie(
        resp, "_auth_token", result["token"], s.auth_session_timeout_minutes * 60, request
    )
    resp.delete_cookie("_pre_token")
    return resp


# ── Account (Passwort ändern) ─────────────────────────────────────────────

@router.get("/account", response_class=HTMLResponse)
async def account_get(request: Request):
    who = _current_username(request)
    if not who:
        return RedirectResponse(url="/auth/login", status_code=303)
    user = _get_user(who)
    return templates.TemplateResponse(
        "account/index.html",
        {
            "request": request,
            "active_page": "account",
            "username": who,
            "totp_enabled": bool(user and user.totp_enabled),
            "error": None,
            "success": None,
        },
    )


@router.post("/account/password", response_class=HTMLResponse)
async def account_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password2: str = Form(...),
):
    who = _current_username(request)
    if not who:
        return RedirectResponse(url="/auth/login", status_code=303)
    user = _get_user(who)
    error = None
    success = None
    if new_password != new_password2:
        error = "Neue Passwörter stimmen nicht überein."
    else:
        try:
            change_password(who, current_password, new_password)
            success = "Passwort wurde geändert."
            who = _current_username(request) or who
            resp = templates.TemplateResponse(
                "account/index.html",
                {
                    "request": request,
                    "active_page": "account",
                    "username": who,
                    "totp_enabled": bool(user and user.totp_enabled),
                    "error": None,
                    "success": success,
                },
            )
            _set_auth_cookie(
                resp,
                "_auth_token",
                create_session_token(who),
                _session_timeout_seconds(),
                request,
            )
            return resp
        except ValueError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        "account/index.html",
        {
            "request": request,
            "active_page": "account",
            "username": who,
            "totp_enabled": bool(user and user.totp_enabled),
            "error": error,
            "success": success,
        },
        status_code=400 if error else 200,
    )


# ── Logout ────────────────────────────────────────────────────────────────

@router.get("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get("_auth_token", "")
    who = session_username(token)
    if who:
        revoke_sessions(who)
    resp = RedirectResponse(url="/auth/login", status_code=303)
    resp.delete_cookie("_auth_token")
    resp.delete_cookie("_pre_token")
    return resp


# ── Admin: IP-Verwaltung ──────────────────────────────────────────────────

@router.get("/admin/blocked-ips")
async def get_blocked_ips(request: Request):
    from fastapi.responses import JSONResponse
    return JSONResponse(list_blocked_ips())


@router.delete("/admin/blocked-ips/{ip}")
async def unblock_ip(ip: str, request: Request):
    from fastapi.responses import JSONResponse
    from fastapi import HTTPException
    _require_super(request)
    success = admin_unblock_ip(ip, note="admin via API")
    if not success:
        raise HTTPException(404, "IP nicht gefunden")
    return JSONResponse({"ok": True, "ip": ip})


def _require_super(request: Request) -> str:
    from fastapi import HTTPException
    token = request.cookies.get("_auth_token", "")
    who = session_username(token)
    if not who or not is_super_user(who):
        raise HTTPException(403, "Nur Super-User")
    return who


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_get(request: Request):
    _require_super(request)
    return templates.TemplateResponse("auth/users.html", {
        "request": request,
        "users": list_login_usernames(),
        "roles": list_app_roles(),
        "error": None,
    })


@router.post("/admin/users/{username}/role", response_class=HTMLResponse)
async def admin_users_set_role(
    request: Request,
    username: str,
    app_role_key: str = Form(""),
):
    _require_super(request)
    error = None
    role = app_role_key.strip() or None
    try:
        set_user_role(username, role)
    except ValueError as exc:
        error = str(exc)
    return templates.TemplateResponse("auth/users.html", {
        "request": request,
        "users": list_login_usernames(),
        "roles": list_app_roles(),
        "error": error,
    })
