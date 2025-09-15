# app/services/material_processing_service/handle_material_processing.py

import io
import logging
from typing import Tuple, Optional
import PyPDF2

from app.core.genai_client import get_gemini_model
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



def _generate_educational_markdown_prompt(page_count: int | None = None, title_fallback: Optional[str] = None) -> str:
    """
    Generate an adaptive, student-friendly study guide prompt with visual process support.
    Emphasizes clarity, compression, and interactive learning elements.
    """
    pages = f"Source material: ~{page_count} pages.\n" if page_count else ""
    
    fallback_title = (title_fallback or "Overview").strip()

    return f"""{pages}## YOUR ROLE
You are an expert educator who transforms complex material into clear, well‑structured study notes that mirror the document’s own organization while explaining each part succinctly.

## CORE MISSION
Create **detailed study notes** that follow the material’s structure (sections, subsections) and clearly explain each part. Do not add outside information beyond what is present in the source.

## FUNDAMENTAL RULES

### Content Integrity
- Use ONLY information present in the provided material. Do not introduce external facts, history, or anecdotes.
- **Never invent** facts, figures, formulas, examples, or steps; if uncertain → omit it.
- Target ≤50% of original length while keeping substance; remove redundancy.
- Every claim must be traceable to source material.

### Mathematical Content
- Use LaTeX formatting: $$formula$$ for all mathematical expressions.
- Include formulas only when central to understanding and show them exactly with $$...$$.
- Add brief intuitive explanation after each formula.
- Variables in running text: use $$x$$, $$y$$ (math notation), never `x` in code blocks or plain text.

### Text Formatting Guidelines
- TITLE: First line must be an H1 with the document’s exact original title as it appears in the material. Do not paraphrase. If the exact title cannot be determined, use: "# {fallback_title}".
- STRUCTURE: Mirror the document’s section hierarchy using markdown headings (##, ###) that correspond to the source sections. Do not invent new sections.
- **Regular text is plain paragraphs** — never place non-code content inside code fences.
- Only use ``` code blocks for real programming code present in the material.
- Mathematical variables: always use $$...$$ notation.
- Keep paragraphs concise (≤4 lines) and use bullets for dense lists from the source.

## INTERACTIVE PROCESS VISUALIZATION

### When to Use stepsjson Format
Create a stepsjson block when the source material explicitly describes:
- **Sequential procedures** (like solving equations, algorithms, experimental methods)
- **Multi-step processes** with clear order (≥3 concrete action steps)
- **Workflows** with decision points or branching
- **Problem-solving methods** with distinct phases

### stepsjson Format Specification
```stepsjson
{{
  "version": 1,
  "title": "Process Name (≤40 chars)",
  "steps": [
    {{"id": "A", "text": "First concrete action", "next": ["B"]}},
    {{"id": "B", "text": "Second action step", "next": ["C", "D"]}},
    {{"id": "C", "text": "Primary outcome path"}},
    {{"id": "D", "text": "Alternative path"}}
  ]
}}
```

### stepsjson Rules
- **Use for:** Procedures, algorithms, problem-solving steps, experimental protocols
- **Don't use for:** Static lists, definitions, features, concepts without action
- **Requirements:**
  - 3-25 steps maximum
  - id: 1-6 characters (A, B, C1, D2) - must be unique
  - text: ≤60 characters, active voice, no trailing period
  - next: array of existing ids; omit if terminal step
- **Language:** Use clear action verbs (Calculate, Apply, Simplify, Check)
- **Maximum:** 5 stepsjson blocks per document

### Good stepsjson Examples
✅ Mathematical procedures: "Solving Quadratic Equations", "Finding LCM"
✅ Scientific methods: "DNA Extraction Process", "Titration Procedure"
✅ Problem-solving: "Debugging Algorithm", "Essay Planning Steps"
✅ Analysis workflows: "Literary Analysis Method", "Data Cleaning Pipeline"

### Poor stepsjson Examples
❌ "Types of Fractions" (static list)
❌ "Features of Democracy" (concepts, not actions)
❌ "Pros and Cons" (comparison, not process)

## STRUCTURE YOUR GUIDE

### 1. Title & Overview
- Begin with the exact document title as H1. Optionally include a very brief orientation if present in the source (What, Why, Learning Objectives).

### 2. Core Content Organization

**For Complex Terms/Concepts:**
First occurrence only, provide layers:
- **Plain Language:** One-sentence everyday explanation
- **Technical Definition:** Precise 1-2 line definition with formula if applicable
- **Key Insight:** Why this matters or how it connects (if explicitly supported)

**For Processes/Workflows:**
- First describe the overall process goal.
- If ≥3 explicit action steps exist, create a stepsjson block.
- Follow with any additional context or tips.
- Include common pitfalls if mentioned in source.

**For Relationships/Comparisons:**
- Highlight causal links, contrasts, hierarchies
- Use tables ONLY if they provide dense comparative value
- Convert to clean markdown format

### 3. Visual Elements Policy

**Tables:** Include only when they:
- Compare multiple items across dimensions
- Clarify complex relationships
- Substantially reduce cognitive load

**Figure Descriptions:** When source includes diagrams/charts:
- Briefly state what it shows
- Highlight key insight or trend
- Omit purely decorative details
- Format: "Figure: [What it shows]. Key insight: [Main takeaway]"

### 4. Examples & Applications
Only include if explicitly present in source:
- **Example:** [Problem statement]
- **Solution Process:** Key steps (compressed)
- **Result:** Final answer with insight

### 5. Reinforcement (Do Not Include as a separate section)
- Do NOT include a separate "Learning Reinforcement" or "Active Recall Questions" section in the notes output.
- If the source itself contains such lists, compress them into the relevant section rather than adding a new section at the end.

## STYLE GUIDELINES

### Voice & Tone
- Conversational but precise
- Encouraging and accessible
- Like a knowledgeable friend explaining concepts
- Maintain energy and engagement

### Emphasis
- Use **bold** sparingly for critical distinctions
- Use *italics* for definitions or emphasis
- Avoid ALL CAPS except in acronyms

### What to Avoid
- Filler phrases ("In conclusion", "It's important to note")
- Code blocks for non-code content
- Speculation beyond source material
- Overly complex vocabulary
- Passive voice when active works better

## QUALITY CHECKLIST
Before finalizing, ensure:
 - ✓ First line is H1 with the exact original title (or the provided fallback)
 - ✓ Structure mirrors the document’s own sections and order
 - ✓ All content traceable to source; no external additions
 - ✓ Processes with ≥3 steps have stepsjson blocks (if applicable)
 - ✓ Math properly formatted in LaTeX $$...$$
 - ✓ No regular text in code blocks; code fences only for real code
 - ✓ Clear, engaging educational tone throughout

Return ONLY the final markdown study guide with any embedded stepsjson blocks.
"""

