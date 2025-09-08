# app/services/material_processing_service/handle_material_processing.py

import io
import logging
from typing import Tuple
import PyPDF2

from app.core.genai_client import get_gemini_model
from app.utils.stepsjson import sanitize_all_blocks, filter_trivial_blocks

logger = logging.getLogger(__name__)

def get_pdf_page_count_from_bytes(pdf_bytes: bytes) -> int:
    """Return number of pages from PDF bytes."""
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        return len(reader.pages)
    except Exception:
        logger.exception("Failed to read PDF page count from bytes")
        raise ValueError("Could not read PDF page count from bytes")



def _generate_educational_markdown_prompt(page_count: int | None = None) -> str:
    """
    Generate an adaptive, student-friendly study guide prompt with visual process support.
    Emphasizes clarity, compression, and interactive learning elements.
    """
    pages = f"Source material: ~{page_count} pages.\n" if page_count else ""
    
    return f"""{pages}## YOUR ROLE
You are an expert educator who transforms complex material into clear, engaging study guides with interactive visual elements that help students truly understand and retain information.

## CORE MISSION
Create a **compressed, clarified study guide** (NOT a rewrite) that captures essential knowledge while making it accessible, memorable, and visually engaging where appropriate.

## FUNDAMENTAL RULES

### Content Integrity
- **Never invent** facts, figures, formulas, examples, or steps
- If uncertain about a detail → omit it
- Target ≤35% of original length (eliminate redundancy, keep substance)
- Every claim must be traceable to source material

### Mathematical Content
- Use LaTeX formatting: $$formula$$ for all mathematical expressions
- Include formulas only when central to understanding
- Add brief intuitive explanation after each formula
- Variables in running text: use $$x$$, $$y$$ (math notation), never `x` or plain x in code blocks

### Text Formatting Guidelines
- **Regular text is just text** - no code blocks for normal content
- Only use ``` code blocks for actual programming code
- Mathematical variables: always use $$...$$ notation
- Keep paragraphs ≤4 lines
- Prefer bullet points for clarity
- Sentences ≤22 words when possible

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
- Create an engaging, outcome-focused title
- Brief orientation (if valuable): What, Why, Learning Objectives (3-5 bullets)

### 2. Core Content Organization

**For Complex Terms/Concepts:**
First occurrence only, provide layers:
- **Plain Language:** One-sentence everyday explanation
- **Technical Definition:** Precise 1-2 line definition with formula if applicable
- **Key Insight:** Why this matters or how it connects (if explicitly supported)

**For Processes/Workflows:**
- First describe the overall process goal
- If ≥3 explicit action steps exist, create a stepsjson block
- Follow with any additional context or tips
- Include common pitfalls if mentioned in source

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

### 5. Learning Reinforcement

**Memory Aids** (if patterns emerge):
- 3-5 memorable patterns, mnemonics, or frameworks
- Focus on "why" connections, not rote memorization

**Active Recall Questions** (6-12):
- Mix of: definitions, applications, "what if" scenarios
- Every question must be answerable from included content
- No answer key needed

**High-Yield Summary** (6-10 bullets):
- Most important takeaways
- No redundancy with earlier content
- Maximum insight per bullet

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
- ✓ All content traceable to source
- ✓ ≤35% of original length achieved
- ✓ Processes with ≥3 steps have stepsjson blocks (if applicable)
- ✓ Math properly formatted in LaTeX $$...$$
- ✓ No regular text in code blocks
- ✓ Questions answerable from content
- ✓ Clear, engaging educational tone throughout

Return ONLY the final markdown study guide with any embedded stepsjson blocks.
"""

# Function to handle non-PDF image files
async def process_image_via_gemini(image_path: str) -> Tuple[str, str]:
    """
    Process a single image file directly to markdown through Gemini API.
    
    Args:
        image_path: Path to the image file
        
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
        markdown_prompt = _generate_educational_markdown_prompt()
        markdown_response = await model.generate_content_async([
            markdown_prompt,
            {"mime_type": "image/jpeg", "data": image_bytes}
        ], generation_config={
            "temperature": 0.65,
            "top_p": 0.85,
            "top_k": 40,
            "max_output_tokens": 16384,
        })

        markdown_content = sanitize_all_blocks(markdown_response.text)
        markdown_content = filter_trivial_blocks(markdown_content)
        print(f"Generated markdown length: {len(markdown_content)} characters")

        # Return empty string for raw_text since we're doing direct processing
        return "", markdown_content
        
    except Exception as e:
        logger.exception(f"Failed to process image via Gemini: {str(e)}")
        # Return empty results in case of failure
        return "", f"# Processing Failed\n\nError: {str(e)}"


# Function to handle PDF files
async def process_pdf_via_gemini(pdf_path: str) -> Tuple[str, str, int]:
    """
    Process a PDF file directly to markdown through Gemini API.
    
    Args:
        pdf_path: Path to the PDF file
        
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
        
        print(f"Processing PDF with {page_count} pages directly to markdown via Gemini...")
        
        # Generate markdown directly from PDF (single step)
        model = get_gemini_model()
        markdown_prompt = _generate_educational_markdown_prompt(page_count)
        markdown_response = await model.generate_content_async([
            markdown_prompt,
            {"mime_type": "application/pdf", "data": pdf_bytes}
        ], generation_config={
            "temperature": 0.65,
            "top_p": 0.85,
            "top_k": 40,
            "max_output_tokens": 16384,
        })

        markdown_content = sanitize_all_blocks(markdown_response.text)
        markdown_content = filter_trivial_blocks(markdown_content)
        print(f"Generated markdown length: {len(markdown_content)} characters")

        # Return empty string for raw_text since we're doing direct processing
        return "", markdown_content, page_count
        
    except Exception as e:
        logger.exception(f"Failed to process PDF directly via Gemini: {str(e)}")
        # Return empty results in case of failure
        return "", f"# Processing Failed\n\nError: {str(e)}", 0
