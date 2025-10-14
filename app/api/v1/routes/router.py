# Main Router - app/api/v1/router.py
from fastapi import APIRouter, Depends
from app.api.dependencies.subscription import ensure_user_has_subscription
from app.api.v1.routes.auth.auth import router as auth_router
from app.api.v1.routes.user.user import router as user_router
from app.api.v1.routes.subscription.subscription import router as subscription_router
from app.api.v1.routes.open.ipinfo import router as ipinfo_router
from app.api.v1.routes.materials.materials import router as materials_router
from app.api.v1.routes.assessments.assessments import router as assessments_router
from app.api.v1.routes.tutoring.tutoring import router as tutoring_router
from app.api.v1.routes.debug.debug import router as debug_router
from app.api.v1.routes.flash_cards.flash_cards import router as flash_cards_router
from app.api.v1.routes.plans.plans import router as plans_router
from app.api.v1.routes.payments.transactions import router as transactions_router
# New payment system
from app.api.v1.routes.payments import (
    checkout_router,
    stripe_webhooks_router,
    paystack_webhooks_router,
)

router = APIRouter()

# Public/Auth routes (no subscription check needed)
router.include_router(auth_router)
router.include_router(ipinfo_router)

# Webhook routes (no user authentication)
router.include_router(stripe_webhooks_router)
router.include_router(paystack_webhooks_router)

# Protected routes (require active subscription)
protected_router = APIRouter(dependencies=[Depends(ensure_user_has_subscription)])
protected_router.include_router(user_router)
protected_router.include_router(subscription_router)
protected_router.include_router(materials_router)
protected_router.include_router(assessments_router)
protected_router.include_router(tutoring_router)
protected_router.include_router(flash_cards_router)
protected_router.include_router(plans_router)
protected_router.include_router(checkout_router)
protected_router.include_router(transactions_router)

# Include protected router in main router
router.include_router(protected_router)

# Debug routes (development - typically would be public or have separate auth)
router.include_router(debug_router)
