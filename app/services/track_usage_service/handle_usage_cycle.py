# app/core/usage.py

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.models.usage_tracking import UsageTracking
from app.services.subscription_access import get_active_subscription

async def get_or_create_usage(
    user,    # now expects the full User instance
    db: AsyncSession
) -> UsageTracking:
    """
    Returns or creates the UsageTracking row for the user's current subscription period.
    
    Note: Subscription renewal is handled by payment provider webhooks, not here.
    This function simply tracks usage for whatever subscription is currently active.
    
    Important: This function assumes the user has an active subscription.
    Use the ensure_user_has_subscription dependency in routes to guarantee this.
    """
    # 1) Get current active subscription (no renewal logic)
    sub = await get_active_subscription(user, db)
    if not sub:
        # This shouldn't happen if routes use ensure_user_has_subscription dependency
        # But handle gracefully just in case
        from app.services.subscription_access import create_free_subscription
        sub = await create_free_subscription(user, db, duration_days=30)
    
    # Convert timestamp to date for usage tracking (billing periods are date-based, not timestamp-based)
    # This prevents duplicate usage records when subscription has timestamp precision
    period_start_date = sub.period_start.date() if hasattr(sub.period_start, 'date') else sub.period_start

    # 2) Look for existing usage for that billing period (date-based comparison)
    q = await db.execute(
        select(UsageTracking)
        .where(
            UsageTracking.user_id == user.id,
            UsageTracking.period_start == period_start_date
        )
    )
    usage = q.scalars().first()
    if usage:
        return usage

    # 3) No usage row yet → create one with date (not timestamp)
    usage = UsageTracking(
        id=str(uuid.uuid4()),
        user_id=user.id,
        period_start=period_start_date,  # Store as date to ensure consistent matching
        uploads_count=0,
        assessments_count=0,
        asked_questions_count=0,
    )
    db.add(usage)
    await db.commit()
    await db.refresh(usage)
    return usage
