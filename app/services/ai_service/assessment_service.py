"""Assessment question generation via Gemini with strict math/JSON rules."""

import json
import logging
from app.core.genai_client import get_gemini_model

logger = logging.getLogger(__name__)
model = get_gemini_model()

CRITICAL_INSTRUCTIONS = r"""
MATH FORMATTING (STRICT):
- Use LaTeX for ALL math. Inline math must use single dollar signs: $...$. Display math must use double dollar signs: $$...$$.
- Fractions must use \frac{numerator}{denominator} (e.g., $\frac{x}{12}$ inline, or $$\frac{x}{12}$$ display).
- Common elements: exponents x^2, subscripts a_n, roots \sqrt{x}, sums \sum_{i=1}^{n} x_i, integrals \int_0^1 f(x) dx, limits \lim_{x\to 0} ...
- Variables and expressions: $x$, $y$, $2x+3$, $x^2-4$.
- Equations: $$x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}$$ when displayed; use inline $...$ inside sentences where appropriate.

JSON ESCAPING (CRITICAL):
- Escape backslashes in JSON strings. A single backslash \ must be written as \\ in JSON.
        Example: write "\\frac{x}{12}" in JSON, which renders as $\frac{x}{12}$.

OUTPUT HYGIENE:
- Return STRICT JSON only: no markdown, no code fences, no extra commentary.
- For multiple-choice, set "correct_answer" to exactly match one of the provided "options" strings (not just "A"/"B").
- Apply LaTeX rules to questions, options, explanations, and any feedback text.
"""


async def generate_assessment_questions(
                text: str,
                operation_type: str,
                num_questions: int = 5,
                difficulty: str = "medium",
) -> dict:
                """Generate assessment questions using Gemini LLM based on operation type."""

                prompts = {
                                "generate_mc": f"""
                Create {num_questions} multiple-choice questions (4 options each, one correct answer + explanation)
                from the following content:
                {text}

                Difficulty: {difficulty}

                {CRITICAL_INSTRUCTIONS}

                OUTPUT FORMAT (STRICT JSON, NO CODE FENCES):
                                Return a JSON with the structure (escape backslashes in LaTeX):
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

                Difficulty: {difficulty}

                {CRITICAL_INSTRUCTIONS}

                OUTPUT FORMAT (STRICT JSON, NO CODE FENCES):
                                Return a JSON with the structure (escape backslashes in LaTeX):
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
                Generate {num_questions} short-answer (essay) questions only — DO NOT provide answers —
                from the following content:
                {text}

                Difficulty: {difficulty}

                {CRITICAL_INSTRUCTIONS}

                OUTPUT FORMAT (STRICT JSON, NO CODE FENCES):
                                Return a JSON with the structure (escape backslashes in LaTeX):
                                {{
                                        "questions": [
                                                {{
                                                        "question": "Question text"
                                                }}
                                        ]
                                }}
                """,
                # NOTE: Flash card generation has moved to the dedicated Flash Cards service.
                }

                if operation_type not in prompts:
                        # Flash cards are no longer supported here; use Flash Cards API instead.
                        raise ValueError(f"Invalid operation type for assessments: {operation_type}")

                try:
                        response = await model.generate_content_async(prompts[operation_type])
                except Exception as e:
                        logger.error(f"Error calling Gemini API in generate_assessment_questions: {str(e)}")
                        base_q = {"question": "Based on the study material, explain the key concepts."}
                        if operation_type == "generate_tf":
                                base_q.update({"correct_answer": True, "explanation": "Fallback due to temporary issue."})
                        if operation_type == "generate_mc":
                                base_q.update({
                                        "options": ["Option A", "Option B", "Option C", "Option D"],
                                        "correct_answer": "Option A",
                                        "explanation": "Fallback due to temporary issue.",
                                })
                        return {"questions": [base_q for _ in range(min(num_questions, 3))]}

                try:
                        response_text = response.text
                        json_str = response_text
                        if "```json" in response_text:
                                json_str = response_text.split("```json")[1].split("```")[0].strip()
                        elif "```" in response_text:
                                json_str = response_text.split("```")[1].strip()

                        result = json.loads(json_str)

                        if operation_type != "summarize" and "questions" not in result:
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
        
