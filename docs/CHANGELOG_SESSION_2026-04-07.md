# Changelog - Session 07.04.2026

## Zusammenfassung
Erweiterungen der **Batch-QA** (08_Batch_QA.py) mit Fokus auf **Prompt-Qualität** und **Export-Dateinamen**.

---

## 1. Interpretations-Haltung (Stance)

### Motivation
Das Modell formulierte Antworten aus Sicht eines neutralen Beraters statt des Auftraggebers – mit Verweisen auf „Klärungsbedarf beim Auftraggeber" und Weichmachern wie „sollte", „könnte".

### Neues Dropdown: Interpretations-Haltung
**Datei:** `app/pages/08_Batch_QA.py`

| Option | Prompt-Injection |
|---|---|
| `(nur gemäss Rolle)` ← **Default** | _(leer – Haltung wird durch Rollenbeschreibung bestimmt)_ |
| `Neutral` | _Bei Interpretationsspielraum wäge objektiv ab._ |
| `Wohlwollend (erlaubend)` | _Bei Spielraum: verbindlich positiv – „ist zulässig, sofern…", „kann akzeptiert werden, wenn…"_ |
| `Restriktiv (ablehnend)` | _Bei Spielraum: verbindlich ablehnend – „ist nicht zulässig", „gilt als unzulässige Subbeauftragung"_ |

Die Injection wird an alle 3 System-Prompts angehängt (role_mode: none / all_merged / individual).

---

## 2. Formulierungsweise (Wording)

### Motivation
Antworten verwendeten Weichmacher und verwiesen auf künftige Klärungen – selbst wenn „Restriktiv" gewählt war. Das Modell brauchte explizite Anweisung zur Verbindlichkeit.

### Neues Dropdown: Formulierungsweise
**Datei:** `app/pages/08_Batch_QA.py`

| Option | Prompt-Injection |
|---|---|
| `(nur gemäss Rolle)` | _(leer)_ |
| `Klar & abschliessend` ← **Default** | _Formuliere verbindlich und direkt. Verwende „ist", „gilt als", „wird als … verstanden". Vermeide Weichmacher. Die Antwort soll Nachfragen unterbinden, nicht auslösen._ |
| `Vage & mit Weichmachern` | _Formuliere offen und mit Vorbehalt. Verwende „sollte", „könnte", weise auf Klärungsbedarf hin._ |

**Layout:** Beide neuen Dropdowns erscheinen nebeneinander (2 Spalten), je mit Hinweis-Caption darunter.

---

## 3. Export-Dateiname: CSV-Stem statt Projekttitel

### Problem
Der Projekttitel war lang und unspezifisch (z.B. `batch_erweiterung-und-optimier_openai...`).

### Lösung
```python
# Vorher:
project_slug = _slug(selected_project, 24)

# Nachher:
csv_stem = selected_csv.filename.rsplit(".", 1)[0]  # z.B. "fragen_los2_v3"
csv_slug = _slug(csv_stem, 28)
```

Stance- und Wording-Slug werden nur eingebaut wenn **nicht** `(nur gemäss Rolle)` gewählt:
```python
stance_slug = _slug(answer_stance, 10) if answer_stance != "(nur gemäss Rolle)" else ""
wording_slug = _slug(answer_wording, 10) if answer_wording != "(nur gemäss Rolle)" else ""
parts = [p for p in ["batch", csv_slug, ..., stance_slug, wording_slug] if p]
```

**Beispiel-Dateiname:**
```
batch_fragen_los2_v3_openai_gpt-4o_projektl_kurz_restrikt_klar.csv
```

---

## 4. Prompt-Vorschau nachgeführt

Die Prompt-Vorschau (Schritt 5) berücksichtigt beide neuen Parameter:
```python
_style = style_instructions[_preview_style] + stance_instructions[answer_stance] + wording_instructions[answer_wording]
```
