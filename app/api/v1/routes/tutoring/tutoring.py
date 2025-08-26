# Standard library imports
import logging
import uuid
from typing import Optional

# Third-party imports
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

# Local imports
from app.core.response import success_response, error_response, ResponseModel
from app.db.deps import get_db
from app.models.plan import Plan as PlanModel
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.api.v1.routes.auth.auth import get_current_user
from app.services.track_subscription_service.handle_track_subscription import (
    renew_subscription_for_user,
)
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.services.ai_service.tutoring_service import chat_with_ai
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
    """Ask a question to the AI tutor with optional study material context"""
    # 1. Ensure subscription is current
    sub = await renew_subscription_for_user(current_user, db)
    if sub.status != SubscriptionStatus.active:
        return error_response("Your subscription is not active", 403)

    # 2. Load plan & usage
    plan = await db.get(PlanModel, current_user.plan_id)
    usage = await get_or_create_usage(current_user, db)
    if usage.asked_questions_count >= plan.monthly_ask_question_limit:
        return error_response(
            msg="You've reached your monthly question‑asking limit. Upgrade to ask more.",
            data={"error_type":"MONTHLY_QUESTION_LIMIT_EXCEEDED","current_plan":plan.name},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 3. Build context if provided
    ctx = ""
    if request.context_id:
        material = await db.get(StudyMaterialModel, request.context_id)
        if not material or material.user_id != current_user.id:
            return error_response(
                msg="Study material not found or access denied", status_code=404
            )
        # Use processed markdown content for better tutoring context
        ctx = material.processed_content or material.content or ""
        
        # Clean markdown for better AI context if we have markdown content
        if material.processed_content:
            from app.services.material_processing_service.markdown_parser import clean_markdown_for_context
            ctx = clean_markdown_for_context(material.processed_content)

    # 4. Generate the answer
    answer = await chat_with_ai(request.question, ctx)

    # 5. Increment the asked_questions_count and save
    usage.asked_questions_count += 1
    db.add(usage)
    await db.commit()

    # 6. Return the response
    return success_response(msg="Answer generated", data=answer)
