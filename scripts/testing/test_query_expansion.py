"""
Test Query Expansion Feature
"""
import sys
sys.path.insert(0, '.')

from src.m09_rag import _detect_acronyms, _expand_acronyms_with_llm

import logging
logging.basicConfig(level=logging.ERROR)

print("=" * 80)
print("Test 1: Akronym-Erkennung")
print("=" * 80)

test_queries = [
    "klauseln sik-agb nicht verhandelbar widersprüche vertragsvorschlag",
    "API REST DSGVO compliance",
    "http server webportal hosting",
    "normale woerter ohne akronyme",
    "HTTP-API SOAP-WSDL integration",
]

for query in test_queries:
    acronyms = _detect_acronyms(query)
    print(f"Query: {query}")
    print(f"  -> Akronyme: {acronyms}")
    print()

print("\n" + "=" * 80)
print("Test 2: LLM-Expansion (echterAPI-Call)")
print("=" * 80)

test_acronyms = ["SIK-AGB", "DSGVO", "API"]
print(f"Zu expandieren: {test_acronyms}")
print("Rufe LLM auf...")

expansions = _expand_acronyms_with_llm(test_acronyms)

print(f"\nExpansionen:")
for acronym, expansion in expansions.items():
    print(f"  {acronym} → {expansion}")

print("\n" + "=" * 80)
print("Tests abgeschlossen")
print("=" * 80)
