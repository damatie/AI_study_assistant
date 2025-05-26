# Main Router - app/api/v1/router.py
from fastapi import APIRouter
from app.api.v1.routes.auth.auth import router as auth_router
from app.api.v1.routes.user.user import router as user_router
from app.api.v1.routes.subscription.subscription import router as subscription_router

router = APIRouter()

# Auth routes
router.include_router(auth_router)

# User routes
router.include_router(user_router)

# Subscription routes
router.include_router(subscription_router)
