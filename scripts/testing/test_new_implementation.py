#!/usr/bin/env python
"""Quick test of new implementations"""

print("Testing imports...")
try:
    from src.m07_projects import upsert_project, _get_task_keys_for_roles
    print("✓ m07_projects imports OK")
except Exception as e:
    print(f"✗ m07_projects import failed: {e}")
    exit(1)

try:
    from src.m09_docs import sync_documents_to_chromadb
    print("✓ m09_docs.sync_documents_to_chromadb imports OK")
except Exception as e:
    print(f"✗ m09_docs import failed: {e}")
    exit(1)

try:
    from src.m07_chroma import get_or_create_project_collection, add_chunks_to_collection
    print("✓ m07_chroma imports OK")
except Exception as e:
    print(f"✗ m07_chroma import failed: {e}")
    exit(1)

print("\n✓ All imports successful!")
