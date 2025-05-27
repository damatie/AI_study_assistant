# app/api/v1/routes/payments.py

import uuid
import httpx
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.deps import get_db
from app.core.config import settings
from app.core.response import success_response, error_response, ResponseModel
from app.api.v1.routes.auth.auth import get_current_user
from app.models.plan import Plan as PlanModel
from app.models.subscription import Subscription as SubscriptionModel
from app.models.transaction  import Transaction as TransactionModel
from app.utils.enums import SubscriptionStatus, TransactionStatus

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


class CheckoutRequest(BaseModel):
    plan_id: uuid.UUID


class CheckoutResponse(BaseModel):
    authorization_url: str
    reference:         str


@router.post(
    "/checkout",
    response_model=ResponseModel[CheckoutResponse],
    status_code=status.HTTP_201_CREATED,
)
async def checkout(
    req: CheckoutRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1) Load the plan, ensure paid
    plan = await db.get(PlanModel, req.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.price_pence <= 0:
        return error_response(
            msg="Cannot checkout a free plan",
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # 2) Create a pending Subscription for the next cycle
    today = date.today()
    sub = SubscriptionModel(
        id=uuid.uuid4(),
        user_id=current_user.id,
        plan_id=plan.id,
        period_start=today,
        period_end=today + timedelta(days=30),  # adjust for exact month if needed
        status=SubscriptionStatus.pending_payment
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)

    # 3) Initialize Paystack transaction
    initialize_url = "https://api.paystack.co/transaction/initialize"
    headers = {
        "Authorization": f"Bearer {settings.paystack_secret_key}",
        "Content-Type":  "application/json",
    }
    payload = {
        "email":        current_user.email,
        "amount":       plan.price_pence,
        "currency":     "GBP",                    # or "NGN", etc.
        "callback_url": f"{settings.app_url}/payments/verify"
    }

    async with httpx.AsyncClient() as client:
        ps_resp = await client.post(initialize_url, json=payload, headers=headers)
        ps_resp.raise_for_status()
        data = ps_resp.json()["data"]

    # 4) Persist Transaction record
    txn = TransactionModel(
        id                = uuid.uuid4(),
        user_id           = current_user.id,
        subscription_id   = sub.id,
        reference         = data["reference"],
        authorization_url = data["authorization_url"],
        amount_pence      = plan.price_pence,
        currency          = "GBP",
        status            = TransactionStatus.pending,
    )
    db.add(txn)
    await db.commit()

    # 5) Return the Paystack link & reference
    return success_response(
        msg="Checkout initialized",
        data=CheckoutResponse(
            authorization_url=data["authorization_url"],
            reference=data["reference"],
        ),
        status_code=status.HTTP_201_CREATED
    )
