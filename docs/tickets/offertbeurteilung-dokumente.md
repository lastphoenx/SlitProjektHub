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

## Ticket 4 — Zwei-Stufen-KI-Auswahl (Input-KI / Output-KI) für Offertbeurteilung

**Ziel:** Gleiches Auswahlmuster wie Projektideen/Visual-Lab statt des heutigen
Einzel-Providers.

**Aktueller Stand:**
- `POST /evaluation/suggest` nimmt aktuell nur ein Provider/Model-Paar
  (`provider: str = Form("openai")`, `model: str = Form("")`,
  `backend/app/routes/evaluation.py:367-368`) — ein einzelner Picker, kein
  Input-KI/Output-KI-Konzept.
- Referenz-Implementierung: `_llm_picker.html`-Partial mit `llm_role='input'|'output'`
  (bereits produktiv in Projektideen/Visual-Lab) plus
  `resolve_vision_provider_model()`-artige Auflösung in `src/m17_visual_lab_refs.py`.

**Aufgabe:**
1. `_llm_picker.html`-Partial unverändert in `backend/templates/evaluation/index.html`
   einbinden (zweimal: `llm_role='input'` für die RAG-Anfrage-Formulierung/Retrieval-seitige
   Modellwahl, `llm_role='output'` für die eigentliche Bewertungsgenerierung — Rollenzuschnitt
   ggf. mit UX abgleichen, da `suggest_score_with_rag()` aktuell nur einen LLM-Call macht).
2. `suggest_score_with_rag()`-Signatur um zweites Provider/Model-Paar erweitern, falls die
   Rollentrennung tatsächlich zwei LLM-Aufrufe ergibt (z. B. Query-Reformulierung vs.
   Bewertungs-Generierung) — falls nicht, mit dem Entwickler klären, ob hier nur *eine*
   Rolle sinnvoll ist und das Picker-Partial entsprechend einfach mit einem Slot verwendet
   wird (nicht künstlich zwei Rollen erzwingen, wo nur ein Cloud-Call stattfindet).
3. Gleiche Provider-Konstanten/Settings-Quelle wiederverwenden wie in Projektideen
   (`load_user_settings()`, `CLOUD_LLM_PROVIDERS`) — keine Parallel-Konfiguration.

**Akzeptanzkriterien:**
- UI-Konsistenz: Offertbeurteilung nutzt dieselbe Picker-Komponente wie Projektideen/Visual-Lab.
- Kein Duplicate-Code für Provider-Resolution.
- Ticket 3 (PII-Gate) gilt unverändert für beide Rollen, sobald ein Cloud-Provider gewählt ist.

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

**Reihenfolge-Empfehlung:** ~~3~~ → 1 → 2 → ~~5~~ → ~~6~~ → ~~7~~ → ~~8~~ → ~~9~~ → ~~10~~ → ~~11~~ → ~~12~~ → ~~13–17~~ → 4
