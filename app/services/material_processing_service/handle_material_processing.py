# app/services/material_processing_service/handle_material_processing.py

import io
import json
import logging
from typing import Any, Dict, List, Tuple
import PyPDF2

from app.core.genai_client import get_gemini_model

logger = logging.getLogger(__name__)
model = get_gemini_model()

def get_pdf_page_count(file_path: str) -> int:
    """
    Return number of pages in a PDF, or raise ValueError if unreadable.
    """
    try:
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            return len(reader.pages)
    except Exception:
        logger.exception("Failed to read PDF page count")
        raise ValueError("Could not read PDF page count")


def _generate_extraction_prompt(is_pdf: bool = False) -> str:
    """
    Generate the text extraction prompt for both PDF and image processing.
    
    Args:
        is_pdf: Whether this is for PDF processing (adds page break instructions)
        
    Returns:
        The extraction prompt string
    """
    page_break_instruction = """
        - Clearly indicate page breaks with "--- PAGE BREAK ---"
        """ if is_pdf else ""
    
    return f"""
        ## INSTRUCTIONS
        Carefully transcribe ALL text visible in this {'PDF document' if is_pdf else 'image'}. Include all text, formulas, equations, and notation exactly as shown. Maintain the original layout and formatting as much as possible so that the document can be reproduced later.

        ## ANALYSIS PROCESS
        1. **Document Assessment**
        - Scan the entire {'PDF to identify all text regions across all pages' if is_pdf else 'image to identify all text regions'}
        - Note the document structure (paragraphs, columns, sections{', chapters' if is_pdf else ''})
        - Identify mathematical elements requiring special formatting

        2. **Text Extraction**
        - Process the document in a logical reading order (left-to-right, top-to-bottom)
        - Maintain proper paragraph breaks and indentation
        - Preserve bullets, numbering, and other formatting elements{page_break_instruction}

        3. **Mathematical Notation Handling (ENFORCE: This should be consistent and permanent going forward)**
        - For any mathematical expression, formula, equation, matrix, or symbol:
            a) Identify the complete mathematical expression
            b) Wrap it using display-math delimiters $$ ... $$
            c) Escape every backslash (\\) and replace it with double (\\\\)
            d) Example: Convert $\\frac{{x}}{{y}}$ to $$\\\\frac{{x}}{{y}}$$

        4. **Layout Verification**
        - Check that spatial relationships between elements are preserved
        - Verify that all text has been captured, including {'headers, footers, and ' if is_pdf else ''}marginal notes
        - Ensure mathematical expressions maintain their original formatting intent
        """


