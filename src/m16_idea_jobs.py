"""Eine serielle KI-Warteschlange für Projektideen (Ollama-Modellwechsel abfangen)."""
from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

_JOB_Q: queue.Queue = queue.Queue()
_START_LOCK = threading.Lock()
_ENQUEUE_LOCK = threading.Lock()
_WORKER_STARTED = False
ACTIVE = ("queued", "running")


def parse_job(raw: Optional[str]) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(idea_id: int, job: dict[str, Any]) -> dict[str, Any]:
    from .m16_idea import set_idea_job_json

    set_idea_job_json(idea_id, json.dumps(job, ensure_ascii=False))
    return job


def update_job(idea_id: int, **fields: Any) -> Optional[dict[str, Any]]:
    from .m16_idea import get_idea

    idea = get_idea(idea_id)
    if not idea:
        return None
    job = parse_job(idea.ki_job_json) or {}
    job.update(fields)
    return _write(idea_id, job)


def _friendly_err(exc: BaseException) -> str:
    s = str(exc) or type(exc).__name__
    low = s.lower()
    if "timeout" in low or "timed out" in low:
        return (
            "Ollama hat nicht rechtzeitig geantwortet (anderes Modell geladen, "
            "Modellwechsel oder hohe Last). Die Anfrage war in der Warteschlange — bitte erneut versuchen."
        )
    if "connection" in low or "refused" in low:
        return "Ollama ist nicht erreichbar."
    return s[:400]


def _queue_message(provider: str, model: str) -> str:
    if (provider or "").strip().lower() != "ollama":
        return "In der Warteschlange — startet gleich."
    from .m08_llm import ollama_runtime_status

    st = ollama_runtime_status(model)
    return st.get("message") or "In der Warteschlange."


