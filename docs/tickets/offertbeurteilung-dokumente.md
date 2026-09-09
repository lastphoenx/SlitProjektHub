# Tickets: Offertbeurteilung – Mehrfach-Dokumente, Klassifikation, PII-Sanitizer, Zwei-Stufen-KI

Kontext: Aktuell läuft der Bieter-Dokument-Upload über die generische Dokumente-Seite
(`/documents/upload`, `backend/main.py:878-909`) + manuelles Verlinken pro Bieter über
Checkboxen (`POST /evaluation/bidder-doc`, `backend/app/routes/evaluation.py:392-406`,
Tabelle `BidderDocumentLink` in `src/m15_evaluation.py:82`). Diese vier Tickets bauen das
direkt ins Offertbeurteilung-UI, ergänzen eine optionale Sub-Klassifikation und schließen
eine bestehende DSGVO-Lücke beim RAG-Cloud-Call. Reihenfolge = Priorität (Ticket 3 zuerst
umsetzen, wenn Kapazität knapp ist — es ist eine Compliance-Lücke, kein UX-Polish).

---

## Ticket 1 — Multi-Dokument-Upload direkt im Offertbeurteilung-UI

**Ziel:** Bieter-Dokumente (Preisblatt, Bilanz, Referenzblätter, Vorbehaltsliste, ...)
werden direkt auf der Offertbeurteilung-Seite hochgeladen und automatisch mit dem
jeweiligen Bieter verknüpft — kein Umweg mehr über die generische Dokumente-Seite.

**Aktueller Stand:**
- Upload-Endpoint existiert generisch: `documents_upload()` (`backend/main.py:878-909`),
  ruft `ingest_document(file_name, file_bytes, classification, chunk_size, csv_delimiter)`
  (`src/m09_docs.py:237`).
- Verlinkung Dokument↔Bieter existiert bereits als N:M-Tabelle: `BidderDocumentLink`
  (`src/m15_evaluation.py:82-96`), Funktionen `link_document_to_bidder()` /
  `unlink_document_from_bidder()` / `get_bidder_document_ids()` (`m15_evaluation.py:369-405`).
- Chunk-Size pro Datei ist bereits ein Parameter von `ingest_document()` — im generischen
  Upload-Formular schon als Form-Feld exponiert (`backend/main.py:883`).

**Aufgabe:**
1. Neuer Upload-Bereich in `backend/templates/evaluation/index.html` (oder Partial), pro
   Bieter-Zeile bzw. Bieter-Detail: Datei-Upload + optionales Klassifikation-Dropdown
   (siehe Ticket 2) + Chunk-Size-Feld (Default 1000, wie im generischen Upload).
2. Neue Route, z. B. `POST /evaluation/bidder-doc-upload` in
   `backend/app/routes/evaluation.py`, analog zu `documents_upload()`:
   - `file: UploadFile`, `project_key`, `bidder_id`, `classification` (optional),
     `chunk_size` (Default 1000).
   - Ruft `ingest_document(..., classification=classification or ANGEbot_CLASSIFICATION)`
     auf (Fallback-Klassifikation = bestehende Konstante `ANGEbot_CLASSIFICATION`,
     `m15_evaluation.py:24`).
   - Verlinkt das neu erzeugte Dokument sofort mit `link_document_to_bidder(bidder_id, doc.id)`
     — kein manueller Zwischenschritt über die Checkbox-Liste mehr nötig.
3. **Wiederverwendung, keine Duplikate:** `ingest_document()`, `link_document_to_bidder()`,
   die Limits aus `DOC_LIMITS` (`src/m03_db.py:37-41`) unverändert übernehmen — keine
   Parallel-Implementierung der Ingestion-Logik.
4. Bestehende Checkbox-Verlinkungsliste (aktuell einziger Weg, `evaluation.py:392-406`)
   bleibt als Fallback/Übersicht bestehen — nützlich falls ein Dokument mehreren Bietern
   zugeordnet werden soll (z. B. gemeinsame Anhänge) oder nachträglich um-verlinkt wird.

