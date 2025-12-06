"""Stripe webhook handler - Process subscription events."""

from __future__ import annotations

from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.response import success_response, ResponseModel
from app.core.logging_config import get_logger
from app.db.deps import get_db
from app.models.user import User
from app.models.transaction import Transaction
from app.models.plan import Plan
from app.utils.enums import (
    TransactionStatus,
    PaymentProvider,
    TransactionStatusReason,
    SubscriptionStatus,
)
from app.services.mail_handler_service import payment_notifications
from app.services.mail_handler_service.mailer_resend import EmailError
from app.services.payments.payment_email_utils import (
    build_billing_dashboard_url,
    format_amount_minor,
    format_period,
    user_display_name,
)
from app.services.payments.subscription_service import SubscriptionService

logger = get_logger(__name__)
router = APIRouter(prefix="/payments/stripe", tags=["webhooks"])

BILLING_DASHBOARD_URL = build_billing_dashboard_url()
MAX_STRIPE_RETRY_ATTEMPTS = 4

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

    # Send confirmation email
    user = await db.get(User, txn.user_id)
    plan = await db.get(Plan, subscription.plan_id)
    amount_minor = session.get("amount_total") or txn.amount_pence
    currency = session.get("currency") or txn.currency

    if user:
        try:
            await payment_notifications.send_payment_success_email(
                email=user.email,
                name=user_display_name(user),
                plan_name=plan.name if plan else metadata.get("plan_name", "Your plan"),
                billing_interval=billing_interval,
                amount=format_amount_minor(amount_minor, currency),
                currency=(currency or "").upper(),
                period_start=format_period(period_start_str),
                period_end=format_period(period_end_str),
                manage_url=BILLING_DASHBOARD_URL,
                provider="stripe",
            )
        except EmailError as exc:
            logger.error("Failed to send Stripe payment success email for user %s: %s", user.id, exc)


async def _handle_invoice_paid(event, db: AsyncSession, service: SubscriptionService):
    """Handle invoice.payment_succeeded - Extend subscription (recurring)."""
    invoice = event["data"]["object"]
    # Handle both old format (subscription at top level) and new format (nested in parent)
    subscription_id = invoice.get("subscription") or (
        invoice.get("parent", {}).get("subscription_details", {}).get("subscription")
    )
    
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
    was_retry = sub.is_in_retry_period
    if was_retry:
        sub.is_in_retry_period = False
        sub.retry_attempt_count = 0
        sub.last_payment_failure_at = None
        db.add(sub)
        logger.info(f"Subscription {sub.id} payment succeeded on retry - resuming normal billing")
    
    # Extend subscription
    updated_sub = await service.extend_subscription(db, sub)
    await db.commit()
    logger.info(f"Subscription {sub.id} extended (recurring payment)")

    # Notify user
    user = await db.get(User, sub.user_id)
    plan = await db.get(Plan, sub.plan_id)
    amount_minor = invoice.get("amount_paid") or invoice.get("amount_due")
    currency = invoice.get("currency")
    billing_interval = updated_sub.billing_interval.value
    period_start = format_period(updated_sub.period_start)
    period_end = format_period(updated_sub.period_end)

    if user:
        try:
            if was_retry:
                await payment_notifications.send_retry_success_email(
                    email=user.email,
                    name=user_display_name(user),
                    plan_name=plan.name if plan else "Your plan",
                    period_end=period_end,
                    manage_url=BILLING_DASHBOARD_URL,
                    provider="stripe",
                )
            else:
                await payment_notifications.send_payment_success_email(
                    email=user.email,
                    name=user_display_name(user),
                    plan_name=plan.name if plan else "Your plan",
                    billing_interval=billing_interval,
                    amount=format_amount_minor(amount_minor, currency),
                    currency=(currency or "").upper(),
                    period_start=period_start,
                    period_end=period_end,
                    manage_url=BILLING_DASHBOARD_URL,
                    provider="stripe",
                )
        except EmailError as exc:
            logger.error("Failed to send Stripe renewal email for subscription %s: %s", sub.id, exc)


