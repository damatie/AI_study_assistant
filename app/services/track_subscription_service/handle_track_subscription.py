from datetime import date
import uuid
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.models.plan import Plan
from app.models.transaction import Transaction
from app.utils.enums import SubscriptionStatus, TransactionStatus, PaymentProvider
from app.models.user import User

async def renew_subscription_for_user(
    user: User, db: AsyncSession
) -> Subscription:
    """
    Ensure the user has an active subscription covering today.
    If expired, attempt renewal (free auto-renew or paid payment).
    Returns the active subscription, even if downgraded on failure.
    """
    today = date.today()
    # 1.  Look for a subscription covering today
    q = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.period_start <= today,
            Subscription.period_end > today,
            # Treat a scheduled-cancel (status=cancelled) as still valid until period_end
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.cancelled])
        )
    )
    sub = q.scalars().first()
    if sub:
        return sub

    # 2. No active subscription → get most recent
    q2 = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.period_end.desc())
        .limit(1)
    )
    last = q2.scalars().first()

    # Determine plan
    plan = await db.get(Plan, user.plan_id)

    # 3. Build new period dates
    start = today
    end   = today + relativedelta(months=1)

    # If the last subscription was scheduled to cancel and the period just ended,
    # do NOT attempt a paid renewal; downgrade to Freemium for the next period.
    if last and last.status == SubscriptionStatus.cancelled and last.period_end <= today:
        free_plan = (await db.execute(select(Plan).where(Plan.sku == 'FREEMIUM'))).scalars().first()
        if free_plan:
            user.plan_id = free_plan.id
            db.add(user)
            await db.commit()
            plan = free_plan

    # 4. Handle free vs paid
    if (plan.sku or '').upper() == 'FREEMIUM':
        # FREEMIUM: auto‐renew without any transaction record
        new_sub = Subscription(
            id=uuid.uuid4(),
            user_id=user.id,
            plan_id=plan.id,
            period_start=start,
            period_end=end,
            status=SubscriptionStatus.active,
        )
        db.add(new_sub)
        await db.commit()
        await db.refresh(new_sub)
        return new_sub

    # PAID PLAN: we do not auto-charge here. Record a failed renewal and downgrade immediately.
    # Create a pending transaction with provider=stripe for visibility; then mark failed.
    txn_ref = str(uuid.uuid4())
    txn = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        subscription_id=None,
        reference=txn_ref,
        authorization_url=None,
        provider=PaymentProvider.stripe,
        amount_pence=0,
        currency="GBP",
        status=TransactionStatus.pending,
    )
    db.add(txn)
    await db.flush()

    # Mark failed since auto-renewal via payment gateway isn't executed here
    txn.status = TransactionStatus.failed

    # Expire most recent subscription if exists
    if last:
        last.status = SubscriptionStatus.expired
        db.add(last)

    # Downgrade user to free plan for the new period
    free_plan = (await db.execute(select(Plan).where(Plan.sku == 'FREEMIUM'))).scalars().first()
    if free_plan:
        user.plan_id = free_plan.id
        db.add(user)

        # Create a free active subscription for the new period
        new_sub = Subscription(
            id=uuid.uuid4(),
            user_id=user.id,
            plan_id=free_plan.id,
            period_start=start,
            period_end=end,
            status=SubscriptionStatus.active,
        )
        db.add(new_sub)

    await db.commit()
    # Optionally: send notification email about failed renewal
    return await renew_subscription_for_user(user, db)