#!/usr/bin/env python3
# scripts/start_backend.py - Backend-Server starten

import subprocess
import sys
from pathlib import Path

def main():
    # Backend-Verzeichnis
    backend_dir = Path(__file__).parent.parent / "backend"
    
    print("🚀 Starte FastAPI Backend...")
    print(f"📁 Verzeichnis: {backend_dir}")
    print("🌐 URL: http://localhost:8000")
    print("📖 Docs: http://localhost:8000/docs")
    print("=" * 50)
    
    try:
        # FastAPI mit uvicorn starten
        subprocess.run([
            sys.executable, "-m", "uvicorn", 
            "main:app", 
            "--host", "0.0.0.0", 
            "--port", "8000", 
            "--reload"
        ], cwd=backend_dir, check=True)
    except KeyboardInterrupt:
        print("\n🛑 Backend gestoppt")
    except subprocess.CalledProcessError as e:
        print(f"❌ Fehler beim Starten: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())