import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m09_rag import retrieve_relevant_chunks_hybrid, retrieve_relevant_chunks, _keyword_search, clear_rag_cache

# Cache leeren
clear_rag_cache()

pkey = "erweiterung-und-optimierung-der-kursverwaltungsl-sung-escada-f-r-unisport-moderner-webauftritt-zahlungsabwicklung-und-portalerneuerung"
query = "Preisstruktur: Welche Preisstruktur wird erwartet (z. B. Fixpreis vs. Aufwandspositionen, Trennung nach Entwicklung/Betrieb)? (Gemäss Pflichtenheft, S. 51, Kapitel 14.1.2.1 Preisbewertung)"

print(f"{'='*80}")
print(f"DIAGNOSE: Frage 65 - Warum fehlt Preisblatt?")
print(f"{'='*80}")
print(f"\nQuery: {query[:100]}...")

# Step 1: Semantic Candidates
print(f"\n{'='*80}")
print("STEP 1: SEMANTIC CANDIDATES (limit × 5, threshold × 0.35)")
print(f"{'='*80}")

semantic = retrieve_relevant_chunks(
    query=query,
    project_key=pkey,
    limit=7 * 5,  # 35
    threshold=0.15,  # discovery_threshold = min(0.45 × 0.35, 0.15) = 0.15
    exclude_classification="FAQ/Fragen-Katalog"
)

docs_semantic = semantic.get("documents", [])
print(f"Total Semantic: {len(docs_semantic)} Chunks")

preisblatt_semantic = [d for d in docs_semantic if "Preisblatt" in d["filename"]]
if preisblatt_semantic:
    print(f"\n✅ Preisblatt gefunden: {len(preisblatt_semantic)} Chunks")
    for d in preisblatt_semantic:
        print(f"   Score: {d['similarity']:.0%} | {d['text'][:80]}...")
else:
    print(f"\n❌ Preisblatt NICHT in semantic_results!")

# Step 2: Keyword Candidates
print(f"\n{'='*80}")
print("STEP 2: KEYWORD CANDIDATES (limit × 10)")
print(f"{'='*80}")

keyword = _keyword_search(
    query=query,
    project_key=pkey,
    limit=7 * 10,  # 70
    exclude_classification="FAQ/Fragen-Katalog"
)

print(f"Total Keyword: {len(keyword)} Chunks")

preisblatt_kw = [d for d in keyword if "Preisblatt" in d["filename"]]
if preisblatt_kw:
    print(f"\n✅ Preisblatt gefunden: {len(preisblatt_kw)} Chunks")
    for d in preisblatt_kw:
        print(f"   Match Score: {d['match_score']:.0%} | {d['text'][:80]}...")
else:
    print(f"\n❌ Preisblatt NICHT in keyword_results!")

# Step 3: Filename Boost Check
print(f"\n{'='*80}")
print("STEP 3: FILENAME BOOST CHECK")
print(f"{'='*80}")

query_words = [w.lower().rstrip(":") for w in query.split() if len(w) > 3]
print(f"Query Words (>3 chars): {query_words[:10]}")

fname = "Anhang2_Preisblatt_Unisport.pdf"
fname_parts = fname.lower().replace(".", " ").replace("_", " ").replace("-", " ").split()
print(f"Filename Parts: {fname_parts}")

matched = False
match_details = []
for qw in query_words:
    for fp in fname_parts:
        if qw in fp or fp in qw:
            matched = True
            match_details.append(f"'{qw}' vs '{fp}' → Teilwort-Match")
        elif len(qw) >= 5 and len(fp) >= 5 and qw[:5] == fp[:5]:
            matched = True
            match_details.append(f"'{qw}' vs '{fp}' → Präfix '{qw[:5]}'")

if matched:
    print(f"\n✅ Filename Boost WÜRDE greifen:")
    for detail in match_details[:3]:
        print(f"   {detail}")
else:
    print(f"\n❌ Filename Boost würde NICHT greifen!")

# Step 4: Final Hybrid
print(f"\n{'='*80}")
print("STEP 4: FINAL HYBRID RESULTS")
print(f"{'='*80}")

results = retrieve_relevant_chunks_hybrid(
    query=query,
    project_key=pkey,
    limit=7,
    threshold=0.45,
    exclude_classification="FAQ/Fragen-Katalog"
)

docs = results.get("documents", [])
print(f"\nTop-7 Results:")

by_doc = {}
for d in docs:
    fname = d["filename"]
    if fname not in by_doc:
        by_doc[fname] = []
    by_doc[fname].append(d)

for fname, chunks in by_doc.items():
    scores = [c.get("similarity", c.get("match_score", 0)) for c in chunks]
    print(f"  {fname[:40]:40} | {len(chunks)} Chunks | Max: {max(scores):.0%}")

preisblatt_final = [d for d in docs if "Preisblatt" in d["filename"]]
if preisblatt_final:
    print(f"\n✅ Preisblatt im Final Result!")
else:
    print(f"\n❌ Preisblatt FEHLT im Final Result!")
