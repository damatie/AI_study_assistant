from datetime import date
import uuid
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.models.plan import Plan
from app.models.transaction import Transaction
from app.utils.enums import SubscriptionStatus, TransactionStatus
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
    # 1. Find a subscription covering today
    q = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.period_start <= today,
            Subscription.period_end > today,
            Subscription.status == SubscriptionStatus.active
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

    # 4. Handle free vs paid
    if plan.price_pence == 0:
        # Freemium: auto-renew success
        new_sub = Subscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            plan_id=plan.id,
            period_start=start,
            period_end=end,
            status=SubscriptionStatus.active,
        )
        db.add(new_sub)
        # Record zero‑amount transaction
        txn = Transaction(
            id=str(uuid.uuid4()),
            user_id=user.id,
            subscription=new_sub,
            amount_pence=0,
            currency="GBP",
            status=TransactionStatus.success
        )
        db.add(txn)
        await db.commit()
        return new_sub

    # Paid plan: create pending transaction
    txn = Transaction(
        id=str(uuid.uuid4()),
        user_id=user.id,
        amount_pence=plan.price_pence,
        currency="GBP",
        status=TransactionStatus.pending
    )
    db.add(txn)
    await db.commit()
    # TODO: replace with real payment gateway call
    payment_ok = await fake_gateway_charge(user, plan.price_pence)

    if payment_ok:
        # mark transaction success and link to subscription
        txn.status = TransactionStatus.success
        new_sub = Subscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            plan_id=plan.id,
            period_start=start,
            period_end=end,
            status=SubscriptionStatus.active,
        )
        db.add(new_sub)
        await db.flush()
        txn.subscription_id = new_sub.id
        await db.commit()
        return new_sub
    else:
        # failed: mark transaction, expire old, downgrade user
        txn.status = TransactionStatus.failed
        # expire most recent if exists
        if last:
            last.status = SubscriptionStatus.expired
            db.add(last)
        # downgrade user to free plan
        free_plan = (await db.execute(select(Plan).where(Plan.price_pence == 0))).scalars().first()
        user.plan_id = free_plan.id
        db.add(user)
        await db.commit()
         # TODO:Send email to notify user
        return await renew_subscription_for_user(user, db)
    
#fake
async def fake_gateway_charge(user, amount_pence):
    # Replace with real API call to Stripe/PayPal/etc.
    # Return True if payment succeeds, False otherwise.
    return False  # or True for testing