"""Helper functions for role markdown file composition and extraction."""


def compose_role_markdown(prose_intro: str, responsibilities: str, qualifications: str, expertise: str) -> str:
    """Compose role markdown document.
    
    Strategy:
    - If prose exists: ONLY save prose (structured sections shown in UI via DB fields)
    - If no prose: Save structured sections in markdown (for backward compatibility)
    
    Args:
        prose_intro: 2-3 paragraphs prose introduction
        responsibilities: Bulletpoint list (markdown)
        qualifications: Bulletpoint list (markdown)
        expertise: Bulletpoint list (markdown)
        
    Returns:
        Complete markdown document string
    """
    # If we have prose, ONLY return the prose (avoid duplication with UI display)
    if prose_intro and prose_intro.strip():
        return prose_intro.strip()
    
    # No prose: fallback to structured sections only (for roles without prose)
    parts = []
    if responsibilities and responsibilities.strip():
        parts.append("## Hauptverantwortlichkeiten")
        parts.append("")
        parts.append(responsibilities.strip())
        parts.append("")
    
    if qualifications and qualifications.strip():
        parts.append("## Qualifikationen")
        parts.append("")
        parts.append(qualifications.strip())
        parts.append("")
    
    if expertise and expertise.strip():
        parts.append("## Fachexpertise")
        parts.append("")
        parts.append(expertise.strip())
    
    return "\n".join(parts)


def extract_prose_intro(markdown_content: str) -> str:
    """Extract prose introduction from role markdown (text before first ## header).
    
    Args:
        markdown_content: Full markdown document content
        
    Returns:
        Prose introduction text (empty string if none found)
    """
    if not markdown_content or not markdown_content.strip():
        return ""
    
    lines = markdown_content.splitlines()
    prose_lines = []
    
    for line in lines:
        # Stop at first H2 header
        if line.startswith("## "):
            break
        prose_lines.append(line)
    
    return "\n".join(prose_lines).strip()
