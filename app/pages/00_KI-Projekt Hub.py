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
  - Erstellen Sie Aufgaben automatisch mit KI
  - Verknüpfen Sie Pflichtenheft-Dokumente mit Rollen
- **Projekte**: Erstellen Sie Projekte mit Briefen und Anforderungen
  - Ordnen Sie Dokumente zu (werden in Chat & Batch-QA verwendet)
- **Kontexte**: Dokumentieren Sie Anforderungen und Einschränkungen
- **Tasks**: Verwalten Sie Aufgaben und Überprüfungen
- **Dokumente**: Upload mit 8 Klassifikationen
  - **Pflichtenheft (Projekt/Rolle)**: Anforderungskataloge
  - **FAQ/Fragen-Katalog**: CSV-Upload für Batch-QA (Spalten: Nr, Lieferant, Frage)
  - **Standard/Richtlinie, API-Doku, Tutorials** etc.
  - **Chunk-Größe**: 200-5000 Zeichen pro Upload konfigurierbar
  - **RAG-Integration**: Dokumente werden automatisch für kontextbasierte Antworten verwendet

### 4️⃣ **Chat** (💬 Seite) - Hauptmenü
Die Kern-Funktionalität:
- Wählen Sie ein **Projekt** aus
- Wählen Sie einen **KI-Provider** (OpenAI, Anthropic, Mistral)
- Wählen Sie ein **Modell** und eine **Temperatur**
- Der Chat wird automatisch mit Projekt-Brief, Rollen und Dokumentation angereichert
- **RAG-Integration**: Verwendet automatisch alle dem Projekt zugeordneten Dokumente
- **Markieren Sie Nachrichten**:
  - 💡 Idee
  - ✅ Entscheidung (wird gespeichert & durchsuchbar)
  - 📌 Todo
  - ❓ Annahme
  - ℹ️ Info/Fakt

### 5️⃣ **Fragen-Batch** (🔄 Seite)
Batch-Verarbeitung von Fragen mit KI:
- **CSV-Upload**: Fragen im CSV-Format (Spalten: Nr, Lieferant, Frage)
  - Validierung der CSV-Struktur
  - Wählbarer Delimiter (; , Tab |)
- **3 Rollen-Modi**:
  - **Sammelrolle**: Eine fusionierte Antwort aus allen Perspektiven
  - **Einzelne Rollen**: Separate Antwort-Spalte pro Rolle (Antwort_RoleName)
  - **Eine Rolle**: Antwort aus einer spezifischen Perspektive
- **RAG pro Frage**: Jede Frage durchsucht automatisch projekt-zugeordnete Dokumente
- **Editable Results**: Antworten nachträglich bearbeiten
- **Download**: CSV/Excel mit allen Fragen und Antworten
- **Anwendungsfall**: 480 Lieferantenfragen aus SIMAP-Ausschreibung beantworten

---

## 🔍 RAG-Architektur (Retrieval-Augmented Generation)

Der KI-Projekt Hub verwendet eine **intelligente Dokumenten-Architektur**:

### Dokumenten-Scoping:
- **Task-Generierung**: Nutzt **NUR rollen-spezifische** Dokumente
  - Typ: "Pflichtenheft (Rolle)" mit Rollen-Zuordnung
  - Filter: `role_key` → Nur dieser Rolle zugewiesene Dokumente
  
- **Chat & Batch-QA**: Nutzt **NUR projekt-zugeordnete** Dokumente
  - Alle Klassifikationen (Pflichtenheft Projekt, FAQ, Standards, API-Doku, etc.)
  - Filter: `project_key` → Nur diesem Projekt zugewiesene Dokumente

### Chunk-Größe & Suchverfahren:
- **Konfigurierbar**: 200-5000 Zeichen pro Upload (Standard: 1000)
- **Hybrid-Suche**: Semantische Vektorsuche + Keyword-Matching
- **ChromaDB**: Vektordatenbank für effiziente Ähnlichkeitssuche

### Klassifikationen:
Jedes Dokument wird einer Kategorie zugeordnet:
1. **Pflichtenheft (Projekt)**: Projekt-weite Anforderungen
2. **Pflichtenheft (Rolle)**: Rollen-spezifische Verantwortlichkeiten
3. **FAQ/Fragen-Katalog**: CSV mit Nr, Lieferant, Frage
4. **Standard/Richtlinie**: Compliance, Best Practices
5. **API-Dokumentation**: Technische Schnittstellen
6. **Tutorial/Anleitung**: How-To Guides
7. **Anforderung/Feature**: Feature-Spezifikationen
8. **Sonstiges**: Weitere relevante Dokumentation

---

## 🎯 Beispiel-Workflows

### Workflow 1: Projekt mit KI entwickeln
1. **Projekt anlegen** → Gehen Sie zu **Stammdaten**, erstellen Sie ein Projekt mit Anforderungen
2. **Rollen definieren** → Definieren Sie die Fachbereiche in **Stammdaten**
3. **Dokumente hochladen** → Upload Pflichtenheft, Standards (Chunk-Größe: 1000-1500)
4. **Chat starten** → Öffnen Sie **Chat**, wählen Sie Projekt & Provider
5. **Mit KI entwickeln** → Stellen Sie Fragen im Projekt-Kontext (RAG nutzt automatisch Projekt-Dokumente)
6. **Entscheidungen dokumentieren** → Markieren Sie wichtige Nachrichten als "Entscheidung"
7. **Später nachschlagen** → Entscheidungen sind über Volltextsuche erreichbar

### Workflow 2: 480 Lieferantenfragen beantworten (Batch-QA)
1. **Projekt vorbereiten** → **Stammdaten**: Projekt erstellen, Rollen definieren
2. **Pflichtenheft hochladen** → CSV-Dokument mit Typ "FAQ/Fragen-Katalog" (Spalten: Nr, Lieferant, Frage)
3. **Projekt-Dokumente zuordnen** → **Stammdaten** → Projekte → Dokumente zuweisen
4. **Batch-QA öffnen** → **Fragen-Batch** Seite
5. **CSV auswählen** → Wählen Sie das hochgeladene CSV-Dokument
6. **Rollen-Modus wählen** → "Einzelne Rollen" für Perspektiven-Vergleich
7. **Batch starten** → KI beantwortet alle 480 Fragen mit RAG-Kontext
8. **Antworten bearbeiten** → Korrigieren Sie Antworten im Editor
9. **Download** → Excel/CSV mit allen Antworten exportieren

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
