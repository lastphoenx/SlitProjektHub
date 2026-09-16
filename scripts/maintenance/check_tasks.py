
from sqlmodel import select
from src.m03_db import get_session, Task

def count_tasks():
    with get_session() as session:
        # Count all tasks for role 'leiter-cash'
        tasks = session.exec(select(Task).where(Task.source_role_key == "leiter-cash")).all()
        total = len(tasks)
        active = len([t for t in tasks if not t.is_deleted])
        deleted = len([t for t in tasks if t.is_deleted])
        embedded = len([t for t in tasks if t.embedding])
        
        print(f"Tasks for 'leiter-cash':")
        print(f"Total: {total}")
        print(f"Active: {active}")
        print(f"Deleted: {deleted}")
        print(f"With Embedding: {embedded}")
        
        print("\nTask List (Active):")
        for t in tasks:
            if not t.is_deleted:
                print(f"- {t.title} (Key: {t.key})")

if __name__ == "__main__":
    count_tasks()
