from __future__ import annotations

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routes.auth.auth import get_current_user
from app.core.config import settings
from app.core.response import ResponseModel, success_response, error_response
from app.db.deps import get_db
from app.models.transaction import Transaction
from app.utils.enums import PaymentProvider, TransactionStatus

router = APIRouter(prefix="/payments/transactions", tags=["payments", "transactions"])


def _ev(v):
    """Extract enum value or return value as-is."""
    return v.value if hasattr(v, "value") else v


@router.get("/", response_model=ResponseModel)
async def list_transactions(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    include_inactive: bool = Query(False, description="Include expired/canceled attempts"),
):
    """List the current user's transactions (most recent first by effective date)."""
    # Order by the same logic as `display_at` to keep UI consistent:
    # success -> updated_at; otherwise -> created_at
    effective_ts = case(
        (Transaction.status == TransactionStatus.success, Transaction.updated_at),
        else_=Transaction.created_at,
    )
    stmt = select(Transaction).where(Transaction.user_id == current_user.id)
    if not include_inactive:
        stmt = stmt.where(Transaction.status != TransactionStatus.expired)
        stmt = stmt.where(Transaction.status != TransactionStatus.canceled)
    result = await db.execute(stmt.order_by(effective_ts.desc()))
    rows = result.scalars().all()

    data = []
    for tx in rows:
        created = tx.created_at.isoformat() if getattr(tx, "created_at", None) else None
        updated = tx.updated_at.isoformat() if getattr(tx, "updated_at", None) else None
        is_success = str(_ev(tx.status)) == "success"
        data.append(
            {
                "id": str(tx.id),
                "subscription_id": str(tx.subscription_id) if tx.subscription_id else None,
                "provider": _ev(tx.provider),
                "amount_minor": tx.amount_pence,
                "amount": (float(tx.amount_pence) / 100.0) if tx.amount_pence is not None else None,
                "currency": tx.currency,
                "status": _ev(tx.status),
                "status_reason": _ev(getattr(tx, 'status_reason', None)) if getattr(tx, 'status_reason', None) else None,
                "status_message": getattr(tx, 'status_message', None),
                "reference": tx.reference,
                "authorization_url": tx.authorization_url,
                "created_at": created,
                "updated_at": updated,
                "expires_at": tx.expires_at.isoformat() if getattr(tx, 'expires_at', None) else None,
                # Effective date: updated_at for success, else created_at
                "display_at": updated if is_success else created,
            }
        )
    return success_response("Transactions retrieved", data=data)


@router.get("/{txn_id}/invoice", response_model=ResponseModel)
async def get_invoice_link(
    txn_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a provider-hosted invoice or receipt URL when available.

    Stripe: tries hosted_invoice_url first, falls back to latest charge receipt_url.
    Paystack: returns verification details; public receipt link may not be available.
    """
    tx = await db.get(Transaction, txn_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if tx.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if tx.provider == PaymentProvider.stripe:
        # Stripe: retrieve session, expand invoice/payment_intent
        try:
            from app.api.v1.routes.payments.stripe_payments import _init_stripe  # lazy import
            import stripe

            _init_stripe()
            session = stripe.checkout.Session.retrieve(
                tx.reference,
                expand=["invoice", "payment_intent.latest_charge"],
            )

            invoice_url = None
            receipt_url = None
            invoice_id = None

            inv = getattr(session, "invoice", None)
            if inv:
                try:
                    invoice_id = getattr(inv, "id", None) or inv
                    inv_obj = stripe.Invoice.retrieve(invoice_id)
                    invoice_url = getattr(inv_obj, "hosted_invoice_url", None)
                    # Optional PDF
                    receipt_url = getattr(inv_obj, "invoice_pdf", None) or receipt_url
                except Exception:
                    pass

            if not invoice_url:
                pi = getattr(session, "payment_intent", None)
                charge = getattr(pi, "latest_charge", None) if pi else None
                if charge:
                    try:
                        ch = stripe.Charge.retrieve(getattr(charge, "id", charge))
                        receipt_url = getattr(ch, "receipt_url", None) or receipt_url
                    except Exception:
                        pass

            return success_response(
                "Invoice link",
                data={
                    "provider": "stripe",
                    "invoice_url": invoice_url,
                    "receipt_url": receipt_url,
                    "session_id": getattr(session, "id", None),
                    "invoice_id": invoice_id,
                },
            )
        except Exception as e:
            return error_response(
                msg=f"Unable to retrieve Stripe invoice: {e}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

    if tx.provider == PaymentProvider.paystack:
        # Paystack: verify to expose transaction status/receipt_number. Public receipt link is not typically provided.
        verify_url = f"https://api.paystack.co/transaction/verify/{tx.reference}"
        headers = {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(verify_url, headers=headers)
                resp.raise_for_status()
                body = resp.json().get("data") or {}
        except httpx.HTTPStatusError as e:
            return error_response(
                msg=f"Paystack verify failed: {e.response.text}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            return error_response(
                msg=f"Paystack verify failed: {e}",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        return success_response(
            "Invoice detail",
            data={
                "provider": "paystack",
                "status": body.get("status"),
                "reference": tx.reference,
                "amount": (float(tx.amount_pence) / 100.0) if tx.amount_pence is not None else None,
                "currency": tx.currency,
                "receipt_number": body.get("receipt_number"),
                "invoice_url": None,
                "receipt_url": None,
            },
        )

    return error_response("Unsupported provider", status_code=400)
