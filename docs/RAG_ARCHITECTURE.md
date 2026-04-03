# RAG-Architektur: Embedding & Chunking

## Übersicht

SlitProjektHub verwendet ein **Hybrid RAG-System** (Retrieval-Augmented Generation) um relevante Dokumente für KI-Antworten zu finden:

```
Dokument-Upload → Extraktion → Chunking → Embedding → Speicherung
                                                            ↓
User-Query → Embedding → Similarity Search → Hybrid Filter → Top-K Results
```

---

## 1. Dokument-Verarbeitung Pipeline

### 1.1 Upload & Text-Extraktion

**Code:** `src/m09_docs.py` - Funktion `ingest_document()`

#### Unterstützte Formate
- **PDF**: PyPDF2 + pdfplumber (Fallback-Strategie)
- **DOCX**: python-docx
- **CSV**: pandas (Semikolon-getrennt, Pflicht-Spalten: Nr, Lieferant, Frage)
- **Text**: .md, .txt, .json, .yaml, .yml

#### SHA256 Duplikat-Erkennung
```python
file_hash = calculate_sha256(file_bytes)
existing = session.exec(select(Document).where(Document.sha256_hash == file_hash)).first()
```

**Bei Re-Upload (gleicher Hash):**
- Hard-Delete alter Chunks
- Vollständiges Re-Processing mit neuem `chunk_size`
- Metadaten-Update (Klassifizierung, Rollen-Links)

---

### 1.2 Chunking-Algorithmus

**Code:** `src/m09_docs.py` - Funktion `chunk_text()`

#### Overlapping Chunks
```python
chunk_size = 1000      # Standard: 1000 Zeichen
overlap = 200          # 20% Überlappung (automatisch: chunk_size // 5)
```

**Algorithmus:**
1. Startposition bei 0
2. Schneide Chunk von `start` bis `start + chunk_size`
3. Intelligentes Schneiden:
   - Suche letzten `.` oder `\n` im Chunk
   - Wenn > 50% von chunk_size → schneide dort
   - Sonst: harte Grenze bei chunk_size
4. Nächster Start: `end - overlap` (200 Zeichen Überlappung)

**Beispiel:**
```
Text: "Das ist Satz 1. Das ist Satz 2. Das ist Satz 3. Das ist Satz 4."
Chunk 1 (0-1000):   "Das ist Satz 1. Das ist Satz 2. Das ist Satz 3."
Chunk 2 (800-1800): "Satz 3. Das ist Satz 4. ..."  [Überlappung: "Satz 3"]
```

**Vorteil:** Sätze die über Chunk-Grenzen gehen bleiben in mindestens einem Chunk vollständig erhalten.

#### CSV-Spezialfall
```python
# Jede Zeile = 1 Chunk
for idx, row in df.iterrows():
    chunk_dict = {
        "Nr": str(row["Nr"]),
        "Lieferant": str(row["Lieferant"]),
        "Frage": str(row["Frage"]),
        "Antwort": str(row["Antwort"])
    }
    chunks.append(chunk_dict)
```

**Gespeichert als:** JSON-String im `chunk_text` Feld.

---

### 1.3 Contextual Chunking (Neu)

**Problem:** Chunks ohne Metadaten verlieren Kontext.

**Lösung:** Metadata-Prefix vor jedem Chunk.

#### Format: DOCX/PDF
```
[{classification} | {filename}]
{chunk_text}
```

**Beispiel:**
```
[Pflichtenheft (Projekt) | Pflichtenheft Unisport Webportal.docx]
Das Webportal muss folgende API-Schnittstellen bereitstellen:
1. REST-API für Kursverwaltung
2. OAuth2 Authentifizierung
3. Payment-Gateway Integration
```

#### Format: CSV
```
[CSV | {filename} | Frage {Nr}]
{JSON-Daten}
```

**Beispiel:**
```
[CSV | simap_Fragen_unisport.csv | Frage 42]
{"Nr": "42", "Lieferant": "Vendor X", "Frage": "Welche Zahlungsmethoden...", "Antwort": ""}
```

#### Code-Implementierung
**CSV:**
```python
frage_texts = []
for row in csv_chunks:
    nr = row.get("Nr", "?")
    frage = row.get("Frage", "")
    prefix = f"[CSV | {file_name} | Frage {nr}]\n"
    frage_texts.append(prefix + frage)
embeddings = embed_texts_batch(frage_texts)
```