def job_public(job: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not job:
        return {"status": "idle", "queue_size": _JOB_Q.qsize()}
    return {
        "id": job.get("id"),
        "kind": job.get("kind") or "",
        "status": job.get("status") or "idle",
        "message": job.get("message") or "",
        "error": job.get("error") or "",
        "queue_size": _JOB_Q.qsize(),
        "provider": job.get("provider") or "",
        "model": job.get("model") or "",
        "ollama": job.get("ollama") or {},
        "already_running": bool(job.get("already_running")),
    }


def idea_job_status(idea_id: int) -> dict[str, Any]:
    from .m16_idea import get_idea

    idea = get_idea(idea_id)
    if not idea:
        return {"status": "idle"}
    job = parse_job(idea.ki_job_json)
    if not job:
        return {"status": "idle", "queue_size": _JOB_Q.qsize()}
    if (job.get("status") or "") == "running" and (job.get("provider") or "") == "ollama":
        from .m08_llm import ollama_runtime_status

        st = ollama_runtime_status(job.get("model") or "")
        job["ollama"] = {k: st.get(k) for k in ("loaded", "other_loaded", "switching", "ok")}
        if st.get("switching"):
            job["message"] = st.get("message") or job.get("message")
    return job_public(job)


def consume_done_job(idea_id: int) -> Optional[dict[str, Any]]:
    """Erledigte Jobs beim Seitenaufruf leeren, Fehler bleiben sichtbar."""
    from .m16_idea import get_idea, set_idea_job_json

    idea = get_idea(idea_id)
    if not idea:
        return None
    job = parse_job(idea.ki_job_json)
    if job and job.get("status") == "done":
        set_idea_job_json(idea_id, None)
        return job
    return job


def _recover_stale() -> None:
    from .m16_idea import list_ideas

    for idea in list_ideas(include_deleted=True):
        job = parse_job(getattr(idea, "ki_job_json", None))
        if job and job.get("status") in ACTIVE:
            job["status"] = "error"
            job["error"] = "Unterbrochen (Dienst neu gestartet). Bitte den Vorgang erneut starten."
            job["message"] = job["error"]
            _write(idea.id, job)


def ensure_worker() -> None:
    global _WORKER_STARTED
    with _START_LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True
        try:
            _recover_stale()
        except Exception:
            log.exception("Stale KI-Jobs konnten nicht bereinigt werden")
        t = threading.Thread(target=_worker, name="idea-ki-jobs", daemon=True)
        t.start()


def enqueue(
    *,
    kind: str,
    idea_id: int,
    run: Callable[[], Any],
    provider: str,
    model: str,
    overwrite_user: bool = False,
) -> dict[str, Any]:
    ensure_worker()
    from .m16_idea import get_idea

    with _ENQUEUE_LOCK:
        idea = get_idea(idea_id)
        existing = parse_job(idea.ki_job_json) if idea else None
        if existing and existing.get("status") in ACTIVE:
            existing["already_running"] = True
            return existing

        from .m08_llm import ollama_runtime_status

        ollama = {}
        if (provider or "").strip().lower() == "ollama":
            st = ollama_runtime_status(model)
            ollama = {k: st.get(k) for k in ("loaded", "other_loaded", "switching", "ok")}

        waiting = _JOB_Q.qsize()
        pos = waiting + 1
        msg = _queue_message(provider, model)
        if pos > 1:
            msg = f"In der Warteschlange (Position {pos}). {msg}"

        job = {
            "id": uuid4().hex[:12],
            "kind": kind,
            "status": "queued",
            "message": msg,
            "error": "",
            "overwrite_user": overwrite_user,
            "queued_at": _now(),
            "provider": provider,
            "model": model,
            "ollama": ollama,
        }
        _write(idea_id, job)
        _JOB_Q.put({
            "idea_id": idea_id,
            "job_id": job["id"],
            "kind": kind,
            "run": run,
            "overwrite_user": overwrite_user,
            "provider": provider,
            "model": model,
        })
        return job


def _worker() -> None:
    while True:
        item = _JOB_Q.get()
        try:
            _run_item(item)
        except Exception:
            log.exception("KI-Job-Worker: unerwarteter Fehler")
        finally:
            _JOB_Q.task_done()


def _run_item(item: dict[str, Any]) -> None:
    from .m08_llm import ollama_runtime_status
    from .m16_idea import reset_user_assessment_from_ai

    idea_id = item["idea_id"]
    kind = item.get("kind") or ""
    provider = (item.get("provider") or "").strip().lower()
    model = item.get("model") or ""
    running_msg = "Bewertung läuft …" if kind == "assess" else "Generierung läuft …"
    ollama = {}
    if provider == "ollama":
        st = ollama_runtime_status(model)
        ollama = {k: st.get(k) for k in ("loaded", "other_loaded", "switching", "ok")}
        running_msg = st.get("message") or running_msg
        if st.get("switching"):
            running_msg = (
                f"{st['message']} Sobald der andere Task fertig ist, startet «{model}» automatisch."
            )
    update_job(
        idea_id,
        status="running",
        message=running_msg,
        started_at=_now(),
        ollama=ollama,
        error="",
    )
    try:
        result = item["run"]()
    except Exception as exc:
        log.exception("KI-Job %s idea_id=%s fehlgeschlagen", kind, idea_id)
        err = _friendly_err(exc)
        update_job(idea_id, status="error", error=err, message=err, finished_at=_now())
        return

    if kind == "assess":
        if result is None:
            err = "KI-Antwort nicht auswertbar (Timeout, Modellwechsel oder ungültiges JSON)."
            update_job(idea_id, status="error", error=err, message=err, finished_at=_now())
            return
        if item.get("overwrite_user"):
            try:
                reset_user_assessment_from_ai(idea_id)
            except Exception:
                log.exception("Einschätzung nach KI-Bewertung nicht überschrieben, idea_id=%s", idea_id)
        update_job(
            idea_id,
            status="done",
            message="Bewertung fertig.",
            error="",
            finished_at=_now(),
        )
        return

    obj, err = result if isinstance(result, tuple) else (result, None)
    if not obj:
        msg = err or "Generierung fehlgeschlagen."
        update_job(idea_id, status="error", error=msg, message=msg, finished_at=_now())
        return
    update_job(
        idea_id,
        status="done",
        message="Generierung fertig.",
        error="",
        finished_at=_now(),
    )
