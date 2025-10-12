from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.response import success_response, error_response
from app.db.deps import get_db

logger = get_logger(__name__)
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.transaction import Transaction
from app.models.user import User
from app.utils.enums import SubscriptionStatus, TransactionStatus, TransactionStatusReason, BillingInterval, TransactionType, PaymentProvider
from app.api.v1.routes.auth.auth import get_current_user

import stripe
import uuid


router = APIRouter(prefix="/payments/stripe", tags=["payments", "stripe"])


def _init_stripe() -> None:
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY not configured")
    stripe.api_key = settings.STRIPE_SECRET_KEY

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
):
    """Handle Stripe webhook events and activate subscription on success."""
    _init_stripe()
    payload = await request.body()
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET

    try:
        if endpoint_secret:
            event = stripe.Webhook.construct_event(
                payload=payload, sig_header=stripe_signature, secret=endpoint_secret
            )
        else:
            event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    except Exception as e:
        return error_response(f"Webhook error: {e}", status_code=400)

    event_type = event["type"]

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("metadata", {}).get("user_id")
        plan_id = session.get("metadata", {}).get("plan_id")

        if not user_id or not plan_id:
            return success_response("Ignored: missing metadata")

        # Fetch plan
        plan_res = await db.execute(select(Plan).where(Plan.id == plan_id))
        plan = plan_res.scalars().first()
        if not plan:
            return success_response("Ignored: plan not found")

        # Extract Stripe subscription and customer IDs
        stripe_subscription_id = session.get("subscription")  # For recurring subscriptions
        stripe_customer_id = session.get("customer")
        
        # Determine billing interval from metadata or default to month
        metadata = session.get("metadata", {})
        billing_interval_str = metadata.get("billing_interval", "month")
        logger.info(f"🔍 WEBHOOK DEBUG: Full metadata: {metadata}")
        logger.info(f"🔍 WEBHOOK DEBUG: billing_interval from metadata: {billing_interval_str!r}")
        
        try:
            billing_interval = BillingInterval(billing_interval_str)
        except ValueError:
            logger.warning(f"⚠️ WEBHOOK: Invalid billing_interval '{billing_interval_str}' - defaulting to 'month'")
            billing_interval = BillingInterval.month
        
        logger.info(f"✅ WEBHOOK: Final billing_interval: {billing_interval.value!r}")
        
        # Calculate period end based on billing interval
        today = date.today()
        if billing_interval == BillingInterval.year:
            period_end = today + timedelta(days=365)  # Annual
        else:
            period_end = today + timedelta(days=30)  # Monthly

        # End existing active subscription and start a new period
        sub_q = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.active,
                Subscription.period_start <= today,
                Subscription.period_end > today,
            )
        )
        existing = sub_q.scalars().first()
        if existing:
            existing.status = SubscriptionStatus.cancelled
            existing.period_end = today
            db.add(existing)

        new_sub = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            stripe_subscription_id=stripe_subscription_id,
            stripe_customer_id=stripe_customer_id,
            billing_interval=billing_interval,
            auto_renew=True,  # Stripe subscriptions auto-renew by default
            period_start=today,
            period_end=period_end,
            status=SubscriptionStatus.active,
        )
        db.add(new_sub)

        # Sync user's effective plan to keep profile consistent
        user_res = await db.execute(select(User).where(User.id == user_id))
        user = user_res.scalars().first()
        if user and user.plan_id != plan.id:
            user.plan_id = plan.id
            db.add(user)

        # Update transaction to success
        txn_q = await db.execute(select(Transaction).where(Transaction.reference == session["id"]))
        txn = txn_q.scalars().first()
        if txn:
            txn.status = TransactionStatus.success
            # Clear any pending reason
            try:
                setattr(txn, "status_reason", None)
                setattr(txn, "status_message", None)
                setattr(txn, "failure_code", None)
            except Exception:
                pass
            txn.subscription = new_sub
            db.add(txn)

    # Handle non-successful outcomes to enrich diagnostics
    # - checkout.session.async_payment_failed / checkout.session.expired
    elif event_type in ("checkout.session.async_payment_failed", "checkout.session.expired"):
        session = event["data"]["object"]
        session_id = session.get("id")
        txn_q = await db.execute(select(Transaction).where(Transaction.reference == session_id))
        txn = txn_q.scalars().first()
        if txn and txn.status != TransactionStatus.success:
            if event_type == "checkout.session.expired":
                txn.status = TransactionStatus.expired
                txn.status_reason = TransactionStatusReason.ttl_elapsed
                txn.status_message = "Payment session expired"
            else:
                txn.status = TransactionStatus.failed
                txn.status_reason = TransactionStatusReason.provider_failed
                # Try to pull best effort error details if payment_intent present
                try:
                    pi_id = session.get("payment_intent")
                    if pi_id:
                        _init_stripe()
                        pi = stripe.PaymentIntent.retrieve(pi_id)
                        last_err = getattr(pi, "last_payment_error", None)
                        if last_err:
                            txn.failure_code = getattr(last_err, "code", None)
                            txn.status_message = getattr(last_err, "message", None)
                        else:
                            txn.status_message = getattr(pi, "last_payment_error", None) or txn.status_message
                except Exception:
                    pass
            db.add(txn)

    # Handle recurring subscription invoice payments
    elif event_type == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        stripe_sub_id = invoice.get("subscription")
        
        # Skip if this is the initial invoice (already handled by checkout.session.completed)
        if invoice.get("billing_reason") == "subscription_create":
            return success_response("Initial invoice, already handled")
        
        if not stripe_sub_id:
            return success_response("Ignored: no subscription ID")
        
        # Find subscription by stripe_subscription_id
        sub_q = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == stripe_sub_id,
                Subscription.status == SubscriptionStatus.active
            )
        )
        sub = sub_q.scalars().first()
        
        if not sub:
            return success_response("Ignored: subscription not found")
        
        # Extend subscription period based on billing interval
        if sub.billing_interval == BillingInterval.year:
            new_period_end = sub.period_end + timedelta(days=365)
        else:
            new_period_end = sub.period_end + timedelta(days=30)
        
        sub.period_end = new_period_end
        db.add(sub)
        
        # Create transaction record for recurring payment
        invoice_id = invoice.get("id")
        charge_id = invoice.get("charge")
        
        txn = Transaction(
            id=uuid.uuid4(),
            user_id=sub.user_id,
            subscription_id=sub.id,
            reference=invoice_id,  # Use invoice ID as reference
            stripe_invoice_id=invoice_id,
            stripe_charge_id=charge_id,
            transaction_type=TransactionType.recurring,
            provider=PaymentProvider.stripe,
            amount_pence=invoice.get("amount_paid", 0),
            currency=invoice.get("currency", "usd").upper(),
            status=TransactionStatus.success,
        )
        db.add(txn)
    
    # Handle subscription cancellations
    elif event_type == "customer.subscription.deleted":
        subscription_obj = event["data"]["object"]
        stripe_sub_id = subscription_obj.get("id")
        
        if not stripe_sub_id:
            return success_response("Ignored: no subscription ID")
        
        # Find and cancel the subscription
        sub_q = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == stripe_sub_id
            )
        )
        sub = sub_q.scalars().first()
        
        if sub:
            from datetime import datetime, timezone
            sub.auto_renew = False
            sub.canceled_at = datetime.now(timezone.utc)
            sub.status = SubscriptionStatus.cancelled
            db.add(sub)
    
    # Handle failed recurring payments
    elif event_type == "invoice.payment_failed":
        invoice = event["data"]["object"]
        stripe_sub_id = invoice.get("subscription")
        
        if not stripe_sub_id:
            return success_response("Ignored: no subscription ID")
        
        # Find subscription
        sub_q = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == stripe_sub_id
            )
        )
        sub = sub_q.scalars().first()
        
        if sub:
            # Create failed transaction record
            invoice_id = invoice.get("id")
            charge_id = invoice.get("charge")
            
            txn = Transaction(
                id=uuid.uuid4(),
                user_id=sub.user_id,
                subscription_id=sub.id,
                reference=invoice_id,
                stripe_invoice_id=invoice_id,
                stripe_charge_id=charge_id,
                transaction_type=TransactionType.recurring,
                provider=PaymentProvider.stripe,
                amount_pence=invoice.get("amount_due", 0),
                currency=invoice.get("currency", "usd").upper(),
                status=TransactionStatus.failed,
                status_reason=TransactionStatusReason.provider_failed,
                status_message="Recurring payment failed",
                failure_code=invoice.get("last_payment_error", {}).get("code") if isinstance(invoice.get("last_payment_error"), dict) else None,
            )
            db.add(txn)
            
            # TODO: Optionally send email notification
            # TODO: Optionally downgrade after N failed attempts

    await db.commit()

    return success_response("Webhook handled")


    


