with open('app/pages/07_Chat.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the line with st.success("✅ Nachricht gespeichert") and insert before it
for i, line in enumerate(lines):
    if 'st.success("✅ Nachricht gespeichert")' in line:
        indent = '                            '
        lines.insert(i, indent + 'st.session_state["chat_input_field"] = ""\n')
        break

with open('app/pages/07_Chat.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('✅ Updated 07_Chat.py - Added input field clearing')