**Akzeptanzkriterien:**
- Bieter-Dokument lässt sich direkt aus der Offertbeurteilung-Seite hochladen, ohne die
  Dokumente-Seite zu besuchen.
- Nach Upload ist das Dokument sofort in der Bieter-Dokumentenliste sichtbar und für RAG
  abrufbar (`retrieve_relevant_chunks_hybrid(..., bidder_id=...)`).
- Bestehender Upload-Weg über `/documents` bleibt funktionsfähig (keine Breaking Changes).

---

## Ticket 2 — Optionale Sub-Klassifikation für Bieter-Dokumente (übersteuert Dateiname)

**Ziel:** Innerhalb von `"Angebot (Bieter)"` feiner klassifizieren können (Preisblatt,
Bilanz/Erfolgsrechnung, Referenzprojektblatt, Vorbehaltsliste, Management Summary,
Grobkonzept/Lösungskonzept, Eignungsnachweis, Zertifizierung, Proof of Concept,
Vorstellung Lieferantin, Sonstiges) — Feld ist **optional**; wenn gesetzt, hat es Vorrang
vor jeder dateinamen-basierten Logik.

**Aktueller Stand:**
- `DOCUMENT_CLASSIFICATIONS` (`src/m03_db.py:25-35`) kennt nur den einen flachen Bucket
  `"Angebot (Bieter)"`, keine Subtypen.
- Es gibt **keine** automatische Dateiname→Klassifikation-Erkennung im Code. Die einzige
  dateinamen-basierte Logik ist der RAG-Retrieval-Boost (+0.04 Score bei Wortübereinstimmung
  Query↔Dateiname, `src/m09_rag.py`) — das ist reine Relevanz-Gewichtung, keine Klassifikation.
- Der Chunk-Prefix beim Ingest ist `[{classification} | {file_name}]` (`m09_docs.py:398-400`).

**Aufgabe:**
1. Neues Feld `Document.doc_subtype: Optional[str]` (SQLModel-Migration nötig — Projekt
   nutzt kein Alembic-artiges Tool, siehe bestehende Migrations-Konvention in `src/m03_db.py`
   bzw. `scripts/` für Schema-Änderungen an bestehenden Tabellen).
2. Konstante `ANGEbot_SUBTYPES` (analog `DOCUMENT_CLASSIFICATIONS`) mit der Liste oben,
   z. B. in `src/m15_evaluation.py` neben `ANGEbot_CLASSIFICATION`.
3. Dropdown im Upload-UI (Ticket 1) ist **optional** (leer lassen erlaubt = Default-Verhalten
   bleibt exakt wie heute: Klassifikation bleibt `"Angebot (Bieter)"`, Retrieval verlässt sich
   weiter auf den Dateinamen-Boost).
4. „Übersteuert Dateiname": Wenn `doc_subtype` gesetzt ist,
   - erscheint es im Chunk-Prefix statt/zusätzlich zum reinen `classification`-Wert, z. B.
     `[Angebot (Bieter) · Preisblatt | {file_name}]`,
   - wird es als Filter/Spalten-Label in der Bieter-Dokumentenliste angezeigt (statt nur
     Dateiname zu raten),
   - **wird der Dateiname-Boost in `retrieve_relevant_chunks_hybrid()` dadurch nicht
     deaktiviert** — er bleibt als zusätzliches Relevanzsignal aktiv, `doc_subtype` ist ein
     zusätzliches, expliziteres Signal on top, keine exklusive Alternative.
5. Kein Zwang, für Alt-Dokumente `doc_subtype` nachzutragen — `NULL`/leer ist ein gültiger,
   dauerhaft unterstützter Zustand (nicht nur Übergang).

**Akzeptanzkriterien:**
- Upload ohne Sub-Klassifikation funktioniert exakt wie heute.
- Upload mit Sub-Klassifikation zeigt diese in Bieter-Dokumentenliste und im RAG-Chunk-Prefix.
- Bestehende Dokumente (ohne `doc_subtype`) werden nicht invalidiert, kein Zwangs-Reingest.

---

