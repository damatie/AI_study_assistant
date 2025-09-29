from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.response import success_response
from app.db.deps import get_db
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.user import User
from app.models.transaction import Transaction
from app.utils.enums import SubscriptionStatus, TransactionStatus, PaymentProvider, TransactionStatusReason
from app.api.v1.routes.auth.auth import get_current_user

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("/")
async def list_plans(
    currency: Optional[str] = Query(None, description="Optional currency hint (e.g., USD, GBP, NGN) - currently informational"),
    db: AsyncSession = Depends(get_db),
):
    """Return all plans with currency-aware prices array (provider, currency, price_minor, scope).
    For backward compatibility, top-level price_minor/currency are omitted; clients should read prices[].
    """
    result = await db.execute(select(Plan).options(selectinload(Plan.prices)))
    plans = result.scalars().all()

    data = []
    for p in plans:
        price_rows = []
        # Fetch prices lazily (relationship may not be eager-loaded)
        # We avoid another DB call per plan by using ORM relationship
        for pr in getattr(p, "prices", []) or []:
            price_rows.append(
                {
                    "currency": pr.currency,
                    "provider": pr.provider.value if hasattr(pr.provider, "value") else pr.provider,
                    "price_minor": pr.price_minor,
                    "provider_price_id": pr.provider_price_id,
                    "scope_type": pr.scope_type.value if hasattr(pr.scope_type, "value") else pr.scope_type,
                    "scope_value": pr.scope_value,
                }
            )
        data.append(
            {
                "id": str(p.id),
                "sku": p.sku,
                "name": p.name,
                "billing_interval": "month",
                "limits": {
                    "monthly_upload_limit": p.monthly_upload_limit,
                    "monthly_assessment_limit": p.monthly_assessment_limit,
                    "questions_per_assessment": p.questions_per_assessment,
                    "monthly_flash_cards_limit": p.monthly_flash_cards_limit,
                    "max_cards_per_deck": p.max_cards_per_deck,
                },
                "prices": price_rows,
            }
        )

    return success_response("Plans retrieved", data=data)


