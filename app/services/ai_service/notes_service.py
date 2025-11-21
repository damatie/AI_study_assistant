"""AI-powered study note generation service."""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pypdf import PdfReader

from app.core.genai_client import (
	DEFAULT_MULTIMODAL_GENERATION_CONFIG,
	FALLBACK_TEXT_GENERATION_CONFIG,
	get_gemini_model,
)
from app.services.document_conversion.gotenberg_client import (
	convert_office_document_to_pdf,
	GotenbergConversionError,
	GotenbergNotConfigured,
)
from app.services.material_processing_service.gemini_files import (
	GeminiFileMetadata,
	SUPPORTED_FILE_MIME_TYPES,
	generate_from_gemini_file,
)
from app.services.material_processing_service.handle_material_processing import (
	get_pdf_page_count_from_bytes,
)
from app.services.material_processing_service.office_documents import (
	get_office_page_count,
	extract_docx_text,
)
from app.utils.stepsjson import (
	filter_trivial_blocks,
	sanitize_all_blocks,
	strip_scanned_table_artifacts,
	unwrap_non_code_fences,
)

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_OFFICE_EXTENSIONS = {".doc", ".docx"}


class NoteGenerationVariant(str, Enum):
	"""Supported study note styles."""

	detailed = "detailed"


@dataclass(frozen=True)
class NoteGenerationResult:
	"""Structured response for generated study notes."""

	markdown: str
	page_count: Optional[int] = None


async def generate_notes_for_material(
	*,
	file_bytes: bytes,
	filename: str,
	title: Optional[str] = None,
	variant: NoteGenerationVariant = NoteGenerationVariant.detailed,
	gemini_file: Optional[GeminiFileMetadata] = None,
	page_count: Optional[int] = None,
) -> NoteGenerationResult:
	"""Generate study notes for a study material asset."""

	if variant is not NoteGenerationVariant.detailed:
		raise ValueError(f"Unsupported notes variant: {variant}")

	ext = _extension_for(filename)
	if ext == ".pdf":
		return await _generate_detailed_notes_from_pdf(
			pdf_bytes=file_bytes,
			title=title,
			page_count=page_count,
			gemini_file=gemini_file,
			filename=filename,
		)

	if ext in _OFFICE_EXTENSIONS:
		return await _generate_detailed_notes_from_office(
			doc_bytes=file_bytes,
			title=title,
			page_count=page_count,
			gemini_file=gemini_file,
			filename=filename,
		)

	if ext in _IMAGE_EXTENSIONS:
		return await _generate_detailed_notes_from_image(
			image_bytes=file_bytes,
			title=title,
			gemini_file=gemini_file,
			filename=filename,
		)

	logger.warning("Unsupported file type for notes generation: %s", ext or "<unknown>")
	return NoteGenerationResult(markdown="# Processing Failed\n\nUnsupported file type.")


def _build_detailed_notes_prompt(page_count: Optional[int], title_fallback: Optional[str]) -> str:
	"""Return the detailed notes prompt shared across material types."""

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


async def _generate_detailed_notes_from_pdf(
	*,
	pdf_bytes: bytes,
	title: Optional[str],
	page_count: Optional[int],
	gemini_file: Optional[GeminiFileMetadata],
	filename: str,
) -> NoteGenerationResult:
	"""Generate detailed notes for a PDF file, preferring Gemini Files when available."""

	computed_page_count: Optional[int] = page_count
	if computed_page_count is None:
		try:
			computed_page_count = get_pdf_page_count_from_bytes(pdf_bytes)
		except Exception:
			logger.warning("Falling back to unknown page count for detailed notes")
			computed_page_count = None

	prompt = _build_detailed_notes_prompt(computed_page_count, title)
	via_files_markdown = await _try_gemini_files_path(
		prompt=prompt,
		gemini_file=gemini_file,
		filename=filename,
		default_mime="application/pdf",
		log_suffix="pdf",
	)
	if via_files_markdown is not None:
		return NoteGenerationResult(markdown=via_files_markdown, page_count=computed_page_count)

	model = get_gemini_model()
	try:
		direct_markdown = await _generate_via_bytes(
			model=model,
			prompt=prompt,
			mime_type="application/pdf",
			payload=pdf_bytes,
			log_suffix="pdf",
		)
		return NoteGenerationResult(markdown=direct_markdown, page_count=computed_page_count)

	except Exception as primary_error:  # noqa: BLE001
		logger.warning(
			"PDF multimodal detailed notes failed (%s); falling back to text-only",
			primary_error,
		)

		extracted_text = _extract_text_from_pdf_bytes(pdf_bytes)
		if not extracted_text:
			logger.error("Unable to extract text for fallback detailed notes")
			raise

		fallback_prompt = _build_pdf_fallback_prompt(
			extracted_text,
			computed_page_count,
			title,
		)
		response = await model.generate_content_async(
			fallback_prompt,
			generation_config=FALLBACK_TEXT_GENERATION_CONFIG.copy(),
		)

		markdown = _post_process_markdown(response.text)
		logger.info("Generated detailed notes (fallback) length=%s", len(markdown))
		return NoteGenerationResult(markdown=markdown, page_count=computed_page_count)