@router.post("/verify")
async def verify_checkout(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify a checkout session and return current subscription status.
    Request: { session_id }
    """
    session_id = payload.get("session_id")
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required")

    _init_stripe()
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to retrieve session: {e}")

    payment_status = session.get("payment_status")

    # Best-effort fallback: if webhook didn't finalize yet, finalize here when Stripe session is paid
    if payment_status in ("paid", "no_payment_required"):
        # Find pending txn
        txn_q = await db.execute(select(Transaction).where(Transaction.reference == session_id))
        txn = txn_q.scalars().first()
        # Find plan from metadata
        plan_id = session.get("metadata", {}).get("plan_id")
        plan_res = await db.execute(select(Plan).where(Plan.id == plan_id))
        plan = plan_res.scalars().first()
        if plan and txn and txn.status != TransactionStatus.success:
            today = date.today()
            # Cancel existing active sub
            sub_q = await db.execute(
                select(Subscription).where(
                    Subscription.user_id == current_user.id,
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.period_start <= today,
                    Subscription.period_end > today,
                )
            )
            existing = sub_q.scalars().first()
            if existing:
                existing.status = SubscriptionStatus.cancelled
                existing.period_end = today
                db.add(existing)

            new_sub = Subscription(
                user_id=current_user.id,
                plan_id=plan.id,
                period_start=today,
                period_end=today + timedelta(days=30),
                status=SubscriptionStatus.active,
            )
            db.add(new_sub)

            txn.status = TransactionStatus.success
            try:
                setattr(txn, "status_reason", None)
                setattr(txn, "status_message", None)
                setattr(txn, "failure_code", None)
            except Exception:
                pass
            txn.subscription = new_sub
            db.add(txn)
            # Sync user's plan to keep profile consistent
            user_res = await db.execute(select(User).where(User.id == current_user.id))
            user = user_res.scalars().first()
            if user and user.plan_id != plan.id:
                user.plan_id = plan.id
                db.add(user)
            await db.commit()

    # Frontend refetches profile after this
    return success_response("Verified", data={"payment_status": payment_status})


@router.get("/verify-redirect")
async def verify_redirect(session_id: str, redirect: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """Finalize payment (dev fallback) and redirect to frontend dashboard.
    This avoids showing a frontend verification page.
    """
    _init_stripe()
    frontend_base = (settings.FRONTEND_APP_URL or "http://localhost:3000").rstrip("/")
    redirect_to = redirect or f"{frontend_base}/dashboard?paid=1#plans"

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        # Redirect regardless; user can refresh profile
        return RedirectResponse(url=redirect_to, status_code=302)

    # Fallback finalize if Stripe says paid (works in all environments)
    payment_status = session.get("payment_status")
    if payment_status in ("paid", "no_payment_required"):
        txn_q = await db.execute(select(Transaction).where(Transaction.reference == session_id))
        txn = txn_q.scalars().first()
        plan_id = session.get("metadata", {}).get("plan_id")
        user_id = session.get("metadata", {}).get("user_id")
        plan_res = await db.execute(select(Plan).where(Plan.id == plan_id))
        plan = plan_res.scalars().first()
        if plan and user_id and txn and txn.status != TransactionStatus.success:
            today = date.today()
            # Cancel existing active sub
            sub_q = await db.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id,
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.period_start <= today,
                    Subscription.period_end > today,
                )
            )
            existing = sub_q.scalars().first()
            if existing:
                existing.status = SubscriptionStatus.cancelled
                existing.period_end = today
                db.add(existing)

            new_sub = Subscription(
                user_id=user_id,
                plan_id=plan.id,
                period_start=today,
                period_end=today + timedelta(days=30),
                status=SubscriptionStatus.active,
            )
            db.add(new_sub)

            txn.status = TransactionStatus.success
            try:
                setattr(txn, "status_reason", None)
                setattr(txn, "status_message", None)
                setattr(txn, "failure_code", None)
            except Exception:
                pass
            txn.subscription = new_sub
            db.add(txn)

            # Keep user's effective plan in sync with the activated subscription
            user_res = await db.execute(select(User).where(User.id == user_id))
            user = user_res.scalars().first()
            if user and user.plan_id != plan.id:
                user.plan_id = plan.id
                db.add(user)

        # Persist changes so the row isn't left pending if webhook misses
        await db.commit()

    return RedirectResponse(url=redirect_to, status_code=302)