**DOCX/PDF:**
```python
contextual_chunks = []
prefix = f"[{classification} | {file_name}]\n"
for chunk_str in chunks:
    contextual_chunks.append(prefix + chunk_str)
embeddings = embed_texts_batch(contextual_chunks)
```

#### Effekte
✅ **Keyword-Boost:** "Preisblatt" matcht alle Chunks aus diesem Dokument  
✅ **Embedding-Awareness:** Model lernt Dokument-Typ als Teil des Kontexts  
✅ **Klassifizierungs-Signal:** "Pflichtenheft (Projekt)" vs. "Anforderung/Feature"  
✅ **Dateiname-Verstärkung:** Kombiniert mit Dateiname-Boost-Algorithmus  

---

### 1.4 Embedding-Generierung

**Code:** `src/m09_rag.py` - Funktion `embed_texts_batch()`

#### Embedding-Modell
```python
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536
```

**Eigenschaften:**
- Provider: **OpenAI Cloud API** (nicht lokal)
- Kosten: **$0.02 / 1M Tokens** (~$0.001 pro Dokument)
- Max Input: **8192 Tokens GESAMT pro Batch**
- Output: 1536-dimensionaler Vektor (float)

#### Batch-Processing
```python
MAX_CHARS_PER_TEXT = 6000      # ~1500 Tokens pro Text
MAX_BATCH_SIZE = 20            # Max 20 Texte pro API-Call

for start in range(0, len(texts), MAX_BATCH_SIZE):
    batch = texts[start:start + MAX_BATCH_SIZE]
    
    # Truncate zu lange Texte
    safe_batch = [t[:MAX_CHARS_PER_TEXT] if len(t) > MAX_CHARS_PER_TEXT else t for t in batch]
    
    # API-Call
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=safe_batch,
        encoding_format="float"
    )
```

**Performance:**
- Vorher (einzeln): 100 Chunks × 50ms = **5 Sekunden**
- Nachher (Batch): 5 Batches × 200ms = **1 Sekunde** ✅

#### Fallback bei Token-Overflow
```python
try:
    response = client.embeddings.create(...)
except Exception as batch_error:
    if "maximum context length" in str(batch_error).lower():
        # Einzelne API-Calls als Fallback
        for input_text in inputs:
            single_response = client.embeddings.create(model=..., input=input_text)
```

**Trigger:** Projekt mit 73 Stammdaten-Chunks (~18.000 Tokens) → Automatisches Splitting.

---

### 1.5 Datenbank-Speicherung

#### SQLite: `DocumentChunk` Tabelle
```python
class DocumentChunk(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id")
    chunk_index: int                    # Position im Dokument (0-basiert)
    chunk_text: str                     # MIT Contextual Prefix
    embedding: str | None               # JSON-Array: [0.123, -0.456, ...]
    embedding_model: str | None         # "text-embedding-3-small"
    tokens_count: int | None            # ~len(chunk_text) // 4
```

#### ChromaDB: Projekt-Stammdaten
**Code:** `src/m07_projects.py` - Funktion `_index_project_chunks_to_chromadb()`

Separater Index für:
- Projekt-Beschreibung
- Rollen (Namen + Beschreibungen)
- Tasks (Titel + Beschreibungen)
- Kontexte

**Format:**
```python
collection.add(
    ids=["project_123_chunk_0", "project_123_chunk_1", ...],
    documents=raw_texts,                 # Liste von Strings
    embeddings=embeddings_to_add,        # Liste von Vektoren
    metadatas=[{"source": "project_data", "project_id": "123"}, ...]
)
```

---

## 2. Retrieval-Algorithmus

### 2.1 Query-Embedding

```python
query_embedding = embed_text(query)  # 1536-dim Vektor
```

---

### 2.2 Similarity Search (Semantic)

**Code:** `src/m09_rag.py` - Funktion `retrieve_relevant_chunks()`

#### Cosine Similarity
```python
def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    magnitude_a = math.sqrt(sum(a ** 2 for a in vec_a))
    magnitude_b = math.sqrt(sum(b ** 2 for b in vec_b))
    return dot_product / (magnitude_a * magnitude_b)
```

**Range:** 0.0 (orthogonal) bis 1.0 (identisch)  
**Typische Werte:**
- 0.80-1.00: Sehr hohe Ähnlichkeit (exakte Begriffe)
- 0.50-0.80: Semantisch verwandt (Synonyme, Kontext)
- 0.30-0.50: Thematisch ähnlich (gleiche Domäne)
- 0.00-0.30: Geringe oder keine Relevanz

