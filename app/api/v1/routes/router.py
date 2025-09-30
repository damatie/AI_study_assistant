# Main Router - app/api/v1/router.py
from fastapi import APIRouter
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
from app.api.v1.routes.payments.stripe_payments import router as stripe_payments_router
from app.api.v1.routes.payments.paystack_payments import router as paystack_payments_router
from app.api.v1.routes.payments.transactions import router as transactions_router

router = APIRouter()

# Auth routes
router.include_router(auth_router)

# User routes
router.include_router(user_router)

# Subscription routes
router.include_router(subscription_router)

# Materials routes
router.include_router(materials_router)

# Assessments routes
router.include_router(assessments_router)

# Tutoring routes
router.include_router(tutoring_router)

# Flash Cards routes
router.include_router(flash_cards_router)

# Plans routes
router.include_router(plans_router)

# Payments routes
router.include_router(stripe_payments_router)
router.include_router(paystack_payments_router)
router.include_router(transactions_router)

# Get ipinfo routes
router.include_router(ipinfo_router)

# Debug routes (development)
router.include_router(debug_router)
