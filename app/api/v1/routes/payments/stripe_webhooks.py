"""Stripe webhook handler - Process subscription events."""

from __future__ import annotations

import stripe
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.response import success_response, ResponseModel
from app.core.logging_config import get_logger
from app.db.deps import get_db
from app.models.user import User
from app.models.transaction import Transaction
from app.utils.enums import TransactionStatus, PaymentProvider, TransactionStatusReason
from app.services.payments.subscription_service import SubscriptionService
from sqlalchemy import select

logger = get_logger(__name__)
router = APIRouter(prefix="/payments/stripe", tags=["webhooks"])

stripe.api_key = settings.STRIPE_SECRET_KEY


@router.post("/webhook", response_model=ResponseModel)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Stripe webhook events.
    
    Supported events:
    - checkout.session.completed: Activate subscription after payment
    - invoice.payment_succeeded: Extend subscription (recurring)
    - invoice.payment_failed: Handle payment failures (grace period)
    - customer.subscription.updated: Detect cancellations/reactivations via Stripe Portal
    - customer.subscription.deleted: Mark subscription as cancelled/expired
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    if not sig_header:
        logger.warning("Stripe webhook: Missing signature header")
        raise HTTPException(status_code=400, detail="Missing signature")
    
    try:
        # Verify webhook signature
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.error("Stripe webhook: Invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.error("Stripe webhook: Invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    event_type = event["type"]
    logger.info(f"Stripe webhook received: {event_type}")
    
    try:
        service = SubscriptionService()
        
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(event, db, service)
        
        elif event_type == "invoice.payment_succeeded":
            await _handle_invoice_paid(event, db, service)
        
        elif event_type == "invoice.payment_failed":
            await _handle_invoice_payment_failed(event, db, service)
        
        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(event, db, service)
        
        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(event, db, service)
        
        else:
            logger.info(f"Stripe webhook: Unhandled event type {event_type}")
        
        return success_response(msg="Webhook processed", data={"event_type": event_type})
    
    except Exception as e:
        logger.error(f"Stripe webhook processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")


async def _handle_checkout_completed(event, db: AsyncSession, service: SubscriptionService):
    """Handle checkout.session.completed - Activate subscription."""
    session = event["data"]["object"]
    session_id = session["id"]
    
    logger.info(f"Processing checkout.session.completed: {session_id}")
    
    # Get transaction by session_id (stored in reference field)
    stmt = select(Transaction).where(Transaction.reference == session_id)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()
    
    if not txn:
        logger.warning(f"Transaction not found for session {session_id}")
        return
    
    if txn.status == TransactionStatus.success:
        logger.info(f"Transaction {txn.id} already completed")
        return
    
    # Get plan_id and billing_interval from session metadata
    metadata = session.get("metadata", {})
    plan_id = metadata.get("plan_id")
    billing_interval = metadata.get("billing_interval")
    
    if not plan_id or not billing_interval:
        logger.error(f"Missing plan_id or billing_interval in session metadata: {metadata}")
        return
    
    # Update transaction status and capture payment method
    txn.status = TransactionStatus.success
    txn.status_reason = None  # Clear reason on success
    txn.status_message = "Payment completed successfully"
    
    # Extract payment channel from session
    payment_method_types = session.get("payment_method_types", [])
    txn.channel = payment_method_types[0] if payment_method_types else None  # card, bank_transfer, etc.
    
    # Fetch subscription details from Stripe to get exact period dates
    stripe_subscription_id = session.get("subscription")
    if not stripe_subscription_id:
        logger.error(f"No subscription ID in session {session_id}")
        return
    
    try:
        # Retrieve subscription object from Stripe API
        stripe_subscription = stripe.Subscription.retrieve(stripe_subscription_id)
        
        # Extract period dates from first subscription item (they're stored per-item, not at subscription level)
        first_item = stripe_subscription['items']['data'][0]
        period_start_unix = first_item['current_period_start']
        period_end_unix = first_item['current_period_end']
        
        # Convert Unix timestamps to ISO format strings (service expects ISO strings)
        from datetime import datetime, timezone
        period_start_dt = datetime.fromtimestamp(period_start_unix, tz=timezone.utc)
        period_end_dt = datetime.fromtimestamp(period_end_unix, tz=timezone.utc)
        period_start_str = period_start_dt.isoformat()
        period_end_str = period_end_dt.isoformat()
        
        logger.info(f"✅ Using Stripe subscription dates: start={period_start_str}, end={period_end_str}")
    except Exception as e:
        logger.error(f"Failed to fetch Stripe subscription {stripe_subscription_id}: {e}")
        return
    
    # Activate subscription with exact provider dates
    subscription = await service.activate_subscription(
        db=db,
        user_id=txn.user_id,
        plan_id=plan_id,
        billing_interval=billing_interval,
        provider=PaymentProvider.stripe,
        provider_subscription_id=stripe_subscription_id,
        provider_customer_id=session.get("customer"),
        period_start_str=period_start_str,
        period_end_str=period_end_str,
    )
    
    # Link transaction to subscription
    txn.subscription_id = subscription.id
    db.add(txn)
    
    await db.commit()
    logger.info(f"Subscription activated: subscription_id={subscription.id}, user_id={txn.user_id}, plan_id={plan_id}")


async def _handle_invoice_paid(event, db: AsyncSession, service: SubscriptionService):
    """Handle invoice.payment_succeeded - Extend subscription (recurring)."""
    invoice = event["data"]["object"]
    subscription_id = invoice.get("subscription")
    
    if not subscription_id:
        logger.info("Invoice has no subscription_id (likely one-time payment)")
        return
    
    logger.info(f"Processing invoice.payment_succeeded for subscription {subscription_id}")
    
    # Check if this is the first payment (handled by checkout.session.completed)
    billing_reason = invoice.get("billing_reason")
    if billing_reason == "subscription_create":
        logger.info("Skipping subscription_create invoice (handled by checkout)")
        return
    
    # Get subscription by stripe_subscription_id
    from app.models.subscription import Subscription
    stmt = select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()
    
    if not sub:
        logger.warning(f"Subscription not found for provider_subscription_id {subscription_id}")
        return
    
    # Check if this was a successful retry (exit retry period)
    if sub.is_in_retry_period:
        sub.is_in_retry_period = False
        sub.retry_attempt_count = 0
        sub.last_payment_failure_at = None
        db.add(sub)
        logger.info(f"✅ Subscription {sub.id} payment succeeded on retry - resuming normal billing")
    
    # Extend subscription
    await service.extend_subscription(db, sub)
    await db.commit()
    logger.info(f"Subscription {sub.id} extended (recurring payment)")


async def _handle_invoice_payment_failed(event, db: AsyncSession, service: SubscriptionService):
    """Handle invoice.payment_failed - Enter retry period."""
    invoice = event["data"]["object"]
    subscription_id = invoice.get("subscription")
    attempt_count = invoice.get("attempt_count", 1)
    
    if not subscription_id:
        logger.info("Invoice has no subscription_id")
        return
    
    logger.warning(f"Processing invoice.payment_failed for subscription {subscription_id} (attempt {attempt_count})")
    
    # Get subscription by stripe_subscription_id
    from app.models.subscription import Subscription
    from app.utils.datetime_utils import get_current_utc_datetime
    
    stmt = select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()
    
    if not sub:
        logger.warning(f"Subscription not found for stripe_subscription_id {subscription_id}")
        return
    
    # Enter retry period - user keeps access!
    sub.is_in_retry_period = True
    sub.retry_attempt_count = attempt_count
    sub.last_payment_failure_at = get_current_utc_datetime()
    # Keep status = active (user retains access during retry period)
    
    db.add(sub)
    await db.commit()
    
    logger.warning(f"⚠️ User {sub.user_id} payment failed (attempt {attempt_count}/4). Entering retry period - user keeps access until retries exhausted")


async def _handle_subscription_updated(event, db: AsyncSession, service: SubscriptionService):
    """Handle customer.subscription.updated - Detect cancellations via Stripe Portal."""
    subscription = event["data"]["object"]
    subscription_id = subscription["id"]
    cancel_at_period_end = subscription.get("cancel_at_period_end", False)
    
    logger.info(f"Processing customer.subscription.updated: {subscription_id}, cancel_at_period_end={cancel_at_period_end}")
    
    # Get subscription by stripe_subscription_id
    from app.models.subscription import Subscription, SubscriptionStatus
    from datetime import datetime, timezone
    
    stmt = select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()
    
    if not sub:
        logger.warning(f"Subscription not found for stripe_subscription_id {subscription_id}")
        return
    
    # Check if user canceled via Stripe Portal
    if cancel_at_period_end and sub.auto_renew:
        # User initiated cancellation via Stripe Portal
        sub.auto_renew = False
        sub.canceled_at = datetime.now(timezone.utc)
        db.add(sub)
        await db.commit()
        logger.info(f"✅ Subscription {sub.id} cancelled via Stripe Portal - will not renew after {sub.period_end}")
    
    # Check if user reactivated subscription
    elif not cancel_at_period_end and not sub.auto_renew and sub.status == SubscriptionStatus.active:
        # User reactivated via Stripe Portal
        sub.auto_renew = True
        sub.canceled_at = None
        db.add(sub)
        await db.commit()
        logger.info(f"✅ Subscription {sub.id} reactivated via Stripe Portal - will renew")


async def _handle_subscription_deleted(event, db: AsyncSession, service: SubscriptionService):
    """Handle customer.subscription.deleted - Mark subscription as cancelled."""
    subscription = event["data"]["object"]
    subscription_id = subscription["id"]
    
    logger.info(f"Processing customer.subscription.deleted: {subscription_id}")
    
    # Get subscription by stripe_subscription_id
    from app.models.subscription import Subscription, SubscriptionStatus
    stmt = select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()
    
    if not sub:
        logger.warning(f"Subscription not found for stripe_subscription_id {subscription_id}")
        return
    
    if sub.status == SubscriptionStatus.CANCELLED:
        logger.info(f"Subscription {sub.id} already cancelled")
        return
    
    # Check if retries were exhausted (downgrade scenario)
    was_in_retry = sub.is_in_retry_period
    
    if was_in_retry:
        # Retries exhausted - downgrade to Freemium
        logger.warning(f"🔻 User {sub.user_id} Stripe retries exhausted - downgrading to Freemium")
        await service.downgrade_to_freemium(db, sub.user_id, reason="stripe_retries_exhausted")
        logger.info(f"Subscription {sub.id} marked as cancelled after retry exhaustion")
    else:
        # User manually cancelled - just mark as cancelled
        sub.status = SubscriptionStatus.CANCELLED
        sub.auto_renew = False
        db.add(sub)
        await db.commit()
        logger.info(f"Subscription {sub.id} marked as cancelled (user-initiated)")