## Ticket 3 — PII-Sanitizer (Stufe 1+2) für `suggest_score_with_rag()` — **erledigt** (`ffa0178`+)

**Status:** Umgesetzt. `suggest_score_with_rag()` ruft bei Cloud-Providern `sanitize_for_cloud_text()` auf
(Vorgaben + Angebotskontext). `validate_evaluation_cloud_gate()` + Bestätigungs-Checkbox in
`backend/templates/evaluation/_cell.html`. Tests: `test_suggest_score_sanitizes_cloud_context`,
`test_validate_evaluation_cloud_gate` in `scripts/testing/test_evaluation.py`.

**Vor Produktivstart mit Referenzprojekten/Kurzprofilen:** Stichprobe mit echtem Auszug über
`/sanitize` und `test_cloud_pii.py` auf dem Server (Flair warm).

---

## Ticket 5 — Zweistufiges Ranking (Phase 1 ZK / Phase 2 Präsentation, z. B. A-01) — **erledigt**

**Ziel:** Nach Bewertung von ZK1–7 Zwischenrangliste für Einladungsentscheid; Bieter, die selbst mit
voller Punktzahl in Phase 2 nicht mehr aufholen können, als «keine Einladung» markieren.

**Umsetzung:**
- `Criterion.ranking_phase` (1 = ZK, 2 = Präsentation); Heuristik `infer_ranking_phase()` (A-01 etc.)
- `compute_rankings()` liefert `interim_score`, `interim_rank`, `max_score`, `can_still_win`
- UI: Tabelle «Vor Präsentation (Phase 1)» + Excel-Blatt «Rangfolge Phase 1»
- Kriterium anlegen / Liste: Phase-Dropdown; KI-Extraktion: `ranking_phase` im JSON

**Akzeptanz:** A-01 als Phase 2 markieren → Zwischenrang nur über ZK; Einladungsspalte «nein» wenn
`max_score < führender interim_score`.

---

## Ticket 6 — Begründungspflicht & Preis-Punkte-Gate — **erledigt**

**a) Begründungen:** Pflicht bei Eignung «Nein» und Zuschlag &lt; Maximalpunktzahl (inkl. Unterfragen).
Server-Validierung in `upsert_score`, UI-Hinweis + Liste offener Pflichten auf `/evaluation`.
**Export:** Wert + Begründung je Bewerter, KI und System; Blatt «Einzelanforderungen» für Unterfragen.

**b) Preis:** `sync_price_criterion_scores` nur wenn alle Bieter TCO &gt; 0. Standard-Formel **reziprok**
(Unisport: Punkte = max × günstigstes / dieses Angebot). Optional `linear_minmax` in Projekt-Einstellungen.

**Export:** CSV/XLSX mit Begründungsspalten je Bewerter + Blatt «Einzelanforderungen».

---

## Ticket 4 — Zwei-Stufen-KI-Auswahl (Input-KI / Output-KI) für Offertbeurteilung ✅ erledigt

**Ziel:** Gleiches Auswahlmuster wie Projektideen/Visual-Lab statt des heutigen
Einzel-Providers.

**Umsetzung:**
- `_llm_picker.html` in Projekt-Einstellungen: **②③** (`vorgaben_ki_*`) und **④** (`bewertung_ki_*`) als getrennte Projekt-Defaults.
- Matrix-Zelle (`_cell.html`): Picker mit Auflösung via `resolve_bewertung_ki()` (Picker > Projekt-Default > globale KI-Einstellungen).
- `POST /evaluation/suggest` nutzt `resolve_bewertung_ki()`; ein LLM-Call pro Vorschlag (kein künstlicher Input/Output-Split).
- DB-Migration `bewertung_ki_provider` / `bewertung_ki_model` in `evaluation_project_config`.

**Akzeptanzkriterien:** erfüllt — gleiche Picker-Komponente, keine parallele Provider-Logik, PII-Gate unverändert.

---

## Ticket 7 — Kriterien-Verwaltung nach dem Übernehmen (Unterfragen sichtbar & editierbar) ✅ erledigt

