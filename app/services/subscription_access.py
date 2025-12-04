"""Simple subscription access checking without auto-renewal logic.

Renewals are handled entirely by payment provider webhooks (Stripe/Paystack).
This module only checks if a user has valid access.
"""

import uuid
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.models.plan import Plan
from app.models.user import User
from app.utils.enums import SubscriptionStatus
from app.utils.datetime_utils import get_current_utc_datetime


async def get_active_subscription(
    user: User, db: AsyncSession
) -> Subscription | None:
    """
    Get the user's currently active subscription, if any.
    
    Does NOT attempt any renewal logic - renewals are handled by webhooks.
    Simply returns the active subscription covering the current time period.
    
    Args:
        user: The user to check
        db: Database session
        
    Returns:
        Active subscription or None
        
    Note:
        This respects provider retry periods. A subscription is considered
        active if:
        1. It's within the period (period_start <= now < period_end), OR
        2. It's in grace/retry period (is_in_retry_period = True)
        
        Status 'cancelled' is included because users keep access until 
        period_end or until provider confirms all retries exhausted.
    """
    now = get_current_utc_datetime()
    
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            # Grant access if EITHER:
            # - Within subscription period, OR
            # - In retry/grace period (provider still attempting payment)
            (
                (
                    (Subscription.period_start <= now) &
                    (Subscription.period_end > now)
                ) |
                (Subscription.is_in_retry_period == True)
            ),
            # Include 'cancelled' because user keeps access until period_end
            # (provider may still be retrying payment during this time)
            Subscription.status.in_([
                SubscriptionStatus.active,
                SubscriptionStatus.cancelled
            ])
        )
        .order_by(Subscription.period_end.desc())
        .limit(1)
    )
    
    return result.scalars().first()


async def expire_lapsed_subscriptions(user_id: uuid.UUID, db: AsyncSession) -> int:
    """Mark any past-due subscriptions as expired so only current rows stay active.
    
    Note: Does NOT expire subscriptions in retry period - those are still valid!
    """
    now = get_current_utc_datetime()
    stale_query = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.active,
            Subscription.period_end <= now,
            Subscription.is_in_retry_period == False,  # Don't expire if in retry!
        )
    )
    stale_rows = stale_query.scalars().all()
    for subscription in stale_rows:
        subscription.status = SubscriptionStatus.expired
        subscription.auto_renew = False
        subscription.is_in_retry_period = False
        subscription.retry_attempt_count = 0
        db.add(subscription)
    
    if stale_rows:
        await db.commit()  # Commit expiration before creating new subscription
    
    return len(stale_rows)


async def create_free_subscription(
    user: User, db: AsyncSession, duration_days: int = 30
) -> Subscription:
    """
    Create a free subscription for a new user or for Freemium plan.
    
    This is ONLY used for:
    - New user registration (initial free period)
    - Freemium plan auto-renewal (no payment needed)
    
    Args:
        user: The user to create subscription for
        db: Database session
        duration_days: Duration in days (default 30)
        
    Returns:
        Created subscription
        
    Note:
        This should NOT be used for paid plan renewals.
        Paid renewals are handled by payment provider webhooks.
    """
    await expire_lapsed_subscriptions(user.id, db)

    now = get_current_utc_datetime()
    period_start = now
    period_end = now + timedelta(days=duration_days)
    
    # Get user's plan
    plan = await db.get(Plan, user.plan_id)
    
    new_sub = Subscription(
        id=uuid.uuid4(),
        user_id=user.id,
        plan_id=plan.id,
        period_start=period_start,
        period_end=period_end,
        status=SubscriptionStatus.active,
    )
    
    db.add(new_sub)
    await db.commit()
    await db.refresh(new_sub)
    
    return new_sub
