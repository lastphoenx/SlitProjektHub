# -*- coding: utf-8 -*-
"""Fix task management section in Lab Forms"""
import re

file_path = r"c:\Users\santinel\Documents\Apps\SlitProjektHub\app\pages\20_Lab_Forms.py"

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern 1: Fix save button to handle new tasks
pattern1 = r'(with col1:\s+if st\.button\(")[^"]*(" Speichern", key="task_save", type="primary"\):\s+try:\s+upsert_task\(\s+title=edit_title,\s+body_text=edit_body,\s+key=task_obj\.key,)'

replacement1 = r'\1\2\n                            try:\n                                if not edit_title.strip():\n                                    st.error("Titel darf nicht leer sein")\n                                else:\n                                    upsert_task(\n                                        title=edit_title,\n                                        body_text=edit_body,\n                                        key=task_obj.key if task_obj else None,'

content = re.sub(pattern1, replacement1, content)

# Pattern 2: Fix upsert_task parameters
content = re.sub(
    r'source_responsibility=task_obj\.source_responsibility,\s+generation_batch_id=task_obj\.generation_batch_id,\s+generated_at=task_obj\.generated_at\s+\)',
    'source_responsibility=task_obj.source_responsibility if task_obj else None,\n                                        generation_batch_id=task_obj.generation_batch_id if task_obj else None,\n                                        generated_at=task_obj.generated_at if task_obj else None\n                                    )',
    content
)

# Pattern 3: Fix delete button
content = re.sub(
    r'(with col2:\s+if st\.button\(")[^"]*(" Löschen", key="task_delete"\):)',
    r'\g<1>Loeschen\g<2>\n                            if not is_new and task_obj:\n                                ',
    content
)

# Pattern 4: Fix indent for delete_task call
content = re.sub(
    r'(with col2:.*?key="task_delete"\):\s+)(if delete_task)',
    r'\1\n                            if not is_new and task_obj:\n                                \2',
    content,
    flags=re.DOTALL
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("File fixed successfully")