**Ziel:** Nach dem einmaligen Übernehmen aus der KI-Vorschau (oder manueller Anlage) müssen
Unterfragen wie F01-001…F01-008 einsehbar, editierbar und einzeln benennbar bleiben — aktuell
verschwindet diese Sicht komplett.

**Umsetzung:**
- `GET /evaluation/criteria-manage` + `POST /evaluation/criteria-save` mit Tabellen-Editor
  (`_criteria_manage.html`, `eval_criteria_preview.js` Manage-Modus inkl. `id`/`deleted_ids`).
- `criteria_editor_payload()` / `save_criteria_editor_payload()` / `update_criterion()` in `m15_evaluation.py`.
- Links von Offertbeurteilung-Index und Matrix-Elternzelle (`_cell_parent.html`).

**Akzeptanzkriterien:** erfüllt (Unterfragen nach Übernahme sichtbar, editierbar, einzeln in Matrix bewertbar).

---

## Ticket 8 — KI-Extraktion erfasst Einzelanforderungen (F01-001…008 etc.), nicht nur die Übersichtstabelle ✅ erledigt

**Ziel:** Jede reale Einzelanforderung aus Kapitel „7. Anforderungen" landet als Unterfrage
im jeweiligen Top-Level-Zuschlagskriterium.

**Umsetzung:**
- Vierter RAG-Pass mit Einzelanforderungs-Query in `extract_criteria_from_tender_docs()`.
- Schritt 2: `_enrich_zuschlag_children_from_requirements()` pro Top-Level per Referenz (F-01 → F01).
- Ticket 7 als manuelles Fallback bei unvollständiger Extraktion.

**Akzeptanzkriterien:** erfüllt für Unisport-Workflow (KI + Nachpflege in «Kriterien verwalten»).

---

## Ticket 9 — Mehrfach-`doc_subtype` pro Bieter-Dokument (analog `tender_role`) ✅ erledigt

**Ziel:** Merge-PDFs mit mehreren Inhaltsarten (Preisblatt, PoC, Grobkonzept, …) pro Kriterium gezielt
bevorzugen — nicht nur einen `Document.doc_subtype`.

**Umsetzung:**
- Tabelle `bidder_document_subtype` mit `UniqueConstraint(bidder_id, document_id, doc_subtype)`.
- `get_bidder_doc_subtypes()` / `set_bidder_doc_subtypes()`; Migration aus `Document.doc_subtype`.
- `bidder_doc_ids_for_criterion()` und `get_bidder_preisblatt_doc_ids()` nutzen Junction-Subtypen.
- UI: Mehrfach-Checkboxen beim Upload und pro verknüpftem Bieter-Dokument (Phase ③).

**Akzeptanzkriterien:** erfüllt (Merge-PDF mit mehreren Subtyp-Tags wird pro Kriteriumart bevorzugt).

---

## Ticket 10 — Kriterien-Schutz nach Bewertungsbeginn ✅ erledigt

**Ziel:** Strukturelle Kriterienänderungen (Gewicht, Löschen, …) nach erstem Score nur mit Bestätigung
(Rekursrisiko BöB/IVöB).

**Umsetzung:** `validate_criteria_manage_save()`, Bestätigungs-Checkbox in «Kriterien verwalten» und
beim manuellen Anlegen; reine Beschreibungsänderungen ohne Gate.

---

## Ticket 11 — Bewerter-Abweichungs-Hinweis ✅ erledigt

**Ziel:** Hohe Streuung zwischen mehreren Bewertern (`user:*`) sichtbar machen, bevor man sich auf
den Mittelwert verlässt.

**Umsetzung:** `list_evaluator_score_discrepancies()` + Warnkarte auf der Offertbeurteilungs-Seite
(analog «Offene Begründungspflichten»).

---

## Ticket 12 — Gescannte PDFs: OCR + Vision-Fallback ✅ erledigt

Siehe **`docs/tickets/pdf-ocr-ingest.md`** — OCR-Pipeline in `ingest_document()`, UI-Warnung bei 0 Chunks.

---

## Tickets 13–16 — KI-Extraktion: Unterfragen vollständig ✅ erledigt

