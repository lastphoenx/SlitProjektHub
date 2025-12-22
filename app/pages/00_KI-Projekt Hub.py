# app/pages/00_KI-Projekt Hub.py
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from src.m01_config import get_settings
from src.m03_db import init_db
from src.m06_ui import render_global_llm_settings
from src.m08_llm import providers_available, get_available_models

S = get_settings()
st.set_page_config(page_title="KI-Projekt Hub", page_icon="🚀", layout="wide")

st.session_state.setdefault("global_llm_provider", None)
st.session_state.setdefault("global_llm_model", None)
st.session_state.setdefault("global_llm_temperature", 0.7)

st.markdown("""
<style>
/* Container + Rhythmus */
.block-container{padding-top:1.4rem;padding-bottom:1.4rem}

/* Card-Look für den linken Bereich */
section.main .stColumn:first-child .stMarkdown h3 { margin-bottom:.4rem }
.stTextInput input, textarea, .stTextArea textarea { border-radius:12px !important }

/* Chips dezenter */
.stButton>button{ border-radius:999px; padding:.42rem .9rem }
.stButton>button[kind="secondary"]{ opacity:.9 }

/* Tabelle Kopf besser lesbar */
.stDataFrame thead th{ font-weight:600 }

/* Labels prominenter: wir nutzen fette Markdown-Zeilen über den Inputs */
</style>
""", unsafe_allow_html=True)
st.markdown("""
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 1.2rem; }
.stButton>button { border-radius: 12px; }
.stTextInput input, textarea, .stTextArea textarea { border-radius: 10px !important; }
[data-testid="stSidebar"] { border-right: 1px solid #E6ECFF; }
</style>
""", unsafe_allow_html=True)


st.title("🚀 KI-Projekt Hub")

with st.sidebar:
    render_global_llm_settings()
    st.markdown("---")
    st.markdown("**App-Info**")
    st.text(f"DB: {S.db_url}")

