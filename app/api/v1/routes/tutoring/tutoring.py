# Standard library imports
import logging
import uuid
from typing import Optional, List, Literal

# Third-party imports
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

# Local imports
from app.core.response import success_response, error_response, ResponseModel
from app.core.plan_limits import plan_limit_error
from app.db.deps import get_db
from app.models.plan import Plan as PlanModel
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.api.v1.routes.auth.auth import get_current_user
from app.services.subscription_access import get_active_subscription
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.services.ai_service.tutoring_service import answer_with_file
from app.services.material_processing_service.gemini_helpers import (
    get_gemini_file_reference_for_material,
)
from app.utils.enums import SubscriptionStatus

# Initialize logger
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/questions", tags=["tutoring"])


# Data models
class QuestionRequest(BaseModel):
    question: str = Field(
        ...,
        description="The question to be answered",
        example="What is photosynthesis?",
    )
    context_id: Optional[uuid.UUID] = Field(
        None,
        description="UUID of the study material for context",
        example="3fa85f64-5717-4562-b3fc-2c963f66afa6",
    )
    tone: Literal['academic', 'conversational'] = Field(
        default='academic',
        description="Response tone: 'academic' (default) or 'conversational'",
        examples=['academic', 'conversational']
    )

    @field_validator("question")
    def question_not_empty(cls, v):
        if v is None or v.strip() == "":
            raise ValueError("Question cannot be empty")
        return v.strip()


# Ask questions
@router.post(
    "/ask",
    response_model=ResponseModel,
)
async def ask_question(
    request: QuestionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask a question to the AI tutor with markdown response"""
    # 1. Check if user has active subscription
    sub = await get_active_subscription(current_user, db)
    if not sub:
        return error_response("No active subscription found. Please subscribe to ask questions.", 403)

    # 2. Load plan & usage
    plan = await db.get(PlanModel, current_user.plan_id)
    usage = await get_or_create_usage(current_user, db)
    if usage.asked_questions_count >= plan.monthly_ask_question_limit:
        return plan_limit_error(
            message="You've reached your monthly question‑asking limit. Upgrade to ask more.",
            error_type="MONTHLY_QUESTION_LIMIT_EXCEEDED",
            current_plan=plan.name,
            metric="monthly_questions",
            used=usage.asked_questions_count,
            limit=plan.monthly_ask_question_limit,
        )

    # 3. Get Gemini file URI if material provided
    gemini_file = None
    if request.context_id:
        material = await db.get(StudyMaterialModel, request.context_id)
        if not material or material.user_id != current_user.id:
            return error_response(
                msg="Study material not found or access denied", status_code=404
            )
        
        # Get Gemini Files API reference
        gemini_file = await get_gemini_file_reference_for_material(material, db)

    # 4. Generate answer using Files API
    answer = await answer_with_file(
        question=request.question,
        tone=request.tone,
        gemini_file=gemini_file,
    )

    # 5. Increment the asked_questions_count and save
    usage.asked_questions_count += 1
    db.add(usage)
    await db.commit()

    # 6. Return the response
    return success_response(msg="Answer generated", data=answer)


# Hint endpoint: provide a brief context hint and suggested questions
@router.get(
    "/hint",
    response_model=ResponseModel,
)
async def get_chat_hint(
    context_id: uuid.UUID = Query(..., description="UUID of the study material for context"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a short hint and exactly 4 content‑aware suggested questions derived from the material."""
    # Authorize & load material
    material = await db.get(StudyMaterialModel, context_id)
    if not material or material.user_id != current_user.id:
        return error_response("Study material not found or access denied", 404)

    # Pull best available markdown
    from app.utils.processed_payload import get_detailed, get_overview, get_suggestions, set_suggestions_env
    from app.services.material_processing_service.markdown_parser import clean_markdown_for_context
    from app.services.ai_service.question_generator import generate_suggested_questions
    from sqlalchemy import update

    title = material.title or "Material"
    
    # Try to get pre-generated AI questions from DB (fast path)
    suggestions = get_suggestions(material.processed_content)
    
    if not suggestions or len(suggestions) != 4:
        # Generate AI questions for old materials (generate-once, cache-forever)
        logger.info(f"Generating AI questions for material {context_id} (first time)")
        md = get_detailed(material.processed_content) or get_overview(material.processed_content) or (material.content or "")
        md = clean_markdown_for_context(md)
        
        try:
            # Generate using LLM
            suggestions = await generate_suggested_questions(content=md, title=title)
            
            # Save to DB for next time
            new_payload = set_suggestions_env(material.processed_content, suggestions)
            await db.execute(
                update(StudyMaterialModel)
                .where(StudyMaterialModel.id == context_id)
                .values(processed_content=new_payload)
            )
            await db.commit()
            logger.info(f"Saved AI questions to DB for material {context_id}")
        except Exception as e:
            logger.error(f"Failed to generate AI questions for {context_id}: {e}")
            # Fallback to generic questions on error
            suggestions = [
                "What are the main concepts covered in this material?",
                "How do the key ideas relate to each other?",
                "What are the practical applications of this content?",
                "What questions does this material raise for further study?"
            ]
    else:
        logger.info(f"Returning cached AI questions from DB for material {context_id}")
    
    # Extract hint from first paragraph (DRY - used for all paths)
    md = get_detailed(material.processed_content) or get_overview(material.processed_content) or (material.content or "")
    md = clean_markdown_for_context(md)
    
    lines = [ln.strip() for ln in md.splitlines()]
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    
    para: List[str] = []
    for ln in lines:
        if ln == "":
            if para:
                break
            continue
        if ln.startswith("!") or ln.startswith("|") or ln.startswith("```"):
            continue
        para.append(ln)
    
    first_para = " ".join(para).strip()
    import re
    sentences = re.split(r"(?<=[.!?])\s+", first_para) if first_para else []
    hint = " ".join(sentences[:2]) if sentences else "Explore the core ideas presented in this material."

    return success_response(
        msg="Hint generated",
        data={
            "hint": hint,
            "suggestions": suggestions,
        },
    )
