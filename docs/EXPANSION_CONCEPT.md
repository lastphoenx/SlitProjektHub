# Ausbau-Konzept: Vollständiger Vergabezyklus

> Stand: 2026-08-24. Baut auf der Mehrbenutzer-/AppRollen-Trennung (`src/m14_auth.py`) auf.

## 1. Zielbild

Drei Phasen, ein Werkzeug:

```
A) Pflichtenheft erstellen        B) Fragerunde                  C) Offertbeurteilung
   (Vorlage-gebunden)                (Bieterfragen → Entwürfe)      (Eignung + Zuschlag, gewichtet)
   ─────────────────────             ─────────────────────          ─────────────────────
   noch nicht vorhanden               grösstenteils vorhanden        noch nicht vorhanden
   (Batch-QA)                                                       (Kern-Lücke)
```

Alle drei Phasen sollen aus der Perspektive verschiedener **AppRollen** nutzbar sein: `super_user`, `projektleiter_intern`, `product_owner`, `auftraggeber` (Tabelle `app_role`, bereits angelegt).

**Wichtige Abgrenzung** (siehe auch `docs/AUTH.md`): `AppRole` (Zugriffsperspektive) ≠ `Role` in `src/m03_db.py` (Fachrolle wie "Leiter Cash" für RAG-Antworten). Beide Konzepte existieren parallel und dürfen im Code nicht vermischt werden.

## 2. Priorisierung

**Reihenfolge: C → B (Erweiterung) → A.**

- **C zuerst**, weil die Offerten laut Auftraggeber jetzt akut vorliegen — es gibt aktuell keinen Weg, sie strukturiert zu vergleichen.
- **B** existiert im Kern (Batch-QA) — hier reicht eine kleinere Erweiterung.
- **A** (Pflichtenheft-Vorlage) ist zeitlich unkritisch (nächste Ausschreibung folgt später) und technisch der aufwändigste Teil (Struktur strikt, Inhalt flexibel — bisher generiert die App nur freien Text). Zuletzt bauen.

## 3. Phase C — Offertbeurteilung (nächster Schritt)

### 3.1 Neues Datenmodell (in `src/m03_db.py` oder neues `m15_evaluation.py`)

| Tabelle | Zweck | Kernfelder |
|---|---|---|
| `Bidder` | Anbieter pro Projekt | `project_key`, `name`, `is_deleted` |
| `Criterion` | Eignungs-/Zuschlagskriterium | `project_key`, `kind` (`eignung`\|`zuschlag`), `name`, `weight_pct`, `parent_id` (Unterkriterien), `scale_max` (default 10) |
| `Score` | Bewertung eines Bieters zu einem Kriterium | `bidder_id`, `criterion_id`, `evaluator_user_id` (FK → `app_user`), `value`, `justification`, `source_chunk_ref` (Zitat aus dem Angebot), `created_at` |

Wichtig: `Score.evaluator_user_id` verweist auf die neue `User`-Tabelle aus Schritt b) — das ist der Grund, warum die Benutzer-Trennung zuerst gebaut wurde. Ohne Benutzerbezug ist eine Bewertung bei einer Beschwerde (Verwaltungsgericht) nicht verantwortbar.

### 3.2 Neuer Dokumenttyp

`DOCUMENT_CLASSIFICATIONS` (`src/m03_db.py:21`) um `"Angebot (Bieter)"` erweitern, verknüpft mit `Bidder` (analog zu `ProjectDocumentLink`, aber `bidder_id` statt/zusätzlich zu `project_key`).

### 3.3 Retrieval-Erweiterung

`retrieve_relevant_chunks_hybrid()` (`src/m09_rag.py`) um Filter `bidder_id` erweitern, damit Retrieval gezielt **im Angebot eines bestimmten Bieters** nach der Passage zu einem Kriterium sucht (heute: nur projektweit).

### 3.4 KI-Vorschlag, Mensch entscheidet

Wie beim KI-Detector (`m13_ki_detector.py`): LLM liefert **Vorschlag** (Score + Zitat + Begründungstext), Bewerter bestätigt/überschreibt. Kein Autopilot — das ist keine Stilfrage, sondern in CH-Vergabeverfahren wegen Beschwerdefähigkeit/Gleichbehandlungsgebot nötig.

