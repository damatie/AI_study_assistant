"""Storage backend abstractions for study material uploads.

Provides a pluggable interface with S3-compatible implementation (Cloudflare R2)
and local fallback. Main functions: store_bytes(), generate_access_url().
"""
from __future__ import annotations
import mimetypes
import uuid
import os
from dataclasses import dataclass
from typing import Protocol, Optional
import logging
from app.core.config import settings

try:
    import boto3
    from botocore.client import Config as BotoConfig
    BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None
    BotoConfig = None
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)

class StorageBackend(Protocol):
    async def store_bytes(self, *, data: bytes, filename: str, content_type: str | None = None) -> str:
        """Store raw bytes and return object key."""
        ...
    async def get_presigned_url(self, *, key: str, expires_in: int | None = None) -> Optional[str]:
        ...
    def public_url(self, *, key: str) -> Optional[str]:
        ...
    async def get_bytes(self, *, key: str) -> bytes:
        """Retrieve raw bytes for a previously stored object key."""
        ...

@dataclass
class LocalStorageBackend:
    base_path: str = "uploads"

    async def store_bytes(self, *, data: bytes, filename: str, content_type: str | None = None) -> str:
        os.makedirs(self.base_path, exist_ok=True)
        ext = filename.rsplit('.', 1)[1] if '.' in filename else ''
        key = f"{uuid.uuid4()}.{ext}" if ext else str(uuid.uuid4())
        path = os.path.join(self.base_path, key)
        with open(path, 'wb') as f:
            f.write(data)
        return key

    async def get_presigned_url(self, *, key: str, expires_in: int | None = None) -> Optional[str]:
        return None  # Local storage not exposed publicly

    def public_url(self, *, key: str) -> Optional[str]:
        return None  # Local storage not exposed publicly

    async def get_bytes(self, *, key: str) -> bytes:
        path = os.path.join(self.base_path, key)
        with open(path, 'rb') as f:
            return f.read()

@dataclass
class S3StorageBackend:
    bucket: str
    endpoint_url: str | None
    region: str | None
    access_key: str
    secret_key: str

    def __post_init__(self):
        if not BOTO3_AVAILABLE:
            raise RuntimeError("boto3 not installed; cannot use S3StorageBackend")
        self.client = boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region or 'auto',
            config=BotoConfig(signature_version='s3v4')
        )

    async def store_bytes(self, *, data: bytes, filename: str, content_type: str | None = None) -> str:
        import asyncio
        # Determine content type and file extension
        content_type = content_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        ext = filename.rsplit('.', 1)[1] if '.' in filename else ''
        key = f"materials/{uuid.uuid4()}.{ext}" if ext else f"materials/{uuid.uuid4()}"
        
        # Upload to S3/R2
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._upload_sync, key, data, content_type)
        return key

    def _upload_sync(self, key: str, data: bytes, content_type: str):
        """Synchronous upload helper for executor."""
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def get_presigned_url(self, *, key: str, expires_in: int | None = None) -> Optional[str]:
        import asyncio
        expires = expires_in or settings.S3_PRESIGN_EXPIRES
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._presign_sync, key, expires)

    def _presign_sync(self, key: str, expires: int) -> str:
        """Synchronous presign helper for executor."""
        return self.client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': self.bucket, 'Key': key},
            ExpiresIn=expires
        )

    def public_url(self, *, key: str) -> Optional[str]:
        if settings.S3_PUBLIC_BASE_URL:
            return f"{settings.S3_PUBLIC_BASE_URL.rstrip('/')}/{key}"
        return None

    async def get_bytes(self, *, key: str) -> bytes:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_object_bytes_sync, key)

    def _get_object_bytes_sync(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj['Body'].read()

_backend: StorageBackend | None = None

def reset_storage_backend():
    """Force reset of cached storage backend for testing."""
    global _backend
    _backend = None

def get_storage_backend() -> StorageBackend:
    """Get storage backend instance (cached)."""
    global _backend
    if _backend is not None:
        return _backend
    
    # Get config values with fallback to alternative names
    bucket = settings.S3_BUCKET_NAME or getattr(settings, 'S3_BUCKET', None)
    access = settings.S3_ACCESS_KEY_ID or getattr(settings, 'AWS_ACCESS_KEY_ID', None)
    secret = settings.S3_SECRET_ACCESS_KEY or getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
    endpoint = settings.S3_ENDPOINT_URL or getattr(settings, 'S3_ENDPOINT', None)
    
    # Determine if S3 should be used
    use_s3 = (settings.STORAGE_BACKEND.lower() == 's3' and 
              bucket and access and secret and BOTO3_AVAILABLE)
    
    if use_s3:
        try:
            _backend = S3StorageBackend(
                bucket=bucket,
                endpoint_url=endpoint,
                region=settings.S3_REGION,
                access_key=access,
                secret_key=secret,
            )
            logger.info(f"Initialized S3StorageBackend with bucket={bucket}")
            return _backend
        except Exception as e:
            logger.error(f"S3StorageBackend initialization failed: {e}")
    
    # Fallback to local storage
    _backend = LocalStorageBackend()
    logger.info("Initialized LocalStorageBackend")
    return _backend

async def store_bytes(*, data: bytes, filename: str, content_type: str | None = None) -> str:
    return await get_storage_backend().store_bytes(data=data, filename=filename, content_type=content_type)

async def generate_access_url(*, key: str) -> Optional[str]:
    backend = get_storage_backend()
    # Prefer presigned if available
    url = await backend.get_presigned_url(key=key)
    if url:
        return url
    return backend.public_url(key=key)
