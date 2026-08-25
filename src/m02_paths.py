"""
02_paths.py
Zentrale Pfad- und Namens-Helfer.
"""
from __future__ import annotations
from pathlib import Path
from .m01_config import get_settings

S = get_settings()

def role_dir() -> Path:
    return S.rag_dir / "roles"

def task_dir() -> Path:
    return S.rag_dir / "tasks"

def context_dir() -> Path:
    return S.rag_dir / "contexts"

def project_dir() -> Path:
    return S.rag_dir / "projects"

def db_file() -> Path:
    return S.db_dir / "slitproj.db"

def docs_dir() -> Path:
    """Directory for uploaded/linked documents shared by Tasks/Contexts.
    We keep it under RAG root to stay consistent with other content folders.
    """
    return S.rag_dir / "docs"
