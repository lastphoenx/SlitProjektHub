from src.m08_llm import generate_project_details

result = generate_project_details('none', 'test description')
print(f'Rückgabewerte: {len(result)}')
print(f'Werte: {result}')
if len(result) == 5:
    title, short_code, short_title, desc, briefing = result
    print(f'✓ Korrekt entpackt: title={title}, short_code={short_code}, short_title={short_title}')
else:
    print(f'✗ Fehler: Expected 5, got {len(result)}')