@router.post("/change-plan")
async def change_plan(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the user's plan.
    - Free plan: switch immediately; end any active subscription today.
    - Paid plan: return a stub checkout payload (integration to be added later).
    """
    plan_id_raw = payload.get("plan_id")
    if not plan_id_raw:
        raise HTTPException(status_code=422, detail="plan_id is required")

    try:
        plan_id = UUID(str(plan_id_raw))
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid plan_id")

    result = await db.execute(
        select(Plan).options(selectinload(Plan.prices)).where(Plan.id == plan_id)
    )
    plan = result.scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # If already on the requested plan, short-circuit
    if current_user.plan_id == plan.id:
        return success_response("You are already on this plan")

    today = date.today()
    # Determine free plan via SKU or zero-priced rows
    prices = getattr(plan, "prices", []) or []
    min_price = None
    try:
        min_price = min((pr.price_minor for pr in prices), default=None)
    except Exception:
        min_price = None
    is_free_plan = (plan.sku or "").upper() == "FREEMIUM" or (min_price == 0)

    if is_free_plan:
        # Switch to free immediately
        current_user.plan_id = plan.id
        # End any active subscription now (do not schedule renewal)
        sub_q = await db.execute(
            select(Subscription).where(
                Subscription.user_id == current_user.id,
                Subscription.status == SubscriptionStatus.active,
                Subscription.period_start <= today,
                Subscription.period_end > today,
            )
        )
        sub = sub_q.scalars().first()
        if sub:
            sub.status = SubscriptionStatus.cancelled
            sub.period_end = today
            db.add(sub)
        db.add(current_user)
        await db.commit()
        return success_response("Switched to Free plan immediately")
    else:
        # Paid plan: resolve regional currency and provider
        country_code = (payload.get("country_code") or "").upper()
        continent_code = (payload.get("continent_code") or "").upper()
        provider_hint = (payload.get("provider") or "auto").lower()

        # Currency resolution (allow explicit override from client display selection)
        resolved_currency = (payload.get("currency") or "").upper()
        if not resolved_currency:
            if country_code == "NG":
                resolved_currency = "NGN"
            elif country_code == "GB" or continent_code == "EU":
                resolved_currency = "GBP"
            else:
                resolved_currency = "USD"

        # Choose price row using shared helper
        from app.services.pricing.selection import pick_price_row
        chosen = pick_price_row(
            prices,
            country_code=country_code,
            continent_code=continent_code,
            resolved_currency=resolved_currency,
        )

        if not chosen:
            raise HTTPException(status_code=422, detail="No price configured for the selected region/currency")

        amount_minor = chosen.price_minor
        provider = chosen.provider
        provider_price_id = getattr(chosen, "provider_price_id", None)

        # Provider override
        if provider_hint in ("stripe", "paystack"):
            provider = PaymentProvider(provider_hint)

        # Optional redirect hint from client so post-payment returns to the current page
        redirect_hint = (
            payload.get("redirect_url")
            or payload.get("return_to")
            or payload.get("current_url")
        )

        # Paystack path
        if provider == PaymentProvider.paystack:
            from app.api.v1.routes.payments.paystack_payments import init_paystack_for_plan
            data = await init_paystack_for_plan(
                db=db,
                current_user=current_user,
                plan=plan,
                currency=resolved_currency,
                amount_minor=amount_minor,
                redirect_url=redirect_hint,
            )
            return success_response("Checkout initialized", data={"provider": data.provider, "checkout_url": data.checkout_url, "reference": data.reference})

        # Stripe path: create a Checkout Session with resolved currency/amount or using a provider-managed price
        from app.api.v1.routes.payments.stripe_payments import _init_stripe
        import stripe

        _init_stripe()

        # URLs for redirect
        api_base = settings.APP_URL.rstrip('/')
        frontend_base = (settings.FRONTEND_APP_URL or "http://localhost:3000").rstrip('/')
        # Allow caller to override URLs if needed; include redirect param for round-trip
        base_success = f"{api_base}/api/v1/payments/stripe/verify-redirect?session_id={{CHECKOUT_SESSION_ID}}"
        if redirect_hint:
            from urllib.parse import quote
            red = quote(redirect_hint, safe='')
            base_success += f"&redirect={red}"
        success_url = payload.get("success_url") or base_success
        # For cancel, prefer sending the user back to the page they started from
        frontend_base = (settings.FRONTEND_APP_URL or "http://localhost:3000").rstrip('/')
        default_cancel = redirect_hint or f"{frontend_base}/dashboard#plans"
        cancel_url = payload.get("cancel_url") or default_cancel

    # Reuse a recent open pending session if it matches; fail stale/mismatched ones
        pending_q = await db.execute(
            select(Transaction)
            .where(
                Transaction.user_id == current_user.id,
                Transaction.provider == PaymentProvider.stripe,
                Transaction.status == TransactionStatus.pending,
            )
            .order_by(Transaction.created_at.desc())
        )
        pending_txns = pending_q.scalars().all()
        reused = False
        # Consider only recent pending sessions to avoid reusing stale rows
        now_utc = datetime.now(timezone.utc)
        max_age = timedelta(hours=24)
        for tx in pending_txns:
            try:
                # Expand to access product name used when the session was created
                sess = stripe.checkout.Session.retrieve(
                    tx.reference,
                    expand=["line_items.data.price.product"],
                )
                sess_status = getattr(sess, "status", None)
                if sess_status == "open":
                    # Skip reuse if the local transaction is too old
                    try:
                        tx_created = getattr(tx, "created_at", None)
                        if not tx_created or (now_utc - tx_created) > max_age:
                            # Expire on Stripe and mark locally failed
                            try:
                                stripe.checkout.Session.expire(tx.reference)
                            except Exception:
                                pass
                            tx.status = TransactionStatus.expired
                            tx.status_reason = TransactionStatusReason.superseded
                            tx.status_message = "Replaced by a newer checkout"
                            tx.expires_at = getattr(tx, 'expires_at', None) or now_utc
                            db.add(tx)
                            continue
                    except Exception:
                        # If we can't compute age, do not reuse
                        try:
                            stripe.checkout.Session.expire(tx.reference)
                        except Exception:
                            pass
                        tx.status = TransactionStatus.expired
                        tx.status_reason = TransactionStatusReason.superseded
                        tx.status_message = "Replaced by a newer checkout"
                        tx.expires_at = getattr(tx, 'expires_at', None) or now_utc
                        db.add(tx)
                        continue
                    meta = getattr(sess, "metadata", {}) or {}
                    same_plan = str(meta.get("plan_id")) == str(plan.id)
                    same_user = str(meta.get("user_id")) == str(current_user.id)
                    # If prior session used a stale product name (e.g., "Basic"), don't reuse
                    displayed_name = None
                    try:
                        li = getattr(sess, "line_items", None)
                        if li and getattr(li, "data", None):
                            item0 = li.data[0]
                            price = getattr(item0, "price", None)
                            product = getattr(price, "product", None)
                            displayed_name = getattr(product, "name", None)
                    except Exception:
                        displayed_name = None
                    name_mismatch = bool(displayed_name) and displayed_name != plan.name
                    # Verify price consistency: if client passed a specific price id, enforce it; else ensure amount/currency match
                    price_ok = True
                    try:
                        li = getattr(sess, "line_items", None)
                        if li and getattr(li, "data", None):
                            item0 = li.data[0]
                            price_obj = getattr(item0, "price", None)
                            sess_price_id = getattr(price_obj, "id", None)
                            sess_currency = getattr(price_obj, "currency", "").upper()
                            sess_unit_amount = getattr(price_obj, "unit_amount", None)
                            client_price_id = payload.get("provider_price_id") or provider_price_id
                            if client_price_id:
                                price_ok = sess_price_id == client_price_id
                            else:
                                price_ok = (sess_currency == resolved_currency.upper() and sess_unit_amount == amount_minor)
                    except Exception:
                        price_ok = False

                    if same_plan and same_user and not name_mismatch and price_ok:
                        # Reuse this session
                        reused = True
                        return success_response(
                            "Checkout initialized",
                            data={
                                "provider": "stripe",
                                "checkout_url": sess.url,
                                "reference": sess.id,
                            },
                        )
                    else:
                        # Different plan/user or stale product name; expire and mark as expired
                        try:
                            stripe.checkout.Session.expire(tx.reference)
                        except Exception:
                            pass
                        tx.status = TransactionStatus.expired
                        tx.status_reason = TransactionStatusReason.superseded
                        tx.status_message = "Replaced by a newer checkout"
                        tx.expires_at = getattr(tx, 'expires_at', None) or now_utc
                        db.add(tx)
                else:
                    # Not open (expired/complete); mark as expired
                    tx.status = TransactionStatus.expired
                    tx.status_reason = TransactionStatusReason.superseded
                    tx.status_message = "Replaced by a newer checkout"
                    tx.expires_at = getattr(tx, 'expires_at', None) or now_utc
                    db.add(tx)
            except Exception:
                # If retrieval fails, mark as expired to avoid perpetually pending records
                tx.status = TransactionStatus.expired
                tx.status_reason = TransactionStatusReason.superseded
                tx.status_message = "Replaced by a newer checkout"
                tx.expires_at = getattr(tx, 'expires_at', None) or now_utc
                db.add(tx)
        if pending_txns:
            await db.commit()

        # Allow client to force a specific price id (e.g., from selected_price_row)
        client_price_id = payload.get("provider_price_id")
        price_id_to_use = client_price_id or provider_price_id
        try:
            if price_id_to_use:
                session = stripe.checkout.Session.create(
                    mode="subscription",
                    line_items=[
                        {
                            "price": price_id_to_use,
                            "quantity": 1,
                        }
                    ],
                    success_url=success_url,
                    cancel_url=cancel_url,
                    metadata={
                        "user_id": str(current_user.id),
                        "plan_id": str(plan.id),
                        "plan_name": plan.name,
                        "plan_sku": plan.sku,
                    },
                )
            else:
                session = stripe.checkout.Session.create(
                    mode="subscription",
                    line_items=[
                        {
                            "price_data": {
                                "currency": resolved_currency.lower(),
                                "unit_amount": amount_minor,
                                "recurring": {"interval": "month"},
                                "product_data": {"name": plan.name},
                            },
                            "quantity": 1,
                        }
                    ],
                    success_url=success_url,
                    cancel_url=cancel_url,
                    metadata={
                        "user_id": str(current_user.id),
                        "plan_id": str(plan.id),
                        "plan_name": plan.name,
                        "plan_sku": plan.sku,
                    },
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Stripe error: {e}")

        # Record a pending transaction so we have a local reference immediately
        try:
            txn = Transaction(
                user_id=current_user.id,
                subscription_id=None,
                reference=session.id,
                authorization_url=session.url,
                provider=PaymentProvider.stripe,
                amount_pence=amount_minor,
                currency=resolved_currency,
                status=TransactionStatus.pending,
                status_reason=TransactionStatusReason.awaiting_payment,
                # Stripe exposes expires_at (seconds since epoch)
                expires_at=(
                    datetime.fromtimestamp(getattr(session, 'expires_at', 0), tz=timezone.utc)
                    if getattr(session, 'expires_at', None) else None
                ),
                status_message="Awaiting payment — session will auto-expire",
            )
            db.add(txn)
            await db.commit()
        except Exception:
            # Non-fatal; webhook/verify can still finalize subscription later
            await db.rollback()

        return success_response(
            msg="Checkout initialized",
            data={
                "provider": "stripe",
                "checkout_url": session.url,
                "reference": session.id,
            },
        )