async def _generate_detailed_notes_from_office(
	*,
	doc_bytes: bytes,
	title: Optional[str],
	page_count: Optional[int],
	gemini_file: Optional[GeminiFileMetadata],
	filename: str,
) -> NoteGenerationResult:
	"""Generate detailed notes for DOC/DOCX files."""

	ext = _extension_for(filename)
	computed_page_count: Optional[int] = page_count
	if computed_page_count is None:
		try:
			computed_page_count = get_office_page_count(doc_bytes, ext)
		except Exception:
			logger.warning("Falling back to unknown page count for Office notes")
			computed_page_count = None

	conversion_pdf_bytes: Optional[bytes] = None
	conversion_filename: Optional[str] = None
	try:
		conversion = await convert_office_document_to_pdf(
			document_bytes=doc_bytes,
			filename=filename,
		)
		conversion_pdf_bytes = conversion.content
		conversion_filename = conversion.filename
		try:
			conversion_page_count = get_pdf_page_count_from_bytes(conversion_pdf_bytes)
		except Exception:
			logger.warning("Failed to derive page count from converted PDF; keeping prior estimate.")
			conversion_page_count = None
		if conversion_page_count is not None:
			computed_page_count = conversion_page_count
	except GotenbergNotConfigured:
		logger.info("Gotenberg not configured; using direct Office notes path.")
	except GotenbergConversionError as conversion_error:
		logger.warning("Gotenberg conversion failed (%s); using direct Office notes path.", conversion_error)

	if conversion_pdf_bytes is not None:
		fallback_pdf_name = conversion_filename or f"{os.path.splitext(filename or 'document')[0]}.pdf"
		return await _generate_detailed_notes_from_pdf(
			pdf_bytes=conversion_pdf_bytes,
			title=title,
			page_count=computed_page_count,
			gemini_file=None,
			filename=fallback_pdf_name,
		)

	prompt = _build_detailed_notes_prompt(computed_page_count, title)
	default_mime = _resolve_mime_type(filename, "application/msword")

	via_files_markdown = await _try_gemini_files_path(
		prompt=prompt,
		gemini_file=gemini_file,
		filename=filename,
		default_mime=default_mime,
		log_suffix="office",
	)
	if via_files_markdown is not None:
		return NoteGenerationResult(markdown=via_files_markdown, page_count=computed_page_count)

	model = get_gemini_model()
	try:
		markdown = await _generate_via_bytes(
			model=model,
			prompt=prompt,
			mime_type=default_mime,
			payload=doc_bytes,
			log_suffix="office",
		)
		return NoteGenerationResult(markdown=markdown, page_count=computed_page_count)

	except Exception as primary_error:  # noqa: BLE001
		logger.warning(
			"Office multimodal detailed notes failed (%s); attempting fallback",
			primary_error,
		)
		if ext == ".docx":
			extracted_text = extract_docx_text(doc_bytes)
			if extracted_text:
				fallback_prompt = (
					prompt
					+ "\n\nUse ONLY the extracted text below:\n\n[BEGIN EXTRACTED TEXT]\n"
					+ extracted_text
					+ "\n[END EXTRACTED TEXT]"
				)
				response = await model.generate_content_async(
					fallback_prompt,
					generation_config=FALLBACK_TEXT_GENERATION_CONFIG.copy(),
				)
				markdown = _post_process_markdown(response.text)
				logger.info(
					"Generated detailed notes (fallback office) length=%s",
					len(markdown),
				)
				return NoteGenerationResult(markdown=markdown, page_count=computed_page_count)

	logger.exception("Unable to generate detailed notes for Office document")
	return NoteGenerationResult(markdown="# Processing Failed\n\nAn error occurred.", page_count=computed_page_count)


