from datetime import datetime, timezone
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.usage_tracking import UsageTracking as UsageModel


async def get_or_create_usage(user_id: str, db: AsyncSession) -> UsageModel:
    # Billing cycle starts on the first of the current month (UTC)
    today = datetime.now(timezone.utc).date()
    period_start = today.replace(day=1)

    q = await db.execute(
        select(UsageModel)
        .where(
            UsageModel.user_id == user_id,
            UsageModel.period_start == period_start
        )
    )
    usage = q.scalars().first()
    if not usage:
        usage = UsageModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            period_start=period_start,
            uploads_count=0,
            assessments_count=0,
            questions_count=0,
        )
        db.add(usage)
        await db.commit()
        await db.refresh(usage)
    return usage
