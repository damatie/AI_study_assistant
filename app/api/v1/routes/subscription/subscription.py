
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, datetime, timezone

from app.db.deps import get_db
from app.utils.datetime_utils import get_current_utc_datetime
from app.api.v1.routes.auth.auth import get_current_user
from app.core.response import success_response, ResponseModel
from app.core.config import settings
from app.core.logging_config import get_logger
from app.models.subscription import Subscription
from app.models.plan import Plan as PlanModel
from app.models.user import User
from app.utils.enums import SubscriptionStatus, PaymentProvider
from app.services.payment_service.refunds import (
    process_immediate_cancel_with_optional_refund,
)
from app.services.payments.subscription_service import SubscriptionService

logger = get_logger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get(
    "/current",
    response_model=ResponseModel,
)
async def get_current_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get the user's current active subscription with billing details.
    Returns subscription info including billing_interval, auto_renew status, and next billing date.
    """
    now = get_current_utc_datetime()
    q = await db.execute(
        select(Subscription)
        .options(select(Subscription).join(Subscription.plan))
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status == SubscriptionStatus.active,
            Subscription.period_start <= now,
            Subscription.period_end > now
        )
    )
    sub = q.scalars().first()
    
    if not sub:
        return success_response(
            msg="No active subscription",
            data={"has_subscription": False}
        )
    
    # Load plan details
    plan_q = await db.execute(select(PlanModel).where(PlanModel.id == sub.plan_id))
    plan = plan_q.scalars().first()
    
    data = {
        "has_subscription": True,
        "subscription_id": str(sub.id),
        "plan_name": plan.name if plan else "Unknown",
        "plan_sku": plan.sku if plan else None,
        "billing_interval": sub.billing_interval.value if sub.billing_interval else "month",
        "auto_renew": sub.auto_renew if hasattr(sub, 'auto_renew') else True,
        "canceled_at": sub.canceled_at.isoformat() if hasattr(sub, 'canceled_at') and sub.canceled_at else None,
        "period_start": sub.period_start.isoformat() if sub.period_start else None,
        "period_end": sub.period_end.isoformat() if sub.period_end else None,
        "next_billing_date": sub.period_end.isoformat() if sub.auto_renew and sub.period_end else None,
        "status": sub.status.value if hasattr(sub.status, 'value') else str(sub.status),
    }
    
    return success_response(msg="Current subscription retrieved", data=data)


@router.post(
    "/cancel",
    response_model=ResponseModel,
)
async def cancel_subscription(
    immediate: bool = Query(
        False,
        description="If true, cancel now and downgrade immediately; otherwise cancel at period end"
    ),
    request_refund: bool = Query(
        False,
        description="When immediate is true, request refund if eligible (cool-off policy)"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel the user's active subscription.
    - If immediate=False (default), subscription remains active until its period_end, then no renewal.
    - If immediate=True, subscription is ended today and user is downgraded to Freemium at once.
    """
    # 1) Load the active subscription
    now = get_current_utc_datetime()
    q = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status == SubscriptionStatus.active,
            Subscription.period_start <= now,
            Subscription.period_end > now
        )
    )
    sub = q.scalars().first()
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription found")

    # 2) Cancel via new service (handles both Stripe and Paystack)
    service = SubscriptionService()
    try:
        await service.cancel_subscription(
            db=db,
            user_id=current_user.id,
            immediate=immediate
        )
        provider_msg = " Provider subscription cancelled successfully."
    except Exception as e:
        # Log error but don't fail the request
        import logging
        logging.error(f"Failed to cancel provider subscription: {e}")
        provider_msg = " (Note: Provider cancellation may require manual intervention)"

    # 3) Perform local cancellation
    if immediate:
        # End the current period now
        sub.period_end = now
        sub.status = SubscriptionStatus.cancelled
        sub.auto_renew = False
        sub.canceled_at = datetime.now(timezone.utc)

        # Downgrade user to Freemium immediately
        plan_q = await db.execute(select(PlanModel).where(PlanModel.sku == 'FREEMIUM'))
        free_plan = plan_q.scalars().first()
        if not free_plan:
            raise HTTPException(
                status_code=500,
                detail="Freemium plan not configured"
            )
        current_user.plan_id = free_plan.id

        refund_msg = ""
        refund_details = None
        if request_refund:
            refund_details, refund_msg = await process_immediate_cancel_with_optional_refund(
                db, current_user.id, request_refund=True
            )

        msg = "Subscription cancelled immediately and downgraded to Freemium." + refund_msg + provider_msg
    else:
        # Schedule cancellation at period_end (do NOT downgrade user yet)
        # Keep status as "active" - user still has access until period_end
        # sub.status stays active (don't change it)
        sub.auto_renew = False
        sub.canceled_at = datetime.now(timezone.utc)
        msg = (
            f"Subscription will not renew after {sub.period_end.isoformat()}. "
            f"You will retain access until then.{provider_msg}"
        )

    # 4) Persist changes
    db.add(sub)
    # Only add current_user if we changed their plan (immediate cancel)
    if immediate:
        db.add(current_user)
    await db.commit()

    data = {
        "subscription_id": str(sub.id),
        "status": sub.status.value if hasattr(sub.status, 'value') else str(sub.status),
        "auto_renew": sub.auto_renew,
        "canceled_at": sub.canceled_at.isoformat() if sub.canceled_at else None,
        "period_end": sub.period_end.isoformat() if sub.period_end else None,
    }
    if immediate and request_refund:
        # Include a minimal payload about the refund decision for clients.
        data["refund"] = {
            "requested": True,
            "eligible": bool(refund_details.eligible) if refund_details else False,
            "reason": refund_details.reason if refund_details else "",
        }
    return success_response(msg=msg, data=data)


@router.get("/manage-payment", status_code=200)
async def manage_payment(
    return_url: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get payment management portal URL for user's provider.
    
    Returns provider-hosted portal URL:
    - Stripe: Customer Portal (update card, invoices, billing)
    - Paystack: Manage subscription link
    
    Args:
        return_url: URL to redirect after portal session (query param)
        current_user: Authenticated user
        db: Database session
    
    Returns:
        { portal_url: str }
    
    Raises:
        404: No active subscription found
        400: Missing provider data or unsupported provider
    """
    try:
        subscription_service = SubscriptionService()
        portal_url = await subscription_service.get_payment_portal_url(
            db=db,
            user_id=current_user.id,
            return_url=return_url,
        )
        
        return success_response(
            msg="Payment portal URL generated",
            data={"portal_url": portal_url}
        )
    
    except ValueError as e:
        # Handle "No active subscription" or missing provider data
        if "No active subscription" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"Failed to get payment portal for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate payment portal URL"
        )
