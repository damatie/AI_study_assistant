"""
Subscription dependency for FastAPI routes.

Ensures every authenticated user has an active subscription.
If not, automatically creates a free subscription (Freemium).
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.db.deps import get_db
from app.api.v1.routes.auth.auth import get_current_user
from app.services.subscription_access import get_active_subscription, create_free_subscription


async def ensure_user_has_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Dependency that ensures the user has an active subscription.
    
    If the user has no subscription (e.g., deleted from DB, new user without
    registration subscription), automatically creates a free Freemium subscription.
    
    This should be used as a dependency for routes that require subscription checks.
    
    Args:
        current_user: The authenticated user
        db: Database session
        
    Returns:
        The user (with guaranteed subscription)
        
    Note:
        This only creates subscriptions if they don't exist.
        It does NOT renew expired subscriptions - that's handled by webhooks.
    """
    # Check if user has any active subscription
    sub = await get_active_subscription(current_user, db)
    
    if not sub:
        # User has no subscription - create a free one
        # This handles edge cases like:
        # - Subscription manually deleted from DB
        # - Database inconsistencies
        # - Migration/testing scenarios
        await create_free_subscription(current_user, db, duration_days=30)
    
    return current_user
