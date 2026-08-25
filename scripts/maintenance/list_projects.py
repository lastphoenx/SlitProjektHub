from src.m03_db import get_session, Project
from sqlmodel import select

session = get_session()
try:
    projects = session.exec(select(Project).where(Project.is_deleted == False)).all()
    print(f"Projekte insgesamt: {len(projects)}\n")
    for p in projects:
        print(f"- {p.title}")
        print(f"  Key: {p.key}")
finally:
    session.close()
