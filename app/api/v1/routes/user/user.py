# app/routes/auth.py

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.v1.routes.auth.auth import get_current_user
from app.models.subscription import Subscription
from app.schemas.auth.auth_schema import UpdatePasswordRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.deps import get_db
from app.models.user import User
from app.core.security import (
    get_password_hash,
    verify_password,
)
from app.models.user import User
from app.models.plan import Plan
from app.core.security import (
    get_password_hash,
)
from app.core.response import error_response, success_response, ResponseModel
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.utils.enums import SubscriptionStatus

router = APIRouter(prefix="/user", tags=["user"])

# Profile
@router.get(
    "/profile",
    response_model=ResponseModel,
)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Load the user's plan
    plan = await db.get(Plan, current_user.plan_id)
    if not plan:
        raise HTTPException(status_code=500, detail="User plan not found")

    # 2. Determine current subscription period
    today = date.today()
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == current_user.id,
            Subscription.status == SubscriptionStatus.active,
            Subscription.period_start <= today,
            Subscription.period_end > today,
        )
    )
    sub = result.scalars().first()

    # 3. Load or create usage tracking for this billing cycle (only two args)
    usage = await get_or_create_usage(current_user, db)

    # 4. Compute amount in currency units (e.g. GBP)
    amount = plan.price_pence / 100  # if price_pence is in pence

    # 5. Build the profile payload
    data = {
        "id":                str(current_user.id),
        "email":             current_user.email,
        "first_name":        current_user.first_name,
        "last_name":         current_user.last_name,
        "role":              current_user.role.value,
        "plan_name":         plan.name,
        "amount":            amount,
        "is_active":         current_user.is_active,
        "is_email_verified": current_user.is_email_verified,
        "subscription_status": (
            sub.status.value if sub else SubscriptionStatus.expired.value
        ),
        "subscription_start": (
            sub.period_start.isoformat() if sub and plan.price_pence != 0 else None
        ),
        "subscription_end": (
            sub.period_end.isoformat() if sub and plan.price_pence != 0 else None
        ),
        "usage_tracking": {
            "uploads_count":         usage.uploads_count,
            "uploads_limit":         plan.monthly_upload_limit,
            "assessments_count":     usage.assessments_count,
            "assessments_limit":     plan.monthly_assessment_limit,
            "asked_questions_count": usage.asked_questions_count,
            "questions_limit":       plan.monthly_ask_question_limit,
        }
    }

    return success_response(msg="Profile fetched", data=data)

# Update password
@router.put(
    "/update-password",
    response_model=ResponseModel,
)
async def update_password(
    req: UpdatePasswordRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Change the current user’s password.
    """
    # 1. Verify current password
    if not verify_password(req.current_password, current_user.password_hash):
        return error_response(
            msg="Current password is incorrect",
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # 2. Update hash
    current_user.password_hash = get_password_hash(req.new_password)
    db.add(current_user)
    await db.commit()

    return success_response(msg="Password updated successfully")
