import json
import logging
from typing import List, Dict, Any, Optional

from app.core.genai_client import get_gemini_model
from app.models.assessment_session import Difficulty

logger = logging.getLogger(__name__)


SYSTEM_INSTRUCTIONS = (
    "You are an expert study coach. Generate high-quality flash cards as pure JSON only. "
    "Do not include markdown, backticks, or commentary. The JSON shape must be: {\n"
    "  \"title\": string,\n"
    "  \"topic\": string | null,\n"
    "  \"difficulty\": one of ['easy','medium','hard'],\n"
    "  \"cards\": [\n"
    "    {\n"
    "      \"prompt\": string (<= 160 chars),\n"
    "      \"correspondingInformation\": string (2-6 sentences),\n"
    "      \"hint\": string | null\n"
    "    }\n"
    "  ]\n"
    "}\n"
    "Prompts must be concise; information must be accurate and self-contained."
)


def _coerce_list(obj: Any) -> List[Any]:
    if isinstance(obj, list):
        return obj
    if obj is None:
        return []
    return [obj]


def _normalize_cards(raw_cards: Any) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for item in _coerce_list(raw_cards):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt", "")).strip()
        info = str(item.get("correspondingInformation", "")).strip()
        hint = item.get("hint")
        if hint is not None:
            hint = str(hint).strip() or None

        if prompt and info:
            cards.append({
                "prompt": prompt[:160],
                "correspondingInformation": info,
                "hint": hint
            })
    return cards


async def generate_flash_cards_from_context(
    *,
    material_title: Optional[str],
    cleaned_markdown_context: str,
    difficulty: Difficulty,
    num_cards: int,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    model = get_gemini_model()
    user_title = material_title or "Flash Cards"

    prompt = [
        {"role": "user", "parts": [SYSTEM_INSTRUCTIONS]},
        {"role": "user", "parts": [
            f"Create {num_cards} flash cards at {difficulty} difficulty.\n"
            f"Title (if helpful): {user_title}.\n"
            f"Topic: {topic or 'general'}.\n"
            "Use the following study material context to ensure accuracy and specificity:\n\n"
            f"{cleaned_markdown_context}"
        ]},
    ]

    response = await model.generate_content_async(prompt)
    text = (response.text or "").strip()

    # Strip code fences or markdown if present
    if text.startswith("```"):
        text = text.strip('`')
        # Attempt to find JSON inside
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            text = text[start:end+1]

    try:
        data = json.loads(text)
    except Exception:
        # Fallback: try to extract a JSON object
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start:end+1])
        else:
            logger.error("Failed to parse generated flash cards JSON")
            raise

    title = str(data.get("title") or user_title).strip() or user_title
    out_topic = data.get("topic")
    if out_topic is not None:
        out_topic = str(out_topic).strip() or None
    out_difficulty = str(data.get("difficulty") or difficulty)
    if out_difficulty not in {"easy","medium","hard"}:
        out_difficulty = difficulty

    cards = _normalize_cards(data.get("cards"))
    if len(cards) < 3:
        raise ValueError("Generated too few valid cards")

    return {
        "title": title,
        "topic": out_topic,
        "difficulty": out_difficulty,
        "cards": cards[: num_cards],
    }