#### Threshold-Filtering
```python
if similarity >= threshold:  # Standard: 0.45
    doc_scores.append({
        "chunk_id": chunk.id,
        "document_id": doc.id,
        "filename": doc.filename,
        "classification": doc.classification,
        "text": chunk.chunk_text,
        "similarity": round(similarity, 3)
    })
```

---

### 2.3 Keyword Search

**Code:** `src/m09_rag.py` - Funktion `_keyword_search()`

#### Einfaches LIKE-Matching
```python
keywords = query.lower().split()  # ["preisblatt", "chf", "kosten"]

matches = []
for chunk in chunks:
    text = (chunk.chunk_text or "").lower()
    match_count = sum(1 for kw in keywords if kw in text)
    
    if match_count > 0:
        matches.append({
            "chunk_id": chunk.id,
            "match_score": match_count / len(keywords)  # 0.0 - 1.0
        })
```

**Beispiel:**
- Query: "Preisblatt CHF Kosten"
- Chunk: `[Anforderung/Feature | Anhang2_Preisblatt_Unisport.pdf]\nUmsetzungskosten | 50000 CHF`
- **match_count:** 2 von 3 (Preisblatt, CHF) = **66%**

#### Limit
```python
limit = rag_top_k * 10  # Standard: 7 × 10 = 70 Treffer
```

**Grund:** Deckt alle Dokumente ab, auch wenn nur 5-10 Chunks pro Dokument relevant sind.

---

### 2.4 Hybrid-Algorithmus

**Code:** `src/m09_rag.py` - Funktion `retrieve_relevant_chunks_hybrid()`

```
┌─────────────────────────────────────────────────┐
│ 1. KANDIDATEN-SAMMLUNG                          │
│    Semantic (limit×5, threshold×0.35)           │
│    + Keyword (limit×10)                         │
│    → Breites Netz spannen                       │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 2. DATEINAME-BOOST                              │
│    Query-Wörter im Dateinamen? → +0.04 Score   │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 3. GARANTIERTE MINDEST-SLOTS                    │
│    Pro Dokument: best_chunk ≥ threshold×0.45   │
│    → mind. 1 Slot garantiert                    │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 4. PFLICHTENHEFT-FALLBACK                       │
│    "Pflichtenheft (Projekt)" immer vertreten    │
│    (wenn Score > 0.10)                          │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ 5. DIVERSITY-CAP + QUALITÄTS-FÜLLUNG           │
│    Max max(2, limit//2) Chunks pro Dokument    │
│    Restliche Slots: Score ≥ threshold×0.45      │
└─────────────────────────────────────────────────┘
                    ↓
          [Top-K Results (sortiert)]
```

#### Parameter
```python
threshold = 0.45                              # User-konfigurierbar
guaranteed_threshold = threshold * 0.45       # ~0.20
discovery_threshold = min(threshold * 0.35, 0.15)  # ~0.15
max_per_doc = max(2, limit // 2)             # Bei limit=7 → 3
```

#### Beispiel-Ergebnis
**Query:** "Preisblatt CHF Umsetzungskosten"  
**limit=7, threshold=0.45**

| Rank | Score | Dokument | Grund |
|---|---|---|---|
| 1 | 0.54 | Preisblatt.pdf | Keyword (94%) + Dateiname-Boost (+4%) |
| 2 | 0.50 | Preisblatt.pdf | Keyword (94%) + Semantic |
| 3 | 0.46 | Pflichtenheft.docx | Semantic + Fallback |
| 4 | 0.42 | Pflichtenheft.docx | Semantic (Diversity-Cap: 2/3) |
| 5 | 0.40 | Pflichtenheft.docx | Semantic (Diversity-Cap: 3/3 erreicht) |
| 6 | 0.35 | Beilagen.pdf | Garantierter Slot (best_chunk ≥ 0.20) |
| 7 | 0.28 | Anhang.docx | Garantierter Slot (best_chunk ≥ 0.20) |

**Diversität:** 4 verschiedene Dokumente ✅

---

### 2.5 Diagnostics & Transparency

**Code:** `src/m09_rag.py` - Funktion `get_all_documents_with_best_scores()`

#### Zweck
Zeigt **ALLE** Projekt-Dokumente mit ihren besten Scores - nicht nur Top-K. Nützlich für:
- ✅ Debugging: Warum fehlt Dokument X in den Ergebnissen?
- ✅ Transparency: User sieht dass System alle Dokumente geprüft hat
- ✅ Threshold-Tuning: Welcher Wert ist optimal für mein Projekt?

