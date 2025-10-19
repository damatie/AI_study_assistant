from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import success_response
from app.db.deps import get_db
from app.models.plan import Plan

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("/all")
async def list_plans(
    currency: Optional[str] = Query(None, description="Optional currency hint"),
    db: AsyncSession = Depends(get_db),
):
    """Return all plans with prices array."""
    result = await db.execute(select(Plan).options(selectinload(Plan.prices)))
    plans = result.scalars().all()

    data = []
    for p in plans:
        price_rows = []
        for pr in getattr(p, "prices", []) or []:
            price_rows.append({
                "currency": pr.currency,
                "provider": pr.provider.value if hasattr(pr.provider, "value") else pr.provider,
                "price_minor": pr.price_minor,
                "provider_price_id": pr.provider_price_id,
                "scope_type": pr.scope_type.value if hasattr(pr.scope_type, "value") else pr.scope_type,
                "scope_value": pr.scope_value,
                "billing_interval": pr.billing_interval.value if hasattr(pr.billing_interval, "value") else pr.billing_interval,
            })
        
        data.append({
            "id": str(p.id),
            "sku": p.sku,
            "name": p.name,
            "limits": {
                "monthly_upload_limit": p.monthly_upload_limit,
                "monthly_assessment_limit": p.monthly_assessment_limit,
                "questions_per_assessment": p.questions_per_assessment,
                "monthly_flash_cards_limit": p.monthly_flash_cards_limit,
                "max_cards_per_deck": p.max_cards_per_deck,
            },
            "prices": price_rows,
        })

    return success_response("Plans retrieved", data=data)
