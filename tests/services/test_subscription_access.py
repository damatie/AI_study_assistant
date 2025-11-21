from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.plan import Plan, SummaryDetail, AIFeedbackLevel
from app.models.user import User, Role
from app.models.subscription import Subscription
from app.utils.enums import SubscriptionStatus
from app.services.subscription_access import create_free_subscription

pytestmark = pytest.mark.anyio


async def _seed_plan_and_user(db_session):
    plan = Plan(
        name="Freemium",
        sku="FREEMIUM",
        monthly_upload_limit=5,
        pages_per_upload_limit=25,
        monthly_assessment_limit=2,
        questions_per_assessment=5,
        monthly_ask_question_limit=10,
        monthly_flash_cards_limit=2,
        max_cards_per_deck=20,
        summary_detail=SummaryDetail.limited_detail,
        ai_feedback_level=AIFeedbackLevel.basic,
    )
    db_session.add(plan)
    await db_session.flush()

    user = User(
        first_name="Free",
        last_name="User",
        email="free@example.com",
        password_hash="hashed",
        role=Role.user,
        plan_id=plan.id,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    return plan, user


async def test_create_free_subscription_expires_lapsed_active(db_session):
    _, user = await _seed_plan_and_user(db_session)
    now = datetime.now(timezone.utc)

    def _normalize(dt: datetime) -> datetime:
        return dt if dt.tzinfo is None else dt.replace(tzinfo=None)

    stale = Subscription(
        user_id=user.id,
        plan_id=user.plan_id,
        period_start=now - timedelta(days=60),
        period_end=now - timedelta(days=1),
        status=SubscriptionStatus.active,
        auto_renew=True,
    )
    db_session.add(stale)
    await db_session.commit()

    new_subscription = await create_free_subscription(user, db_session, duration_days=15)

    rows = (
        await db_session.execute(
            select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.period_start)
        )
    ).scalars().all()

    assert len(rows) == 2
    stale_ref = next(sub for sub in rows if sub.id == stale.id)
    assert stale_ref.status == SubscriptionStatus.expired
    assert stale_ref.auto_renew is False
    assert _normalize(stale_ref.period_end) <= _normalize(now)

    assert new_subscription.status == SubscriptionStatus.active
    assert _normalize(new_subscription.period_start) >= _normalize(now)
    expected_end = new_subscription.period_start + timedelta(days=15)
    assert _normalize(new_subscription.period_end) == _normalize(expected_end)
