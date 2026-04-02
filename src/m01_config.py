"""
01_config.py
Lädt .env und YAML-Konfiguration. Liefert Settings als Singleton.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")  # ignoriert, falls nicht vorhanden

USER_SETTINGS_PATH = BASE_DIR / "config" / "user_settings.yaml"

@dataclass(frozen=True)
class Settings:
    app_name: str
    db_url: str
    db_wal_mode: bool  # WAL-Modus: config.yaml → database.wal_mode
    base_dir: Path
    data_dir: Path
    db_dir: Path
    rag_dir: Path
    backups_dir: Path
    ui_title: str
    ui_sidebar_title: str
    llm_defaults: dict

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    cfg_path = BASE_DIR / "config" / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    base_dir = (BASE_DIR / cfg["paths"]["base_dir"]).resolve()
    data_dir = (BASE_DIR / cfg["paths"]["data_dir"]).resolve()
    db_dir = (BASE_DIR / cfg["paths"]["db_dir"]).resolve()
    rag_dir = (BASE_DIR / cfg["paths"]["rag_dir"]).resolve()
    backups_dir = (BASE_DIR / cfg["paths"]["backups_dir"]).resolve()

    return Settings(
        app_name=cfg["app_name"],
        db_url=cfg["database"]["url"],
        db_wal_mode=cfg["database"].get("wal_mode", False),  # WAL-Modus, Standard: aus
        base_dir=base_dir,
        data_dir=data_dir,
        db_dir=db_dir,
        rag_dir=rag_dir,
        backups_dir=backups_dir,
        ui_title=cfg["ui"]["title"],
        ui_sidebar_title=cfg["ui"]["sidebar_title"],
        llm_defaults=cfg.get("llm_defaults", {}),
    )

def load_user_settings() -> dict:
    """Lädt persistente User-Settings (LLM-Einstellungen etc.)"""
    if USER_SETTINGS_PATH.exists():
        with open(USER_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

def save_user_settings(settings: dict) -> None:
    """Speichert User-Settings persistent"""
    USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(settings, f, default_flow_style=False, allow_unicode=True)

