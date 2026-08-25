import sys
sys.path.insert(0, ".")

from src.m09_rag import retrieve_relevant_chunks_hybrid

query = (
    "wie antworten wir hier (bitte kurz und bündig antworten!) Frage von Anbieter 5 (Nr. 1): "
    "In den Ausschreibungsunterlagen wird die Zusammenarbeit mit Subunternehmen untersagt. "
    "Wir bitten um Klarstellung, ob dieses Verbot auch für verbundene Unternehmen im Sinne eines Konzernverbunds gilt. "
    "Konkret betrifft dies die Einbeziehung unserer 100%igen Tochtergesellschaft, die als fester Bestandteil unserer internen Lieferkette und Ressourcenplanung agiert. "
    "Da die operative Projektabwicklung bei uns standortübergreifend als Einheit erfolgt, stellt sich die Frage, ob die Nutzung interner Konzernressourcen (Personal/Infrastruktur) der Tochtergesellschaft als unzulässige Subbeauftragung gewertet wird, oder ob diese als Eigenleistung der Bieterin im Rahmen der Konzernprivilegierung anerkannt wird."
)

project_key = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"

results = retrieve_relevant_chunks_hybrid(
    query,
    project_key=project_key,
    limit=7,
    threshold=0.45,
    exclude_classification="FAQ/Fragen-Katalog",
)

docs = results.get("documents", [])
print("COUNT", len(docs))
for i, doc in enumerate(docs, 1):
    sem = doc.get("similarity", 0.0)
    kw = doc.get("match_score", 0.0)
    combined = max(sem, kw * 1.3)
    text = (doc.get("text", "") or "").replace("\n", " ")
    print(f"{i}. {doc.get('filename')} | cls={doc.get('classification')} | sem={sem:.3f} kw={kw:.3f} comb={combined:.3f}")
    print(text[:220])
    print("---")
