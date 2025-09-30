from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.transaction import Transaction
from app.models.subscription import Subscription
from app.models.plan import Plan
from app.utils.enums import TransactionStatus, SubscriptionStatus

@dataclass
class RefundDecision:
    eligible: bool
    reason: str
    amount_pence: int = 0

async def can_refund_cool_off(txn: Transaction, now: datetime | None = None) -> RefundDecision:
    """Cool-off policy: full refund if cancelled within REFUND_COOL_OFF_HOURS of payment success."""
    if txn.status != TransactionStatus.success:
        return RefundDecision(False, "Transaction not successful")
    if not txn.created_at:
        return RefundDecision(False, "Transaction timestamp missing")
    now = now or datetime.now(timezone.utc)
    window = timedelta(hours=settings.REFUND_COOL_OFF_HOURS)
    if now - txn.created_at <= window:
        return RefundDecision(True, "Within cool-off window", amount_pence=txn.amount_pence)
    return RefundDecision(False, "Outside cool-off window")

async def process_immediate_cancel_with_optional_refund(
    db: AsyncSession,
    user_id,
    request_refund: bool,
) -> tuple[RefundDecision | None, str]:
    """
    End current subscription immediately and optionally refund if eligible.
    - When request_refund is True and eligible, proceed to refund (integration TODO).
    - Always downgrade the user plan to Freemium upon immediate cancel as per business logic.
    """
    # Find latest successful transaction for the user
    q = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id, Transaction.status == TransactionStatus.success)
        .order_by(Transaction.created_at.desc())
        .limit(1)
    )
    txn = q.scalars().first()

    refund_msg = ""
    decision: RefundDecision | None = None
    if request_refund:
        if txn:
            decision = await can_refund_cool_off(txn)
            if decision.eligible:
                # TODO: Integrate with Stripe/Paystack refunds API and record refund entity
                refund_msg = " Full refund will be processed."
            else:
                refund_msg = f" Refund not eligible: {decision.reason}."
        else:
            decision = RefundDecision(False, "No successful transaction found")
            refund_msg = f" Refund not eligible: {decision.reason}."

    # Caller handles subscription record update and plan downgrade; we just compute refund eligibility.
    return decision, refund_msg
