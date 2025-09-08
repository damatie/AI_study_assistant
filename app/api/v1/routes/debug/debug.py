# app/api/v1/routes/debug/debug.py
from fastapi import APIRouter, HTTPException
from app.core.response import success_response
from app.core.config import settings
from app.services.storage_service import get_storage_backend, reset_storage_backend
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/debug", tags=["debug"])

@router.get("/storage")
async def debug_storage():
    """Debug storage backend configuration and test connectivity."""
    try:
        # Force reset cached backend to test current config
        reset_storage_backend()
        
        # Get current backend
        backend = get_storage_backend()
        backend_type = type(backend).__name__
        
        # Sanitized config snapshot
        config_info = {
            "STORAGE_BACKEND": settings.STORAGE_BACKEND,
            "S3_BUCKET_NAME": settings.S3_BUCKET_NAME,
            "S3_BUCKET": getattr(settings, 'S3_BUCKET', None),
            "S3_ENDPOINT_URL": settings.S3_ENDPOINT_URL,
            "S3_ENDPOINT": getattr(settings, 'S3_ENDPOINT', None),
            "S3_REGION": settings.S3_REGION,
            "has_access_key": bool(settings.S3_ACCESS_KEY_ID or getattr(settings, 'AWS_ACCESS_KEY_ID', None)),
            "has_secret_key": bool(settings.S3_SECRET_ACCESS_KEY or getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)),
            "S3_PUBLIC_BASE_URL": settings.S3_PUBLIC_BASE_URL,
            "R2_PUBLIC_BASE_URL": getattr(settings, 'R2_PUBLIC_BASE_URL', None),
        }
        
        # Test backend functionality
        test_results = {}
        
        if backend_type == "S3StorageBackend":
            # Test S3 connectivity
            try:
                # Try to generate a presigned URL for a test key
                test_key = "test/connectivity-check.txt"
                presigned_url = await backend.get_presigned_url(key=test_key, expires_in=60)
                test_results["presigned_url_generation"] = "success"
                test_results["sample_presigned_url"] = presigned_url[:50] + "..." if presigned_url else None
            except Exception as e:
                test_results["presigned_url_generation"] = f"failed: {str(e)}"
        else:
            test_results["local_backend_base_path"] = getattr(backend, 'base_path', 'unknown')
        
        return success_response(
            msg="Storage debug info",
            data={
                "backend_type": backend_type,
                "config": config_info,
                "tests": test_results,
            }
        )
        
    except Exception as e:
        logger.exception("Debug storage endpoint failed")
        raise HTTPException(status_code=500, detail=f"Debug failed: {str(e)}")
