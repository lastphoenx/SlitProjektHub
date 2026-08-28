"""Clusterweiter Ollama-Mutex (Redis auf LearnAI CT 135)."""

from __future__ import annotations

import contextvars
import logging
import os
import time
from contextlib import contextmanager
from typing import Callable

log = logging.getLogger(__name__)

_LOCK_KEY = "ollama:inference:lock"
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""

_wait_callback: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "ollama_wait_callback",
    default=None,
)
_lock_holder: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ollama_lock_holder",
    default=None,
)

_redis_client = None
_redis_unavailable = False


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _lock_redis_url() -> str:
    return (os.getenv("OLLAMA_LOCK_REDIS_URL") or os.getenv("REDIS_URL") or "").strip()


def _redis_client_for_lock():
    global _redis_client, _redis_unavailable
    if _redis_unavailable:
        return None
    url = _lock_redis_url()
    if not url:
        return None
    if _redis_client is None:
        try:
            import redis

            _redis_client = redis.from_url(url, decode_responses=True)
            _redis_client.ping()
        except Exception as exc:
            log.warning("ollama_lock redis unavailable: %s", exc)
            _redis_unavailable = True
            return None
    return _redis_client


def _holder_label(holder: str) -> str:
    raw = str(holder or "").strip()
    if raw.startswith("learnai:"):
        return "LearnAI"
    if raw.startswith("slitprojekthub:"):
        return "SlitProjektHub"
    return raw.split(":", 1)[0] if ":" in raw else raw or "einem anderen Dienst"


def _app_name() -> str:
    return (os.getenv("OLLAMA_LOCK_APP_NAME") or "slitprojekthub").strip() or "slitprojekthub"


@contextmanager
def ollama_lock_holder(holder: str):
    token = _lock_holder.set(holder)
    try:
        yield
    finally:
        _lock_holder.reset(token)


def resolve_lock_holder() -> str:
    explicit = _lock_holder.get()
    if explicit:
        return explicit
    return f"{_app_name()}:inference"


@contextmanager
def ollama_wait_callback(callback: Callable[[str], None] | None):
    token = _wait_callback.set(callback)
    try:
        yield
    finally:
        _wait_callback.reset(token)


@contextmanager
def ollama_inference_lock(holder: str | None = None, *, model: str | None = None):
    if not _env_bool("OLLAMA_LOCK_ENABLED", True):
        yield
        return
    client = _redis_client_for_lock()
    if not client:
        yield
        return

    lock_holder = holder or resolve_lock_holder()
    ttl = int(os.getenv("OLLAMA_LOCK_TTL_SEC") or "960")
    wait_sec = int(os.getenv("OLLAMA_LOCK_WAIT_SEC") or "3600")
    poll = float(os.getenv("OLLAMA_LOCK_POLL_SEC") or "2.0")
    required = _env_bool("OLLAMA_LOCK_REQUIRED", True)
    wait_cb = _wait_callback.get()
    acquired = False
    deadline = time.monotonic() + max(30, wait_sec)

    try:
        while time.monotonic() < deadline:
            try:
                if client.set(_LOCK_KEY, lock_holder, nx=True, ex=ttl):
                    acquired = True
                    break
            except Exception as exc:
                log.warning("ollama_lock acquire failed holder=%s err=%s", lock_holder, exc)
                break
            current = client.get(_LOCK_KEY)
            if current and not str(current).startswith(f"{_app_name()}:"):
                msg = f"Wartet auf Ollama ({_holder_label(str(current))})…"
            else:
                msg = "Wartet auf freien Ollama-Slot…"
            if model:
                msg = f"{msg} (Modell «{model}»)"
            if wait_cb:
                wait_cb(msg)
            time.sleep(poll)

        if not acquired and required:
            raise RuntimeError(
                "Ollama ist durch einen anderen Dienst belegt — bitte in ein paar Minuten erneut versuchen."
            )
        yield
    finally:
        if acquired:
            try:
                client.eval(_RELEASE_SCRIPT, 1, _LOCK_KEY, lock_holder)
            except Exception:
                log.warning("ollama_lock release failed holder=%s", lock_holder, exc_info=True)
