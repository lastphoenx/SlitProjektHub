# scripts/testing/test_analogy_prompt.py
import os
import sys
from pathlib import Path

# Pfad-Setup
ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.m08_llm import try_models_with_messages

def test_distillation_with_analogy():
    original_query = (
        "In den Ausschreibungsunterlagen wird die Zusammenarbeit mit Subunternehmen untersagt. "
        "Wir bitten um Klarstellung, ob dieses Verbot auch für verbundene Unternehmen im Sinne eines Konzernverbunds gilt. "
        "Konkret betrifft dies die Einbeziehung unserer 100%igen Tochtergesellschaft, die als fester Bestandteil "
        "unserer internen Lieferkette und Ressourcenplanung agiert. Da die operative Projektabwicklung bei uns "
        "standortübergreifend als Einheit erfolgt, stellt sich die Frage, ob die Nutzung interner Konzernressourcen "
        "(Personal/Infrastruktur) der Tochtergesellschaft als unzulässige Subbeauftragung gewertet wird, "
        "oder ob diese als Eigenleistung der Bieterin im Rahmen der Konzernprivilegierung anerkannt wird."
    )

    new_system_prompt = (
        "Du bist ein Retrieval-Experte. Deine Aufgabe ist es, eine Nutzeranfrage in "
        "eine präzise Such-Query (Schlüsselwörter) für eine Dokumentensuche umzuwandeln.\\n\\n"
        "KERNPRINZIP: Unterscheide zwischen RAHMEN und THEMA.\\n"
        "- RAHMEN: Beschreibt den Fundort, die Quelle oder den prozessualen Kontext "
        "(z.B. Dokumentennamen, Projektphasen, Art der Unterlagen).\\n"
        "- THEMA: Beschreibt den eigentlichen fachlichen, rechtlichen oder technischen Inhalt der Frage.\\n\\n"
        "ANALOGIE:\\n"
        "Wenn die Frage lautet: 'Wie lange muss ich im REZEPTBUCH nachschauen für das BRATEN VON FLEISCH?', "
        "dann ist 'Rezeptbuch' der RAHMEN (löschen!) und 'Fleisch braten' das THEMA (behalten!).\\n\\n"
        "Wende dieses Prinzip auf die vorliegende Fachdomäne an:\\n"
        "1. Entferne Höflichkeit, UI-Wrapper ('Frage von...') und alle RAHMEN-Wörter.\\n"
        "2. Extrahiere nur die technischen/fachlichen Substantive des THEMAS.\\n"
        "3. Antwort: NUR die optimierten Schlüsselwörter, keine Erklärung."
    )

    print("--- TEST: QUERY DISTILLATION MIT ANALOGY-PROMPT ---")
    print(f"ORIGINAL QUERY:\\n{original_query[:150]}...\\n")
    
    try:
        # Wir nutzen GPT-4o-mini wie in der Config vorgesehen
        distilled = try_models_with_messages(
            provider="openai",
            system=new_system_prompt,
            messages=[{"role": "user", "content": original_query}],
            max_tokens=100,
            temperature=0.0,
            model="gpt-4o-mini"
        )
        
        print(f"DESTILLIERTE QUERY (ERGEBNIS):\\n=> {distilled}\\n")
        
        # Check ob \"Ausschreibung\" oder \"Pflichtenheft\" noch drin sind
        bad_words = ["ausschreibung", "unterlagen", "pflichtenheft", "frage"]
        found_bad = [w for w in bad_words if w in distilled.lower()]
        
        if not found_bad:
            print("OK: Keine Rahmen-Wörter mehr enthalten.")
        else:
            print(f"WARNUNG: Folgende Rahmen-Wörter wurden noch gefunden: {found_bad}")
            
    except Exception as e:
        print(f"Fehler beim API-Aufruf: {e}")

if __name__ == "__main__":
    test_distillation_with_analogy()