async def _handle_invoice_payment_failed(event, db: AsyncSession, service: SubscriptionService):
    """Handle invoice.payment_failed - Enter retry period."""
    invoice = event["data"]["object"]
    # Handle both old format (subscription at top level) and new format (nested in parent)
    subscription_id = invoice.get("subscription")
    if not subscription_id:
        parent = invoice.get("parent") or {}
        sub_details = parent.get("subscription_details") or {}
        subscription_id = sub_details.get("subscription")
    
    customer_id = invoice.get("customer")
    attempt_count = invoice.get("attempt_count", 1)
    invoice_id = invoice.get("id")
    
    # Get subscription by stripe_subscription_id OR stripe_customer_id (fallback)
    from app.models.subscription import Subscription
    from app.utils.datetime_utils import get_current_utc_datetime
    
    if subscription_id:
        logger.info(f"Processing payment failure for subscription {subscription_id} (attempt {attempt_count})")
        stmt = select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
    elif customer_id:
        logger.warning(f"Invoice {invoice_id} missing subscription_id, using customer_id fallback: {customer_id}")
        stmt = select(Subscription).where(
            Subscription.stripe_customer_id == customer_id,
            Subscription.status == SubscriptionStatus.active
        ).order_by(Subscription.created_at.desc())
    else:
        logger.error(f"Invoice {invoice_id} has neither subscription_id nor customer_id")
        return
    
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()
    
    if not sub:
        logger.error(f"Subscription not found for stripe_subscription_id {subscription_id} or customer_id {customer_id}")
        return
    
    # Enter retry period - user keeps access!
    sub.is_in_retry_period = True
    sub.retry_attempt_count = attempt_count
    sub.last_payment_failure_at = get_current_utc_datetime()
    
    db.add(sub)
    await db.commit()
    
    logger.warning(f"User {sub.user_id} payment failed (attempt {attempt_count}/{MAX_STRIPE_RETRY_ATTEMPTS}). Retry period active - user keeps access until retries exhausted")

    user = await db.get(User, sub.user_id)
    plan = await db.get(Plan, sub.plan_id)
    next_payment_attempt = invoice.get("next_payment_attempt")
    if next_payment_attempt:
        next_retry_date = format_period(datetime.fromtimestamp(next_payment_attempt, tz=timezone.utc))
    else:
        next_retry_date = "soon"
    update_payment_url = BILLING_DASHBOARD_URL
    if user:
        try:
            await payment_notifications.send_payment_failure_email(
                email=user.email,
                name=user_display_name(user),
                plan_name=plan.name if plan else "Your plan",
                billing_interval=sub.billing_interval.value,
                attempt_number=attempt_count,
                max_attempts=MAX_STRIPE_RETRY_ATTEMPTS,
                next_retry_date=next_retry_date,
                update_payment_url=update_payment_url,
                provider="stripe",
            )
        except EmailError as exc:
            logger.error("Failed to send Stripe payment failure email for subscription %s: %s", sub.id, exc)


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
        logger.info(f"Subscription {sub.id} cancelled via Stripe Portal - will not renew after {sub.period_end}")
        user = await db.get(User, sub.user_id)
        plan = await db.get(Plan, sub.plan_id)
        if user:
            try:
                await payment_notifications.send_cancellation_email(
                    email=user.email,
                    name=user_display_name(user),
                    plan_name=plan.name if plan else "Your plan",
                    effective_date=format_period(sub.period_end),
                    reactivate_url=BILLING_DASHBOARD_URL,
                    provider="stripe",
                )
            except EmailError as exc:
                logger.error("Failed to send Stripe cancellation email for subscription %s: %s", sub.id, exc)
    
    # Check if user reactivated subscription
    elif not cancel_at_period_end and not sub.auto_renew and sub.status == SubscriptionStatus.active:
        # User reactivated via Stripe Portal
        sub.auto_renew = True
        sub.canceled_at = None
        db.add(sub)
        await db.commit()
        logger.info(f"Subscription {sub.id} reactivated via Stripe Portal - will renew")


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
    
    if sub.status == SubscriptionStatus.cancelled:
        logger.info(f"Subscription {sub.id} already cancelled")
        return
    
    # Check if retries were exhausted (downgrade scenario)
    was_in_retry = sub.is_in_retry_period
    
    if was_in_retry:
        # Retries exhausted - downgrade to Freemium
        logger.warning(f"User {sub.user_id} Stripe retries exhausted - downgrading to Freemium")
        await service.downgrade_to_freemium(db, sub.user_id, reason="stripe_retries_exhausted")
        logger.info(f"Subscription {sub.id} marked as cancelled after retry exhaustion")
    else:
        # User manually cancelled - mark subscription and downgrade to Freemium
        sub.status = SubscriptionStatus.cancelled
        sub.auto_renew = False
        db.add(sub)

        user = await db.get(User, sub.user_id)
        plan = await db.get(Plan, sub.plan_id)

        await service.downgrade_to_freemium(db, sub.user_id, reason="stripe_manual_cancel")

        logger.info(f"Subscription {sub.id} marked as cancelled (user-initiated)")
        if user:
            try:
                await payment_notifications.send_cancellation_email(
                    email=user.email,
                    name=user_display_name(user),
                    plan_name=plan.name if plan else "Your plan",
                    effective_date=format_period(sub.period_end),
                    reactivate_url=BILLING_DASHBOARD_URL,
                    provider="stripe",
                )
            except EmailError as exc:
                logger.error("Failed to send Stripe cancellation email for subscription %s: %s", sub.id, exc)
