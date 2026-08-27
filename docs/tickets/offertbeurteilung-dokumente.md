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

## Ticket 3 — PII-Sanitizer (Stufe 1+2) für `suggest_score_with_rag()` — **Priorität hoch**

**Ziel:** Bieter-Dokument-Inhalte, die an Cloud-LLMs gehen, durchlaufen dieselbe
zweistufige Sanitize-Pipeline wie Projektideen/Visual-Lab, **bevor** mehr Dokumenttypen
(Bilanz, Referenzblätter mit echten Namen, unterschriebene Vorbehaltsliste) über Ticket 1/2
in diesen Pfad kommen.

**Aktueller Stand (Lücke, verifiziert):**
- `suggest_score_with_rag()` (`src/m15_evaluation.py:639-728`) baut `context` direkt aus
  rohen RAG-Chunk-Texten (`context_parts.append(f"[{i}] Datei: {fname}, ...\n{text}")`,
  Zeile 668-672) und schickt das ungefiltert an `try_models_with_messages(provider, ...)`
  (Zeile 689-696).
- **Kein** Aufruf von `sanitize_for_cloud_text()` (`src/m16_idea_visual.py`), **kein**
  DSGVO-Bestätigungs-Gate (vergleichbar `validate_assess_cloud_gates()` /
  `validate_cloud_gates_for_references()`), **keine** Stufe-2-Anonymisierung
  (`src/m18_cloud_pii.py::apply_swiss_pii_sanitize()`).
- Provider-Default ist `"openai"` (`backend/app/routes/evaluation.py:367`) — also Cloud per
  Default, nicht lokal.
- Das eigene, bereits produktiv genutzte Paket **swiss-pii-anonymizer**
  (https://github.com/lastphoenx/swiss-pii-anonymizer, Presidio + Flair) ist bereits
  Dependency (`requirements.txt`) und über `src/m18_cloud_pii.py` fertig integriert —
  hier **nicht neu bauen, nur denselben Einstiegspunkt aufrufen** (keine Redundanz).

**Aufgabe:**
1. In `suggest_score_with_rag()`: jeden `context_parts`-Textblock (bzw. den fertigen
   `context`-String vor dem Prompt-Bau) durch `sanitize_for_cloud_text()`
   (`m16_idea_visual.py`) schicken — das deckt Stufe 1 (Regex) **und** Stufe 2
   (`m18_cloud_pii.apply_swiss_pii_sanitize()`) automatisch ab, da Stufe 2 bereits intern in
   Stufe 1 eingehängt ist (siehe bestehende Architektur-Entscheidung, ein zentraler
   Sanitize-Punkt für alle Call-Sites).
2. Cloud-Gate/Bestätigungs-Checkbox analog Projektideen ergänzen: nur wenn `provider` in
   `CLOUD_LLM_PROVIDERS` (`m16_idea_visual.py`, `is_cloud_llm_provider()`) UND Bieter hat
   verlinkte Dokumente → Checkbox „Ich bestätige, dass die an {Provider} übermittelten
   Angebotsauszüge auf personenbezogene Daten geprüft wurden" muss aktiv sein, bevor
   `POST /evaluation/suggest` ausgeführt wird (analog `_cloud_gate_attachment_state()`-Pattern).
3. Kein neuer Sanitize-Code — ausschließlich bestehende Funktionen aus
   `m16_idea_visual.py` / `m18_cloud_pii.py` wiederverwenden.
4. Test ergänzen in `scripts/testing/` (analog `test_cloud_pii.py`): Mock von
   `sanitize_for_cloud_text()` in `suggest_score_with_rag()`, prüft dass der an
   `try_models_with_messages()` übergebene `user`-Prompt keine rohen PII-Marker mehr enthält.

**Akzeptanzkriterien:**
- Kein RAG-Chunk-Text erreicht `try_models_with_messages()` mehr ungesanitized, wenn
  `provider` ein Cloud-Provider ist.
- Ohne bestätigte Checkbox liefert `/evaluation/suggest` bei Cloud-Provider einen Block
  (HTTP 4xx oder UI-Hinweis), analog Projektideen-Verhalten.
- Lokale Provider (Ollama o. ä.) bleiben von der Gate-Pflicht unberührt (wie bei
  Projektideen bereits gehandhabt).

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

**Reihenfolge-Empfehlung:** 3 → 1 → 2 → 4 (Compliance-Lücke zuerst, dann die UX-Erweiterung,
die die Lücke sonst vergrößert; Sub-Klassifikation und KI-Picker sind unabhängig
nachziehbar).