# Function to handle non-PDF image files
async def process_image_via_gemini(image_path: str, mode: str = "overview", title: Optional[str] = None) -> Tuple[str, str]:
    """
    Process a single image file directly to markdown through Gemini API.

    Args:
        image_path: Path to the image file
        mode: "overview" for concise overview, "detailed" for full study guide

    Returns:
        raw_text: Empty string (no longer extracted separately)
        markdown_content: Direct markdown analysis of the image content
    """
    try:
        # Read image file
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        # Generate markdown directly from image (single step)
        model = get_gemini_model()
        # Pick prompt based on mode
        if (mode or "overview").lower() == "overview":
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
        else:
            markdown_prompt = _generate_educational_markdown_prompt()
        markdown_response = await model.generate_content_async(
            [
                markdown_prompt,
                {"mime_type": "image/jpeg", "data": image_bytes},
            ],
            generation_config={
                "temperature": 0.65,
                "top_p": 0.85,
                "top_k": 40,
                "max_output_tokens": 16384,
            },
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
        model = get_gemini_model()
        # Pick prompt based on mode
        if (mode or "overview").lower() == "overview":
            exact_title = (title or "Overview").strip()
            markdown_prompt = (
                f"Source material: ~{page_count} pages.\n"  # metadata for the model; not for output
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
        else:
            markdown_prompt = _generate_educational_markdown_prompt(page_count, title_fallback=title)
        try:
            markdown_response = await model.generate_content_async(
                [
                    markdown_prompt,
                    {"mime_type": "application/pdf", "data": pdf_bytes},
                ],
                generation_config={
                    "temperature": 0.65,
                    "top_p": 0.85,
                    "top_k": 40,
                    "max_output_tokens": 16384,
                },
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
            if (mode or "overview").lower() == "overview":
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
            else:
                base_prompt = _generate_educational_markdown_prompt(page_count, title_fallback=title)
            fallback_prompt = base_prompt + "\n\n[BEGIN EXTRACTED TEXT]\n" + text + "\n[END EXTRACTED TEXT]"
            markdown_response = await model.generate_content_async(
                fallback_prompt,
                generation_config={
                    "temperature": 0.6,
                    "top_p": 0.85,
                    "top_k": 40,
                    "max_output_tokens": 16384,
                },
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
