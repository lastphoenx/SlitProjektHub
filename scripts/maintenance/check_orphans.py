
from sqlmodel import select
from src.m03_db import get_session, Task

def list_orphans():
    with get_session() as session:
        tasks = session.exec(select(Task).where(Task.source_role_key == None)).all()
        print("Orphan Tasks:")
        for t in tasks:
            print(f"- {t.title} (Key: {t.key})")

if __name__ == "__main__":
    list_orphans()