Siehe **`docs/tickets/ki-kriterien-extraktion-13-16.md`** — `requirement_ref`, RAG-Cap-Fix,
kind-agnostisches Enrichment (Eignung + Zuschlag), CSV/XLSX-Zeilen.

---

## Ticket 17 — Groundedness & flache Eignung ✅ erledigt

Siehe **`docs/tickets/ki-kriterien-extraktion-13-16.md`** (Abschnitt Ticket 17) —
Eignung ohne `children`, Zuschlag nur mit Zeilenstruktur-Nachweis, Schritt-1-Artefakte verwerfen,
F01-Leak-Filter, `auto_price` ohne Unterfragen.

---

## Ticket 19 — Referenzschlüssel sichtbar & importierbar ✅ erledigt

**Ziel:** EK1/EK2/EK3 und F-01/T-01/S-01 etc. in Vorschau und DB (`Criterion.referenz`), nicht nur intern in `requirement_ref`.

**Umsetzung:**
- Spalte **Ref** in Kriterien-Vorschau und «Kriterien verwalten» (`eval_criteria_preview.js`).
- `import_criteria_payload()` / Editor-Save: `requirement_ref` → `Criterion.referenz` (Parent + Kind).
- `_ensure_criteria_refs()` nach Schritt 1: EK1… sequenziell, Zuschlag per Regex aus Text.
- Schritt-1-Prompt: `requirement_ref` auch für Eignung (EK1/EK2/EK3).
- DB-Migration `criterion.referenz`.

**Hinweis F-02/T-01 ohne Kinder:** Nach Ref-Fix neu extrahieren; P2-Badge + KI-Hinweise («keine Zeilenstruktur» vs. «keine RAG-Treffer») gegen PDF Kapitel 7 prüfen — separates Diagnose-Thema, kein Rückfall Ticket 17.

---

## Ticket 20 — Ref-gezielter RAG für Unterfragen + Kinder-Refs ✅ erledigt

**Ziel:** F-02/T-01 etc. bekommen Chunks mit Zeilennummern; Kinder zeigen `F01-001` statt leerem Platzhalter.

**Umsetzung:**
- `_ref_enrichment_query()` + `_retrieve_enrichment_context()` — zweiter Hybrid-Pass mit `F02-001 F-02 …` (niedrigerer Threshold).
- `requirement_ref` bei LLM-/Merge-/Structured-Kindern aus Name (`_stamp_child_requirement_refs`).
- KI-Hinweise differenziert: «X Zeilennummer(n) im RAG-Kontext (≥2 nötig)».
- UI: Kinder-Placeholder `F01-001`, Client inferiert Ref aus Name.

---

## Ticket 21 — Literal-Chunk-Scan für Unterfragen-RAG ✅ erledigt

**Ziel:** Wenn Hybrid-RAG &lt;2 Zeilennummern liefert, alle Vorgaben-Chunks nach `F02-001`/`T01-001` durchsuchen und passende Chunks in den Kontext mergen.

**Umsetzung:** `_literal_chunks_for_requirement_ref()` + Diagnose-Hinweise (`im Kontext: F02-001, …` vs. `T01-001 nicht wörtlich im Kontext`) + Server-Log bei rotem Gate.

---

## Ticket 18 — Vollständigkeits-Badge für Einzelanforderungen ✅ erledigt

**Ziel:** Nach KI-Extraktion sichtbar machen, wenn weniger Unterfragen erkannt wurden als im Pflichtenheft angekündigt (z. B. «5 von 18 Einzelanforderungen»).

**Umsetzung:**
- `parse_expected_child_count()`, `criteria_child_completeness()`, `criteria_completeness_warnings()` in `m15_evaluation.py`.
- Range-Hints: «18 Einzelanforderungen», «F01-001 bis F01-008» in Name/Beschreibung.
- Warnungen in `extract_criteria_from_tender_docs()` + `criteria_preview_meta()`.
- UI: `eval_criteria_preview.js` `renderAlerts()` zeigt gelbe Hinweise pro Zuschlagskriterium.

