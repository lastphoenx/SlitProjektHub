"""Test Query Expansion - simplified"""
import sys
sys.path.insert(0, '.')

from src.m09_rag import _detect_acronyms

# Test mit sik-agb (lowercase wie im distilled query)
query = "klauseln sik-agb nicht verhandelbar widersprueche vertragsvorschlag"
acronyms = _detect_acronyms(query)

print("Query:", query)
print("Erkannte Akronyme:", acronyms)
print()

if "sik-agb" in acronyms:
    print("SUCCESS: sik-agb wurde erkannt!")
else:
    print("FAIL: sik-agb wurde NICHT erkannt")
