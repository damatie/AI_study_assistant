# Standard library imports
import logging
import re
from typing import Literal, Optional

# Local imports
from app.core.genai_client import get_gemini_model
from app.services.material_processing_service.gemini_files import (
    GeminiFileMetadata,
    generate_from_gemini_file,
)

# Initialize logger
logger = logging.getLogger(__name__)

# Initialize Gemini model
model = get_gemini_model()


async def answer_with_file(
    question: str,
    tone: Literal['academic','conversational'] = 'academic',
    gemini_file: Optional[GeminiFileMetadata] = None,
) -> dict:
    """Answer a question with markdown response and proper mathematical formatting
    
    Args:
        question: The student's question
        tone: Response tone (academic or conversational)
        gemini_file_uri: Optional Gemini file URI reference (None for questions without material)
        
    Returns:
        dict: Response containing the AI tutor's markdown answer
        
    Raises:
        Exception: If AI service fails
    """

    # Build tone-specific persona
    if tone == 'academic':
        persona = (
            "You are Knoledg's AI tutor. "
            "When the student's message is ONLY a greeting (hi, hello, hey, who are you, what can you help with) with no academic question, "
            "respond warmly using first-person (I'm Knoledg's AI tutor), acknowledge the material they have, "
            "highlight 2-3 interesting topics from it as a teaser, and ask what they'd like to explore. "
            "For ANY substantive question or request (even if it starts with a greeting), skip the intro and directly answer the question "
            "in third-person, formal style, grounded in the study material. Keep explanations objective, precise, and well-structured."
        )
    else:
        persona = (
            "You are Knoledg's AI tutor. "
            "When the student's message is ONLY a greeting (hi, hello, hey, who are you, what can you help with) with no academic question, "
            "respond warmly using first-person (I'm Knoledg's AI tutor), acknowledge the material they have, "
            "highlight 2-3 interesting topics from it as a teaser, and ask what they'd like to explore. "
            "For ANY substantive question or request (even if it starts with a greeting), skip the intro and directly answer the question "
            "in a friendly, direct way using second-person (you/your). "
            "Be clear, supportive, and conversational. Draw on external knowledge when it helps understanding, but always tie it back to the material."
        )

    prompt = f"""
{persona}

**MATHEMATICAL NOTATION (INTERNAL):** Do not mention LaTeX or formatting; just use it.
    
    When including ANY mathematical content, you MUST use proper LaTeX formatting within double dollar signs ($...$):
    
    **Fractions:** Always use \\frac{{numerator}}{{denominator}}
    - Instead of: x/12 → Use: $\\frac{{x}}{{12}}$
    - Instead of: 2x/15 → Use: $\\frac{{2x}}{{15}}$
    - Instead of: (x+1)/2x → Use: $\\frac{{x+1}}{{2x}}$
    
    **Variables and Expressions:**
    - Single variables: $x$, $y$, $a$, $b$
    - Expressions: $2x + 3$, $x^2 - 4$
    - Equations: $x = \\frac{{-b \\pm \\sqrt{{b^2 - 4ac}}}}{{2a}}$
    
    **Common Mathematical Elements:**
    - Exponents: $x^2$, $e^{{-x}}$, $2^n$
    - Square roots: $\\sqrt{{x}}$, $\\sqrt{{x^2 + y^2}}$
    - Subscripts: $x_1$, $a_n$, $v_0$
    - Greek letters: $\\alpha$, $\\beta$, $\\pi$, $\\theta$
    - Summation: $\\sum_{{i=1}}^{{n}} x_i$
    - Integration: $\\int_0^1 f(x) dx$
    - Limits: $\\lim_{{x \\to 0}} \\frac{{\\sin x}}{{x}}$
    
    **Complex Expressions:**
    - Matrices: $\\begin{{pmatrix}} a & b \\\\ c & d \\end{{pmatrix}}$
    - Systems: $\\begin{{cases}} x + y = 5 \\\\ 2x - y = 1 \\end{{cases}}$
    - Aligned equations: $\\begin{{align}} x &= 2 \\\\ y &= 3x + 1 \\end{{align}}$
    
    **CRITICAL RULES:**
    - NEVER use plain text for mathematical expressions
    - NEVER mention "LaTeX", "formatting", or any technical details to students
    - NEVER say formulas "weren't provided in LaTeX format" or similar
    - Simply present formulas naturally as part of your explanation

QUESTION:
{question}

Answer the question using markdown.
"""

    try:
        if gemini_file:
            # Generate using Files API with PDF context
            response_text = await generate_from_gemini_file(
                file_uri=gemini_file.uri,
                prompt=prompt,
                mime_type=gemini_file.mime_type or "application/pdf",
            )
            text = response_text or ""
        else:
            # Generate without file context (for questions without material)
            response = await model.generate_content_async(prompt)
            text = response.text or ""
        
        if not text.strip():
            text = (
                "Hello! I'm Knoledg's AI tutor, ready to help you explore this material. Feel free to ask a specific question, request a summary, or say what you need help understanding."
            )

        return {"answer": text}

    except Exception as e:
        logger.error(f"Error in answer_with_file: {str(e)}")
        # Provide a fallback markdown response
        fallback_answer = f"""# Technical Difficulty

I apologize, but I'm experiencing technical difficulties at the moment. 

## Your Question
{question}

## What I Can Tell You
I'm here to help answer your question based on your study material.

## Next Steps
Please try asking your question again in a few moments. If the problem persists, you may want to:

- **Rephrase** your question more simply
- **Break** complex questions into smaller parts  
- **Contact support** if the issue continues

I'm here to help once the technical issue is resolved! 🤖"""
        
        return {"answer": fallback_answer}
