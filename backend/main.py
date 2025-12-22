# backend/main.py - FastAPI Backend für SlitProjektHub
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import sys
from pathlib import Path

# Add src to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Working directory auf ROOT setzen (wichtig für DB-Pfad!)
import os
os.chdir(ROOT)

from src.m07_roles import list_roles_df, load_role, upsert_role, soft_delete_role
from src.m07_tasks import list_tasks_df, load_task, upsert_task
from src.m08_llm import providers_available, generate_role_text, generate_summary

app = FastAPI(
    title="SlitProjektHub API",
    description="Backend API für das Projektmanagement-Tool",
    version="1.0.0"
)

# CORS für Streamlit Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://localhost:8502"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ PYDANTIC MODELS ============
class RoleCreate(BaseModel):
    title: str
    group_name: Optional[str] = None
    body_text: Optional[str] = ""

class RoleResponse(BaseModel):
    key: str
    title: str
    group_name: Optional[str]
    body_text: str

# ============ ROLES API ============
@app.get("/api/roles", response_model=List[dict])
async def get_roles():
    """Alle Rollen abrufen"""
    try:
        df = list_roles_df(include_deleted=False)
        if df is None or df.empty:
            return []
        return df.to_dict('records')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/roles/{role_key}")
async def get_role(role_key: str):
    """Einzelne Rolle abrufen"""
    try:
        role_obj, role_body = load_role(role_key)
        if not role_obj:
            raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
        
        return {
            "key": role_obj.key,
            "title": role_obj.title,
            "short_code": role_obj.short_code,
            "body_text": role_body or ""
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/roles")
async def create_role(role: RoleCreate):
    """Neue Rolle erstellen oder bestehende aktualisieren"""
    try:
        role_obj, created = upsert_role(
            title=role.title,
            body_text=role.body_text
        )
        
        return {
            "key": role_obj.key,
            "title": role_obj.title,
            "short_code": role_obj.short_code,
            "created": created
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/roles/{role_key}")
async def delete_role(role_key: str):
    """Rolle löschen"""
    try:
        success = soft_delete_role(role_key)
        if not success:
            raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============ LLM API ============
@app.get("/api/llm/providers")
async def get_llm_providers():
    """Verfügbare LLM-Provider abrufen"""
    try:
        return {"providers": providers_available()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/llm/generate-role")
async def generate_role(provider: str, title: str, group_name: Optional[str] = None):
    """KI-generierte Rollenbeschreibung"""
    try:
        role_text = generate_role_text(provider, title, group_name)
        return {"generated_text": role_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/llm/generate-summary")
async def generate_summary_api(provider: str, title: str, content: str):
    """KI-generierte Zusammenfassung"""
    try:
        summary = generate_summary(provider, title, content)
        return {"summary": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============ HEALTH CHECK ============
@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "SlitProjektHub API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)