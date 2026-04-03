import sys
sys.path.insert(0, ".")

from src.m09_rag import _keyword_search

query = (
    "wie antworten wir hier (bitte kurz und bündig antworten!) Frage von Anbieter 5 (Nr. 1): "
    "In den Ausschreibungsunterlagen wird die Zusammenarbeit mit Subunternehmen untersagt. "
    "Wir bitten um Klarstellung, ob dieses Verbot auch für verbundene Unternehmen im Sinne eines Konzernverbunds gilt. "
    "Konkret betrifft dies die Einbeziehung unserer 100%igen Tochtergesellschaft, die als fester Bestandteil unserer internen Lieferkette und Ressourcenplanung agiert. "
    "Da die operative Projektabwicklung bei uns standortübergreifend als Einheit erfolgt, stellt sich die Frage, ob die Nutzung interner Konzernressourcen (Personal/Infrastruktur) der Tochtergesellschaft als unzulässige Subbeauftragung gewertet wird, oder ob diese als Eigenleistung der Bieterin im Rahmen der Konzernprivilegierung anerkannt wird."
)
project_key = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"
rows = _keyword_search(query, project_key=project_key, limit=200, exclude_classification='FAQ/Fragen-Katalog')
for i, row in enumerate(rows, 1):
    text = (row.get('text') or '').lower()
    if 'subunternehm' in text:
        print('RANK', i)
        print('SCORE', row.get('match_score'))
        print('RAW', row.get('raw_bm25_score'))
        print('IDF', row.get('keyword_idf_score'))
        print('COVERAGE', row.get('keyword_coverage'))
        print((row.get('text') or '').replace('\n', ' ')[:300])
        break
else:
    print('NOT FOUND')
