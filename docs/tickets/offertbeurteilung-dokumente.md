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

## Ticket 7 — Kriterien-Verwaltung nach dem Übernehmen (Unterfragen sichtbar & editierbar)

**Ziel:** Nach dem einmaligen Übernehmen aus der KI-Vorschau (oder manueller Anlage) müssen
Unterfragen wie F01-001…F01-008 einsehbar, editierbar und einzeln benennbar bleiben — aktuell
verschwindet diese Sicht komplett.

**Aktueller Stand (Lücke, verifiziert):**
- `POST /evaluation/criterion` (`evaluation.py:319`) legt nur neue Kriterien an, keine Edit-Route
  für Name/Beschreibung/Gewicht eines bestehenden Kriteriums.
- `POST /evaluation/criterion-phase` (`evaluation.py:351`) ändert ausschließlich `ranking_phase`.
- Die Tabellen-Editor-Ansicht aus Ticket 6/der KI-Vorschau (`evaluation/_criteria_extract.html`)
  existiert nur gegen den flüchtigen `_CRITERIA_PREVIEW_CACHE` (Ticket 5-Umfeld) — sobald
  übernommen, ist sie weg. Die Matrix-Zellenansicht (`_cell.html`) zeigt pro Top-Level-Kriterium
  nur den aggregierten Wert, keine Liste seiner Unterfragen.
- `Criterion.parent_id` und `rolled_up_score()` unterstützen die Hierarchie im Datenmodell
  bereits vollständig (`m15_evaluation.py:47, 272-300`) — es fehlt nur die UI/Route-Seite.

**Aufgabe:**
1. Neue Ansicht „Kriterien verwalten" (separat von der Bewertungs-Matrix), die dieselbe
   Tabellen-Editor-Komponente wie die KI-Vorschau wiederverwendet, aber gegen `list_criteria()`
   rendert statt gegen den Preview-Cache — pro Top-Level-Zeile aufklappbar mit allen Unterfragen.
2. Edit-Route für bestehende Kriterien (Name, Beschreibung, Gewicht, Skala, Phase) — sowohl
   Top-Level als auch Unterfragen einzeln editierbar.
3. Von dort aus auch neue Unterfragen zu einem bestehenden Top-Level-Kriterium hinzufügen
   können (nicht nur beim Erst-Import) — wichtig, falls Ticket 8 nicht alle F01-001…008 auf
   Anhieb sauber extrahiert und manuell nachgetragen werden muss.
4. Matrix-Zellenansicht (`_cell.html`) verlinkt bei Kriterien mit Unterfragen dorthin, statt nur
   den aggregierten Wert ohne Kontext zu zeigen.

**Akzeptanzkriterien:**
- F01-001…F01-008 sind nach dem Übernehmen als eigene, benannte Zeilen unter F-01 sichtbar und
  jede einzeln bearbeitbar (nicht nur beim einmaligen Vorschau-Schritt).
- Jede Unterfrage ist im Matrix-Workflow einzeln bewertbar (bereits unterstützt durch
  `rolled_up_score()` — hier geht es nur um Sichtbarkeit/Verwaltung, nicht um neue Bewertungslogik).

---

## Ticket 8 — KI-Extraktion erfasst Einzelanforderungen (F01-001…008 etc.), nicht nur die Übersichtstabelle

**Ziel:** Jede reale Einzelanforderung aus Kapitel „7. Anforderungen" (Fragenr./Kategorie/
Funktion/Referenz/Anforderung, z. B. F01-001…F01-008, T01-001…T01-010) landet als Unterfrage
im jeweiligen Top-Level-Zuschlagskriterium — nicht nur dessen generische Kapitel-Einleitung.

**Root Cause (verifiziert):** Der Zuschlag-RAG-Pass sucht mit der Query „Zuschlagskriterien
Gewichtung Punkte Bewertungsmatrix" (`m15_evaluation.py:1769`) — das trifft semantisch die
**Übersichtstabelle** (Kapitel 6: Name, Gewichtung, ein Einleitungssatz), nicht das separate,
umfangreichere Anforderungen-Kapitel mit den einzelnen Fragen. Deshalb entstehen die
Top-Level-Kriterien korrekt (Name, Gewicht stimmen), aber ohne oder mit unvollständigen
Unterfragen.

**Aufgabe:**
1. Eigener, vierter RAG-Pass gezielt für Einzelanforderungen, mit einer Query, die auf die
   tatsächliche Tabellenstruktur zielt (z. B. „Anforderungen Fragenr. Pflicht Antwort Lieferant
   Ja Nein Teilweise Begründung") statt auf die Gewichtungs-Übersicht.
2. Zweistufige Extraktion statt eines großen Einzel-Passes: Schritt 1 wie bisher Top-Level-
   Zuschlagskriterien (Name+Gewicht) aus der Übersichtstabelle. Schritt 2 sucht **pro erkanntem
   Top-Level-Kriterium gezielt per Referenz** (z. B. „F01") im Anforderungen-Kapitel nach allen
   zugehörigen Fragenr. und hängt sie als Unterfragen an — robuster als ein einzelner Pass mit
   mehr Chunk-Budget, weil bei dieser Ausschreibung allein für die Zuschlagskriterien ca. 25+
   Einzelfragen über viele Seiten verteilt sind und `max_tokens=4000` für die Antwort sonst eng wird.
3. Voraussetzung für Ticket 7: manuelles Nachtragen fehlender Unterfragen muss so oder so möglich
   sein, auch wenn Schritt 1+2 nicht jede Ausschreibung perfekt abdecken.

**Akzeptanzkriterien:**
- Bei der Unisport-Ausschreibung: F-01 bekommt beim Übernehmen automatisch die Unterfragen
  F01-001 bis F01-008 mit ihrem jeweils echten Anforderungstext (nicht nur der generischen
  Kapitel-Einleitung), analog für die anderen Zuschlagskriterien mit Einzelanforderungen.

---

**Reihenfolge-Empfehlung:** ~~3~~ → 1 → 2 → ~~5~~ → ~~6~~ → 7 → 8 → 4
(Ticket 3, 5, 6 erledigt; 7 vor 8, weil 8 ohne die Nachpflege-Möglichkeit aus 7 nie
vollständig zuverlässig sein wird; Ticket 4 optional).
