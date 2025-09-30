import hmac
import hashlib
import json
import uuid
from datetime import date, timedelta, datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.response import ResponseModel, error_response, success_response
from starlette.responses import RedirectResponse
from app.db.deps import get_db
from app.models.plan import Plan as PlanModel
from app.models.subscription import Subscription as SubscriptionModel
from app.models.user import User as UserModel
from app.models.transaction import Transaction as TransactionModel
from app.utils.enums import PaymentProvider, SubscriptionStatus, TransactionStatus, TransactionStatusReason


router = APIRouter(prefix="/payments/paystack", tags=["payments", "paystack"])


class CheckoutData(BaseModel):
    provider: str
    checkout_url: str
    reference: str


async def init_paystack_for_plan(*, db: AsyncSession, current_user, plan: PlanModel, currency: str = "GBP", amount_minor: int | None = None, redirect_url: str | None = None) -> CheckoutData:
    """Create pending subscription, initialize Paystack, record transaction, and return CheckoutData.

    currency: 'NGN' | 'USD' | 'GBP'
    amount_minor: Minor units for the chosen currency. Must be provided by caller from plan_prices.
    """
    # Do not create a Subscription yet; mirror Stripe flow. We'll create on verify success.

    # Cleanup: expire any prior pending Paystack transactions for this user
    try:
        pending_q = await db.execute(
            select(TransactionModel)
            .where(
                TransactionModel.user_id == current_user.id,
                TransactionModel.provider == PaymentProvider.paystack,
                TransactionModel.status == TransactionStatus.pending,
            )
        )
        now_utc = datetime.now(timezone.utc)
        for old_tx in pending_q.scalars().all():
            old_tx.status = TransactionStatus.expired
            old_tx.status_reason = TransactionStatusReason.superseded
            old_tx.status_message = "Replaced by a newer checkout"
            if not getattr(old_tx, 'expires_at', None):
                old_tx.expires_at = now_utc
            db.add(old_tx)
        if pending_q:
            await db.commit()
    except Exception:
        # Non-fatal; continue to create a fresh checkout
        await db.rollback()

    # Initialize Paystack transaction
    initialize_url = "https://api.paystack.co/transaction/initialize"
    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    # Callback should hit backend verify-redirect to finalize subscription and redirect to frontend
    api_base = settings.APP_URL.rstrip("/")
    if not amount_minor or amount_minor <= 0:
        raise HTTPException(status_code=422, detail="Invalid amount for Paystack checkout")

    # Build callback URL, preserving intended redirect target if provided (to match Stripe behavior)
    callback_url = f"{api_base}/api/v1/payments/paystack/verify-redirect"
    try:
        if redirect_url:
            from urllib.parse import quote
            callback_url = f"{callback_url}?redirect={quote(redirect_url, safe='')}"
    except Exception:
        # Fallback silently to base callback if quoting fails
        pass

    payload = {
        "email": current_user.email,
        # Paystack expects minor units
        "amount": amount_minor,
        # Currency can be NGN, USD, GBP depending on account configuration
        "currency": currency,
        "callback_url": callback_url,
        # Optional metadata for reconciliation
        "metadata": {
            "user_id": str(current_user.id),
            "plan_id": str(plan.id),
            "plan_name": plan.name,
            "plan_sku": plan.sku,
            **({"redirect": redirect_url} if redirect_url else {}),
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(initialize_url, json=payload, headers=headers)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Surface as HTTP 502 for upstream failure when reused from endpoints
            raise HTTPException(status_code=502, detail=f"Paystack init failed: {e.response.text}")
        data = resp.json().get("data")
        if not data:
            raise HTTPException(status_code=502, detail="Invalid response from Paystack: missing data")

    authorization_url = data.get("authorization_url")
    reference = data.get("reference")
    if not authorization_url or not reference:
        raise HTTPException(status_code=502, detail="Invalid response from Paystack: missing url/reference")

    # 4) Persist Transaction record
    # Set a TTL for Paystack attempts (e.g., 60 minutes)
    expires = datetime.now(timezone.utc) + timedelta(minutes=60)
    txn = TransactionModel(
        id=uuid.uuid4(),
        user_id=current_user.id,
        subscription_id=None,
        reference=reference,
        authorization_url=authorization_url,
        provider=PaymentProvider.paystack,
        amount_pence=amount_minor,
        currency=currency,
    status=TransactionStatus.pending,
        expires_at=expires,
    status_reason=TransactionStatusReason.awaiting_payment,
    # Human-readable tooltip without ISO timestamp
    status_message="Awaiting payment — session will auto-expire",
    )
    db.add(txn)
    await db.commit()

    return CheckoutData(provider="paystack", checkout_url=authorization_url, reference=reference)



class VerifyRequest(BaseModel):
    reference: str


@router.post(
    "/verify",
    response_model=ResponseModel,
)
async def paystack_verify(
    req: VerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify a Paystack transaction by reference and activate subscription on success."""
    verify_url = f"https://api.paystack.co/transaction/verify/{req.reference}"
    headers = {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}
    # Call Paystack verify API
    async with httpx.AsyncClient() as client:
        resp = await client.get(verify_url, headers=headers)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return error_response(
                msg=f"Paystack verify failed: {e.response.text}",
                status_code=e.response.status_code,
            )
        data = resp.json().get("data")
        if not data:
            return error_response("Invalid response from Paystack: missing data")

    status_str = data.get("status")
    reference = data.get("reference") or req.reference
    meta = data.get("metadata") or {}
    plan_id_str = meta.get("plan_id")

    # Update transaction + subscription
    result = await db.execute(
        select(TransactionModel).where(TransactionModel.reference == reference)
    )
    txn: TransactionModel | None = result.scalar_one_or_none()
    if not txn:
        # Strict: require exact reference match to avoid touching stale rows
        return error_response("Transaction not found for reference", status_code=404)

    if status_str == "success":
        txn.status = TransactionStatus.success
        txn.status_reason = None
        try:
            txn.status_message = None
            txn.failure_code = None
        except Exception:
            pass
        # Ensure there is an active subscription for this user/plan
        if txn.subscription_id:
            sub = await db.get(SubscriptionModel, txn.subscription_id)
        else:
            # Create a new subscription if none was staged at init time
            sub = None
        # Before creating/activating, cancel any currently active subscription for this user (mirror Stripe behavior)
        try:
            if txn.user_id:
                today = date.today()
                existing_q = await db.execute(
                    select(SubscriptionModel).where(
                        SubscriptionModel.user_id == txn.user_id,
                        SubscriptionModel.status == SubscriptionStatus.active,
                        SubscriptionModel.period_start <= today,
                        SubscriptionModel.period_end > today,
                    )
                )
                existing = existing_q.scalars().first()
                # Avoid cancelling the same sub if already linked
                if existing and (not sub or existing.id != sub.id):
                    existing.status = SubscriptionStatus.cancelled
                    existing.period_end = today
                    db.add(existing)
        except Exception:
            pass

        if not sub:
            # Resolve plan and user
            plan = await db.get(PlanModel, plan_id_str) if plan_id_str else None
            user = await db.get(UserModel, txn.user_id) if txn.user_id else None
            if plan and user:
                today = date.today()
                sub = SubscriptionModel(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    plan_id=plan.id,
                    period_start=today,
                    period_end=today + timedelta(days=30),
                    status=SubscriptionStatus.active,
                )
                db.add(sub)
                await db.flush()  # to get sub.id
                txn.subscription_id = sub.id
                # Sync user's effective plan similar to Stripe verify
                if getattr(user, "plan_id", None) != plan.id:
                    user.plan_id = plan.id
                    db.add(user)
        else:
            # Make sure staged subscription is active
            sub.status = SubscriptionStatus.active
            db.add(sub)
        db.add(txn)
        # Mark other pending Paystack txns for this user as failed to avoid stale pendings
        if txn.user_id:
            others_q = await db.execute(
                select(TransactionModel)
                .where(
                    TransactionModel.user_id == txn.user_id,
                    TransactionModel.provider == PaymentProvider.paystack,
                    TransactionModel.status == TransactionStatus.pending,
                    TransactionModel.reference != reference,
                )
            )
            for other in others_q.scalars().all():
                other.status = TransactionStatus.failed
                # Mark as superseded by a successful attempt
                other.status_reason = TransactionStatusReason.superseded
                db.add(other)
        await db.commit()
        return success_response("Verified", data={"status": "success"})

    # Any other state is treated as failed for now
    txn.status = TransactionStatus.failed
    txn.status_reason = TransactionStatusReason.provider_failed
    # Best-effort map of provider error details
    try:
        txn.failure_code = data.get("gateway_response") or status_str
        # Paystack may include a top-level message
        msg = data.get("message")
        txn.status_message = msg or f"Paystack status: {status_str}"
    except Exception:
        pass
    await db.commit()
    return error_response("Payment failed", status_code=400)
    
@router.get(
    "/verify",
    response_model=ResponseModel,
)
async def paystack_verify_get(reference: str, db: AsyncSession = Depends(get_db)):
    """GET variant to verify a Paystack transaction by reference (handy for manual/local testing)."""
    return await paystack_verify(VerifyRequest(reference=reference), db)

@router.get("/verify-redirect")
async def paystack_verify_redirect(trxref: str | None = None, reference: str | None = None, redirect: str | None = None, db: AsyncSession = Depends(get_db)):
    """Paystack callback endpoint (GET) that verifies a transaction and redirects to the app dashboard.

    Paystack sends `reference` and often `trxref` as query params. We'll use whichever is present.
    """
    ref = reference or trxref
    frontend_base = (settings.FRONTEND_APP_URL or "http://localhost:3000").rstrip("/")
    # Default to dashboard if no redirect is provided anywhere
    redirect_to = redirect or f"{frontend_base}/dashboard?paid=1#plans"

    if not ref:
        # If reference is missing, just redirect back to dashboard
        return RedirectResponse(url=redirect_to, status_code=302)

    # Reuse the verify logic, and attempt to pull metadata.redirect if not provided in query
    try:
        # Call verify and inspect any metadata in Paystack response by re-calling their API
        result = await paystack_verify(VerifyRequest(reference=ref), db)
        if (not redirect) and isinstance(result, dict) and result.get("status") == "success":
            # Best effort: if we had access to Paystack response, we'd read metadata.redirect here.
            # Our paystack_verify already updates DB; we can look up the transaction to fetch stored info if needed.
            # For safety and simplicity, we rely on the callback_url query param we set at init time above.
            pass
    except Exception:
        # Swallow errors and redirect
        pass
    return RedirectResponse(url=redirect_to, status_code=302)


@router.post("/webhook")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str | None = Header(None, alias="x-paystack-signature"),
    db: AsyncSession = Depends(get_db),
):
    """Handle Paystack webhook events.

    Validates the request with HMAC SHA512 using PAYSTACK_WEBHOOK_SECRET and updates
    local Transaction/Subscription state on charge.success.
    """
    raw_body = await request.body()

    secret = settings.PAYSTACK_WEBHOOK_SECRET
    if secret and x_paystack_signature:
        computed = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(computed, x_paystack_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = payload.get("event")
    data = payload.get("data", {})
    reference = data.get("reference")
    metadata = data.get("metadata") or {}

    if event == "charge.success" and reference:
        result = await db.execute(
            select(TransactionModel).where(TransactionModel.reference == reference)
        )
        txn: TransactionModel | None = result.scalar_one_or_none()
        if txn:
            txn.status = TransactionStatus.success
            sub = None
            if txn.subscription_id:
                sub = await db.get(SubscriptionModel, txn.subscription_id)
                if sub:
                    sub.status = SubscriptionStatus.active
            # If no subscription linked, create one using metadata (plan_id) and txn.user_id
            if not sub:
                plan_id_str = metadata.get("plan_id")
                user_id = getattr(txn, 'user_id', None) or metadata.get("user_id")
                if plan_id_str and user_id:
                    try:
                        # Before creating/activating, cancel any currently active subscription for this user (mirror Stripe behavior)
                        try:
                            today = date.today()
                            existing_q = await db.execute(
                                select(SubscriptionModel).where(
                                    SubscriptionModel.user_id == user_id,
                                    SubscriptionModel.status == SubscriptionStatus.active,
                                    SubscriptionModel.period_start <= today,
                                    SubscriptionModel.period_end > today,
                                )
                            )
                            existing = existing_q.scalars().first()
                            if existing:
                                existing.status = SubscriptionStatus.cancelled
                                existing.period_end = today
                                db.add(existing)
                        except Exception:
                            pass

                        plan = await db.get(PlanModel, plan_id_str)
                        user = await db.get(UserModel, user_id)
                        if plan and user:
                            today = date.today()
                            sub = SubscriptionModel(
                                id=uuid.uuid4(),
                                user_id=user.id,
                                plan_id=plan.id,
                                period_start=today,
                                period_end=today + timedelta(days=30),
                                status=SubscriptionStatus.active,
                            )
                            db.add(sub)
                            await db.flush()
                            txn.subscription_id = sub.id
                            # Sync user's effective plan
                            if getattr(user, 'plan_id', None) != plan.id:
                                user.plan_id = plan.id
                                db.add(user)
                    except Exception:
                        pass
            # Mark other pending Paystack transactions for this user as failed
            try:
                if txn.user_id:
                    others_q = await db.execute(
                        select(TransactionModel)
                        .where(
                            TransactionModel.user_id == txn.user_id,
                            TransactionModel.provider == PaymentProvider.paystack,
                            TransactionModel.status == TransactionStatus.pending,
                            TransactionModel.reference != reference,
                        )
                    )
                    for other in others_q.scalars().all():
                        other.status = TransactionStatus.failed
                        other.status_reason = TransactionStatusReason.superseded
                        db.add(other)
            except Exception:
                pass
            await db.commit()

    return {"received": True}
