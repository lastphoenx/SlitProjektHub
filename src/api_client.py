# src/api_client.py - API Client für Streamlit Frontend
import requests
import streamlit as st
from typing import List, Dict, Optional
import json

class APIClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        
    def _request(self, method: str, endpoint: str, **kwargs):
        """Basis HTTP-Request mit Fehlerbehandlung"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError:
            st.error("❌ Keine Verbindung zur API. Ist der Backend-Server gestartet?")
            return None
        except requests.exceptions.HTTPError as e:
            st.error(f"❌ API-Fehler: {e}")
            return None
        except Exception as e:
            st.error(f"❌ Unerwarteter Fehler: {e}")
            return None
    
    # ============ ROLES API ============
    def get_roles(self) -> Optional[List[Dict]]:
        """Alle Rollen abrufen"""
        return self._request("GET", "/api/roles")
    
    def get_role(self, role_key: str) -> Optional[Dict]:
        """Einzelne Rolle abrufen"""
        return self._request("GET", f"/api/roles/{role_key}")
    
    def create_role(self, title: str, group_name: str = None, body_text: str = "") -> Optional[Dict]:
        """Rolle erstellen/aktualisieren"""
        data = {
            "title": title,
            "group_name": group_name,
            "body_text": body_text
        }
        return self._request("POST", "/api/roles", json=data)
    
    def delete_role(self, role_key: str) -> bool:
        """Rolle löschen"""
        result = self._request("DELETE", f"/api/roles/{role_key}")
        return result is not None and result.get("success", False)
    
    # ============ LLM API ============
    def get_llm_providers(self) -> Optional[List[str]]:
        """Verfügbare LLM-Provider"""
        result = self._request("GET", "/api/llm/providers")
        return result.get("providers", []) if result else []
    
    def generate_role_text(self, provider: str, title: str, group_name: str = None) -> Optional[str]:
        """KI-Rollenbeschreibung generieren"""
        data = {"provider": provider, "title": title, "group_name": group_name}
        result = self._request("POST", "/api/llm/generate-role", json=data)
        return result.get("generated_text") if result else None
    
    def generate_summary(self, provider: str, title: str, content: str) -> Optional[str]:
        """KI-Zusammenfassung generieren"""
        data = {"provider": provider, "title": title, "content": content}
        result = self._request("POST", "/api/llm/generate-summary", json=data)
        return result.get("summary") if result else None
    
    # ============ HEALTH CHECK ============
    def health_check(self) -> bool:
        """API-Status prüfen"""
        result = self._request("GET", "/health")
        return result is not None and result.get("status") == "healthy"

# Globaler API-Client (Singleton-Pattern)
@st.cache_resource
def get_api_client() -> APIClient:
    return APIClient()