#### Rückgabe-Format
```python
[
    {
        "document_id": 6,
        "filename": "Anhang2_Preisblatt_Unisport.pdf",
        "classification": "Anforderung/Feature",
        "best_score": 0.04,  # Bester Chunk dieses Dokuments
        "included": False,   # >= threshold?
        "reason": "Semantisch irrelevant (<5%)"
    },
    {
        "document_id": 3,
        "filename": "Pflichtenheft Unisport Webportal.docx",
        "classification": "Pflichtenheft (Projekt)",
        "best_score": 0.51,
        "included": True,
        "reason": ""
    }
]
```

#### Algorithmik
```python
for doc in all_project_docs:
    best_score = 0.0
    for chunk in doc.chunks:
        query_emb = embed_text(query)
        chunk_emb = json.loads(chunk.embedding)
        similarity = _cosine_similarity(query_emb, chunk_emb)
        best_score = max(best_score, similarity)
    
    included = best_score >= threshold
    
    if not included:
        if best_score < 0.05:
            reason = "Semantisch irrelevant (<5%)"
        else:
            reason = f"Score {best_score:.0%} < Threshold {threshold:.0%}"
```

#### UI-Integration (Batch-QA Prompt-Vorschau)

**Datei:** `app/pages/08_Batch_QA.py`

```python
with st.expander("🔍 RAG-Diagnostics (alle Dokumente)", expanded=False):
    st.caption("Zeigt **alle** Projekt-Dokumente mit ihrem besten Score:")
    
    diagnostics = get_all_documents_with_best_scores(
        query=question_text,
        project_key=project_key,
        threshold=rag_threshold,
        exclude_classification="FAQ/Fragen-Katalog"
    )
    
    included = [d for d in diagnostics if d["included"]]
    excluded = [d for d in diagnostics if not d["included"]]
    
    # ✅ Eingeschlossen
    if included:
        st.markdown("**✅ Eingeschlossen (>= Threshold):**")
        for d in included:
            st.text(f"  {d['best_score']:.0%} | {d['filename'][:35]}")
    
    # ⚠️ Ausgeschlossen
    if excluded:
        st.markdown("**⚠️ Ausgeschlossen:**")
        for d in excluded:
            st.text(f"  {d['best_score']:>3.0%} | {d['filename'][:25]} | {d['reason']}")
```

#### Beispiel-Output

**Query:** "Preisstruktur: Welche Preisstruktur wird erwartet?"  
**Threshold:** 0.45 (45%)

```
✅ Eingeschlossen (>= Threshold):
  51% | Pflichtenheft Unisport Webportal.docx
  50% | Anhang2_Preisblatt_Unisport.pdf

⚠️ Ausgeschlossen:
  39% | Anhang Ausschreibung Unisport.docx | Score 39% < Threshold 45%
  36% | Beilagen zum Pflichtenheft.pdf     | Score 36% < Threshold 45%
```

**Interpretation:**
- ✅ User sieht: System hat **alle 4** Dokumente geprüft
- ✅ Preisblatt mit 50% → **korrekt eingeschlossen** (trotz leeres Formular)
- ✅ Anhang/Beilagen mit 36-39% → **zu niedrig, korrekt ausgeschlossen**
- ✅ Wenn User meint Anhang sollte erscheinen → Threshold auf 0.35 senken

#### Performance
**Benchmark:** 4 Dokumente, 150 Chunks, Query-Embedding gecacht
```
get_all_documents_with_best_scores(): ~80ms
  ├─ 150 Similarity Berechnungen: ~60ms
  ├─ Sortierung + Formatierung: ~20ms
  └─ Kein API-Call (lokale Berechnung)
```

**Kosten:** $0 (keine zusätzlichen Embedding-Calls)

---

## 3. Vergleich: Embedding-Modelle

### Warum text-embedding-3-small?

| Modell | Dimensionen | Kosten/1M Tokens | Speed | Qualität (Deutsch) |
|---|---|---|---|---|
| **text-embedding-3-small** ✅ | 1536 | $0.02 | ⚡⚡⚡ | ★★★★☆ |
| text-embedding-3-large | 3072 | $0.13 | ⚡⚡ | ★★★★★ |
| cohere-embed-v3 | 1024 | $0.10 | ⚡⚡ | ★★★★☆ |
| voyage-3-large | 1024 | $0.12 | ⚡⚡ | ★★★★★ |