async def _generate_detailed_notes_from_image(
	*,
	image_bytes: bytes,
	title: Optional[str],
	gemini_file: Optional[GeminiFileMetadata],
	filename: str,
) -> NoteGenerationResult:
	"""Generate detailed notes for an image file."""

	prompt = _build_detailed_notes_prompt(page_count=None, title_fallback=title)
	default_mime = _resolve_mime_type(filename, "image/jpeg")

	via_files_markdown = await _try_gemini_files_path(
		prompt=prompt,
		gemini_file=gemini_file,
		filename=filename,
		default_mime=default_mime,
		log_suffix="image",
	)
	if via_files_markdown is not None:
		return NoteGenerationResult(markdown=via_files_markdown)

	model = get_gemini_model()
	try:
		markdown = await _generate_via_bytes(
			model=model,
			prompt=prompt,
			mime_type=default_mime,
			payload=image_bytes,
			log_suffix="image",
		)
		return NoteGenerationResult(markdown=markdown)

	except Exception as exc:  # noqa: BLE001
		logger.exception("Failed to generate detailed notes from image: %s", exc)
		return NoteGenerationResult(markdown=f"# Processing Failed\n\nError: {exc}")


def _post_process_markdown(raw_text: str) -> str:
	"""Clean Gemini output for consistent markdown."""

	markdown = sanitize_all_blocks(raw_text)
	markdown = unwrap_non_code_fences(markdown)
	markdown = strip_scanned_table_artifacts(markdown)
	markdown = filter_trivial_blocks(markdown)
	return markdown


def _extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
	"""Best-effort plain-text extraction for fallback summarization."""

	try:
		reader = PdfReader(io.BytesIO(pdf_bytes))
		texts: list[str] = []
		for page in reader.pages:
			try:
				text = page.extract_text() or ""
				texts.append(text.strip())
			except Exception:  # noqa: BLE001
				continue
		return "\n\n".join([t for t in texts if t])
	except Exception:  # noqa: BLE001
		logger.exception("Failed to extract text from PDF bytes for fallback notes")
		return ""


def _build_pdf_fallback_prompt(
	extracted_text: str,
	page_count: Optional[int],
	title: Optional[str],
) -> str:
	"""Construct the fallback prompt when multimodal generation fails."""

	base_prompt = _build_detailed_notes_prompt(page_count, title)
	return (
		base_prompt
		+ "\n\n[BEGIN EXTRACTED TEXT]\n"
		+ extracted_text
		+ "\n[END EXTRACTED TEXT]"
	)


def _extension_for(filename: str) -> str:
	"""Return the lowercase extension with leading dot ("" for empty)."""
	return os.path.splitext(filename or "")[1].lower()


def _resolve_mime_type(filename: str, default: str) -> str:
	"""Resolve MIME type from Gemini mapping with a safe default."""
	ext = _extension_for(filename)
	return SUPPORTED_FILE_MIME_TYPES.get(ext, default)


async def _try_gemini_files_path(
	*,
	prompt: str,
	gemini_file: Optional[GeminiFileMetadata],
	filename: str,
	default_mime: str,
	log_suffix: str,
) -> Optional[str]:
	"""Attempt generation via Gemini Files URI; return markdown when successful."""

	if not gemini_file:
		return None

	mime_type = gemini_file.mime_type or _resolve_mime_type(filename, default_mime)
	try:
		text = await generate_from_gemini_file(
			file_uri=gemini_file.uri,
			prompt=prompt,
			mime_type=mime_type,
			generation_config=DEFAULT_MULTIMODAL_GENERATION_CONFIG.copy(),
		)
		markdown = _post_process_markdown(text)
		logger.info(
			"Generated detailed notes (%s via Gemini Files) length=%s",
			log_suffix,
			len(markdown),
		)
		return markdown
	except Exception as gemini_error:  # noqa: BLE001
		logger.warning(
			"Gemini Files %s path failed (%s); falling back to direct bytes",
			log_suffix,
			gemini_error,
		)
		return None


async def _generate_via_bytes(
	*,
	model,
	prompt: str,
	mime_type: str,
	payload: bytes,
	log_suffix: str,
) -> str:
	"""Generate markdown by streaming the raw bytes to the Gemini model."""

	response = await model.generate_content_async(
		[
			prompt,
			{"mime_type": mime_type, "data": payload},
		],
		generation_config=DEFAULT_MULTIMODAL_GENERATION_CONFIG.copy(),
	)

	markdown = _post_process_markdown(response.text)
	logger.info("Generated detailed notes (%s) length=%s", log_suffix, len(markdown))
	return markdown
