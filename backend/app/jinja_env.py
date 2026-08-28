"""Shared Jinja2 environment for all FastAPI HTML routes."""
from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.templating import Jinja2Templates as StarletteJinja2Templates

from src.m16_idea import format_idea_dt

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BACKEND_DIR / "templates"


class Jinja2Templates(StarletteJinja2Templates):
    """Compat: Starlette 0.40+ request-first API + Jinja2 3.1 cache_key fix."""

    def get_template(self, name: str):
        return self.env.get_template(name)

    def TemplateResponse(self, *args, **kwargs):
        # Legacy calls: TemplateResponse("tpl.html", {"request": request, ...})
        # Starlette 0.40+: TemplateResponse(request, "tpl.html", {...})
        if len(args) >= 2 and isinstance(args[0], str):
            name, context = args[0], args[1]
            request = context.get("request")
            if not isinstance(request, Request):
                raise ValueError("Template context must include Starlette Request as 'request'")
            return super().TemplateResponse(request, name, context, *args[2:], **kwargs)
        return super().TemplateResponse(*args, **kwargs)


templates = Jinja2Templates(directory=TEMPLATES_DIR)
_css = BACKEND_DIR / "static" / "app.css"
templates.env.globals["static_v"] = str(int(_css.stat().st_mtime)) if _css.exists() else "1"
templates.env.filters["idea_dt"] = format_idea_dt
