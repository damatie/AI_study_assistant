# Main Router - app/api/v1/router.py
from fastapi import APIRouter
from app.api.v1.routes.auth.auth import router as auth_router


router = APIRouter()

# Auth routes
router.include_router(auth_router)