**Entscheidung:** `3-small` ist optimal für:
- ✅ Strukturierte Projektdokumente (klare Fachbegriffe)
- ✅ Keyword-lastige Queries ("Preisblatt", "API", "Kosten")
- ✅ Deutsch/Englisch Mix (gute Cross-Language Performance)
- ✅ Kostenbewusstsein ($0.001 pro Dokument)

**Wann `3-large` erwägen:**
- Abstrakte Konzepte ("Ethische Implikationen von KI-gestützter Entscheidungsfindung")
- Wissenschaftliche Papers mit Feinheiten
- Cross-Language Recherche über 3+ Sprachen

**Für dieses Projekt:** Unterschied <5%, aber 6.5× teurer → nicht empfohlen.

---

## 4. Alternative Ansätze

### 4.1 BM25 (statt LIKE-Search)

**Vorteile:**
- Bessere Term-Frequency / Inverse-Document-Frequency Gewichtung
- "agil" AND "scrum" NOT "waterfall" Queries möglich
- Standard für Information Retrieval

**Nachteile:**
- Externe Library (Tantivy, Whoosh) oder Elasticsearch
- Migration-Aufwand
- Overhead bei <1000 Dokumenten

**Empfehlung:** Erst ab >500 Dokumente im Projekt.

---

### 4.2 Reranking

**Konzept:**
1. RAG holt Top-50 Kandidaten (Embedding + Keyword)
2. Reranker-Model bewertet jede Query-Chunk-Kombination neu
3. Top-K werden umsortiert

**Modelle:**
- cohere-rerank-v3: $2/1000 Requests
- bge-reranker-v2: Open-Source (lokal)

**Kosten-Beispiel:**
- 480 Fragen im Batch × $2/1000 = **$0.96 pro Batch**
- vs. Embedding: $0.001 pro Batch

**Empfehlung:** Erst ab >10.000 Chunks oder Legal/Medical Use-Cases mit extremer Präzisions-Anforderung.

---

### 4.3 Query-Expansion

**Konzept:**
```python
query = "Preisstruktur"
expanded = ["Preisstruktur", "Preis", "Kosten", "Budget", "Kalkulation"]
# Führe 5 parallele Searches aus, kombiniere Ergebnisse
```

**Vorteile:**
- Findet Synonyme automatisch
- Robuster gegen Tippfehler

**Nachteile:**
- 5× mehr API-Calls (teurer)
- Noise durch zu breite Expansion

**Empfehlung:** Erst testen wenn User-Feedback zeigt: "System findet Synonyme nicht".

---

## 5. Monitoring & Debugging

### 5.1 RAG-Treffer visualisieren

**Batch-QA Export:** Spalte `_RAG_Chunks`
```
Pflichtenheft.docx (46%): Das Webportal muss...
Preisblatt.pdf (42%): Umsetzungskosten | 50000 CHF
Beilagen.pdf (35%): Technische Anforderungen...
```

**Checks:**
- [ ] Sind verschiedene Dokumente vertreten? (Diversity)
- [ ] Ist das relevanteste Dokument dabei? (Qualität)
- [ ] Sind Scores plausibel? (>40% = gut, <30% = schwach)

---

### 5.2 Console-Warnings

```bash
⚠️ Text 23 zu lang (8532 Zeichen), kürze auf 6000
⚠️ Batch zu groß, verarbeite 20 Texte einzeln...
```

**Bedeutung:** Automatische Safety-Mechanismen greifen → kein Fehler, aber Performance-Impact.

---

### 5.3 Embedding-Debug

**Test einzelner Chunk:**
```python
from src.m09_rag import embed_text, _cosine_similarity

chunk1 = "[Pflichtenheft | File.docx]\nREST-API mit OAuth2"
chunk2 = "[Preisblatt | File.pdf]\nUmsetzungskosten 50000 CHF"
query = "API Authentifizierung"

emb1 = embed_text(chunk1)
emb2 = embed_text(chunk2)
emb_q = embed_text(query)

print(f"Query ↔ Pflichtenheft: {_cosine_similarity(emb_q, emb1):.2%}")
print(f"Query ↔ Preisblatt:    {_cosine_similarity(emb_q, emb2):.2%}")
```

**Erwartete Ausgabe:**
```
Query ↔ Pflichtenheft: 68%
Query ↔ Preisblatt:    22%
```

---

## 6. Best Practices

### 6.1 Chunk-Size Wahl

