import sys
from pathlib import Path
from sqlmodel import select

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m03_db import get_session, Task
from src.m07_tasks import delete_task

NEEDLE = "neue Applikationen Programmieren".lower()


def main():
    with get_session() as ses:
        rows = ses.exec(select(Task)).all()

    matches = []
    for r in rows:
        title = (r.title or "").lower()
        if NEEDLE in title:
            matches.append((r.key, r.title))

    print(f"Gefunden: {len(matches)} Einträge mit Titel enthält '{NEEDLE}'")
    for k, t in matches:
        print("-", k, "|", t)

    print("\nLöschversuche über delete_task():")
    results = []
    for k, _ in matches:
        ok = delete_task(k)
        results.append((k, ok))
        print("-", k, ok)

    # Fallback: direkter Delete über Session, falls obiges False war
    fallback = [k for k, ok in results if not ok]
    if fallback:
        print("\nDirekter Delete-Fallback via Session für Keys:", fallback)
        with get_session() as ses:
            for k in fallback:
                obj = ses.exec(select(Task).where(Task.key == k)).first()
                if obj:
                    # versuche Markdown zu entfernen
                    try:
                        from pathlib import Path
                        if obj.text_path:
                            Path(obj.text_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    ses.delete(obj)
            ses.commit()
        with get_session() as ses:
            remaining = [k for k in fallback if ses.exec(select(Task).where(Task.key == k)).first() is not None]
        print("Verbleibend nach Fallback:", remaining)


if __name__ == "__main__":
    main()
