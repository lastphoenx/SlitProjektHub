# Tickets: Offertbeurteilung — Export pro Bieter / Bewerter

Kontext: Der projektweite CSV/XLSX-Export (`build_evaluation_export_sheets`, Ticket 6 in
`offertbeurteilung-dokumente.md`) listet alle Bieter und alle Bewerter in breiten Spalten.
Für Vergabeprotokolle braucht es **schlanke Exporte pro Bieter und Quelle** (KI oder eine
Person), optional als bearbeitbares DOCX oder druckbares HTML.

Reihenfolge: Ticket 1 (Daten + Tabellen) vor Ticket 2 (HTML/DOCX) — gemeinsame
`EvaluationExportContext`-Schicht.

---

## Ticket 1 — Export-Context + gefilterter CSV/XLSX ✅

**Ziel:** Pro Bieter und Bewertungsquelle (`ai` oder `user:<id>`) tabellarisch exportieren.

**Aufgabe:**
1. `EvaluationExportContext` + `build_evaluation_export_context()` in `src/m15_evaluation.py`
2. `context_to_tabular_sheets()` — schmale Spalten (Kriterium, Wert, Begründung, …)
3. Query-Parameter an bestehende Routen:
   - `bidder_id` (Pflicht für gefilterten Export)
   - `source=ai|user` + `evaluator_id` bei `user`
4. Legacy-Export ohne `bidder_id` unverändert (alle Bieter, breite Spalten)
5. Export-UI: Bieter + Quelle wählen, CSV/XLSX

**Akzeptanzkriterien:**
- Export mit `bidder_id` + `source=ai` enthält nur KI-Werte dieses Bieters
- Export mit `source=user&evaluator_id=N` enthält nur diese Person
- Blatt «Einzelanforderungen» bei gefiltertem XLSX
- `can_view_evaluator_details` — Personenexport ohne Berechtigung → 403
- Bestehende projektweite Exporte und Tests bleiben grün

---

## Ticket 2 — HTML + DOCX pro Bieter/Bewerter ✅

**Ziel:** Fachlich lesbare, bearbeitbare Protokolle (nicht nur Tabellen).

**Aufgabe:**
1. Jinja-Template `backend/templates/evaluation/export_report.html`
2. `build_evaluation_docx_bytes()` — python-docx, analog Idea-Reports
3. Routen `GET /evaluation/export.html` und `GET /evaluation/export.docx` (gleiche Query-Parameter wie Ticket 1)
4. UI-Buttons HTML / Word neben CSV/XLSX

**Akzeptanzkriterien:**
- Kopf: Projekt, Bieter, Quelle (KI-Label oder Username), Datum
- Abschnitte Eignung / Zuschlag; Anhang Unterfragen
- KI-Export: Begründung + optional RAG-Basis (`rag_basis_json`)
- DOCX downloadbar, in Word bearbeitbar
- HTML druckfreundlich (`@media print`)

**Nicht in Scope (Follow-up):**
- ~~PDF (WeasyPrint / Playwright)~~ → Ticket 3
- ~~ZIP-Batch aller Kombinationen~~ → Ticket 4

---

## Ticket 3 — PDF-Export pro Bieter/Bewerter ✅

**Ziel:** Druckfertiges Protokoll als PDF (gleicher Inhalt wie HTML).

**Umsetzung:**
- `build_evaluation_pdf_bytes()` — HTML → PDF via WeasyPrint
- `backend/app/evaluation_export_render.py` — gemeinsames HTML-Rendering
- Route `GET /evaluation/export.pdf` (gleiche Query-Parameter wie CSV)
- UI-Button PDF

**Server:** `pip install weasyprint` + Linux-Pakete (Pango/Cairo), siehe WeasyPrint-Doku.

---

## Ticket 4 — ZIP-Batch (alle Bieter × Quellen) ✅

**Ziel:** Ein Download mit allen Kombinationen, die gespeicherte Scores haben.

**Umsetzung:**
- `list_export_combinations()` + `build_evaluation_export_zip_bytes()`
- Route `GET /evaluation/export.zip?project_key=&format=xlsx|docx|csv|html|pdf`
- UI: Formatwahl + «ZIP herunterladen»

**Akzeptanzkriterien:**
- ZIP enthält je `(Bieter, ai|user:N)` eine Datei, wenn Score existiert
- Dateiname: `{Bieter}_{Quelle}.{format}`
- Ohne Bewerter-Rechte: nur KI-Zeilen pro Bieter
- Leeres Projekt → HTTP 404
