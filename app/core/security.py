# app/core/security.py
from datetime import datetime, timedelta, timezone
import secrets
from passlib.context import CryptContext
from jose import jwt
import pyotp

from app.core.config import settings
from app.models.user import Role

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    to_encode = {
        "exp": expire,
        "sub": str(subject),
    }
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload = {"exp": expire, "sub": str(subject)}
    return jwt.encode(
        payload, settings.JWT_REFRESH_SECRET, algorithm=settings.JWT_ALGORITHM
    )


def verify_token(token: str, refresh: bool = False) -> str:
    """
    Decode a JWT and return the subject (user_id).
    Raises JWTError on any failure.
    """
    secret = settings.JWT_REFRESH_SECRET if refresh else settings.JWT_SECRET
    alg = settings.JWT_ALGORITHM
    payload = jwt.decode(token, secret, algorithms=[alg])
    return payload.get("sub")


def generate_totp_secret() -> str:
    """
    Generate a new base32 secret for TOTP.
    Store this secret in the DB, and use the code() function to get the time‐based OTP.
    """
    return pyotp.random_base32()


def get_totp_code(secret: str, interval: int = 600) -> str:
    """
    Given a base32 secret, return the OTP code valid for 'interval' seconds.
    """
    totp = pyotp.TOTP(secret, interval=interval)
    return totp.now()


def verify_totp_code(secret: str, code: str, interval: int = 600) -> bool:
    """
    Verify a user‐supplied code against the secret.
    Allows for a one‐step leeway by default.
    """
    totp = pyotp.TOTP(secret, interval=interval)
    return totp.verify(code, valid_window=1)


def require_roles(*allowed: Role):
    """
    Dependency factory: ensures current_user.role in allowed.
    """
    from fastapi import Depends, HTTPException, status
    from app.api.v1.routes.auth.auth import get_current_user

    def _check(current_user=Depends(get_current_user)):
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return current_user

    return _check