def _generate_direct_markdown_prompt(page_count: int = None) -> str:
    """
    Generate direct markdown processing prompt that analyzes document content 
    and creates markdown in a single step (no text extraction needed)
    
    Args:
        page_count: Number of pages (for PDF processing only)
        
    Returns:
        The direct markdown processing prompt string
    """
    page_count_info = f"This document has {page_count} pages." if page_count else ""
    
    return f"""
        You are a brilliant, engaging educator who excels at making complex subjects accessible and interesting.
        Analyze this document directly and create a comprehensive markdown study guide from the visual content.

        {page_count_info}

        DIRECT ANALYSIS PROCESS:
        1. **Visual Document Analysis**
           - Read ALL text, formulas, equations, and notation exactly as shown
           - Identify document structure (headings, sections, chapters, columns)
           - Note visual elements (diagrams, charts, tables, images)
           - Preserve spatial relationships and layout hierarchy
           - Recognize mathematical expressions and convert to LaTeX format

        2. **Content Understanding**
           - Identify the core subject area and learning objectives
           - Extract key concepts with precise definitions and relationships
           - Isolate technical terminology, formulas, theorems, and principles
           - Record examples with complete context from the document
           - Understand the logical flow and progression of topics

        3. **Educational Enhancement**
           - Identify real-world applications and practical significance
           - Create self-assessment opportunities based on content
           - Structure for progressive disclosure to prevent information overload
           - Add engaging elements while preserving academic accuracy

        4. **Visual Element Integration**
           - Describe diagrams, charts, and figures in context
           - Preserve table structures in markdown format
           - Maintain figure-caption relationships
           - Include references to visual elements in explanations

        OUTPUT FORMAT (MARKDOWN ONLY):
        Generate a complete markdown document following this structure:

        ```markdown
        <!-- DOCUMENT_METADATA -->
        <!-- CONTENT_TAGS: ["tag1", "tag2", "tag3"] -->
        <!-- DIFFICULTY_LEVEL: "beginner|intermediate|advanced" -->
        <!-- ESTIMATED_READ_TIME: "15-20" -->

        # [Document Title - Extract from document]

        > 🎯 **Study Time:** [X] minutes | **Difficulty:** [Level] | **Prerequisites:** [List if any]

        ## 📋 Complete Overview

        ### What You'll Master
        - [ ] [Learning objective 1 - based on document content]
        - [ ] [Learning objective 2 - based on document content]
        - [ ] [Learning objective 3 - based on document content]

        ### 🗺️ Content Map
        **This material covers [X] main areas:**

        1. **🔍 Core Concepts** ([X] min read)
           - [Concept 1]: [Brief description from document]
           - [Concept 2]: [Brief description from document]

        2. **📐 Key Formulas & Methods** ([X] min read)
           - [Formula 1]: [What it does - from document]
           - [Method 1]: [When to use - from document]

        3. **🔬 Practice Applications** ([X] min practice)
           - [Application 1]: [Example scenario from document]
           - Real-world case study: [Brief description]

        ### 🌟 Why This Matters
        [Compelling overview of practical importance with specific examples]

        **Real-World Impact:**
        - **Technology:** [Specific example relevant to content]
        - **Business:** [Specific example relevant to content]
        - **Daily Life:** [Relatable personal example]
        - **Career Paths:** [Jobs that use this knowledge]

        ### 🎯 Learning Path Options
        **📚 Deep Dive ([X] minutes):** Overview → Core Concepts → Practice → Review
        **⚡ Quick Review ([X] minutes):** Overview → Quick Reference → Self-Assessment

        ---

        ## 📚 Core Concepts

        <!-- TOPIC_START: topic-id -->
        <!-- TOPIC_METADATA: {{"id": "topic-id", "difficulty": "level", "key_terms": ["term1", "term2"]}} -->

        ### Concept 1: [Name from document]

        > **🔑 Key Idea:** [One-sentence summary from document content]
        > **⏱️ Read Time:** [X] minutes

        [Brief introduction paragraph based on document content]

        <details>
        <summary>📖 <strong>Complete Explanation</strong></summary>

        [Detailed explanation with examples from the document]

        **Visual Elements:**
        [If document contains diagrams/charts, describe them here]

        </details>

        <details>
        <summary>🧮 <strong>Mathematical Details</strong></summary>

        **Formula:** [Extract from document]
        $$[LaTeX formula with escaped backslashes - from document]$$

        **Where:**
        - `variable1` = [description from document]
        - `variable2` = [description from document]

        **Step-by-Step Process:** [If shown in document]
        1. [Step 1 from document]
        2. [Step 2 from document]

        </details>

        <details>
        <summary>🧠 <strong>Test Your Understanding</strong></summary>

        **Before looking at answers, try to:**
        1. [Question based on document content]
        2. [Question based on document content]

        **Answers:**
        1. [Answer with explanation from document]
        2. [Answer with explanation from document]

        </details>

        <details>
        <summary>⚠️ <strong>Common Mistakes & How to Avoid Them</strong></summary>

        **Mistake 1:** [Common error - infer from document content]
        - **Why it happens:** [Explanation]
        - **How to avoid:** [Prevention strategy]

        </details>

        <!-- TOPIC_END: topic-id -->

        ---

        ## 🔬 Practice & Application

        <details>
        <summary>💪 <strong>Practice Problems</strong></summary>

        ### Problem 1: [Type - based on document examples]
        **Scenario:** [Real-world context]
        **Given:** [What you know - from document]
        **Find:** [What to solve for - from document]

        **Solution:**
        [Step-by-step solution based on document methods]

        **Key Insight:** [What this problem teaches]

        </details>

        <details>
        <summary>🌍 <strong>Real-World Case Study</strong></summary>

        ### Case: [Specific Example - create relevant case]
        **Background:** [Context]
        **Challenge:** [Problem that needed solving]
        **Solution:** [How this concept was applied]
        **Results:** [Outcomes and impact]

        </details>

        ---

        ## 🚀 Quick Reference

        ### 📋 Formula Sheet
        | Formula | Use Case | Variables |
        |---------|----------|-----------|
        | `[Formula from document]` | [When to use] | [Key variables] |

        ### ✅ Self-Assessment Checklist
        **Core Understanding:**
        - [ ] I can explain [concept from document] in my own words
        - [ ] I can solve [problem type from document] problems

        **Application Skills:**
        - [ ] I can apply this to real scenarios
        - [ ] I can identify when to use this knowledge

        ### 📅 Spaced Review Schedule
        - **Day 1:** Complete initial learning (today)
        - **Day 3:** Review Quick Reference (10 min)
        - **Day 7:** Practice problems (15 min)
        - **Day 14:** Full concept review (20 min)

        ---

        ## 🔗 Connections & Next Steps

        ### 🏗️ Building On This Knowledge
        **What Comes Next:**
        - [Advanced Topic 1 - infer from document]
        - [Related Field - infer from document]

        ### 📖 Additional Resources
        - [Resource 1] - [What it offers]
        - [Resource 2] - [What it offers]

        ---

        ## ✅ Completion Checklist

        ### 📚 Learning Completed
        - [ ] Read and understood overview
        - [ ] Mastered core concepts
        - [ ] Completed practice problems

        ### 🎯 Skills Developed
        - [ ] Can explain concepts without notes
        - [ ] Can solve problems independently
        - [ ] Can identify real-world applications

        **🎉 Congratulations! You've mastered [Topic Name from document]**

        <!-- DOCUMENT_END -->
        <!-- TOPICS_COVERED: ["topic1", "topic2"] -->
        ```

        CRITICAL REQUIREMENTS:
        1. Analyze the document DIRECTLY - don't ask for text, work from the visual content
        2. Use the EXACT structure above
        3. Fill in ALL placeholders with actual content from the document
        4. Convert all math to LaTeX with escaped backslashes (\\\\)
        5. Include metadata comments for service integration
        6. Make content engaging and student-friendly
        7. Ensure progressive disclosure with collapsible sections
        8. Include real-world applications throughout
        9. Describe visual elements (diagrams, charts, tables) when present
        10. Preserve the document's original structure and hierarchy

        Respond with ONLY the markdown document, no additional text.
        """


