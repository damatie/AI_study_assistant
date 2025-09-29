from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.response import success_response, error_response
from app.db.deps import get_db
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.transaction import Transaction
from app.models.user import User
from app.utils.enums import SubscriptionStatus, TransactionStatus, TransactionStatusReason
from app.api.v1.routes.auth.auth import get_current_user

import stripe


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

        # End existing active subscription and start a new one month period
        today = date.today()
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
                txn.status_message = "Expired after 60m"
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

    return RedirectResponse(url=redirect_to, status_code=302)
