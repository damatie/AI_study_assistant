# app/services/material_processing_service/handle_material_processing.py

import io
import logging
from typing import Tuple, Optional
import PyPDF2

from app.core.genai_client import (
    DEFAULT_MULTIMODAL_GENERATION_CONFIG,
    FALLBACK_TEXT_GENERATION_CONFIG,
    get_gemini_model,
)
from app.utils.stepsjson import (
    sanitize_all_blocks,
    filter_trivial_blocks,
    unwrap_non_code_fences,
    strip_scanned_table_artifacts,
)

logger = logging.getLogger(__name__)

def get_pdf_page_count_from_bytes(pdf_bytes: bytes) -> int:
    """Return number of pages from PDF bytes."""
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        return len(reader.pages)
    except Exception:
        logger.exception("Failed to read PDF page count from bytes")
        raise ValueError("Could not read PDF page count from bytes")


def _extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Best-effort text extraction from PDF bytes using PyPDF2.

    Returns a string (may be empty) with pages joined by two newlines.
    """
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        texts = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
                texts.append(t.strip())
            except Exception:
                # continue on extract failures per-page
                continue
        return "\n\n".join([t for t in texts if t])
    except Exception:
        logger.exception("Failed to extract text from PDF bytes")
        return ""

# Function to handle non-PDF image files
async def process_image_via_gemini(image_path: str, mode: str = "overview", title: Optional[str] = None) -> Tuple[str, str]:
    """Generate overview markdown for image-based materials."""
    try:
        if (mode or "overview").lower() != "overview":
            raise ValueError("Detailed notes are generated via notes_service. Use overview mode here.")

        # Read image file
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        # Generate markdown directly from image (single step)
        model = get_gemini_model()
        exact_title = (title or "Overview").strip()
        markdown_prompt = (
            "You will generate a VERY SHORT overview for the provided image content.\n"
            "\n"
            "STRICT OUTPUT RULES (MANDATORY):\n"
            f"- First line must be an H1 with the exact title provided: '# {exact_title}'. Do not alter it.\n"
            "- Follow with ONE short paragraph (60–120 words) summarizing purpose, scope, and key ideas.\n"
            "- If mathematical content is present, include key formula(s) in proper LaTeX using $$...$$.\n"
            "- No other headings, lists, tables, images, or code blocks. Paragraph only.\n"
            "- Do NOT include page counts, citations, or links.\n"
            "\n"
            "Return ONLY the markdown described above."
        )
        markdown_response = await model.generate_content_async(
            [
                markdown_prompt,
                {"mime_type": "image/jpeg", "data": image_bytes},
            ],
            generation_config=DEFAULT_MULTIMODAL_GENERATION_CONFIG.copy(),
        )

        markdown_content = sanitize_all_blocks(markdown_response.text)
        markdown_content = unwrap_non_code_fences(markdown_content)
        markdown_content = strip_scanned_table_artifacts(markdown_content)
        markdown_content = filter_trivial_blocks(markdown_content)
        # Use logger instead of print to avoid Windows pipe issues in background tasks
        logger.info(
            f"Generated markdown length: {len(markdown_content)} characters"
        )

        # Return empty string for raw_text since we're doing direct processing
        return "", markdown_content

    except Exception as e:
        logger.exception(f"Failed to process image via Gemini: {str(e)}")
        # Return empty results in case of failure
        return "", f"# Processing Failed\n\nError: {str(e)}"


# Function to handle PDF files
async def process_pdf_via_gemini(pdf_path: str, mode: str = "overview", title: Optional[str] = None) -> Tuple[str, str, int]:
    """
    Process a PDF file directly to markdown through Gemini API.

    Args:
        pdf_path: Path to the PDF file
        mode: "overview" for concise overview, "detailed" for full study guide

    Returns:
        raw_text: Empty string (no longer extracted separately)
        markdown_content: Direct markdown analysis of the PDF content
        page_count: Number of pages in the PDF
    """
    try:
        # Read PDF file as bytes
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        # Get page count from bytes (more efficient - single read)
        page_count = get_pdf_page_count_from_bytes(pdf_bytes)

        # Use logger instead of print to avoid Windows pipe issues in background tasks
        logger.info(
            f"Processing PDF with {page_count} pages directly to markdown via Gemini..."
        )

        # Generate markdown directly from PDF (single step)
        if (mode or "overview").lower() != "overview":
            raise ValueError("Detailed notes are generated via notes_service. Use overview mode here.")

        model = get_gemini_model()
        exact_title = (title or "Overview").strip()
        markdown_prompt = (
            f"Source material: ~{page_count} pages.\n"
            "You will generate a VERY SHORT overview for the provided PDF.\n"
            "\n"
            "STRICT OUTPUT RULES (MANDATORY):\n"
            f"- First line must be an H1 with the exact title provided: '# {exact_title}'. Do not alter it.\n"
            "- Follow with ONE short paragraph (60–120 words) summarizing purpose, scope, key concepts, and main results.\n"
            "- If mathematical content appears, include key formula(s) in proper LaTeX using $$...$$.\n"
            "- No other headings, lists, tables, images, or code blocks. Paragraph only.\n"
            "- Do NOT include page counts, citations, or links.\n"
            "\n"
            "Return ONLY the markdown described above."
        )
        try:
            markdown_response = await model.generate_content_async(
                [
                    markdown_prompt,
                    {"mime_type": "application/pdf", "data": pdf_bytes},
                ],
                generation_config=DEFAULT_MULTIMODAL_GENERATION_CONFIG.copy(),
            )

            markdown_content = sanitize_all_blocks(markdown_response.text)
            markdown_content = unwrap_non_code_fences(markdown_content)
            markdown_content = strip_scanned_table_artifacts(markdown_content)
            markdown_content = filter_trivial_blocks(markdown_content)
            # Use logger instead of print to avoid Windows pipe issues in background tasks
            logger.info(
                f"Generated markdown length: {len(markdown_content)} characters"
            )

            # Return empty string for raw_text since we're doing direct processing
            return "", markdown_content, page_count
        except Exception as primary_error:
            # Windows pipe or multimodal upload path failed: fallback to text-only summarization
            logger.warning(
                f"PDF multimodal path failed ({primary_error}). Falling back to text-only summarization."
            )
            text = _extract_text_from_pdf_bytes(pdf_bytes)
            if not text:
                raise
            # Trim very large texts to keep token usage bounded
            if len(text) > 200_000:
                text = text[:200_000]
            # Choose matching fallback prompt
            exact_title = (title or "Overview").strip()
            base_prompt = (
                f"Source material: ~{page_count} pages.\n"
                "You will generate a VERY SHORT overview from the extracted text below.\n"
                "\n"
                "STRICT OUTPUT RULES (MANDATORY):\n"
                f"- First line must be an H1 with the exact title provided: '# {exact_title}'. Do not alter it.\n"
                "- Follow with ONE short paragraph (60–120 words) summarizing purpose, scope, key concepts, and main results.\n"
                "- If mathematical content appears, include key formula(s) in proper LaTeX using $$...$$.\n"
                "- No other headings, lists, tables, images, or code blocks. Paragraph only.\n"
                "- Do NOT include page counts, citations, or links.\n"
                "\n"
                "Return ONLY the markdown described above.\n"
            )
            fallback_prompt = base_prompt + "\n\n[BEGIN EXTRACTED TEXT]\n" + text + "\n[END EXTRACTED TEXT]"
            markdown_response = await model.generate_content_async(
                fallback_prompt,
                generation_config=FALLBACK_TEXT_GENERATION_CONFIG.copy(),
            )
            markdown_content = sanitize_all_blocks(markdown_response.text)
            markdown_content = unwrap_non_code_fences(markdown_content)
            markdown_content = strip_scanned_table_artifacts(markdown_content)
            markdown_content = filter_trivial_blocks(markdown_content)
            logger.info(
                f"Generated markdown (fallback) length: {len(markdown_content)} characters"
            )
            return "", markdown_content, page_count

    except Exception as e:
        logger.exception(f"Failed to process PDF directly via Gemini: {str(e)}")
        # Return empty results in case of failure
        return "", f"# Processing Failed\n\nError: {str(e)}", 0
