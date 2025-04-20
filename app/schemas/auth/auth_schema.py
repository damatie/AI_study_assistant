# app/schemas/auth_schema.py
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional


# User/auth
class UserCreate(BaseModel):
    email: EmailStr = Field(
        ..., description="User's email address", example="user@example.com"
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="User's password (must be between 8 and 128 characters and include letters, numbers, and special characters)",
        example="securePassword1!",
    )

    # Normalize email
    @field_validator("email", mode="before")
    def normalize_email(cls, email: str) -> str:
        return email.strip().lower()

    # Validate password with strong rules
    @field_validator("password")
    def validate_password(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Password cannot be empty.")
        if not any(char.isdigit() for char in value):
            raise ValueError("Password must include at least one number.")
        if not any(char.isalpha() for char in value):
            raise ValueError("Password must include at least one letter.")
        if not any(char in "!@#$%^&*()-_+=" for char in value):
            raise ValueError(
                "Password must include at least one special character (!@#$%^&*()-_+=)."
            )
        return value


class LoginRequest(BaseModel):
    email: EmailStr = Field(
        ..., description="User's email address", example="user@example.com"
    )
    password: str = Field(..., example="securePassword1!")


# Token
class Token(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


# Token Data
class TokenData(BaseModel):
    user_id: Optional[str] = None


# Email verification
class EmailVerificationRequest(BaseModel):
    email: EmailStr = Field(
        ..., description="User's email address", example="user@example.com"
    )
    otp: str = Field(..., description="OTP code", example="123456")


# Forgot/reset password
class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(
        ..., description="User's email address", example="user@example.com"
    )


# Reset password request
class ResetPasswordRequest(BaseModel):
    email: EmailStr = Field(
        ..., description="User's email address", example="user@example.com"
    )
    otp: str = Field(..., description="OTP code", example="123456")
    new_password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="User's password (must be between 8 and 128 characters and include letters, numbers, and special characters)",
        example="securePassword1!",
    )

    # Validate new password with strong rules
    @field_validator("new_password")
    def validate_password(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Password cannot be empty.")
        if not any(char.isdigit() for char in value):
            raise ValueError("Password must include at least one number.")
        if not any(char.isalpha() for char in value):
            raise ValueError("Password must include at least one letter.")
        if not any(char in "!@#$%^&*()-_+=" for char in value):
            raise ValueError(
                "Password must include at least one special character (!@#$%^&*()-_+=)."
            )
        return value


# Refresh token
class RefreshTokenRequest(BaseModel):
    refresh_token: str
