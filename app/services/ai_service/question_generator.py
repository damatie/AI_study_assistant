"""AI service for generating context-aware study questions using Gemini."""

import json
import logging
from typing import List

from app.core.genai_client import get_gemini_model

logger = logging.getLogger(__name__)


async def generate_suggested_questions(content: str, title: str = "Material") -> List[str]:
    """Generate 4 context-aware study questions using Gemini.
    
    Args:
        content: The study material content (overview markdown preferred)
        title: Material title for context
        
    Returns:
        List of exactly 4 question strings
    """
    model = get_gemini_model()
    
    # Truncate content to avoid token limits while keeping context
    max_chars = 8000
    truncated = content[:max_chars] if len(content) > max_chars else content
    
    prompt = f"""You are an expert educator. Based on this study material, generate exactly 4 thoughtful questions that encourage deeper understanding.

MATERIAL TITLE: {title}

MATERIAL CONTENT:
{truncated}

REQUIREMENTS:
- Generate EXACTLY 4 questions
- Questions should be specific to this material's content
- Vary question types: conceptual understanding, application, analysis, synthesis
- Each question should be 10-20 words
- Avoid yes/no questions
- Don't mention the material title in questions - use "this material" or "the content"
- Focus on key concepts, relationships, processes, and practical applications

Return ONLY a JSON array of 4 strings, no other text:
["Question 1?", "Question 2?", "Question 3?", "Question 4?"]
"""
    
    try:
        response = await model.generate_content_async(prompt)
        text = (response.text or "").strip()
        
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.strip('`')
            if text.startswith("json"):
                text = text[4:].strip()
        
        # Extract JSON array
        start = text.find('[')
        end = text.rfind(']')
        if start != -1 and end != -1 and end > start:
            text = text[start:end+1]
        
        questions = json.loads(text)
        
        # Validate
        if not isinstance(questions, list):
            raise ValueError("Response is not a list")
        
        # Ensure exactly 4 questions
        questions = [str(q).strip() for q in questions if q][:4]
        
        # Pad with generic questions if needed
        fallbacks = [
            "What are the main concepts covered in this material?",
            "How do the key ideas relate to each other?",
            "What are the practical applications of this content?",
            "What assumptions or limitations are discussed?"
        ]
        
        while len(questions) < 4:
            questions.append(fallbacks[len(questions)])
        
        logger.info(f"Generated {len(questions)} questions for material: {title}")
        return questions[:4]
        
    except Exception as e:
        logger.error(f"Failed to generate questions: {e}", exc_info=True)
        # Return generic fallback questions
        return [
            "What are the main concepts covered in this material?",
            "How do the key ideas relate to each other?",
            "What are the practical applications of this content?",
            "What questions does this material raise for further study?"
        ]