def _create_fallback_structure(title: str, error_message: str, extracted_text: str = "", page_count: int = None) -> dict:
    """
    Create a fallback JSON structure when processing fails.
    
    Args:
        title: Title for the fallback structure
        error_message: Error message to include
        extracted_text: Any extracted text to include
        page_count: Page count for PDF processing
        
    Returns:
        Fallback JSON structure
    """
    structure = {
        "title": title,
        "summary": error_message,
        "topics": [],
        "practical_applications": [],
        "study_suggestions": []
    }
    
    if page_count is not None:
        structure["page_count"] = page_count
    
    if extracted_text:
        structure["topics"] = [{
            "name": "Extracted Content",
            "description": "Text extracted from the document",
            "key_points": [{"name": "Raw content", "description": extracted_text[:200] + "..." if len(extracted_text) > 200 else extracted_text}],
            "formulas": [],
            "common_misconceptions": [],
            "subtopics": []
        }]
    
    return structure


def _parse_json_response(response_text: str, fallback_title: str, error_context: str, extracted_text: str = "", page_count: int = None) -> dict:
    """
    Parse JSON response from Gemini with fallback handling.
    
    Args:
        response_text: Raw response text from Gemini
        fallback_title: Title to use in fallback structure
        error_context: Context for error messages
        extracted_text: Extracted text for fallback
        page_count: Page count for PDF processing
        
    Returns:
        Parsed JSON structure or fallback structure
    """
    try:
        # First try to parse the entire response as JSON
        return json.loads(response_text)
    except json.JSONDecodeError:
        # If that fails, try to extract JSON from the text using regex
        import re
        json_match = re.search(r'```json\n(.*?)\n```', response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # If all parsing fails, return fallback structure
        return _create_fallback_structure(
            fallback_title,
            f"{error_context} but couldn't be fully analyzed",
            extracted_text,
            page_count
        )


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
        markdown_prompt = _generate_direct_markdown_prompt()
        markdown_response = await model.generate_content_async([
            markdown_prompt,
            {"mime_type": "image/jpeg", "data": image_bytes} 
        ])
        
        markdown_content = markdown_response.text
        print(f'Generated markdown length: {len(markdown_content)} characters')
        
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
        # Get page count first
        page_count = get_pdf_page_count(pdf_path)
        
        # Read PDF file as bytes
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        
        print(f"Processing PDF with {page_count} pages directly to markdown via Gemini...")
        
        # Generate markdown directly from PDF (single step)
        markdown_prompt = _generate_direct_markdown_prompt(page_count)
        markdown_response = await model.generate_content_async([
            markdown_prompt,
            {"mime_type": "application/pdf", "data": pdf_bytes}
        ])
        
        markdown_content = markdown_response.text
        print(f'Generated markdown length: {len(markdown_content)} characters')
        
        # Return empty string for raw_text since we're doing direct processing
        return "", markdown_content, page_count
        
    except Exception as e:
        logger.exception(f"Failed to process PDF directly via Gemini: {str(e)}")
        # Return empty results in case of failure
        return "", f"# Processing Failed\n\nError: {str(e)}", 0
