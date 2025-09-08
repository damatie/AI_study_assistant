# Standard library imports
import json
import logging

# Local imports
from app.core.genai_client import get_gemini_model

# Initialize logger
logger = logging.getLogger(__name__)

# Initialize Gemini model
model = get_gemini_model()


async def generate_assessment_questions(
    text: str, 
    operation_type: str, 
    num_questions: int = 5, 
    difficulty: str = "medium"
) -> dict:
    """Generate assessment questions using Gemini LLM based on operation type.

    Args:
        text: The content to analyze
        operation_type: Type of analysis to perform (generate_mc, generate_tf, generate_sa, generate_fc)
        num_questions: Number of questions to generate (default: 5)
        difficulty: Difficulty level of questions (easy, medium, hard)

    Returns:
        dict: Generated questions in the appropriate format

    Raises:
        ValueError: If operation_type is invalid
        Exception: If LLM response processing fails
    """

    Critical = f"""

        **MATHEMATICAL NOTATION REQUIREMENTS (CRITICAL):**
        
        When including ANY mathematical content in questions, options, answers, explanations, or feedback, you MUST use proper LaTeX formatting within double dollar signs ($$...$$):
        
        **Fractions:** Always use \\\\frac{{numerator}}{{denominator}}
        - Instead of: x/12 → Use: $$\\\\frac{{x}}{{12}}$$
        - Instead of: 2x/15 → Use: $$\\\\frac{{2x}}{{15}}$$
        - Instead of: (x+1)/2x → Use: $$\\\\frac{{x+1}}{{2x}}$$
        
        **Variables and Expressions:**
        - Single variables: $$x$$, $$y$$, $$a$$, $$b$$
        - Expressions: $$2x + 3$$, $$x^2 - 4$$
        - Equations: $$x = \\\\frac{{-b \\\\pm \\\\sqrt{{b^2 - 4ac}}}}{{2a}}$$
        
        **Common Mathematical Elements:**
        - Exponents: $$x^2$$, $$e^{{-x}}$$, $$2^n$$
        - Square roots: $$\\\\sqrt{{x}}$$, $$\\\\sqrt{{x^2 + y^2}}$$
        - Subscripts: $$x_1$$, $$a_n$$, $$v_0$$
        - Greek letters: $$\\\\alpha$$, $$\\\\beta$$, $$\\\\pi$$, $$\\\\theta$$
        - Summation: $$\\\\sum_{{i=1}}^{{n}} x_i$$
        - Integration: $$\\\\int_0^1 f(x) dx$$
        - Limits: $$\\\\lim_{{x \\\\to 0}} \\\\frac{{\\\\sin x}}{{x}}$$
        
        **Complex Expressions:**
        - Matrices: $$\\\\begin{{pmatrix}} a & b \\\\\\\\\\\\ c & d \\\\end{{pmatrix}}$$
        - Systems: $$\\\\begin{{cases}} x + y = 5 \\\\\\\\\\\\ 2x - y = 1 \\\\end{{cases}}$$
        - Chemical equations: $$C_{{6}}H_{{12}}O_{{6}} + 6O_{{2}} \\\\longrightarrow 6CO_{{2}} + 6H_{{2}}O + ATP$$
        
        **ADDITIONAL GUIDELINES:**
        - **NEVER use plain text for mathematical expressions.** Every mathematical element must be properly formatted in LaTeX.
        - For multiple choice questions, don't use options like A, B, C, D as the correct answers alone for clarity
        - Apply LaTeX formatting to ALL mathematical content in questions, options, answers, explanations, and feedback
        
                """

    prompts = {
        
        "generate_mc": f"""
        Create {num_questions} multiple-choice questions (with 4 options each, one correct answer + explanation)
        from the following content:
        {text}

        Difficulty Level: {difficulty}

       {Critical}
        
        OUTPUT FORMAT:
        Return a JSON with the following structure:
        {{
            "questions": [
                {{
                    "question": "Question text",
                    "options": ["Option A", "Option B", "Option C", "Option D"],
                    "correct_answer": "Correct option",
                    "explanation": "Why this is the correct answer"
                }}
            ]
        }}
        """,
        "generate_tf": f"""
        Create {num_questions} true/false questions (with correct answer + explanation)
        from the following content:
        {text}

        Difficulty Level: {difficulty}

        {Critical}
        
        
        OUTPUT FORMAT:
        Return a JSON with the following structure:
        {{
            "questions": [
                {{
                    "question": "Question text",
                    "correct_answer": true or false,
                    "explanation": "Why this is the correct answer"
                }}
            ]
        }}
        """,
        "generate_sa": f"""
        Generate {num_questions} short-answer (essay) questions only—do NOT provide answers—
        based on the following content:
        {text}

        Difficulty Level: {difficulty}

        {Critical}

        
        OUTPUT FORMAT:
        Return a JSON with the following structure:
        {{
            "questions": [
                {{
                    "question": "Question text"
                }}
            ]
        }}
        """,
        "generate_fc": f"""
        From the content below, generate {num_questions} flash cards, each designed for effective learning and recall.

        {Critical}

        Each flashcard should contain the following key-value pairs:

        * "prompt": This side presents a concise question, term, or cue derived from the content. It should encourage active recall.
        * "correspondingInformation": This side provides the direct answer, definition, or explanation related to the prompt.
        * "hint": Offer a brief clue or partial information that guides the learner towards the 'correspondingInformation' without revealing it entirely. The hint should aid memory retrieval.

        Ensure the 'prompt' and 'correspondingInformation' pairings are clear and directly related to the provided content. The 'hint' should be distinct from both and serve as a helpful intermediary.

        CONTENT:
        {text}

        Difficulty Level: {difficulty}

        OUTPUT FORMAT:
        Return a JSON object with the following structure:
        {{
          "flash_cards": [
            {{
              "prompt": "...",
              "correspondingInformation": "...",
              "hint": "..."
            }},
            ...
          ]
        }}
        """,
    }

    # Check if the operation_type exists in the prompts dictionary
    if operation_type not in prompts:
        raise ValueError(f"Invalid operation type: {operation_type}")

    # Call Gemini with the appropriate prompt
    try:
        response = await model.generate_content_async(prompts[operation_type])
    except Exception as e:
        logger.error(f"Error calling Gemini API in generate_assessment_questions: {str(e)}")
        # Provide fallback questions based on operation type
        if operation_type == "generate_fc":
            return {
                "flash_cards": [
                    {
                        "prompt": "Study the provided material",
                        "correspondingInformation": "Review the content and key concepts from your study material",
                        "hint": "Focus on the main topics and important details"
                    }
                    for _ in range(min(num_questions, 3))  # Limit fallback to 3 questions
                ]
            }
        else:
            return {
                "questions": [
                    {
                        "question": f"Based on the study material, explain the key concepts related to {operation_type.replace('generate_', '').replace('_', ' ')}.",
                        **({"correct_answer": True, "explanation": "This is a fallback question due to technical difficulties."} if operation_type == "generate_tf" else {}),
                        **({"options": ["Option A", "Option B", "Option C", "Option D"], "correct_answer": "Option A", "explanation": "This is a fallback question due to technical difficulties."} if operation_type == "generate_mc" else {})
                    }
                    for _ in range(min(num_questions, 3))  # Limit fallback to 3 questions
                ]
            }

    # Parse the JSON response
    try:
        # Extract JSON from response
        response_text = response.text
        json_str = response_text
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            # Handle case where JSON is wrapped in ``` without language specifier
            json_str = response_text.split("```")[1].strip()

        result = json.loads(json_str)

        # Validate essential structure based on operation_type
        if operation_type == "generate_fc" and "flash_cards" not in result:
            raise ValueError("Response missing 'flash_cards' key")
        elif (
            operation_type != "generate_fc"
            and operation_type != "summarize"
            and "questions" not in result
        ):
            raise ValueError("Response missing 'questions' key")

        return result
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        logger.error(f"Response was: {response.text}")
        raise Exception(f"Failed to parse JSON from LLM response: {e}")
    except Exception as e:
        logger.error(f"Error processing response: {e}")
        logger.error(f"Response was: {response.text}")
        raise Exception(f"Failed to process LLM response: {e}")
