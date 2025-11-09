import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth.auth import get_current_user
from app.core.response import success_response, error_response, ResponseModel
from app.core.plan_limits import plan_limit_error
from app.db.deps import get_db, AsyncSessionLocal
from app.models.flash_card_set import FlashCardSet
from app.models.study_material import StudyMaterial
from app.models.plan import Plan as PlanModel
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.schemas.flash_cards import FlashCardGenerateRequest, FlashCardSetCreate
from app.utils.enums import FlashCardStatus
from app.services.material_processing_service.gemini_helpers import (
    get_gemini_file_reference_for_material,
)
from app.services.flash_cards.generator import generate_flash_cards_from_file


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/flash-cards", tags=["flash-cards"])


def _ensure_hints(cards: list[dict]) -> list[dict]:
    """Guarantee a non-empty hint for each card based on information or prompt."""
    out: list[dict] = []
    for c in cards or []:
        if not isinstance(c, dict):
            continue
        prompt = str(c.get("prompt", "")).strip()
        info = str(c.get("correspondingInformation", "")).strip()
        hint = c.get("hint")
        if hint is None or not str(hint).strip():
            # derive basic hint from first sentence of info, else prompt
            # keep it short
            base = info.split(". ")[0] if info else prompt
            hint = (base or "").strip()[:100]
        out.append({
            "prompt": prompt,
            "correspondingInformation": info,
            "hint": hint,
        })
    return out






