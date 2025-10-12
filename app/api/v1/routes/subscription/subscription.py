
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, datetime, timezone

from app.db.deps import get_db
from app.api.v1.routes.auth.auth import get_current_user
from app.core.response import success_response, ResponseModel
from app.core.config import settings
from app.models.subscription import Subscription
from app.models.plan import Plan as PlanModel
from app.models.user import User
from app.utils.enums import SubscriptionStatus, PaymentProvider
from app.services.payment_service.refunds import (
    process_immediate_cancel_with_optional_refund,
)

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
    today = date.today()
    q = await db.execute(
        select(Subscription)
        .options(select(Subscription).join(Subscription.plan))
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status == SubscriptionStatus.active,
            Subscription.period_start <= today,
            Subscription.period_end > today
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
    today = date.today()
    q = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status == SubscriptionStatus.active,
            Subscription.period_start <= today,
            Subscription.period_end > today
        )
    )
    sub = q.scalars().first()
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription found")

    # 2) Cancel Stripe/Paystack recurring subscription if exists
    stripe_cancel_msg = ""
    if sub.stripe_subscription_id and not immediate:
        # Cancel Stripe subscription at period end
        try:
            from app.api.v1.routes.payments.stripe_payments import _init_stripe
            import stripe
            
            _init_stripe()
            stripe.Subscription.modify(
                sub.stripe_subscription_id,
                cancel_at_period_end=True
            )
            stripe_cancel_msg = " Stripe subscription will cancel at period end."
        except Exception as e:
            # Log error but don't fail the request
            import logging
            logging.error(f"Failed to cancel Stripe subscription: {e}")
            stripe_cancel_msg = " (Note: Stripe cancellation may require manual intervention)"
    
    elif sub.stripe_subscription_id and immediate:
        # Immediately cancel Stripe subscription
        try:
            from app.api.v1.routes.payments.stripe_payments import _init_stripe
            import stripe
            
            _init_stripe()
            stripe.Subscription.cancel(sub.stripe_subscription_id)
            stripe_cancel_msg = " Stripe subscription cancelled immediately."
        except Exception as e:
            import logging
            logging.error(f"Failed to cancel Stripe subscription immediately: {e}")
            stripe_cancel_msg = " (Note: Stripe cancellation may require manual intervention)"

    # 3) Perform local cancellation
    if immediate:
        # End the current period now
        sub.period_end = today
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

        msg = "Subscription cancelled immediately and downgraded to Freemium." + refund_msg + stripe_cancel_msg
    else:
        # Schedule cancellation at period_end (do NOT downgrade user yet)
        sub.status = SubscriptionStatus.cancelled
        sub.auto_renew = False
        sub.canceled_at = datetime.now(timezone.utc)
        msg = (
            f"Subscription will not renew after {sub.period_end.isoformat()}. "
            f"You will retain access until then.{stripe_cancel_msg}"
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
