
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date

from app.db.deps import get_db
from app.api.v1.routes.auth.auth import get_current_user
from app.core.response import success_response, ResponseModel
from app.models.subscription import Subscription
from app.models.plan import Plan as PlanModel
from app.models.user import User
from app.utils.enums import SubscriptionStatus
from app.services.payment_service.refunds import (
    process_immediate_cancel_with_optional_refund,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

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

    # 2) Perform cancellation
    if immediate:
        # End the current period now
        sub.period_end = today
        sub.status = SubscriptionStatus.cancelled

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

        msg = "Subscription cancelled immediately and downgraded to Freemium." + refund_msg
    else:
        # Schedule cancellation at period_end (do NOT downgrade user yet)
        sub.status = SubscriptionStatus.cancelled
        msg = (
            f"Subscription will not renew after {sub.period_end.isoformat()}. "
            "You will retain access until then."
        )

    # 3) Persist changes
    db.add(sub)
    # Only add current_user if we changed their plan (immediate cancel)
    if immediate:
        db.add(current_user)
    await db.commit()

    data = {}
    if immediate and request_refund:
        # Include a minimal payload about the refund decision for clients.
        data["refund"] = {
            "requested": True,
            "eligible": bool(refund_details.eligible) if refund_details else False,
            "reason": refund_details.reason if refund_details else "",
        }
    return success_response(msg=msg, data=data or None)
