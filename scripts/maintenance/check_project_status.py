
import json
from sqlmodel import select
from src.m03_db import get_session, Project, Role, Context, Task, Document, ProjectDocumentLink, DocumentChunk

def check_project(search_term):
    with get_session() as session:
        # 1. Find Project
        projects = session.exec(select(Project)).all()
        target_project = None
        for p in projects:
            if search_term.lower() in (p.title or "").lower():
                target_project = p
                break
        
        if not target_project:
            print(f"❌ Projekt mit Suchbegriff '{search_term}' nicht gefunden.")
            return

        print(f"✅ Projekt gefunden: {target_project.title} (Key: {target_project.key})")
        print(f"   ID: {target_project.id}")
        print("-" * 50)

        # 2. Check Associations
        # Roles
        role_keys = []
        if target_project.role_keys:
            try:
                role_keys = json.loads(target_project.role_keys)
            except:
                print(f"   ⚠️ Fehler beim Parsen von role_keys: {target_project.role_keys}")
        
        print(f"\n👤 Zugeordnete Rollen ({len(role_keys)}):")
        for rk in role_keys:
            role = session.exec(select(Role).where(Role.key == rk)).first()
            if role:
                has_emb = "✅" if role.embedding else "❌"
                print(f"   - {role.title} ({rk}) [Embedding: {has_emb}]")
            else:
                print(f"   - {rk} (Nicht in DB gefunden)")

        # Contexts
        context_keys = []
        if target_project.context_keys:
            try:
                context_keys = json.loads(target_project.context_keys)
            except:
                print(f"   ⚠️ Fehler beim Parsen von context_keys: {target_project.context_keys}")

        print(f"\n📚 Zugeordnete Kontexte ({len(context_keys)}):")
        for ck in context_keys:
            ctx = session.exec(select(Context).where(Context.key == ck)).first()
            if ctx:
                has_emb = "✅" if ctx.embedding else "❌"
                print(f"   - {ctx.title} ({ck}) [Embedding: {has_emb}]")
            else:
                print(f"   - {ck} (Nicht in DB gefunden)")

        # Tasks (using task_keys JSON or legacy task_key)
        task_keys = []
        if target_project.task_keys:
            try:
                task_keys = json.loads(target_project.task_keys)
            except:
                pass
        
        # Fallback legacy
        if not task_keys and target_project.task_key:
             task_keys = [target_project.task_key]

        print(f"\n📋 Zugeordnete Aufgaben ({len(task_keys)}):")
        for tk in task_keys:
            task = session.exec(select(Task).where(Task.key == tk)).first()
            if task:
                has_emb = "✅" if task.embedding else "❌"
                print(f"   - {task.title} ({tk}) [Embedding: {has_emb}]")
            else:
                print(f"   - {tk} (Nicht in DB gefunden)")

        # Documents
        links = session.exec(select(ProjectDocumentLink).where(ProjectDocumentLink.project_key == target_project.key)).all()
        print(f"\n📄 Zugeordnete Dokumente ({len(links)}):")
        for link in links:
            doc = session.get(Document, link.document_id)
            if doc:
                # Check chunks
                chunks = session.exec(select(DocumentChunk).where(DocumentChunk.document_id == doc.id)).all()
                embedded_chunks = sum(1 for c in chunks if c.embedding)
                print(f"   - {doc.filename} (ID: {doc.id})")
                print(f"     Chunks: {len(chunks)} | Embedded: {embedded_chunks}/{len(chunks)}")
            else:
                print(f"   - Doc ID {link.document_id} (Nicht gefunden)")

        # 3. Project Embedding
        print("-" * 50)
        has_proj_emb = "✅" if target_project.embedding else "❌"
        print(f"🗂️ Projekt-Embedding vorhanden: {has_proj_emb}")
        
        # 4. Simulation: Was würde indexiert werden?
        print("-" * 50)
        print("🔍 Simulation des Index-Textes (Was sieht die KI?):")
        
        text_parts = [
            f"Titel: {target_project.title}",
            f"Beschreibung: {target_project.description or ''}"
        ]
        
        # Rollen simulieren
        if role_keys:
            print(f"   + Füge {len(role_keys)} Rollen hinzu...")
            for rk in role_keys:
                role = session.exec(select(Role).where(Role.key == rk)).first()
                if role:
                    text_parts.append(f"Rolle: {role.title}")
                    if role.description:
                        text_parts.append(f"   ({role.description})")
        
        # Kontexte simulieren
        if context_keys:
            print(f"   + Füge {len(context_keys)} Kontexte hinzu...")
            for ck in context_keys:
                ctx = session.exec(select(Context).where(Context.key == ck)).first()
                if ctx:
                    text_parts.append(f"Kontext: {ctx.title}")
                    if ctx.description:
                        text_parts.append(f"   ({ctx.description})")

        print("\n--- Generierter Text-Block (Auszug) ---")
        full_text = "\n".join(text_parts)
        print(full_text[:500] + "..." if len(full_text) > 500 else full_text)
        print("---------------------------------------")

if __name__ == "__main__":
    check_project("Entwicklung einer modularen Verwaltungssoftware")
