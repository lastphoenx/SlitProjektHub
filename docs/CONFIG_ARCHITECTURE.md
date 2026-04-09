# RAG Configuration Architecture 🎯

## Zwei-Ebenen-Hierarchie

### 1. **User-Settings** (`config/user_settings.yaml`)
**Runtime-Toggles** die vom Nutzer im UI gesteuert werden.

```yaml
# KI-Provider & Modell
provider: openai
model: gpt-4o
temperature: 0.9

# RAG-Einstellungen (UI-Toggles)
rag_enabled: true                    # Master-Toggle für RAG
rag_query_expansion: false           # Akronym-Auflösung bei niedrigem Confidence
rag_reranking_enabled: true          # Top-15 → Top-7 Reranking

# RAG-Parameter (UI-Slider)
rag_top_k: 7                         # Anzahl finaler Chunks
rag_similarity_threshold: 0.45       # Mindest-Score (45%)
rag_chunk_size: 1200                 # Chunk-Größe für neue Uploads
```

**Wird automatisch gespeichert** wenn Nutzer Settings im UI ändert.

---

### 2. **Retrieval-Config** (`config/retrieval.yaml`)
**Technische Parameter** für Power-User (manuelles Editieren).

```yaml
# BM25 Scoring-Gewichte
bm25:
  coverage_weight: 2.0
  idf_weight: 0.75
  priority_boost: 6.0

# Hybrid RRF
hybrid:
  rrf_k: 60                          # Reciprocal Rank Fusion Parameter

# Query-Expansion (technische Ausführung)
query:
  enable_expansion: true             # ⚠️ YAML-Fallback (nur wenn user_settings fehlt)
  expansion_model: "gpt-4o-mini"     # Welches Modell für Expansion
  expansion_temperature: 0.0
  expansion_max_retries: 1

# Reranking (technische Ausführung)
reranking:
  enable: true                       # ⚠️ YAML-Fallback
  mode: "score"                      # "score" | "llm"
  initial_k: 15                      # Wie viele Kandidaten holen
  final_k: 7                         # Wie viele zurückgeben
  llm_model: "gpt-4o-mini"           # Nur für mode="llm"
```

**Manuelle Bearbeitung** für Feintuning.

---

## Prioritäts-Hierarchie ⚙️

```
User-Settings > YAML-Defaults
```

**Beispiel (Query-Expansion):**
1. Code prüft: Gibt es `enable_expansion` Parameter beim Funktionsaufruf?
   - ✅ Ja → verwende diesen (höchste Priorität)
   - ❌ Nein → weiter zu Schritt 2

2. Code liest `user_settings.yaml`:
   - ✅ `rag_query_expansion: true` → **Expansion läuft**
   - ❌ `rag_query_expansion: false` → **Expansion deaktiviert**
   - ⚠️ Wert fehlt → weiter zu Schritt 3

3. Fallback auf `retrieval.yaml`:
   - `query.enable_expansion: true` → Expansion läuft
   - **Aber:** UI zeigt Warnung dass user_settings fehlt

---

## Wo welche Settings ändern?

| Setting | Wo ändern | Wie ändern |
|---------|-----------|------------|
| **Provider/Modell** | `user_settings.yaml` | UI: KI-Einstellungen (Sidebar) |
| **RAG ON/OFF** | `user_settings.yaml` | UI: Toggle "RAG aktivieren" |
| **Query-Expansion** | `user_settings.yaml` | UI: ToggleQuery-Expansion" |
| **Reranking** | `user_settings.yaml` | UI: Toggle "Reranking aktivieren" |
| **Top-K** | `user_settings.yaml` | UI: Slider "RAG-Kontexte" |
| **Threshold** | `user_settings.yaml` | UI: Slider "Similarity-Threshold" |
| | | |
| **BM25 Gewichte** | `retrieval.yaml` | Manuell editieren |
| **RRF k-Parameter** | `retrieval.yaml` | Manuell editieren |
| **Reranking mode** | `retrieval.yaml` | Manuell editieren (`score` vs `llm`) |
| **Reranking initial_k** | `retrieval.yaml` | Manuell editieren (15-20 empfohlen) |
| **Expansion Modell** | `retrieval.yaml` | Manuell editieren |

---

## Code-Beispiele

### ✅ Korrekte Implementierung (mit Override-Support):

```python
# Legacy Streamlit
rr = retrieve_relevant_chunks_hybrid(
    query,
    enable_expansion=st.session_state.get("global_rag_query_expansion"),  # User-Setting override
)
```

```python
# FastAPI Backend
s = _load_settings_ctx()  # Merged: user_settings > retrieval.yaml
rr = retrieve_relevant_chunks_hybrid(
    query,
    enable_expansion=s.get("rag_query_expansion"),  # User-Setting override
)
```

### ❌ Falsche Implementierung (hardcoded):

```python
# NICHT SO:
rr = retrieve_relevant_chunks_hybrid(query, enable_expansion=True)  # ❌ Ignoriert User-Settings!
```

---

## Debugging

**Problem:** "Query-Expansion läuft obwohl Toggle OFF ist"

**Checklist:**
1. ✅ Prüfe `user_settings.yaml`: Ist `rag_query_expansion: false`?
2. ✅ Prüfe Code: Wird Parameter übergeben? (`enable_expansion=...`)
3. ✅ Prüfe `retrieval.yaml`: Ist `query.enable_expansion: true`? (Fallback!)
4. ✅ Frontend neu laden (Cache leeren)

**Lösung:**
- Entweder `user_settings.yaml` editieren: `rag_query_expansion: false`
- Oder `retrieval.yaml` editieren: `enable_expansion: false` (Fallback)
- **Best Practice:** User-Settings speichern sich automatisch beim Toggle-Klick

---

## Best Practices 🏆

1. **UI-Toggles** → Werden in `user_settings.yaml` gespeichert (automatisch)
2. **Technisches Tuning** → Editiere `retrieval.yaml` (manuell, selten nötig)
3. **Testing:** Setze `user_settings.yaml` zurück wenn du YAML-Defaults testen willst
4. **Dokumentation:** Neue Toggles in BEIDEN Files dokumentieren:
   - `user_settings.yaml`: Kommentar mit Erklärung
   - `retrieval.yaml`: ⚠️ YAML-Fallback Kommentar

---

## Feature-Flags Status ✅

| Feature | User-Toggle | YAML-Default | Status |
|---------|-------------|--------------|--------|
| **RAG** | `rag_enabled` | (kein YAML-Pendant) | ✅ Aktiv |
| **Query-Expansion** | `rag_query_expansion` | `query.enable_expansion` | ✅ Implementiert |
| **Reranking** | `rag_reranking_enabled` | `reranking.enable` | ⚠️ Config OK, Code fehlt |

**Nächster Schritt:** Reranking-Code in `m09_rag.py` implementieren (siehe `reranking_implementation.py`)