st.markdown("""
# 📖 Willkommen zum KI-Projekt Hub

## 🎯 Was ist das?

Der **KI-Projekt Hub** ist deine zentrale Plattform für strukturierte, kontextabhängige KI-Zusammenarbeit im Projekt-Rahmen. Anders als Standard-Web-Chats mit KI-Providern bietet diese App eine durchdachte Architektur für professionelle Projektentwicklung.

---

## 🌟 Kernvorteile gegenüber normalen Web-Chats

| Feature | Standard Web-Chat | KI-Projekt Hub |
|---------|-----------------|-----------------|
| **Projekt-Kontext** | ❌ Nur Ad-hoc | ✅ Strukturiert über alle Chats |
| **Rollen-Management** | ❌ Nicht vorhanden | ✅ Rollen mit spezifischen Leitlinien |
| **Entscheidungs-Tracking** | ❌ Verloren im Chat | ✅ Dokumentiert & durchsuchbar |
| **Modell-Vergleich** | ❌ Ein Provider | ✅ Parallel testen & vergleichen |
| **Persistent Memory** | ❌ Session-basiert | ✅ Dauerhafte Datenbank |
| **RAG-Integration** | ❌ Nicht vorhanden | ✅ Automatischer Projekt-Kontext |

---

## 💡 Praktische Vorteile

✨ **Bessere KI-Antworten**: Das System befüllt automatisch Projekt-Briefe, Rollen und Anforderungen

🧪 **A/B-Testing**: Testen Sie verschiedene KI-Provider und Modelle direkt nebeneinander

📋 **Struktur**: Markieren Sie wichtige Nachrichten als Entscheidungen, Todos oder Ideen

🔍 **Suchbarkeit**: Finden Sie frühere Diskussionen über spezifische Themen

⚡ **Workflow-Integration**: Entscheidungen werden automatisch in die Projekt-Wissensbasis übernommen

---

## 🚀 Wie bediene ich die App?

### 1️⃣ **KI-Status** (🚦 Seite)
Erste Anlaufstelle um KI-Provider zu testen und zu vergleichen:
- **Ampel-Status**: Sehen Sie auf einen Blick, welche Provider verfügbar sind
- **Parallel-Test**: Senden Sie denselben Prompt an alle Provider und vergleichen Sie die Antworten
- **Live-Chat**: Chatten Sie mit einem Provider und experimentieren Sie mit verschiedenen Modellen & Temperaturen
- **Model & Temp testen**: Hier können Sie A/B testen, um die beste Einstellung herauszufinden

### 2️⃣ **Dashboard** (📊 Seite)
Übersicht über Ihr Projekt:
- Projekt-Statistiken
- Letzte Aktivitäten
- Schneller Zugriff auf Projekt-Elemente

### 3️⃣ **Stammdaten** (🧹 Seite)
Verwaltung aller Master-Daten:
- **Rollen**: Definieren Sie Projekt-Rollen (z.B. "DevOps", "Frontend", "Security")
- **Projekte**: Erstellen Sie Projekte mit Briefen und Anforderungen
- **Kontexte**: Dokumentieren Sie Anforderungen und Einschränkungen
- **Tasks**: Verwalten Sie Aufgaben und Überprüfungen

### 4️⃣ **Chat** (💬 Seite) - Hauptmenü
Die Kern-Funktionalität:
- Wählen Sie ein **Projekt** aus
- Wählen Sie einen **KI-Provider** (OpenAI, Anthropic, Mistral)
- Wählen Sie ein **Modell** und eine **Temperatur**
- Der Chat wird automatisch mit Projekt-Brief, Rollen und Dokumentation angereichert
- **Markieren Sie Nachrichten**:
  - 💡 Idee
  - ✅ Entscheidung (wird gespeichert & durchsuchbar)
  - 📌 Todo
  - ❓ Annahme
  - ℹ️ Info/Fakt

---

## 🎯 Beispiel-Workflow

1. **Projekt anlegen** → Gehen Sie zu **Stammdaten**, erstellen Sie ein Projekt mit Anforderungen
2. **Rollen definieren** → Definieren Sie die Fachbereiche in **Stammdaten**
3. **Chat starten** → Öffnen Sie **Chat**, wählen Sie Projekt & Provider
4. **Mit KI entwickeln** → Stellen Sie Fragen im Projekt-Kontext
5. **Entscheidungen dokumentieren** → Markieren Sie wichtige Nachrichten als "Entscheidung"
6. **Später nachschlagen** → Entscheidungen sind über Volltextsuche erreichbar

---

## ⚙️ Globale KI-Einstellungen

Oben rechts in der Sidebar können Sie jederzeit die **globalen KI-Einstellungen** ändern:
- 🤖 **KI-Provider**: Der Standard-Provider für alle Seiten
- 🧠 **Modell**: Das Modell für diesen Provider
- 🌡️ **Temperatur**: Kreativität einstellen (0=präzise, 2=sehr variabel)

Diese Einstellungen gelten über alle Seiten hinweg und können jederzeit geändert werden.

---

## 💡 Pro-Tipps

🔗 **Verlauf durchsuchen**: Im **KI-Status** können Sie versteckte Nachrichten wieder einblenden

📈 **Modelle vergleichen**: Nutzen Sie die **KI-Status** Seite um zu sehen, welches Modell für Ihre Aufgabe am besten ist

🎬 **Templates**: Speichern Sie häufig verwendete Prompts in Ihren Projekt-Briefen

🔄 **Iteration**: Verwenden Sie die Entscheidungs-Historie um auf Entscheidungen aufzubauen

---

**Viel Erfolg bei der Zusammenarbeit mit KI! 🚀**
""")

st.info("💡 **Tipp**: Lesen Sie die **Stammdaten**-Seite um ein Projekt zu erstellen und dann zur **Chat**-Seite zu wechseln!")

init_db()
