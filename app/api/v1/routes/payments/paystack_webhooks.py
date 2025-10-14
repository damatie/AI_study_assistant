"""Paystack webhook handler - Process subscription events."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.response import success_response, ResponseModel
from app.core.logging_config import get_logger
from app.db.deps import get_db
from app.models.user import User
from app.models.transaction import Transaction
from app.utils.enums import TransactionStatus, PaymentProvider, TransactionStatusReason, SubscriptionStatus
from app.services.payments.subscription_service import SubscriptionService
from sqlalchemy import select, or_

logger = get_logger(__name__)
router = APIRouter(prefix="/payments/paystack", tags=["webhooks"])


@router.post("/webhook", response_model=ResponseModel)
async def paystack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Paystack webhook events.
    
    Supported events:
    - charge.success: Activate subscription after first payment
    - subscription.create: Log subscription creation
    - subscription.not_renew: Handle user cancellation via portal (sets auto_renew=False)
    - subscription.disable: Mark subscription as cancelled (final expiration)
    - charge.failed: Handle payment failures and retry logic
    """
    payload = await request.body()
    sig_header = request.headers.get("x-paystack-signature")
    
    if not sig_header:
        logger.warning("Paystack webhook: Missing signature header")
        raise HTTPException(status_code=400, detail="Missing signature")
    
    # Verify webhook signature
    computed_hash = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode(),
        payload,
        hashlib.sha512
    ).hexdigest()
    
    if not hmac.compare_digest(computed_hash, sig_header):
        logger.error("Paystack webhook: Invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    try:
        event = await request.json()
    except Exception:
        logger.error("Paystack webhook: Invalid JSON")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = event.get("event")
    logger.info(f"Paystack webhook received: {event_type}")
    
    try:
        service = SubscriptionService()
        
        if event_type == "subscription.create":
            await _handle_subscription_create(event, db, service)
        
        elif event_type == "charge.success":
            await _handle_charge_success(event, db, service)
        
        elif event_type == "charge.failed":
            await _handle_charge_failed(event, db, service)
        
        elif event_type == "subscription.disable":
            await _handle_subscription_disable(event, db, service)
        
        elif event_type == "subscription.not_renew":
            await _handle_subscription_not_renew(event, db, service)
        
        else:
            logger.info(f"Paystack webhook: Unhandled event type {event_type}")
        
        return success_response(msg="Webhook processed", data={"event_type": event_type})
    
    except Exception as e:
        logger.error(f"Paystack webhook processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")


async def _handle_subscription_create(event, db: AsyncSession, service: SubscriptionService):
    """
    Handle subscription.create - Create subscription with real Paystack subscription_code.
    
    This webhook is sent when Paystack successfully creates a subscription.
    charge.success has already marked the transaction as successful.
    Now we create the subscription record using the real subscription_code.
    """
    data = event.get("data", {})
    
    # Get subscription details from Paystack
    subscription_code = data.get("subscription_code")
    customer_code = data.get("customer", {}).get("customer_code")
    plan_code = data.get("plan", {}).get("plan_code")
    
    # Extract exact dates from Paystack
    created_at = data.get("createdAt")  # Subscription start date
    next_payment_date = data.get("next_payment_date")  # When subscription renews (period_end)
    
    if not subscription_code or not customer_code or not plan_code:
        logger.warning("subscription.create missing required fields")
        return
    
    logger.info(f"subscription.create received: code={subscription_code}, customer={customer_code}, plan={plan_code}, start={created_at}, end={next_payment_date}")
    
    # Find the most recent successful transaction (charge.success already marked it as successful)
    # Retry a few times in case charge.success is still committing (race condition)
    import asyncio
    txn = None
    for attempt in range(3):
        stmt = (
            select(Transaction)
            .where(
                Transaction.provider == "paystack",
                Transaction.status == TransactionStatus.success,
                Transaction.subscription_id.is_(None)
            )
            .order_by(Transaction.created_at.desc())
            .limit(1)
        )
        
        result = await db.execute(stmt)
        txn = result.scalar_one_or_none()
        
        if txn:
            break
        
        if attempt < 2:
            logger.info(f"Transaction not found yet (attempt {attempt + 1}/3), waiting for charge.success to commit...")
            await asyncio.sleep(1)  # Wait 1 second before retrying
    
    if not txn:
        logger.warning(f"Could not find successful transaction for subscription {subscription_code} after 3 attempts. charge.success may have failed or arrived out of order.")
        return
    
    # Look up plan details from PlanPrice using the plan_code from Paystack
    from app.models.plan_price import PlanPrice
    
    price_stmt = select(PlanPrice).where(PlanPrice.provider_price_id == plan_code)
    price_result = await db.execute(price_stmt)
    price = price_result.scalar_one_or_none()
    
    if not price:
        logger.error(f"Could not find PlanPrice for plan_code {plan_code}")
        return
    
    plan_id = price.plan_id
    billing_interval = price.billing_interval.value
    
    # Create subscription with real Paystack data and exact dates from provider
    subscription = await service.activate_subscription(
        db=db,
        user_id=txn.user_id,
        plan_id=plan_id,
        billing_interval=billing_interval,
        provider="paystack",
        provider_subscription_id=subscription_code,
        provider_customer_id=customer_code,
        period_start_str=created_at,
        period_end_str=next_payment_date
    )
    
    # Link transaction to subscription
    txn.subscription_id = subscription.id
    
    await db.commit()
    logger.info(f"✅ Subscription created: id={subscription.id}, user_id={txn.user_id}, plan_id={plan_id}, code={subscription_code}")


async def _handle_charge_success(event, db: AsyncSession, service: SubscriptionService):
    """
    Handle charge.success - Update transaction status only.
    
    When using plan_code, Paystack auto-creates subscription and sends subscription.create webhook.
    We'll create subscription record when subscription.create arrives with real subscription_code.
    """
    data = event.get("data", {})
    
    # Get transaction reference
    reference = data.get("reference")
    if not reference:
        logger.warning("No reference in charge.success")
        return
    
    logger.info(f"Processing charge.success with reference {reference}")
    
    # Find transaction
    stmt = select(Transaction).where(Transaction.reference == reference)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()
    
    if not txn:
        logger.warning(f"Transaction not found for reference {reference}")
        return
    
    if txn.status != TransactionStatus.pending:
        logger.info(f"Transaction {reference} already processed with status {txn.status}")
        return
    
    # Update transaction to success and capture channel
    txn.status = TransactionStatus.success
    txn.status_reason = None
    txn.status_message = "Payment completed successfully"
    txn.channel = data.get("channel")  # card, bank, bank_transfer, etc.
    
    # Check if subscription exists and was in retry period (successful retry)
    if txn.subscription_id:
        from app.models.subscription import Subscription
        sub_result = await db.execute(select(Subscription).where(Subscription.id == txn.subscription_id))
        sub = sub_result.scalar_one_or_none()
        
        if sub and sub.is_in_retry_period:
            sub.is_in_retry_period = False
            sub.retry_attempt_count = 0
            sub.last_payment_failure_at = None
            db.add(sub)
            logger.info(f"✅ Subscription {sub.id} payment succeeded on retry - resuming normal billing")
    
    await db.commit()
    logger.info(f"✅ Transaction {reference} marked as successful via {txn.channel}. Waiting for subscription.create webhook...")


async def _handle_charge_failed(event, db: AsyncSession, service: SubscriptionService):
    """Handle charge.failed - Enter retry period."""
    data = event.get("data", {})
    
    # Get customer and failure details
    customer_code = data.get("customer", {}).get("customer_code")
    gateway_response = data.get("gateway_response", "Payment failed")
    reference = data.get("reference")
    
    logger.warning(f"Processing charge.failed with reference {reference}, customer {customer_code}, reason: {gateway_response}")
    
    if not customer_code:
        logger.warning("No customer_code in charge.failed")
        return
    
    # Find most recent active subscription for this customer
    from app.models.subscription import Subscription
    from app.utils.datetime_utils import get_current_utc_datetime
    
    stmt = (
        select(Subscription)
        .where(
            Subscription.paystack_customer_code == customer_code,
            Subscription.status == SubscriptionStatus.active
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()
    
    if not sub:
        logger.warning(f"No active subscription found for customer {customer_code}")
        return
    
    # Enter retry period - user keeps access!
    sub.is_in_retry_period = True
    sub.retry_attempt_count += 1
    sub.last_payment_failure_at = get_current_utc_datetime()
    # Keep status = active (user retains access during retry period)
    
    db.add(sub)
    await db.commit()
    
    logger.warning(f"⚠️ User {sub.user_id} Paystack payment failed (attempt {sub.retry_attempt_count}). Entering retry period - user keeps access. Reason: {gateway_response}")


async def _handle_subscription_not_renew(event, db: AsyncSession, service: SubscriptionService):
    """
    Handle subscription.not_renew - User cancelled subscription via Paystack portal.
    
    This is sent when a user cancels their subscription through the Paystack manage link.
    Similar to Stripe's cancel_at_period_end=True behavior:
    - Set auto_renew=False
    - Set canceled_at timestamp
    - Keep status='active' (subscription stays active until period_end)
    """
    data = event.get("data", {})
    subscription_code = data.get("subscription_code")
    
    if not subscription_code:
        logger.warning("subscription.not_renew missing subscription_code")
        return
    
    logger.info(f"Processing subscription.not_renew: {subscription_code}")
    
    # Get subscription by paystack_subscription_code
    from app.models.subscription import Subscription
    from app.utils.datetime_utils import get_current_utc_datetime
    
    stmt = select(Subscription).where(Subscription.paystack_subscription_code == subscription_code)
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()
    
    if not sub:
        logger.warning(f"Subscription not found for paystack_subscription_code {subscription_code}")
        return
    
    # Mark for cancellation at period end (like Stripe's cancel_at_period_end)
    sub.auto_renew = False
    sub.canceled_at = get_current_utc_datetime()
    # Keep status = active (user retains access until period_end)
    
    db.add(sub)
    await db.commit()
    
    logger.info(f"✅ Subscription {sub.id} marked for cancellation at period end (user_id={sub.user_id}, period_end={sub.period_end})")


async def _handle_subscription_disable(event, db: AsyncSession, service: SubscriptionService):
    """Handle subscription.disable - Mark subscription as cancelled."""
    data = event.get("data", {})
    subscription_code = data.get("subscription_code")
    
    logger.info(f"Processing subscription.disable: {subscription_code}")
    
    # Get subscription by paystack_subscription_code
    from app.models.subscription import Subscription, SubscriptionStatus
    stmt = select(Subscription).where(Subscription.paystack_subscription_code == subscription_code)
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()
    
    if not sub:
        logger.warning(f"Subscription not found for paystack_subscription_code {subscription_code}")
        return
    
    if sub.status == SubscriptionStatus.cancelled:
        logger.info(f"Subscription {sub.id} already cancelled")
        return
    
    # Check if retries were exhausted (downgrade scenario)
    was_in_retry = sub.is_in_retry_period
    
    if was_in_retry:
        # Retries exhausted - downgrade to Freemium
        logger.warning(f"🔻 User {sub.user_id} Paystack retries exhausted - downgrading to Freemium")
        await service.downgrade_to_freemium(db, sub.user_id, reason="paystack_retries_exhausted")
        logger.info(f"Subscription {sub.id} marked as cancelled after retry exhaustion")
    else:
        # User manually cancelled - just mark as cancelled
        sub.status = SubscriptionStatus.cancelled
        sub.auto_renew = False
        db.add(sub)
        await db.commit()
        logger.info(f"Subscription {sub.id} marked as cancelled (user-initiated)")
