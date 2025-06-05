# app/routes/auth.py

import secrets
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.schemas.auth.auth_schema import EmailVerificationCodeRequest, LoginRequest, RefreshTokenRequest, Token
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError, jwt

from app.db.deps import get_db
from app.models.user import Role, User
from app.schemas.auth.auth_schema import (
    UserCreate,
    Token,
    EmailVerificationRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.core.security import (
    create_refresh_token,
    get_password_hash,
    require_roles,
    verify_password,
    create_access_token,
    verify_token,
)
from app.core.config import settings
from app.models.user import User
from app.models.plan import Plan
from app.core.security import (
    get_password_hash,
    generate_totp_secret,
    get_totp_code,
    verify_totp_code,
)
from app.services.mail_handler_service.mailer import (
    send_verification_email,
    # send_reset_password_email,
)
from app.services.mail_handler_service.mailer_resend import (
    send_reset_password_email,
    send_verification_email,
    send_welcome_email
)


from app.core.response import error_response, success_response, ResponseModel
from app.services.track_subscription_service.handle_track_subscription import (
    renew_subscription_for_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


async def get_user_by_email(db: AsyncSession, email: str):
    q = await db.execute(select(User).where(User.email == email))
    return q.scalars().first()


async def authenticate_user(db: AsyncSession, email: str, password: str):
    user = await get_user_by_email(db, email)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


# Register User
@router.post("/register", status_code=201, response_model=ResponseModel)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    # 1. Check email isn't already registered
    q_user = await db.execute(select(User).where(User.email == user_in.email))
    if q_user.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # 2. Lookup the default "Freemium" plan
    q_plan = await db.execute(select(Plan).where(Plan.name == "Freemium"))
    default_plan = q_plan.scalars().first()
    if not default_plan:
        raise HTTPException(
            status_code=500, detail="Default subscription plan not configured"
        )

    # 3. Create the User with that plan
    # generate TOTP secret & code
    secret = generate_totp_secret()
    code = get_totp_code(secret, interval=600)

    user = User(
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        email=user_in.email,
        password_hash=get_password_hash(user_in.password),
        plan_id=default_plan.id,
        is_email_verified=False,
        email_verification_secret=secret,
   )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # send the OTP via email to user.email
    await send_verification_email(user.email, code, user.first_name)
    await renew_subscription_for_user(user, db)

    return success_response(
        msg="Registration successful; check your email for a verification code",
        data={"user_id": str(user.id), "first_name": user.first_name, "last_name":  user.last_name, "email": user.email},
        status_code=201,
    )


# User Login
@router.post("/login", response_model=ResponseModel)
async def login(
    creds: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    # 1. Lookup user by email
    q = await db.execute(select(User).where(User.email == creds.email))
    user = q.scalars().first()

    # 2. User not found
    if not user:
        return error_response(
            msg="User not found.",
            data={"error_type": "USER_NOT_FOUND"},
            status_code=status.HTTP_404_NOT_FOUND
        )

    # 3. Invalid password
    if not verify_password(creds.password, user.password_hash):
        return error_response(
            msg="Incorrect email or password.",
            data={"error_type": "INVALID_CREDENTIALS"},
            status_code=status.HTTP_401_UNAUTHORIZED
        )

    # 4. Email not verified
    if not user.is_email_verified:
        return error_response(
            msg="Your email address is not verified. Please check your inbox for the verification code.",
            data={"error_type": "EMAIL_NOT_VERIFIED"},
            status_code=status.HTTP_403_FORBIDDEN
        )

    # 5. All good—issue tokens
    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))

    return success_response(
        msg="Login successful!",
        data={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        },
    )


# Refresh token
@router.post("/refresh", response_model=Token)
async def refresh_token(
    req: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        user_id = verify_token(req.refresh_token, refresh=True)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid user")

    # issue new pair (you may choose to rotate the refresh token)
    new_access = create_access_token(subject=user.id)
    new_refresh = create_refresh_token(subject=user.id)
    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


# Get Current user
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_exception
    return user


# helper to get user by email
async def get_user_by_email(db: AsyncSession, email: str):
    q = await db.execute(select(User).where(User.email == email))
    return q.scalars().first()


def generate_otp(length: int = 6) -> str:
    # numeric OTP; adjust charset if you want alphanumeric
    return "".join(secrets.choice("0123456789") for _ in range(length))


### Email verification endpoint
@router.post("/verify-email", response_model=ResponseModel)
async def verify_email(
    req: EmailVerificationRequest, db: AsyncSession = Depends(get_db)
):
    user = await get_user_by_email(db, req.email)
    if not user:
        raise HTTPException(404, "User not found")
    if user.is_email_verified:
        return success_response(msg="Email is already verified")

    if not user.email_verification_secret or not verify_totp_code(
        user.email_verification_secret, req.otp
    ):
        raise HTTPException(400, "Invalid or expired code")

    
    user.is_email_verified = True
    user.email_verification_secret = None
    await db.commit()

    await send_welcome_email(user.email, user.first_name)
    return success_response(
        msg="Email verified successfully", data=None, status_code=200
    )


### Forgot‑password: generate & store reset OTP
@router.post("/forgot-password", response_model=ResponseModel)
async def forgot_password(
    req: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
):
    user = await get_user_by_email(db, req.email)
    if not user:
        raise HTTPException(404, "User not found")

    # generate & store a new secret
    secret = generate_totp_secret()
    code = get_totp_code(secret, interval=600)
    user.password_reset_secret = secret
    await db.commit()

    # send `code` via email
    await send_reset_password_email(user.email, code, user.first_name)
    return success_response(msg="Password reset code sent", data=None, status_code=200)


### Reset‑password: validate OTP and set new hash
@router.post("/reset-password", response_model=ResponseModel)
async def reset_password(req: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    user = await get_user_by_email(db, req.email)
    if not user or not user.password_reset_secret:
        raise HTTPException(404, "User not found")

    if not verify_totp_code(user.password_reset_secret, req.otp):
        raise HTTPException(400, "Invalid or expired code")

    user.password_hash = get_password_hash(req.new_password)
    user.password_reset_secret = None
    await db.commit()
    return success_response(
        msg="Password has been reset successfully", data=None, status_code=200
    )


# Resend verification otp
@router.post("/resend-verification", response_model=ResponseModel)
async def resend_verification(
    req: EmailVerificationCodeRequest, db: AsyncSession = Depends(get_db)
):
    # 1. Fetch user
    q = await db.execute(select(User).where(User.email == req.email))
    user = q.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 2. If already verified
    if user.is_email_verified:
        return success_response(msg="Email is already verified")

    # 3. Generate & store a new secret
    secret = generate_totp_secret()
    user.email_verification_secret = secret
    await db.commit()

    # 4. Send email
    code = get_totp_code(secret, interval=600)
    await send_verification_email(user.email, code, user.first_name)

    return success_response(msg="Verification code resent to your email")


# Resend reset password
@router.post("/resend-reset-password", response_model=ResponseModel)
async def resend_reset_password(
    req: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
):
    # 1. Fetch user
    q = await db.execute(select(User).where(User.email == req.email))
    user = q.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 2. Generate & store a new secret
    secret = generate_totp_secret()
    user.password_reset_secret = secret
    await db.commit()

    # 3. Send email
    code = get_totp_code(secret, interval=600)
    await send_reset_password_email(user.email, code, user.first_name)

    return success_response(msg="Password reset code resent to your email")

# Logout 
@router.post(
    "/logout",
    response_model=ResponseModel,
)
async def logout(
    current_user=Depends(get_current_user),
):
    """
    Stateless logout: clients should discard their JWT.
    """
    # If you implement token blacklisting, you could add the JWT's jti here.
    return success_response(msg="Logged out successfully")

# # Example admin‑only route
# @router.get("/users", dependencies=[Depends(require_roles(Role.admin))])
# async def list_users(db: AsyncSession = Depends(get_db)):
#     q = await db.execute(select(User))
#     users = q.scalars().all()
#     data = [{"id": str(u.id), "email": u.email, "role": u.role} for u in users]
#     return success_response(msg="User list", data=data)


# @router.get("/me")
# async def read_me(current_user=Depends(get_current_user)):
#     # any logged‑in user can hit this
#     return success_response(msg="Here you are", data={…})


# @router.get(
#   "/admin/dashboard",
#   dependencies=[Depends(require_roles(Role.admin))]
# )
# async def admin_dashboard():