---

## Ticket 22 — Gap-Fill fehlender Fragenzeilen ✅ erledigt (`3301d5e`)

Lücken in Nummernfolge deterministisch aus RAG-Kontext (generisch F/T/EK).

---

## Ticket 23 — Abzugsbegründung Beleg + Konsistenz ✅ erledigt

KI-Vorschlag: Prompt Anti-Selbstwiderspruch, `_suggestion_deduction_grounding_issues()`, Retry + LLM-Check, UI-Warnung.

---

## Ticket 24 — Eignungs-Unterfragen EK1-01… ✅ erledigt

Schritt 2 extrahiert Fragenkatalog-Kinder unter EK1/EK2/EK3 analog Zuschlag (Ref `EK1-01`).

---

## Ticket 25 — Kriterien-Dedup auf `referenz` + Konstanten ✅ erledigt

- `import_criteria_payload()`: Dedup primär über `Criterion.referenz` (+ `parent_id`), Name nur Fallback.
- `ANGEbot_*` → `ANGEBOT_CLASSIFICATION` / `ANGEBOT_SUBTYPES`.

---

## Ticket 26 — Referenz-Präfixe & Skala projektspezifisch ✅ erledigt

- `EvaluationProjectConfig`: `ref_prefixes_json`, `scale_bands_json` (Defaults Unisport).
- KI-Extraktion + Bewertungs-Prompt lesen Projekt-Config; UI in Projekt-Einstellungen.

---

## Ticket 27 — Kapitel/Seite am Chunk ✅ erledigt

- `DocumentChunk.page_number`, `section_path`; PDF/DOCX-Ingest; Prefix im Chunk.
- `suggest_score_with_rag`: Nachbar-Chunks gleicher Section (Cap 3); Zitat mit S./Kap.

---

## Ticket 28 — Matrix KI-Vorschlag: RAG-Protokoll + Referenz-Pinning ✅ erledigt

**Ziel:** Nachvollziehbarkeit wie Batch QA (`_RAG_Chunks`): welche Vorgaben- und Angebots-Passagen
der KI-Vorschlag tatsächlich als Kontext hatte — nicht nur das vom Modell gewählte Zitat.
Zusätzlich deterministisches Retrieval für Unterfragen (F01-001 …), damit Tabellenzeilen in
Merge-PDFs (~200 Seiten) nicht dem Semantic-Ranking zum Opfer fallen.

**Problem:** `source_chunk_ref` = ein LLM-Zitat; `rag_documents` war intern, nicht persistiert.
Hybrid-Suche allein kann die richtige Tabellenzeile verfehlen oder Narrativ-Kapitel bevorzugen.

**Umsetzung:**

1. **Referenz-Pinning (Dual-RAG erweitert)**
   - `_resolve_suggestion_line_ref()` / `_literal_chunks_for_line_ref()` — exakte Zeilennummer
     (z. B. `F01-001`) per Volltext-Scan + BM25-Ref-Pass (`_line_ref_enrichment_query`).
   - **Vorgaben:** zusätzlich Parent-Ref (`F01`) via bestehendem `_literal_chunks_for_requirement_ref`
     — Block-Kontext aus Pflichtenheft/Bewertungsvorgaben.
   - **Angebot:** **nur Zeilen-Ref** (`F01-001`), **kein** Parent/Sibling-Pass (F01-002 …) —
     Tabellenform: Vermischung getrennter Anforderungen vermeiden.
   - Section-Nachbarn am Angebot nur, wenn der Nachbar-Chunk dieselbe Zeilennummer enthält
     (Chunk-Grenze, kein F01-002).

2. **RAG-Protokoll**
   - `Score.rag_basis_json`: strukturierte Liste Vorgaben + Angebot (Datei, S./Kap., Chunk-ID,
     Methode `hybrid|literal_line|literal_parent|ref_pass|neighbor`, Score, Preview).
   - UI: aufklappbar unter KI-Vorschlag (`_rag_basis.html`); persistiert bei «Nur als KI-Vorschlag»
     und Stapelverarbeitung.
   - `suggest_score_with_rag()` liefert `rag_basis` + `tender_rag_documents` / `offer_rag_documents`.