| Dokumenttyp | Empfohlene Größe | Begründung |
|---|---|---|
| **CSV** | 250 | Jede Zeile = 1 Chunk (strukturiert) |
| **Pflichtenheft** | 1000-1500 | Anforderungen oft 2-3 Absätze |
| **Preisblatt** | 500-800 | Tabellenzeilen + Kontext |
| **API-Docs** | 800-1200 | Endpoint + Parameter + Beispiel |
| **Chat-Protokolle** | 400-600 | Kurze Nachrichten-Sequenzen |

**Faustregel:**
- Zu klein (<300): Kontext geht verloren
- Zu groß (>2000): Noise, mehrere Themen vermischt
- Overlap: 20% (Standard) bis 30% (bei Tabellen)

---

### 6.2 Klassifizierung nutzen

```python
classification_filter = "Pflichtenheft (Projekt)"
rag_results = retrieve_relevant_chunks_hybrid(
    query, 
    project_key=pkey, 
    classification_filter=classification_filter
)
```

**Use-Case:** "Zeige mir nur Anforderungen aus dem Pflichtenheft, nicht aus FAQs oder Beilagen."

---

### 6.3 Re-Ingest bei Änderungen

**Trigger für Re-Ingest:**
- ✅ Chunk-Size geändert
- ✅ Klassifizierung korrigiert
- ✅ Neue Contextual-Prefix-Strategie (wie heute)
- ❌ NICHT bei: Typo-Fix im Dateinamen (Embedding ändert sich eh nicht)

**Workflow:**
1. Dokument löschen (Soft-Delete)
2. Gleiche Datei neu hochladen
3. System erkennt SHA256 → Hard-Reset + Re-Chunking

---

## 7. Architektur-Diagramm

```
┌─────────────────────────────────────────────────────────────────────┐
│                         UPLOAD-PIPELINE                              │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                ┌────────────────┼────────────────┐
                ▼                ▼                ▼
          [PDF/DOCX]          [CSV]           [TXT]
                │                │                │
                ▼                ▼                ▼
         [Text Extract]    [pandas Parse]   [UTF-8 Read]
                │                │                │
                └────────────────┼────────────────┘
                                 ▼
                       [Contextual Prefix]
                       [{class} | {file}]
                                 ▼
                          [Chunking]
                    (1000 chars, 20% overlap)
                                 ▼
                      [Batch-Embedding]
                   (20 chunks per API call)
                                 ▼
                    ┌────────────┴────────────┐
                    ▼                         ▼
              [SQLite DB]               [ChromaDB]
           DocumentChunk Table        (Projekt-Index)
                                 

┌─────────────────────────────────────────────────────────────────────┐
│                       RETRIEVAL-PIPELINE                             │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                      [User Query String]
                                 ▼
                       [Query Embedding]
                    (text-embedding-3-small)
                                 ▼
                ┌────────────────┴────────────────┐
                ▼                                 ▼
       [Semantic Search]                  [Keyword Search]
     (Cosine Similarity)                    (LIKE Match)
      Top 35 Kandidaten                    Top 70 Matches
                │                                 │
                └────────────────┬────────────────┘
                                 ▼
                        [Deduplication]
                      (by chunk_id)
                                 ▼
                        [Dateiname-Boost]
                         (+4% if match)
                                 ▼
                   [Guaranteed Slots + Fallback]
                  (min 1 per doc if score > 0.20)
                                 ▼
                      [Diversity Filter]
                  (max 3 chunks per document)
                                 ▼
                      [Quality Fill]
                 (rest: score ≥ 0.20, sorted)
                                 ▼
                    [Top-K Results (7)]
                                 ▼
                    [RAG Context for LLM]
```

---

## Appendix: Glossar

| Begriff | Bedeutung |
|---|---|
| **Chunk** | Text-Segment eines Dokuments (500-1500 Zeichen) |
| **Embedding** | 1536-dim Vektor-Repräsentation eines Textes |
| **Cosine Similarity** | Maß für Ähnlichkeit zweier Vektoren (0.0-1.0) |
| **Threshold** | Min. Similarity für RAG-Treffer (Standard: 0.45) |
| **Top-K** | Anzahl zurückgegebener Ergebnisse (Standard: 7) |
| **Hybrid Search** | Semantic (Embedding) + Keyword (LIKE) kombiniert |
| **Diversity Filter** | Max. Chunks pro Dokument begrenzen |
| **Contextual Prefix** | Metadaten am Anfang jedes Chunks |
| **Batch-Embedding** | N Texte in 1 API-Call statt N Calls |
| **Re-Ingest** | Dokument neu hochladen → Re-Chunking + Re-Embedding |
