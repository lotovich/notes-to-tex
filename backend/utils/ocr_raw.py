#!/usr/bin/env python3
"""
OCR baseline creation module for verbatim accuracy validation.

Creates ocr_raw.txt containing unprocessed text extraction from input files:
- PDF files: uses PyMuPDF (fitz) for direct text extraction
- Image files: uses Gemini with strict verbatim transcription prompt
"""

from pathlib import Path
import fitz  # PyMuPDF
from google import genai
from google.genai import types as genai_types
import os
from dotenv import load_dotenv

load_dotenv()


def make_ocr_baseline(input_path: Path, job_dir: Path) -> str:
    """
    Create baseline OCR text from input file.

    Args:
        input_path: Path to uploaded file (PDF/JPG/PNG)
        job_dir: Job directory for saving intermediate files

    Returns:
        String with recognized text (verbatim transcription)
    """
    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf_text(input_path)
    elif suffix in [".jpg", ".jpeg", ".png"]:
        return _extract_image_text(input_path)
    else:
        return f"# Unsupported file format: {suffix}"


def _extract_pdf_text(pdf_path: Path) -> str:
    """Extract text from PDF using PyMuPDF."""
    text_blocks = []

    try:
        with fitz.open(pdf_path) as doc:
            for page_num, page in enumerate(doc, start=1):
                # Extract text from page
                page_text = page.get_text("text")
                if page_text and page_text.strip():
                    text_blocks.append(f"--- Page {page_num} ---\n{page_text}")

        if text_blocks:
            return "\n\n".join(text_blocks)
        else:
            return "# No text extracted from PDF"

    except Exception as e:
        return f"# Error extracting PDF text: {str(e)}"


def _extract_image_text(image_path: Path) -> str:
    """Extract text from image using Gemini with verbatim prompt."""
    try:
        # Initialize Gemini client
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "# Error: GEMINI_API_KEY not set"

        client = genai.Client(api_key=api_key)

        # Verbatim prompt (strict transcription)
        system_prompt = (
            "You are a precise transcriber. "
            "Transcribe EXACTLY what you see in the image. "
            "Do not paraphrase, translate, or interpret. "
            "Preserve original words, punctuation, and line breaks. "
            "If text is unclear, write [[illegible]] instead of guessing."
        )

        # Read image
        image_bytes = image_path.read_bytes()
        mime_type = "image/jpeg" if image_path.suffix.lower() in [".jpg", ".jpeg"] else "image/png"

        # Form request
        parts = [
            genai_types.Part.from_text(text=system_prompt),
            genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        ]

        config = genai_types.GenerateContentConfig(
            temperature=0.0,  # minimal creativity
            max_output_tokens=8192
        )

        # Call model
        response = client.models.generate_content(
            model="gemini-2.5-pro",  # use current model
            contents=[genai_types.Content(role="user", parts=parts)],
            config=config
        )

        raw_text = getattr(response, "text", "") or ""
        return raw_text.strip() if raw_text else "# No text recognized"

    except Exception as e:
        return f"# Error during image OCR: {str(e)}"