# app/core/usage.py

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.models.usage_tracking import UsageTracking
from app.services.track_subscription_service.handle_track_subscription import renew_subscription_for_user

async def get_or_create_usage(
    user,    # now expects the full User instance
    db: AsyncSession
) -> UsageTracking:
    """
    Ensures the user's subscription is current (and auto‑renewed/downgraded if needed),
    then returns or creates the UsageTracking row for that subscription period.
    """
    # 1) Force a renewal check (auto‑renew freemium, attempt paid renewal, downgrade on failure)
    sub = await renew_subscription_for_user(user, db)
    period_start = sub.period_start

    # 2) Look for existing usage for that exact period
    q = await db.execute(
        select(UsageTracking)
        .where(
            UsageTracking.user_id == user.id,
            UsageTracking.period_start == period_start
        )
    )
    usage = q.scalars().first()
    if usage:
        return usage

    # 3) No usage row yet → create one
    usage = UsageTracking(
        id=str(uuid.uuid4()),
        user_id=user.id,
        period_start=period_start,
        uploads_count=0,
        assessments_count=0,
        asked_questions_count=0,
    )
    db.add(usage)
    await db.commit()
    await db.refresh(usage)
    return usage
