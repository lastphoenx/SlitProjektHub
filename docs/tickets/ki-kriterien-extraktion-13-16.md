# Tickets 13–16 — KI-Kriterien-Extraktion: Unterfragen vollständig

Kontext: Schritt 1 (Übersicht) funktionierte, Schritt 2 (Einzelanforderungen) schlug still fehl,
weil `_criterion_ref_prefix()` nur auf `name` lief und menschenlesbare Namen («Preis», «Funktionale
Anforderungen») nie matchten. Zusätzlich kürzte `_retrieve_tender_context_multi()` den Kontext auf
~12 Chunks zurück, obwohl 4 RAG-Pässe bis ~48 sammelten.

---

## Ticket 13 — Referenz-Auflösung + kind-agnostisches Enrichment ✅

**Ziel:** Schritt 2 läuft für **alle** Top-Level-Kriterien (Eignung + Zuschlag), ohne Namens-Mapping-Tabelle.

**Umsetzung:**
- `requirement_ref` als **Pflichtfeld** im LLM-JSON (Schritt 1)
- `_resolve_requirement_search()`: LLM-Ref → Regex auf name/description → Fallback: `name` als Suchbegriff
- `_enrich_criteria_children_from_requirements()` ersetzt `_enrich_zuschlag_children_from_requirements()`
- Eignung inklusive (keine Parallelfunktion)
- `scale_max=1` für Eignungs-Kinder beim Import automatisch

---

## Ticket 14 — RAG-Cap-Fix + Config ✅

**Ziel:** Gesammelter RAG-Kontext wird nicht mehr auf `limit_per_role` zurückgekappt.

**Umsetzung:**
- `format_limit`-Bug entfernt; `max_format_chunks` Parameter
- Neues Feld `rag_chunks_extraction` (Default 36, Range 16–48) in `evaluation_project_config`
- Unterfragen-Pass: `limit=24`, `threshold=0.20`

---

## Ticket 15 — (in Ticket 13 enthalten)

Eignungs-Enrichment in derselben kind-agnostischen Funktion — kein separates Modul.

---

## Ticket 16 — Strukturierte CSV/XLSX-Vorgaben ✅

**Ziel:** Anforderungsblätter zeilenweise → deterministische `children` ohne RAG-Raten.

**Umsetzung:** `_enrich_children_from_structured_tender_docs()` vor dem KI-Pass;
Zeilenfilter per `Nr`/`Referenz` + `requirement_ref` oder Dateiname.

---

## Tests

```bash
.venv/bin/python scripts/testing/test_evaluation.py
```

Neue Tests: `_resolve_requirement_search`, `_normalize_requirement_ref`, RAG-Format-Cap.

---

## Akzeptanzkriterien

- [x] Schritt 2 läuft auch bei «Funktionale Anforderungen» (ohne F01 im Namen)
- [x] Eignung: **keine** Unterfragen (flache K.O.-Gates)
- [x] Zuschlag: Kinder nur mit Struktur-Nachweis (CSV/XLSX ≥2 Zeilen oder ≥2 Zeilennummern im RAG)
- [x] Groundedness-Filter verwirft F01-001-Leaks unter S/R/A
- [x] Schritt-1-Halluzinationen werden vor Schritt 2 verworfen

---

## Ticket 17 — Groundedness & flache Eignung ✅ erledigt

- `_flatten_eignung_payload()` — Eignung nie mit `children`
- Zuschlag: Schritt-1-`children` verworfen; Enrichment nur bei Zeilenstruktur-Nachweis
- `_filter_grounded_children()` — Name/Text muss im RAG-Kontext belegbar sein; Ref-Prefix muss passen
- `auto_price` (W-01): keine Unterfragen
