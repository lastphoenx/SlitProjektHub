"""Vision API and OCR integration for document processing."""
from __future__ import annotations
import base64
import os
from pathlib import Path
from typing import Optional


def analyze_image_with_vision(image_path: str, provider: str, prompt: str = "Describe this image in detail. If it's a diagram or chart, explain the structure and relationships.") -> Optional[str]:
    """
    Analyzes an image using Vision API (OpenAI GPT-4V, Claude, or Mistral).
    
    Args:
        image_path: Path to image file
        provider: Provider name ("openai", "anthropic", "mistral")
        prompt: Custom prompt for analysis
    
    Returns:
        Analysis text or None on failure
    """
    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return None
    
    if provider == "openai":
        return _analyze_with_openai_vision(image_data, image_path, prompt)
    elif provider == "anthropic":
        return _analyze_with_claude_vision(image_data, image_path, prompt)
    elif provider == "mistral":
        return _analyze_with_mistral_vision(image_data, image_path, prompt)
    
    return None


def _analyze_with_openai_vision(image_data: str, image_path: str, prompt: str) -> Optional[str]:
    """Analyze image using OpenAI GPT-4V."""
    try:
        from openai import OpenAI
        
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        
        client = OpenAI(api_key=api_key)
        
        suffix = Path(image_path).suffix.lower()
        media_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp"
        }
        media_type = media_type_map.get(suffix, "image/jpeg")
        
        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1024
        )
        
        return response.choices[0].message.content
    except Exception:
        return None


def _analyze_with_claude_vision(image_data: str, image_path: str, prompt: str) -> Optional[str]:
    """Analyze image using Claude (Anthropic)."""
    try:
        from anthropic import Anthropic
        
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        
        client = Anthropic(api_key=api_key)
        
        suffix = Path(image_path).suffix.lower()
        media_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp"
        }
        media_type = media_type_map.get(suffix, "image/jpeg")
        
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )
        
        return response.content[0].text
    except Exception:
        return None


def _analyze_with_mistral_vision(image_data: str, image_path: str, prompt: str) -> Optional[str]:
    """Analyze image using Mistral vision."""
    try:
        from mistralai.client import MistralClient
        from mistralai.models.chat_message import ChatMessage
        
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            return None
        
        client = MistralClient(api_key=api_key)
        
        response = client.chat(
            model="pixtral-12b-2409",
            messages=[
                ChatMessage(
                    role="user",
                    content=[
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": f"data:image/jpeg;base64,{image_data}"
                        }
                    ]
                )
            ],
            max_tokens=1024
        )
        
        return response.choices[0].message.content
    except Exception:
        return None


def extract_images_from_pdf(pdf_path: str, output_dir: Optional[str] = None) -> list[str]:
    """
    Extracts images from PDF file.
    
    Args:
        pdf_path: Path to PDF file
        output_dir: Directory to save images. If None, uses temp directory
    
    Returns:
        List of extracted image paths
    """
    image_paths = []
    
    try:
        import pdf2image
        from PIL import Image
        import tempfile
        
        if output_dir is None:
            output_dir = tempfile.gettempdir()
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        images = pdf2image.convert_from_path(pdf_path, dpi=150)
        
        for idx, image in enumerate(images):
            img_path = output_dir / f"pdf_page_{idx:03d}.png"
            image.save(str(img_path), "PNG")
            image_paths.append(str(img_path))
        
        return image_paths
    except Exception:
        return []


def apply_ocr_to_pdf(pdf_path: str, output_path: Optional[str] = None) -> Optional[str]:
    """
    Applies OCR to PDF (for scanned documents).
    Uses ocrmypdf to make scanned PDFs searchable.
    
    Args:
        pdf_path: Path to input PDF
        output_path: Path to output PDF. If None, overwrites input
    
    Returns:
        Path to OCR'd PDF or None on failure
    """
    try:
        import ocrmypdf
        
        if output_path is None:
            output_path = pdf_path
        
        ocrmypdf.ocr(
            pdf_path,
            output_path,
            language="deu+eng",
            force_ocr=False,
            remove_background=True,
            deskew=True
        )
        
        return output_path
    except ImportError:
        return None
    except Exception:
        return None


def is_pdf_scanned(pdf_path: str) -> bool:
    """
    Checks if PDF is scanned (image-based) or digital (text-based).
    
    Args:
        pdf_path: Path to PDF
    
    Returns:
        True if PDF is mostly scanned, False if text-based
    """
    try:
        import pdfplumber
        
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:3]:
                text = page.extract_text()
                if text and len(text.strip()) > 100:
                    return False
        return True
    except Exception:
        return False
