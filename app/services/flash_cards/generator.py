import json
import logging
import re
from typing import List, Dict, Any, Optional

from app.core.genai_client import get_gemini_model
from app.models.assessment_session import Difficulty
from app.services.material_processing_service.gemini_files import (
    GeminiFileMetadata,
    generate_from_gemini_file,
)

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
    "      \"hint\": string (a concise clue, <= 100 chars)\n"
    "    }\n"
    "  ]\n"
    "}\n"
    "Every card must include a helpful, concise hint. Prompts must be concise; information must be accurate and self-contained."
)


def _coerce_list(obj: Any) -> List[Any]:
    if isinstance(obj, list):
        return obj
    if obj is None:
        return []
    return [obj]


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip()) if text else []
    return parts[0] if parts else ""


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
            # Fallback hint if missing/empty: derive from first sentence of info, else from prompt
            if not hint:
                candidate = _first_sentence(info) or prompt
                hint = (candidate[:100]).strip()
            cards.append({
                "prompt": prompt[:160],
                "correspondingInformation": info,
                "hint": hint
            })
    return cards


async def generate_flash_cards_from_file(
    *,
    material_title: Optional[str],
    gemini_file: Optional[GeminiFileMetadata],
    difficulty: Difficulty,
    num_cards: int,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate flash cards from a Gemini file URI or standalone if no file provided.
    Uses the same prompt structure and parsing as generate_flash_cards_from_context.
    """
    user_title = material_title or "Flash Cards"
    
    # Build prompt (same structure as context-based)
    prompt_text = f"""{SYSTEM_INSTRUCTIONS}

Create {num_cards} flash cards at {difficulty} difficulty.
Title (if helpful): {user_title}.
Topic: {topic or 'general'}.
"""
    
    if gemini_file:
        prompt_text += "Use the attached study material to ensure accuracy and specificity."

        response_text = await generate_from_gemini_file(
            file_uri=gemini_file.uri,
            prompt=prompt_text,
            mime_type=gemini_file.mime_type or "application/pdf",
        )
    else:
        # Generate without file context (for manual/topic-based cards)
        prompt_text += f"Create general flash cards on the topic: {topic or user_title}."
        model = get_gemini_model()
        response = await model.generate_content_async(prompt_text)
        response_text = response.text
    
    text = response_text.strip()
    
    # Strip code fences or markdown if present (same logic as context-based)
    if text.startswith("```"):
        text = text.strip('`')
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
            logger.error("Failed to parse generated flash cards JSON from file")
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
        raise ValueError("Generated too few valid cards from file")

    return {
        "title": title,
        "topic": out_topic,
        "difficulty": out_difficulty,
        "cards": cards[: num_cards],
    }
