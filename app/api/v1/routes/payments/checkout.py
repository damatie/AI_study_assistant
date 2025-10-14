"""Checkout endpoint - Unified checkout for Stripe and Paystack."""

from __future__ import annotations

from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import success_response, ResponseModel
from app.core.logging_config import get_logger
from app.db.deps import get_db
from app.api.v1.routes.auth.auth import get_current_user
from app.models.user import User
from app.services.payments.subscription_service import SubscriptionService

logger = get_logger(__name__)
router = APIRouter(prefix="/checkout", tags=["payments"])


class CheckoutRequest(BaseModel):
    """Checkout request payload."""
    plan_id: UUID
    billing_interval: str  # 'month' or 'year'
    country_code: Optional[str] = None
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


@router.post("", response_model=ResponseModel)
async def create_checkout(
    req: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a checkout session (Stripe or Paystack based on location).
    
    Request:
        - plan_id: Plan UUID
        - billing_interval: 'month' or 'year'
        - country_code: Optional (e.g., 'NG', 'US', 'GB')
        - success_url: Optional custom success URL
        - cancel_url: Optional custom cancel URL
    
    Response:
        - provider: 'stripe' or 'paystack'
        - checkout_url: URL to redirect user to for payment
        - reference: Transaction reference (session_id or paystack reference)
    """
    try:
        # Validate billing_interval
        if req.billing_interval not in ("month", "year"):
            raise HTTPException(
                status_code=422,
                detail="billing_interval must be 'month' or 'year'"
            )
        
        logger.info(f"Checkout requested: user={current_user.id}, plan={req.plan_id}, interval={req.billing_interval}")
        
        # Create checkout via service
        service = SubscriptionService()
        result = await service.create_checkout(
            db=db,
            user=current_user,
            plan_id=req.plan_id,
            billing_interval=req.billing_interval,
            country_code=req.country_code,
            success_url=req.success_url,
            cancel_url=req.cancel_url,
        )
        
        return success_response(
            msg="Checkout initialized",
            data=result
        )
    
    except ValueError as e:
        logger.error(f"Checkout validation error: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    
    except Exception as e:
        logger.error(f"Checkout creation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create checkout session")
