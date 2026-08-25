"""Shared Jinja2 environment for all FastAPI HTML routes."""
from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.templating import Jinja2Templates as StarletteJinja2Templates

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
