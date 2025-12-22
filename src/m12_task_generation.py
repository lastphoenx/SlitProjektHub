"""Task generation from roles using LLM."""
from typing import Optional
import uuid
import re


def generate_tasks_from_role(
    provider: str,
    role_title: str,
    role_key: str,
    responsibilities: str,
    min_per_resp: int = 3,
    max_per_resp: int = 7
) -> list[dict]:
    """Generate tasks from a role's responsibilities.
    
    Args:
        provider: LLM provider (openai, anthropic, mistral)
        role_title: Title of the role
        role_key: Key of the role (for source tracking)
        responsibilities: Bulletpoint list of responsibilities
        min_per_resp: Minimum tasks per responsibility
        max_per_resp: Maximum tasks per responsibility
        
    Returns:
        List of task dicts with:
        - responsibility: The source responsibility text
        - title: Task title
        - short_code: Task abbreviation
        - description: Task description
        - source_role_key: Original role key
        - source_responsibility: Responsibility text
        - generation_batch_id: UUID for batch operations
    """
    if not responsibilities or not responsibilities.strip():
        return []
    
    # Parse responsibilities into individual items
    resp_lines = [line.strip() for line in responsibilities.split('\n') if line.strip().startswith('- ')]
    resp_items = [line[2:].strip() for line in resp_lines]  # Remove '- ' prefix
    
    if not resp_items:
        return []
    
    # Generate batch ID for this generation run
    batch_id = str(uuid.uuid4())
    
    # Build prompt
    prompt = f"""Du bist ein Experte für Aufgaben-Dekomposition im Unternehmenskontext.

ROLLE: {role_title}

VERANTWORTLICHKEITEN:
{chr(10).join(f'{i+1}. {r}' for i, r in enumerate(resp_items))}

AUFGABE:
Generiere für JEDE der {len(resp_items)} Verantwortlichkeiten zwischen {min_per_resp} und {max_per_resp} konkrete, operationale Aufgaben.

ANFORDERUNGEN:
- Aufgaben müssen spezifisch und umsetzbar sein
- Keine Duplikate
- Klare Abgrenzung zwischen Aufgaben
- Titel: Max 80 Zeichen, präzise und aussagekräftig
- Kürzel: Max 14 Zeichen, eindeutig (z.B. "LIQ-PLAN", "ZAHLV-CAMT")
- Beschreibung: 2-3 Sätze: Was ist die Aufgabe? Warum ist sie wichtig?

AUSGABE-FORMAT:
Für jede Verantwortlichkeit:

VERANTWORTLICHKEIT: [Nummer]

AUFGABE:
TITEL: (Aufgaben-Titel)
KÜRZEL: (Kurzes Kürzel)
BESCHREIBUNG: (2-3 Sätze)

AUFGABE:
TITEL: (Nächste Aufgabe)
KÜRZEL: (Kürzel)
BESCHREIBUNG: (Beschreibung)

[Weitere Aufgaben...]

---

VERANTWORTLICHKEIT: [Nächste Nummer]
[Aufgaben...]

Wichtig: Halte dich strikt an das Format mit VERANTWORTLICHKEIT:, AUFGABE:, TITEL:, KÜRZEL:, BESCHREIBUNG:
"""
    
    try:
        # Get LLM client based on provider
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI()
        elif provider == "anthropic":
            from anthropic import Anthropic
            client = Anthropic()
        elif provider == "mistral":
            from mistralai import Mistral
            client = Mistral()
        else:
            raise ValueError(f"Unknown provider: {provider}")
        
        # Call LLM
        model = _get_model_for_provider(provider)
        
        if provider == "openai":
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Du bist ein Experte für organisatorische Aufgabenstrukturierung."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=4000
            )
            raw_text = response.choices[0].message.content
        elif provider == "anthropic":
            response = client.messages.create(
                model=model,
                max_tokens=4000,
                temperature=0.7,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            raw_text = response.content[0].text
        elif provider == "mistral":
            response = client.chat.complete(
                model=model,
                messages=[
                    {"role": "system", "content": "Du bist ein Experte für organisatorische Aufgabenstrukturierung."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=4000
            )
            raw_text = response.choices[0].message.content
        
        # Parse response
        tasks = _parse_task_generation_response(raw_text, resp_items, role_key, batch_id)
        
        return tasks
        
    except Exception as e:
        # Fallback: Create minimal tasks
        print(f"Error in LLM call: {e}")
        import traceback
        traceback.print_exc()
        return _create_fallback_tasks(resp_items, role_key, batch_id)


def _get_model_for_provider(provider: str) -> str:
    """Get model name for provider."""
    models = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-sonnet-20241022",
        "mistral": "mistral-large-latest"
    }
    return models.get(provider, "gpt-4o-mini")


def _parse_task_generation_response(
    text: str,
    responsibilities: list[str],
    role_key: str,
    batch_id: str
) -> list[dict]:
    """Parse LLM response into task dictionaries."""
    tasks = []
    
    # Split by VERANTWORTLICHKEIT blocks
    blocks = re.split(r'VERANTWORTLICHKEIT:\s*(\d+)', text, flags=re.IGNORECASE)
    
    # blocks[0] is text before first match, then alternating: number, content, number, content...
    for i in range(1, len(blocks), 2):
        if i + 1 >= len(blocks):
            break
            
        resp_num = int(blocks[i]) - 1  # Convert to 0-based index
        block_content = blocks[i + 1]
        
        # Get responsibility text
        if 0 <= resp_num < len(responsibilities):
            resp_text = responsibilities[resp_num]
        else:
            continue
        
        # Find all AUFGABE blocks in this responsibility
        aufgabe_blocks = re.split(r'AUFGABE:', block_content, flags=re.IGNORECASE)[1:]  # Skip first empty part
        
        for aufgabe_text in aufgabe_blocks:
            # Extract TITEL, KÜRZEL, BESCHREIBUNG
            title_match = re.search(r'TITEL:\s*(.+?)(?=\n|KÜRZEL:|BESCHREIBUNG:|$)', aufgabe_text, re.IGNORECASE | re.DOTALL)
            code_match = re.search(r'KÜRZEL:\s*(.+?)(?=\n|BESCHREIBUNG:|$)', aufgabe_text, re.IGNORECASE | re.DOTALL)
            desc_match = re.search(r'BESCHREIBUNG:\s*(.+?)(?=\n\n|$)', aufgabe_text, re.IGNORECASE | re.DOTALL)
            
            title = title_match.group(1).strip() if title_match else "Aufgabe"
            short_code = code_match.group(1).strip()[:14] if code_match else ""
            description = desc_match.group(1).strip() if desc_match else ""
            
            # Clean up
            title = title.replace('\n', ' ').strip()
            short_code = short_code.replace('\n', ' ').strip()
            description = description.strip()
            
            tasks.append({
                "responsibility": resp_text,
                "title": title,
                "short_code": short_code,
                "description": description,
                "source_role_key": role_key,
                "source_responsibility": resp_text,
                "generation_batch_id": batch_id
            })
    
    return tasks


def _create_fallback_tasks(
    responsibilities: list[str],
    role_key: str,
    batch_id: str
) -> list[dict]:
    """Create minimal fallback tasks when LLM fails."""
    tasks = []
    
    for i, resp in enumerate(responsibilities, start=1):
        # Create 3 generic tasks per responsibility
        for j in range(1, 4):
            tasks.append({
                "responsibility": resp,
                "title": f"Aufgabe {j} für: {resp[:50]}",
                "short_code": f"T{i}-{j}",
                "description": f"Konkretisierung der Verantwortlichkeit: {resp}",
                "source_role_key": role_key,
                "source_responsibility": resp,
                "generation_batch_id": batch_id
            })
    
    return tasks
