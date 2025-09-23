import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth.auth import get_current_user
from app.core.response import success_response, error_response, ResponseModel
from app.db.deps import get_db
from app.models.flash_card_set import FlashCardSet
from app.models.study_material import StudyMaterial
from app.schemas.flash_cards import (
    FlashCardSetCreate,
    FlashCardSetOut,
    FlashCardSetListItem,
    FlashCardGenerateRequest,
    FlashCardGenerateResponse,
)
from app.utils.processed_payload import get_detailed, get_overview
from app.services.material_processing_service.markdown_parser import (
    clean_markdown_for_context,
)
from app.services.flash_cards.generator import generate_flash_cards_from_context


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


@router.get("/", response_model=ResponseModel)
async def list_sets(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(FlashCardSet).where(FlashCardSet.user_id == current_user.id).order_by(FlashCardSet.created_at.desc())
    res = await db.execute(stmt)
    rows: List[FlashCardSet] = list(res.scalars().all())
    data = [
        {
            "id": row.id,
            "title": row.title,
            "topic": row.topic,
            "difficulty": row.difficulty,
            "count": len(row.cards_payload or []),
        }
        for row in rows
    ]
    return success_response("Sets fetched", data=data)


@router.get("/by-material/{material_id}", response_model=ResponseModel)
async def get_latest_by_material(
    material_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(FlashCardSet)
        .where(
            FlashCardSet.user_id == current_user.id,
            FlashCardSet.material_id == material_id,
        )
        .order_by(FlashCardSet.created_at.desc())
        .limit(1)
    )
    res = await db.execute(stmt)
    row = res.scalars().first()
    if not row:
        return success_response("No set", data=None)
    return success_response(
        "Set fetched",
        data={
            "id": row.id,
            "title": row.title,
            "topic": row.topic,
            "difficulty": row.difficulty,
            "count": len(row.cards_payload or []),
        },
    )


@router.get("/by-material/{material_id}/all", response_model=ResponseModel)
async def list_by_material(
    material_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
    "cards": _ensure_hints(row.cards_payload or []),
    }
    return success_response("Set fetched", data=out)


@router.post("/", response_model=ResponseModel)
async def create_set(
    payload: FlashCardSetCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # If provided, confirm material ownership
    if payload.material_id:
        material = await db.get(StudyMaterial, payload.material_id)
        if not material or material.user_id != current_user.id:
            return error_response("Study material not found or access denied", 404)

    row = FlashCardSet(
        user_id=current_user.id,
        material_id=payload.material_id,
        title=payload.title,
        topic=payload.topic,
        difficulty=payload.difficulty,
        cards_payload=[c.model_dump() for c in payload.cards],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return success_response("Set created", data={"id": row.id})


@router.post("/generate", response_model=ResponseModel)
async def generate_set(
    body: FlashCardGenerateRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Load/clean context if material provided
    context = ""
    material_title = None
    if body.material_id:
        material = await db.get(StudyMaterial, body.material_id)
        if not material or material.user_id != current_user.id:
            return error_response("Study material not found or access denied", 404)
        material_title = material.title
        detailed_md = get_detailed(material.processed_content)
        overview_md = get_overview(material.processed_content)
        raw_md = detailed_md or overview_md or material.content or ""
        context = clean_markdown_for_context(raw_md)

    # Generate via AI
    gen = await generate_flash_cards_from_context(
        material_title=body.title or material_title,
        cleaned_markdown_context=context,
        difficulty=body.difficulty,
        num_cards=body.num_cards,
        topic=body.topic,
    )

    # Persist
    row = FlashCardSet(
        user_id=current_user.id,
        material_id=body.material_id,
        title=gen["title"],
        topic=gen.get("topic"),
        difficulty=body.difficulty,
        cards_payload=gen["cards"],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return success_response(
        "Generated",
        data={
            "id": row.id,
            "title": row.title,
            "topic": row.topic,
            "difficulty": row.difficulty,
            "cards": _ensure_hints(row.cards_payload or []),
        },
    )


@router.delete("/{set_id}", response_model=ResponseModel, status_code=status.HTTP_200_OK)
async def delete_set(
    set_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(FlashCardSet, set_id)
    if not row or row.user_id != current_user.id:
        return error_response("Flash card set not found", 404)
    await db.delete(row)
    await db.commit()
    return success_response("Deleted", data={"id": str(set_id)})
