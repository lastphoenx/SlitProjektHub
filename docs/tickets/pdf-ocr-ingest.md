# Ticket 12 — Gescannte PDFs: OCR + Vision-Fallback beim Ingest

**Problem:** Eingescannte Angebots-PDFs (z. B. Vorbehaltsliste) lieferten beim Upload zwar «Erfolg», aber
0 Chunks — `is_pdf_scanned()` und `apply_ocr_to_pdf()` in `m11_vision.py` waren nicht an den Ingest
angebunden. Die KI/RAG fand in solchen Dokumenten nichts.

**Ziel:** Beim PDF-Upload automatisch erkennen, wenn wenig Text extrahierbar ist, OCR anwenden, optional
Vision-LLM als letzter Fallback, und in der UI klar warnen wenn weiterhin 0 Chunks indexiert wurden.

---

## Umsetzung ✅ erledigt

### Backend (`src/m09_docs.py`, `src/m11_vision.py`)

1. **`extract_pdf_text_with_fallback()`** — Pipeline:
   - Native Extraktion (`extract_text_from_pdf` via PyPDF2/pdfplumber)
   - Bei wenig Text (`pdf_extracted_text_is_sparse`, Schwellwert 80 Zeichen) und/oder `is_pdf_scanned()`:
     **`apply_ocr_to_pdf()`** (ocrmypdf, `deu+eng`, `skip_text=True`)
   - Wenn OCR nicht reicht: optional **`_extract_pdf_text_via_vision()`** — bis 12 Seiten als PNG,
     Vision-Modell aus `resolve_vision_provider_model()` + `try_models_with_messages()`
2. **`ingest_document()`** / **`force_rechunk_document()`** nutzen die Fallback-Pipeline für `.pdf`.
3. **`IngestResult(ok, message, status)`** — `status`: `ok`, `ocr_ok`, `vision_ok`, `ocr_failed`, `no_text`,
   `linked`, `error`. Tuple-Unpacking `(ok, msg)` bleibt via `__iter__` kompatibel.

### UI

- **Offertbeurteilung** (`evaluation/index.html`): Flash-Karten nach Upload (`doc_upload` / `tender_upload`)
  für `ocr_failed` («Gescannt — OCR fehlgeschlagen»), `ocr_ok`, `vision_ok`, `no_text`.
- Persistente Warnung **«Angebots-Dokumente ohne Index»** wenn `chunk_count == 0`.
- **Vorgaben-Tabelle** und **Dokumente-Liste**: `0 ⚠` bei fehlendem Index.

### Routes

- `POST /evaluation/bidder-doc-upload` und `tender-doc-upload` leiten `result.status` als Query-Param weiter.
- Generischer Upload (`backend/main.py`): HX-Toast mit `warning` bei `ocr_failed` / `no_text`.

---

## Server-Abhängigkeiten

Auf dem Host (z. B. `/opt/slitprojekthub`) zusätzlich zu Python-Paketen:

| Paket | Zweck |
|-------|--------|
| `ocrmypdf` | in `requirements.txt` |
| **Tesseract** (`deu`, `eng`) | OCR-Engine für ocrmypdf |
| **Poppler** (`pdftoppm`) | pdf2image für Vision-Fallback |

Debian/Ubuntu-Beispiel:

```bash
sudo apt install tesseract-ocr tesseract-ocr-deu poppler-utils
pip install -r requirements.txt
```

Vision-Fallback ist optional — nur aktiv wenn ein Vision-fähiger Provider konfiguriert ist
(`OPENAI_API_KEY` o. ä.).

---

## Tests

```bash
.venv/bin/python scripts/testing/test_pdf_ingest_ocr.py
```

Mock-Tests für native → OCR → Vision → Fehlschlag und `IngestResult`-Kompatibilität.

---

## Akzeptanzkriterien

- [x] Gescanntes PDF löst OCR aus, wenn native Extraktion < 80 Zeichen liefert.
- [x] Nach erfolgreichem OCR werden Chunks indexiert; Status `ocr_ok`.
- [x] Wenn OCR und Vision scheitern: Dokument gespeichert, 0 Chunks, UI «Gescannt — OCR fehlgeschlagen».
- [x] Vision-LLM als optionaler letzter Schritt (max. 12 Seiten).
- [x] Bestehende Streamlit-/Tuple-Caller brechen nicht (`IngestResult` iterierbar).
