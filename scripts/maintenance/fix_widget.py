import re

file_path = "app/pages/04_Stammdaten.py"

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_pattern = r'''                    with col3:
                        project_types = \["", "IT", "Business", "Finance", "HR", "Operations", "Custom"\]
                        current_type = st\.session_state\.get\(f"project_edit_type_\{current_key\}", ""\)
                        type_index = project_types\.index\(current_type\) if current_type in project_types else 0
                        edit_type = st\.selectbox\(
                            "Projekt-Typ",
                            options=project_types,
                            index=type_index,
                            key=f"project_edit_type_\{current_key\}"
                        \)'''

new_text = '''                    with col3:
                        project_types = ["", "IT", "Business", "Finance", "HR", "Operations", "Custom"]
                        edit_type = st.selectbox(
                            "Projekt-Typ",
                            options=project_types,
                            key=f"project_edit_type_{current_key}"
                        )'''

content = re.sub(old_pattern, new_text, content)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ Fixed selectbox widget conflict")