3. **Tests:** Literal nur F01-001 (nicht F01-002); Parent nur Vorgaben; `rag_basis` im Return;
   `upsert_score(..., rag_basis_json=...)`.

**Akzeptanzkriterien:**
- Nach KI-Vorschlag F01-001 sind alle verwendeten Quellen (Pflichtenheft + Angebot-PDF) sichtbar
  und nach Reload noch in der 🤖-Zeile abrufbar.
- Angebots-RAG enthält garantiert die Zeile `F01-001`, zieht aber **nicht** F01-002 als Literal/Nachbar.
- Vorgaben-RAG kann optional Parent-Block `F01` mitliefern.

---

**Reihenfolge-Empfehlung:** ~~3~~ → … → ~~27~~ → ~~28~~ → ~~29~~ → ~~30~~

---

## Ticket 29 — RAG-Basis: Sichtbarkeit im UI + Deployment-Check ✅ erledigt

**Anlass:** Rückfrage nach Prüfung eines KI-Vorschlags (F01-001, Wert 7.0): "Ist die Begründung
nur auf die sichtbare Tabellenzeile/den Screenshot gestützt, oder auf das ganze Pflichtenheft +
den ganzen Angebotskontext?" — Antwort per Code-Review von Ticket 28 (`2ed64c4`): **Ja, breit
abgestützt.** `suggest_score_with_rag()` (`src/m15_evaluation.py:2929`) holt für **Vorgaben**
alle projektweiten Tender-Dokumente (`get_tender_document_ids`, gefiltert nach Rolle) und für
**Angebot** alle mit dem Bieter verknüpften Dokumente (`bidder_doc_ids_for_criterion`) — jeweils
per Hybrid-RAG (`retrieve_relevant_chunks_hybrid`, bis zu `rag_chunks_per_role` = 12 Chunks,
projektseitig 4–24 einstellbar) **plus** deterministischem Volltext-Scan auf die exakte Zeile
(z. B. `F01-001`), damit die Tabellenzeile in ~200-seitigen Merge-PDFs nicht dem semantischen
Ranking zum Opfer fällt. Vorgabenseitig kommt zusätzlich der Parent-Block (`F01`) als Kontext
dazu; angebotsseitig bewusst **nicht** (keine Vermischung mit F01-002 etc.). Das ist also klar
mehr als "nur die 2 Screenshot-Bilder" — die zwei Bilder zeigen nur eine UI-Momentaufnahme *eines*
Laufs, nicht den tatsächlichen Kontextumfang.

**Beobachtetes Problem:** Genau die neue Transparenz aus Ticket 28 (`📎 RAG-Basis (X Quellen)`,
`_rag_basis.html`) — die diese Frage im Livebetrieb selbst beantworten würde — war im
Screenshot nicht sichtbar. Ursachenanalyse:
1. **Layout:** `_rag_basis.html` wird in `_suggestion.html`/`_cell.html` erst **nach** Begründung
   und "Angebotszitat (RAG-Quelle)" eingebunden — im Screenshot endet die sichtbare Fläche exakt
   beim Zitat (Scroll-Indikator am unteren Rand sichtbar). Sehr wahrscheinlich einfach nicht
   weit genug gescrollt.
2. **Deployment:** `backend/main.py:2798` läuft mit `reload=False`; Template-/Code-Änderungen aus
   `2ed64c4` werden auf einer laufenden Produktivinstanz erst nach einem Service-Neustart
   (`deployment/systemd/projekthub-backend.service.example`) wirksam. Falls die getestete Instanz
   seit dem Merge nicht neu gestartet wurde, fehlt der Block tatsächlich.

**Aufgabe:**
1. **Sofort-Check (kein Code):** Auf der Zielinstanz prüfen, ob `git log -1` `2ed64c4` oder neuer
   zeigt, und den Backend-Service neu starten, falls nicht bereits geschehen. Danach denselben
   KI-Vorschlag erneut auslösen und im Suggestion-Panel bis unter das Zitat scrollen — dort sollte
   `📎 RAG-Basis (… Quellen) · Zeile F01-001` erscheinen und aufklappbar sein.
