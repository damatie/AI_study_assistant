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


def _generate_processing_prompt(extracted_text: str, page_count: int = None) -> str:
    """
    Generate the content processing prompt for both PDF and image processing.
    
    Args:
        extracted_text: The text extracted from the document
        page_count: Number of pages (for PDF processing only)
        
    Returns:
        The processing prompt string
    """
    page_count_field = f'"page_count": {page_count},' if page_count is not None else ""
    
    return f"""
        You are a brilliant, engaging educator who excels at making complex subjects accessible and interesting. Your task is to transform the provided educational material into a structured knowledge framework (in JSON format).

        CONTENT TO ANALYZE:
        {extracted_text}

        ANALYSIS PROCESS:
        1. First, identify the core subject area and learning objectives
        2. Extract all key concepts with precise definitions and contextual relationships
        3. Isolate and explain technical terminology, formulas, theorems, and principles
        4. For mathematical/technical content:
        - Capture ALL equations, formulas, and notation EXACTLY as presented
        - Document each step of any worked examples or derivations shown
        - Use the exact variable names and symbols from the original material
        - For any mathematical expression, formula, equation, matrix, or symbol convert them to LaTeX format:
            * Wrap it using display-math delimiters $$ ... $$
            * Escape every backslash (\\) and replace it with double (\\\\)
            * Example: Convert $$\\frac{{x}}{{y}}$$ to $$\\\\frac{{x}}{{y}}$$
        5. Record all examples from the document with their complete context
        6. Recognize hierarchical relationships between topics and subtopics
        7. Identify knowledge dependencies and progression pathways
        8. Highlight real-world applications and practical significance
        9. Note areas of potential confusion or conceptual difficulty
        10. For any abbreviations or acronyms provide full meaning and make it easy to understand
        11. I want you to exhaust the extracted text - don't leave anything untouched

        CRITICAL GUIDELINES TO PREVENT REPETITION:
        - Each concept, definition, formula, or example should appear EXACTLY ONCE in the output
        - If a concept relates to multiple topics, place it in the most relevant section only
        - Cross-reference related concepts rather than repeating them
        - Use the subtopic structure to organize information hierarchically, not to duplicate it
        - Ensure information in parent topics is not repeated in child topics
        - Focus on concise, unique contributions in each section

        OUTPUT FORMAT (JSON ONLY):
        {{
            "title": "Suggested title based on content",
            "summary": "Comprehensive overview of the material including its significance and core learning objectives",
            {page_count_field}
            "topics": [
                {{
                    "name": "Topic name",
                    "description": "Brief explanation of this topic's importance",
                    "key_points": [
                        {{
                            "name": "Key point 1",
                            "description": "with relative examples from the material if available, explain in more details in a basic manner if needed for proper understanding (do not repeat examples from elsewhere)."
                        }},
                        {{
                            "name": "Key point 2",
                            "description": "with relative examples from the material if available, explain in more details in a basic manner if needed for proper understanding (do not repeat examples from elsewhere)."
                        }}
                    ],
                    "formulas": [
                        {{
                            "name": "Formula name (only if not presented elsewhere)",
                            "expression": "Full formula expression in LaTeX format with escaped backslashes",
                            "variables": {{"variable": "what it represents (convert them to LaTeX format)"}},
                            "explanation": "What the formula represents and how to apply it"
                        }}
                    ],
                    "common_misconceptions": [
                        {{
                            "misconception": "Common misunderstanding",
                            "correction": "Proper understanding"
                        }}
                    ],
                    "subtopics": [
                        {{
                            "name": "Subtopic name",
                            "key_points": [
                                {{
                                    "name": "Key point (must be unique)",
                                    "description": "With example if available, explain in more details in a basic manner if needed for proper understanding (must not duplicate examples from parent topic)"
                                }}
                            ]
                        }}
                    ]
                }}
            ],
            "practical_applications": [
                {{
                    "context": "Application area",
                    "example": "Specific example of how this knowledge applies"
                }}
            ],
            "study_suggestions": [
                {{
                    "technique": "Study method",
                    "implementation": "How to apply this technique to this material (unique to this section)"
                }}
            ]
        }}

        QUALITY VERIFICATION STEPS:
        1. After completing the analysis, systematically check for any duplicated information
        2. Ensure each concept, definition, formula, and example appears exactly once in the JSON
        3. Validate that the output is comprehensive but non-repetitive
        4. Check that mathematical expressions are properly formatted with escaped backslashes
        5. Make sure that any mathematical expression, formula, equation, matrix, or symbol converted to the required format irrespective of where it's used (title, topics, subtopics, etc.)

        Respond with ONLY the JSON object, no additional text or formatting.
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
async def process_image_via_gemini(image_path: str) -> Tuple[str, dict]:
    """
    Process a single image file through Gemini API.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        raw_text: Extracted text from the image
        processed_content: JSON analysis of the image content
    """
    try:
        # Read image file
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        
        # Extract text using shared prompt
        extraction_prompt = _generate_extraction_prompt(is_pdf=False)
        extraction_response = await model.generate_content_async([
            extraction_prompt,
            {"mime_type": "image/jpeg", "data": image_bytes} 
        ])
        
        extracted_text = extraction_response.text
        print('extracted_text', extracted_text)
        
        # Process the extracted text using shared prompt
        process_prompt = _generate_processing_prompt(extracted_text)
        process_response = await model.generate_content_async(process_prompt)
        
        # Parse response using shared function
        processed_json = _parse_json_response(
            process_response.text,
            "Analysis of Image Content",
            "Content extracted from image",
            extracted_text
        )
        
        return extracted_text, processed_json
        
    except Exception as e:
        logger.exception(f"Failed to process image via Gemini: {str(e)}")
        # Return empty results in case of failure
        return "", _create_fallback_structure(
            "Processing Failed",
            f"Image processing error: {str(e)}"
        )


# Function to handle PDF files
async def process_pdf_via_gemini(pdf_path: str) -> Tuple[str, dict, int]:
    """
    Process a PDF file directly through Gemini API without image conversion.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        raw_text: Extracted text from the entire PDF
        processed_content: JSON analysis of the PDF content
        page_count: Number of pages in the PDF
    """
    try:
        # Get page count first
        page_count = get_pdf_page_count(pdf_path)
        
        # Read PDF file as bytes
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        
        print(f"Processing PDF with {page_count} pages directly via Gemini...")
        
        # Extract text using shared prompt
        extraction_prompt = _generate_extraction_prompt(is_pdf=True)
        extraction_response = await model.generate_content_async([
            extraction_prompt,
            {"mime_type": "application/pdf", "data": pdf_bytes}
        ])
        
        extracted_text = extraction_response.text
        print(f'Extracted text length: {len(extracted_text)} characters')
        
        # Process the extracted text using shared prompt
        process_prompt = _generate_processing_prompt(extracted_text, page_count)
        process_response = await model.generate_content_async(process_prompt)
        
        # Parse response using shared function
        processed_json = _parse_json_response(
            process_response.text,
            "Analysis of PDF Content",
            "Content extracted from PDF",
            extracted_text,
            page_count
        )
        
        return extracted_text, processed_json, page_count
        
    except Exception as e:
        logger.exception(f"Failed to process PDF directly via Gemini: {str(e)}")
        # Return empty results in case of failure
        return "", _create_fallback_structure(
            "Processing Failed",
            f"PDF processing error: {str(e)}",
            page_count=0
        ), 0
