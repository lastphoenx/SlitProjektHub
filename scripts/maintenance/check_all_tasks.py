
from sqlmodel import select
from src.m03_db import get_session, Task

def check_all_tasks():
    with get_session() as session:
        tasks = session.exec(select(Task)).all()
        print(f"Total tasks in DB: {len(tasks)}")
        
        by_role = {}
        for t in tasks:
            r = t.source_role_key or "None"
            by_role[r] = by_role.get(r, 0) + 1
            
        print("Tasks by role:")
        for r, count in by_role.items():
            print(f"  {r}: {count}")

if __name__ == "__main__":
    check_all_tasks()