@router.get("/by-material/{material_id}/all", response_model=ResponseModel)
async def list_by_material(
    material_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all sets for a given study material, newest first.

    Method/Path: GET /api/v1/flash-cards/by-material/{material_id}/all
    Auth: required and must own the material
    Returns: array of summary items (id, title, topic, difficulty, status, count)
    """
    stmt = (
        select(FlashCardSet)
        .where(
            FlashCardSet.user_id == current_user.id,
            FlashCardSet.material_id == material_id,
        )
        .order_by(FlashCardSet.created_at.desc())
    )
    res = await db.execute(stmt)
    rows: List[FlashCardSet] = list(res.scalars().all())
    data = [
        {
            "id": row.id,
            "title": row.title,
            "topic": row.topic,
            "difficulty": row.difficulty,
            "status": getattr(row, 'status', None),
            "count": len(row.cards_payload or []),
        }
        for row in rows
    ]
    return success_response("Sets fetched", data=data)


@router.get("/{set_id}", response_model=ResponseModel)
async def get_set(
    set_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a full flash-card set with all cards.

    Method/Path: GET /api/v1/flash-cards/{set_id}
    Auth: required and must own the set
    Returns: full set details including cards array. 404 if not found/owned.
    """
    row = await db.get(FlashCardSet, set_id)
    if not row or row.user_id != current_user.id:
        return error_response("Flash card set not found", 404)

    out = {
        "id": row.id,
        "user_id": row.user_id,
        "material_id": row.material_id,
        "title": row.title,
        "topic": row.topic,
        "difficulty": row.difficulty,
        "status": getattr(row, 'status', None),
    "cards": _ensure_hints(row.cards_payload or []),
    }
    return success_response("Set fetched", data=out)


@router.post("/", response_model=ResponseModel)
async def create_set(
    payload: FlashCardSetCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a flash-card set manually from provided cards.

    Method/Path: POST /api/v1/flash-cards/
    Body: FlashCardSetCreate { title, topic?, difficulty, material_id?, cards[] }
    Notes:
      - If material_id is provided, verifies you own the material.
      - Created sets are marked status=completed since cards are provided.
    Returns: { id } of the new set.
    """
    # If provided, confirm material ownership
    if payload.material_id:
        material = await db.get(StudyMaterial, payload.material_id)
        if not material or material.user_id != current_user.id:
            return error_response("Study material not found or access denied", 404)

    # Load plan & usage for limit checks
    plan = await db.get(PlanModel, current_user.plan_id)
    usage = await get_or_create_usage(current_user, db)

    # Enforce monthly flash-card sets limit
    if plan and usage and hasattr(usage, 'flash_card_sets_count') and hasattr(plan, 'monthly_flash_cards_limit'):
        if plan.monthly_flash_cards_limit and usage.flash_card_sets_count >= plan.monthly_flash_cards_limit:
            return plan_limit_error(
                message="You've reached your monthly flash cards limit. Upgrade to continue.",
                error_type="MONTHLY_FLASHCARDS_LIMIT_EXCEEDED",
                current_plan=plan.name,
                metric="monthly_flash_card_sets",
                used=usage.flash_card_sets_count,
                limit=plan.monthly_flash_cards_limit,
            )

    # Enforce per-deck cards limit if provided in plan
    if plan and hasattr(plan, 'max_cards_per_deck') and plan.max_cards_per_deck:
        if len(payload.cards or []) > plan.max_cards_per_deck:
            return plan_limit_error(
                message=f"You can include at most {plan.max_cards_per_deck} cards per deck on your current plan. Please upgrade to create larger decks.",
                error_type="CARDS_PER_DECK_LIMIT_EXCEEDED",
                current_plan=plan.name,
                metric="cards_per_deck",
                actual=len(payload.cards or []),
                limit=plan.max_cards_per_deck,
            )

    row = FlashCardSet(
        user_id=current_user.id,
        material_id=payload.material_id,
        title=payload.title,
        topic=payload.topic,
        difficulty=payload.difficulty,
        cards_payload=[c.model_dump() for c in payload.cards],
        status=FlashCardStatus.completed,
    )
    db.add(row)
    # Increment usage on success
    if usage and hasattr(usage, 'flash_card_sets_count'):
        usage.flash_card_sets_count += 1
        db.add(usage)
    await db.commit()
    await db.refresh(row)

    return success_response("Set created", data={"id": row.id})


@router.delete("/{set_id}", response_model=ResponseModel)
async def delete_set(
    set_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a flash-card set you own.

    Method/Path: DELETE /api/v1/flash-cards/{set_id}
    Auth: required and must own the set
    Returns: { id } of the deleted set or 404 if not found/owned.
    """
    row = await db.get(FlashCardSet, set_id)
    if not row or row.user_id != current_user.id:
        return error_response("Flash card set not found", 404)
    await db.delete(row)
    await db.commit()
    return success_response("Deleted", data={"id": str(set_id)})




@router.post("/generate", response_model=ResponseModel)
async def generate_set(
    body: FlashCardGenerateRequest,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start asynchronous generation of a flash-card set.

    Method/Path: POST /api/v1/flash-cards/generate
    Body: FlashCardGenerateRequest { material_id?, title?, topic?, difficulty?, num_cards? }
    Flow:
        - Creates a placeholder set with status=processing and no cards.
        - Schedules a background task to generate cards and update the set to
          status=completed (or status=failed on error).
        - Returns immediately with { id, status: "processing" }.
    Auth: required; if material_id is provided, you must own the material.
    """
    # Validate ownership if material used
    material_title = None
    if body.material_id:
        material = await db.get(StudyMaterial, body.material_id)
        if not material or material.user_id != current_user.id:
            return error_response("Study material not found or access denied", 404)
        material_title = material.title

    # Load plan & usage for limit checks
    plan = await db.get(PlanModel, current_user.plan_id)
    usage = await get_or_create_usage(current_user, db)

    # Enforce monthly flash-card sets limit
    if plan and usage and hasattr(usage, 'flash_card_sets_count') and hasattr(plan, 'monthly_flash_cards_limit'):
        if plan.monthly_flash_cards_limit and usage.flash_card_sets_count >= plan.monthly_flash_cards_limit:
            return plan_limit_error(
                message="You've reached your monthly flash cards limit. Upgrade to continue.",
                error_type="MONTHLY_FLASHCARDS_LIMIT_EXCEEDED",
                current_plan=plan.name,
                metric="monthly_flash_card_sets",
                used=usage.flash_card_sets_count,
                limit=plan.monthly_flash_cards_limit,
            )

    # Enforce per-deck cards limit if provided in plan
    if plan and hasattr(plan, 'max_cards_per_deck') and plan.max_cards_per_deck:
        if body.num_cards and body.num_cards > plan.max_cards_per_deck:
            return plan_limit_error(
                message=f"You can request at most {plan.max_cards_per_deck} cards per deck on your current plan. Please upgrade to create larger decks.",
                error_type="CARDS_PER_DECK_LIMIT_EXCEEDED",
                current_plan=plan.name,
                metric="cards_per_deck",
                actual=body.num_cards,
                limit=plan.max_cards_per_deck,
            )

    # Create placeholder row with processing status
    row = FlashCardSet(
        user_id=current_user.id,
        material_id=body.material_id,
        title=body.title or material_title or "Flash Cards",
        topic=body.topic,
        difficulty=body.difficulty,
        cards_payload=[],
        status=FlashCardStatus.processing,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Schedule background generation job
    background_tasks.add_task(
        _bg_generate_flash_cards,
        set_id=row.id,
        user_id=current_user.id,
        body=body,
    )

    # Return immediately with processing status
    return success_response(
        "Generation started",
        data={"id": row.id, "status": FlashCardStatus.processing},
    )


async def _bg_generate_flash_cards(*, set_id: uuid.UUID, user_id: uuid.UUID, body: FlashCardGenerateRequest):
    """Background job to generate flash cards and update DB status."""
    async with AsyncSessionLocal() as session:
        try:
            # Load material
            material_title = None
            gemini_file = None
            
            if body.material_id:
                material = await session.get(StudyMaterial, body.material_id)
                if material and material.user_id == user_id:
                    material_title = material.title
                    # Fetch or refresh Gemini Files API URI for this material
                    gemini_file = await get_gemini_file_reference_for_material(material, session)

            # Generate using Files API
            gen = await generate_flash_cards_from_file(
                material_title=body.title or material_title,
                gemini_file=gemini_file,
                difficulty=body.difficulty,
                num_cards=body.num_cards,
                topic=body.topic,
            )

            row = await session.get(FlashCardSet, set_id)
            if not row:
                return
            row.title = gen.get("title", row.title)
            row.topic = gen.get("topic", row.topic)
            row.cards_payload = gen.get("cards", [])
            row.status = FlashCardStatus.completed
            # Increment usage after successful generation
            from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
            from app.models.user import User as UserModel
            user = await session.get(UserModel, user_id)
            if user:
                usage = await get_or_create_usage(user, session)
                if hasattr(usage, 'flash_card_sets_count'):
                    usage.flash_card_sets_count += 1
                    session.add(usage)
            await session.commit()
        except Exception:
            # mark failed
            row = await session.get(FlashCardSet, set_id)
            if row:
                row.status = FlashCardStatus.failed
                await session.commit()