2. **Sichtbarkeit erhöhen (UX):** In beiden Vorkommen (`_suggestion.html`, `_cell.html`) ist
   `_rag_basis.html` immer eine Einzel-Instanz (Modal bzw. Zellen-Dialog je Kriterium — keine
   Listen-Wiederholung), Auto-Aufklappen würde also nicht wie in einer Matrixzeile "fluten".
   Vorschlag: kompakte Quellen-Zusammenfassung (z. B. "📎 7 Quellen · Zeile F01-001") direkt unter
   "Wert: X" plazieren (vor Begründung/Zitat), Detail-Liste bleibt weiterhin per Klick aufklappbar
   darunter an bisheriger Stelle — macht die Nachvollziehbarkeit sofort sichtbar, ohne die
   bestehende Reihenfolge (Wert → Begründung → Zitat → Details) inhaltlich zu verändern.

**Akzeptanzkriterien:**
- Ohne zu scrollen ist am KI-Vorschlag sofort erkennbar, wie viele Quellen (Vorgaben + Angebot)
  zugrunde lagen.
- Deployment-Runbook (README/Docs) erwähnt explizit: Template-/Route-Änderungen erfordern
  Service-Neustart (kein Hot-Reload in Produktion).

**Umsetzung UX (Sep 2026):** `_rag_basis_summary.html` — kompakte Zeile direkt unter «Wert: X»
(📎 N Quellen · Zeile F01-001 · Aufteilung Vorgaben/Angebot); volle Liste bleibt in
`_rag_basis.html` als aufklappbare Details darunter. Einbindung in `_suggestion.html` und
`_cell.html` (🤖-Zeile in der Matrix).

---

## Ticket 30 — RAG-Basis: Score-Anzeige + Chunk-IDs in Begründung ✅ erledigt

**Anlass:** Live-Check nach Ticket-28-Deployment (Screenshots F01-001, Apps With Love).
Ticket 28 funktioniert wie geplant (Dateiname am Zitat, RAG-Basis mit 19 Quellen, Methoden,
Parent-Ref-Hinweis). Zwei reine Bugs/Qualitätsprobleme beim genauen Lesen.

**Befund 1 — Prozentangabe kaputt:** RAG-Basis-Panel zeigte Werte wie 918 %, 2469 %, 3566 %.
Ursache: `_rag_basis_doc_entry()` nutzte rohen BM25 `match_score` (kann >> 1) statt
`normalized_match_score` (0–1). Nur Anzeige-Bug, kein Einfluss auf Retrieval-Ranking.

**Befund 2 — Chunk-IDs in KI-Begründung:** Texte wie «…im Kontext (Chunk 1124)…» /
«…Ausschreibungsunterlagen (Chunk 1449)…». Ursache: `_format_rag_context()` labelte Blöcke mit
`Chunk {id}`; Modell übernahm das in die rekursfähige Begründung — für BöB/IVöB unbrauchbar.

**Umsetzung:**

1. `_rag_basis_doc_entry()`: `normalized_match_score` statt `match_score` für `entry["score"]`.
2. `_format_rag_context()`: kein `Chunk {id}` mehr im LLM-Kontext (Datei/S./Kap. bleiben).
3. System-Prompt `suggest_score_with_rag()`: explizites Verbot interner Chunk-/DB-IDs.
4. `_sanitize_suggestion_justification()`: entfernt `(Chunk NNN)` als Sicherheitsnetz.

**Akzeptanzkriterien:**
- RAG-Basis-Prozentwerte liegen in 0–100 % (keine vierstelligen Werte).
- Neue KI-Begründungen enthalten keine `(Chunk …)`-Verweise.
- Chunk-ID bleibt im RAG-Basis-Panel (Admin-Transparenz) sichtbar.

**Hinweis:** Bereits gespeicherte 🤖-Vorschläge behalten alte Texte/Scores bis zum erneuten
KI-Lauf.
