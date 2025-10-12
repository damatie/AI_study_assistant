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
from app.models.transaction import Transaction
from sqlalchemy.orm import selectinload
from app.core.security import (
    get_password_hash,
)
from app.core.response import error_response, success_response, ResponseModel
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.utils.enums import SubscriptionStatus, TransactionStatus

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
    # Load plan with prices to compute display amount
    result = await db.execute(
        select(Plan).options(selectinload(Plan.prices)).where(Plan.id == current_user.plan_id)
    )
    plan = result.scalars().first()
    if not plan:
        raise HTTPException(status_code=500, detail="User plan not found")

    # 2. Determine current subscription period
    today = date.today()
    # Consider both active and scheduled-cancel subscriptions as current until period_end
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == current_user.id,
            Subscription.period_start <= today,
            Subscription.period_end > today,
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.cancelled]),
        )
    )
    sub = result.scalars().first()

    # 3. Load or create usage tracking for this billing cycle (only two args)
    usage = await get_or_create_usage(current_user, db)

    # 4. Compute display amount and currency
    # Strategy:
    # - If there's a current subscription, use the latest successful Transaction for that subscription (amount + currency)
    # - Else (or if none), fall back to a reasonable price row from plan.prices (cheapest global), and include its currency
    from app.services.pricing.selection import pick_price_row
    amount = 0.0
    amount_currency = None
    try:
        if sub:
            tx_q = await db.execute(
                select(Transaction)
                .where(
                    Transaction.subscription_id == sub.id,
                    Transaction.status == TransactionStatus.success,
                )
                .order_by(Transaction.created_at.desc())
            )
            txn = tx_q.scalars().first()
            if txn and getattr(txn, 'amount_pence', None) is not None:
                amount = float(txn.amount_pence) / 100.0
                amount_currency = getattr(txn, 'currency', None)

        if amount_currency is None:
            # Fallback: latest successful transaction for this user, prefer matching plan if metadata present
            tx_any_q = await db.execute(
                select(Transaction)
                .where(Transaction.user_id == current_user.id, Transaction.status == TransactionStatus.success)
                .order_by(Transaction.created_at.desc())
            )
            any_txn = tx_any_q.scalars().first()
            if any_txn and getattr(any_txn, 'amount_pence', None) is not None:
                amount = float(any_txn.amount_pence) / 100.0
                amount_currency = getattr(any_txn, 'currency', None)

        if amount_currency is None:
            # Fallback to a global/cheapest price row
            rows = getattr(plan, 'prices', []) or []
            chosen = None
            try:
                # Prefer a global price row for any currency; pick the cheapest among active ones
                globals_only = [
                    r for r in rows
                    if (getattr(getattr(r, 'scope_type', None), 'value', getattr(r, 'scope_type', None)) == 'global')
                    and getattr(r, 'active', False)
                ]
                if globals_only:
                    chosen = sorted(globals_only, key=lambda r: r.price_minor)[0]
                else:
                    # As a fallback of last resort, pick the absolute cheapest active row
                    actives = [r for r in rows if getattr(r, 'active', False)]
                    if actives:
                        chosen = sorted(actives, key=lambda r: r.price_minor)[0]
            except Exception:
                chosen = None
            if chosen:
                amount = float(chosen.price_minor) / 100.0
                amount_currency = getattr(chosen, 'currency', None)
    except Exception:
        amount = 0.0
        amount_currency = None

    # 5. Derive subscription display fields
    # Freemium if SKU says so
    if (plan.sku or '').upper() == 'FREEMIUM':
        # Freemium: treat as active plan with no subscription window
        subscription_status_value = SubscriptionStatus.active.value
        subscription_start_value = None
        subscription_end_value = None
        billing_interval_value = None
        auto_renew_value = None
        canceled_at_value = None
    else:
        subscription_status_value = (
            sub.status.value if sub else SubscriptionStatus.expired.value
        )
        subscription_start_value = (
            sub.period_start.isoformat() if sub else None
        )
        subscription_end_value = (
            sub.period_end.isoformat() if sub else None
        )
        billing_interval_value = (
            sub.billing_interval.value if sub and hasattr(sub, 'billing_interval') and sub.billing_interval else None
        )
        auto_renew_value = (
            sub.auto_renew if sub and hasattr(sub, 'auto_renew') else None
        )
        canceled_at_value = (
            sub.canceled_at.isoformat() if sub and hasattr(sub, 'canceled_at') and sub.canceled_at else None
        )

    # 6. Build the profile payload
    data = {
        "id":                str(current_user.id),
        "email":             current_user.email,
        "first_name":        current_user.first_name,
        "last_name":         current_user.last_name,
        "role":              current_user.role.value,
        "plan_name":         plan.name,
        "plan_sku":          getattr(plan, "sku", None),
    "amount":            amount,
    "amount_currency":   amount_currency,
        "is_active":         current_user.is_active,
        "is_email_verified": current_user.is_email_verified,
        "subscription_status": subscription_status_value,
        "subscription_start": subscription_start_value,
        "subscription_end": subscription_end_value,
        "billing_interval": billing_interval_value,
        "auto_renew": auto_renew_value,
        "canceled_at": canceled_at_value,
        "usage_tracking": {
            "uploads": {
                "used": usage.uploads_count,
                "monthly_limit": plan.monthly_upload_limit,
                "per_upload_pages_limit": plan.pages_per_upload_limit,
            },
            "assessments": {
                "used": usage.assessments_count,
                "monthly_limit": plan.monthly_assessment_limit,
                "per_assessment_questions_limit": plan.questions_per_assessment,
            },
            "questions": {
                "asked": usage.asked_questions_count,
                "monthly_limit": plan.monthly_ask_question_limit,
            },
            "flash_cards": {
                "used": getattr(usage, "flash_card_sets_count", 0),
                "monthly_limit": getattr(plan, "monthly_flash_cards_limit", 0),
                "per_deck_cards_limit": getattr(plan, "max_cards_per_deck", 0),
            },
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