### 3.5 UI

Neue Matrix-Ansicht: Kriterien (Zeilen) × Bieter (Spalten), gewichtete Summe, automatische Rangfolge. Export als Bewertungsprotokoll (Excel) — Formatierungslogik aus Batch-QA-Export (`08_Batch_QA.py`) wiederverwendbar, nur transponiert.

### 3.6 AppRollen-Rechte (Vorschlag, muss vom Auftraggeber/Product Owner bestätigt werden)

| Aktion | super_user | projektleiter_intern | product_owner | auftraggeber |
|---|---|---|---|---|
| Kriterien anlegen/gewichten | ✅ | ✅ | ✅ | ❌ (nur einsehen) |
| Bewertung erfassen | ✅ | ✅ | ✅ | ❌ |
| Eigene Bewertung nach Abschluss ändern | ✅ | ❌ (Audit-Trail) | ❌ | ❌ |
| Finale Matrix/Rangfolge einsehen | ✅ | ✅ | ✅ | ✅ |
| Einzelbewertungen der Bewerter einsehen | ✅ | ✅ | ❌/✅ (klären) | ❌ (nur Aggregat) |
| Export Bewertungsprotokoll | ✅ | ✅ | ✅ | ✅ (read-only) |

**Offene Entscheidung**: Sieht der Auftraggeber nur die aggregierte Rangfolge oder auch Einzel-Scores der internen Bewerter? Das ist keine technische, sondern eine organisatorische Frage — bitte vor Umsetzung klären.

## 4. Phase B — Fragerunde (Erweiterung, kein Neubau)

Batch-QA (`app/pages/08_Batch_QA.py`) deckt CSV-Import, RAG-gestützte Entwürfe, Export bereits ab. Für die Rollen-Perspektive fehlt nur:

- Sichtbarkeits-/Bearbeitungsrechte je AppRolle (z. B. Auftraggeber sieht Entwürfe erst nach Freigabe durch Projektleiter intern — Freigabe-Status-Feld auf den bestehenden Antworten ergänzen)
- Zuordnung "wer hat den Entwurf zuletzt bearbeitet/freigegeben" → `User`-FK, analog zu `Score.evaluator_user_id`

## 5. Phase A — Pflichtenheft-Erstellung nach Vorlage (später)

### 5.1 Neues Datenmodell

| Tabelle | Zweck |
|---|---|
| `PflichtenheftTemplate` | Vorlagen-Definition: Name, Quelldatei-Referenz |
| `TemplateChapter` | Kapitel: exakter Titel (fix), Pflicht-Flag, Reihenfolge, Prompt-Hinweis für Inhaltsgenerierung |

### 5.2 Merge-Engine

- LLM befüllt **nur den Inhalt** pro Kapitel gemäss Fachanforderungen; Struktur/Titel bleiben fix (kein freies Gesamtdokument-Generieren).
- `python-docx` (bereits in `requirements.txt`) für Word-Vorlagen direkt nutzbar.
- **Validierung nach Generierung**: Prüfung, dass alle Pflichtkapitel vorhanden sind und Titel exakt der Vorlage entsprechen (Formalfehler sind in Vergabeverfahren angreifbar).

### 5.3 AppRollen

Vermutlich: `product_owner`/`projektleiter_intern` erstellen, `super_user` administriert Vorlagen, `auftraggeber` review/Freigabe vor Publikation — analog zu Phase C, im Detail zu klären wenn's ansteht.

## 6. Offene Entscheidungen (bitte vor Umsetzungsstart klären)

1. Auftraggeber-Sicht auf Einzel-Score vs. nur Aggregat (siehe 3.6)
2. Ein Bewerter pro Kriterium oder Mehrfachbewertung mit Konsens/Mittelwert (Gremium)?
3. Skala fix 1–10 oder pro Kriterium konfigurierbar?
4. Sollen Eignungskriterien (Pass/Fail) ein Angebot vollständig ausschliessen können (K.O.), bevor Zuschlagskriterien überhaupt bewertet werden? (Standard in CH-Vergabeverfahren: ja)
