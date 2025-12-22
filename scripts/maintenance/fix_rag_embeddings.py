from sqlmodel import select
from src.m03_db import get_session, Task, Project
from src.m09_rag import index_task, index_project

def fix_embeddings():
    print("🚀 Starte Embedding-Fix...")
    
    with get_session() as ses:
        # 1. Alle Tasks indexieren
        tasks = ses.exec(select(Task)).all()
        print(f"📋 Gefunden: {len(tasks)} Aufgaben.")
        
        for task in tasks:
            print(f"   - Indexiere Task: {task.title}...", end="")
            success = index_task(task.key, force=True)
            print(" ✅" if success else " ❌")
            
        # 2. Projekt neu indexieren (mit neuen Task-Infos)
        projects = ses.exec(select(Project)).all()
        print(f"\n🏗️ Gefunden: {len(projects)} Projekte.")
        
        for project in projects:
            print(f"   - Indexiere Projekt: {project.title}...", end="")
            success = index_project(project.key, force=True)
            print(" ✅" if success else " ❌")

    print("\n✨ Fertig!")

if __name__ == "__main__":
    fix_embeddings()
