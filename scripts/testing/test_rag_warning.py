"""
Test script für RAG Low Confidence Warning
"""
import sys
sys.path.insert(0, '.')

from src.m09_rag import rag_low_confidence_warning

# Test 1: Keine Dokumente
print("=" * 80)
print("Test 1: Keine Dokumente")
result = {"documents": []}
warning = rag_low_confidence_warning(result, 0.45)
print(f"Result: {warning}")
assert warning is not None, "Sollte Warnung geben"
print("✅ PASS")

# Test 2: Alle Scores unter Threshold (dein Fall)
print("\n" + "=" * 80)
print("Test 2: Alle Scores unter Threshold (SIK-AGB Fall)")
result = {
    "documents": [
        {"similarity": 0.434, "normalized_match_score": 1.0, "filename": "Doc1.pdf"},
        {"similarity": 0.437, "normalized_match_score": 0.0, "filename": "Doc2.pdf"},
        {"similarity": 0.391, "normalized_match_score": 0.0, "filename": "Doc3.pdf"},
    ]
}
warning = rag_low_confidence_warning(result, 0.45)
print(f"Result: {warning}")
assert warning is not None, "Sollte Warnung geben (max 43.7% < 45%)"
assert "43%" in warning or "44%" in warning, f"Sollte max Score enthalten, got: {warning}"
print("✅ PASS")

# Test 3: Ein Score über Threshold
print("\n" + "=" * 80)
print("Test 3: Ein Score über Threshold")
result = {
    "documents": [
        {"similarity": 0.50, "filename": "GoodDoc.pdf"},
        {"similarity": 0.30, "filename": "BadDoc.pdf"},
    ]
}
warning = rag_low_confidence_warning(result, 0.45)
print(f"Result: {warning}")
assert warning is None, "Sollte KEINE Warnung geben (50% > 45%)"
print("✅ PASS")

# Test 4: Exakt am Threshold
print("\n" + "=" * 80)
print("Test 4: Exakt am Threshold")
result = {
    "documents": [
        {"similarity": 0.45, "filename": "BorderlineDoc.pdf"},
    ]
}
warning = rag_low_confidence_warning(result, 0.45)
print(f"Result: {warning}")
assert warning is None, "Sollte KEINE Warnung geben (45% == 45%)"
print("✅ PASS")

# Test 5: Knapp unter Threshold
print("\n" + "=" * 80)
print("Test 5: Knapp unter Threshold")
result = {
    "documents": [
        {"similarity": 0.4499, "filename": "AlmostDoc.pdf"},
    ]
}
warning = rag_low_confidence_warning(result, 0.45)
print(f"Result: {warning}")
assert warning is not None, "Sollte Warnung geben (44.99% < 45%)"
print("✅ PASS")

print("\n" + "=" * 80)
print("🎉 ALLE TESTS BESTANDEN")
print("=" * 80)
