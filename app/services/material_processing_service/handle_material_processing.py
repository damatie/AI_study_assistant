# app/services/material_processing_service/handle_material_processing.py

import io
import logging
import os
import tempfile
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
from app.services.material_processing_service.office_documents import (
    get_office_page_count,
    extract_docx_text,
)
from app.services.document_conversion.gotenberg_client import (
    convert_office_document_to_pdf,
    GotenbergConversionError,
    GotenbergNotConfigured,
)
from app.services.material_processing_service.gemini_files import SUPPORTED_FILE_MIME_TYPES

logger = logging.getLogger(__name__)


def _normalize_markdown(raw_text: str) -> str:
    """Return sanitized markdown string for overview outputs."""

    markdown_content = sanitize_all_blocks(raw_text)
    markdown_content = unwrap_non_code_fences(markdown_content)
    markdown_content = strip_scanned_table_artifacts(markdown_content)
    markdown_content = filter_trivial_blocks(markdown_content)
    return markdown_content


def _build_overview_prompt(title: Optional[str], page_count: Optional[int]) -> str:
    exact_title = (title or "Overview").strip()
    page_fragment = f"Source material: ~{page_count} pages.\n" if page_count else ""
    return (
        f"{page_fragment}You will generate a VERY SHORT overview for the provided document.\n"
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

        ext = os.path.splitext(image_path or "")[1].lower()
        mime_type = SUPPORTED_FILE_MIME_TYPES.get(ext, "image/jpeg")

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
                {"mime_type": mime_type, "data": image_bytes},
            ],
            generation_config=DEFAULT_MULTIMODAL_GENERATION_CONFIG.copy(),
        )

        markdown_content = _normalize_markdown(markdown_response.text)
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
        markdown_prompt = _build_overview_prompt(title, page_count)
        mime_type = SUPPORTED_FILE_MIME_TYPES.get(".pdf", "application/pdf")
        try:
            markdown_response = await model.generate_content_async(
                [
                    markdown_prompt,
                    {"mime_type": mime_type, "data": pdf_bytes},
                ],
                generation_config=DEFAULT_MULTIMODAL_GENERATION_CONFIG.copy(),
            )

            markdown_content = _normalize_markdown(markdown_response.text)
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
            base_prompt = _build_overview_prompt(title, page_count)
            fallback_prompt = (
                base_prompt
                + "\n\nUse ONLY the extracted text below:\n\n[BEGIN EXTRACTED TEXT]\n"
                + text
                + "\n[END EXTRACTED TEXT]"
            )
            markdown_response = await model.generate_content_async(
                fallback_prompt,
                generation_config=FALLBACK_TEXT_GENERATION_CONFIG.copy(),
            )
            markdown_content = _normalize_markdown(markdown_response.text)
            logger.info(
                f"Generated markdown (fallback) length: {len(markdown_content)} characters"
            )
            return "", markdown_content, page_count

    except Exception as e:
        logger.exception(f"Failed to process PDF directly via Gemini: {str(e)}")
        # Return empty results in case of failure
        return "", f"# Processing Failed\n\nError: {str(e)}", 0


async def process_office_doc_via_gemini(
    doc_path: str,
    mode: str = "overview",
    title: Optional[str] = None,
) -> Tuple[str, str, int]:
    """Process DOC or DOCX files using Gemini."""

    ext = os.path.splitext(doc_path or "")[1].lower()
    if ext not in {".doc", ".docx"}:
        raise ValueError(f"Unsupported Office document type: {ext}")

    try:
        with open(doc_path, "rb") as f:
            doc_bytes = f.read()

        page_count = get_office_page_count(doc_bytes, ext)
        logger.info(
            "Processing Office document (%s) with ~%s pages via Gemini",
            ext,
            page_count,
        )

        if (mode or "overview").lower() != "overview":
            raise ValueError("Detailed notes are generated via notes_service. Use overview mode here.")

        try:
            conversion = await convert_office_document_to_pdf(
                document_bytes=doc_bytes,
                filename=os.path.basename(doc_path) or f"document{ext}",
            )
            converted_page_count: Optional[int]
            try:
                converted_page_count = get_pdf_page_count_from_bytes(conversion.content)
            except Exception:
                logger.warning("Failed to derive page count from converted PDF; falling back to estimate.")
                converted_page_count = None

            tmp_pdf_path = None
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
                tmp_pdf.write(conversion.content)
                tmp_pdf_path = tmp_pdf.name
            try:
                _, markdown_content, pdf_page_count = await process_pdf_via_gemini(
                    tmp_pdf_path,
                    mode=mode,
                    title=title,
                )
            finally:
                if tmp_pdf_path:
                    try:
                        os.unlink(tmp_pdf_path)
                    except OSError:
                        pass

            effective_pages = pdf_page_count or converted_page_count or page_count
            return "", markdown_content, effective_pages
        except GotenbergNotConfigured:
            logger.info("Gotenberg is not configured; using direct Office processing path.")
        except GotenbergConversionError as conversion_error:
            logger.warning("Gotenberg conversion failed (%s); using direct Office processing path.", conversion_error)

        model = get_gemini_model()
        prompt = _build_overview_prompt(title, page_count)
        mime_type = SUPPORTED_FILE_MIME_TYPES.get(ext, "application/octet-stream")

        try:
            response = await model.generate_content_async(
                [
                    prompt,
                    {"mime_type": mime_type, "data": doc_bytes},
                ],
                generation_config=DEFAULT_MULTIMODAL_GENERATION_CONFIG.copy(),
            )
            markdown_content = _normalize_markdown(response.text)
            logger.info(
                "Generated markdown length: %s characters",
                len(markdown_content),
            )
            return "", markdown_content, page_count
        except Exception as primary_error:
            logger.warning(
                "Office multimodal path failed (%s); attempting fallback",
                primary_error,
            )
            if ext == ".docx":
                plain_text = extract_docx_text(doc_bytes)
                if plain_text:
                    fallback_prompt = (
                        prompt
                        + "\n\nUse ONLY the extracted text below:\n\n[BEGIN EXTRACTED TEXT]\n"
                        + plain_text
                        + "\n[END EXTRACTED TEXT]"
                    )
                    fallback_response = await model.generate_content_async(
                        fallback_prompt,
                        generation_config=FALLBACK_TEXT_GENERATION_CONFIG.copy(),
                    )
                    markdown_content = _normalize_markdown(fallback_response.text)
                    logger.info(
                        "Generated markdown length (fallback): %s characters",
                        len(markdown_content),
                    )
                    return "", markdown_content, page_count

        logger.error("Unable to process Office document via Gemini")
        return "", "# Processing Failed\n\nAn error occurred while processing this document.", page_count

    except Exception as e:  # noqa: BLE001
        logger.exception(f"Failed to process Office document via Gemini: {e}")
        return "", f"# Processing Failed\n\nError: {str(e)}", 0

