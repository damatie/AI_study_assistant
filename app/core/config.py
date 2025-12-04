# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,  # Environment vars are uppercase
        extra="ignore",      # Ignore unexpected vars instead of raising
    )

    # Core application settings
    DATABASE_URL: str
    GOOGLE_API_KEY: str
    HOST: str = "0.0.0.0"
    PORT: int = 8101
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    APP_URL: str
    FRONTEND_APP_URL: str | None = None
    LOGO: str = 'https://res.cloudinary.com/webmataz/image/upload/v1763240346/full-logo-blue_d0pynz.png'
    
    # CORS settings
    ALLOWED_ORIGINS: list[str] = ["*"]  # In production, specify actual origins
    ALLOW_CREDENTIALS: bool = True
    ALLOWED_METHODS: list[str] = ["*"]
    ALLOWED_HEADERS: list[str] = ["*"]
    
    # Assessment settings
    DEFAULT_MAX_QUESTIONS: int = 5

    # JWT settings
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 43200  # 30 days
    JWT_REFRESH_SECRET: str
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    EMAIL_TOKEN_EXPIRE_MINUTES: int = 120
    VALID_WINDOW: int = 20

    # Email sender settings (Sendgrid)
    EMAIL_USERNAME: str
    EMAIL_PASSWORD: str
    SMTP_SERVER: str
    SMTP_PORT: int = 587
    FROM_EMAIL: str

    # Email sender settings (Resend)
    RESEND_API_KEY: str
    RESEND_FROM_EMAIL: str
    SUPPORT_EMAIL: str = "support@knoledg.com"

    # Payment gateway settings
    PAYSTACK_SECRET_KEY: str
    PAYSTACK_PUBLIC_KEY: str
    PAYSTACK_WEBHOOK_SECRET: str
    STRIPE_SECRET_KEY: str
    STRIPE_PUBLIC_KEY: str
    STRIPE_WEBHOOK_SECRET: str

    # External object storage (Cloudflare R2 / S3 compatible)
    STORAGE_BACKEND: str = "local"  # 'local' | 's3'
    S3_BUCKET_NAME: str | None = None
    S3_ENDPOINT_URL: str | None = None
    S3_REGION: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_PUBLIC_BASE_URL: str | None = None
    S3_PRESIGN_EXPIRES: int = 3600  # seconds
    
    # Alternative naming variants for backward compatibility
    S3_BUCKET: str | None = None
    S3_ENDPOINT: str | None = None
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    R2_PUBLIC_BASE_URL: str | None = None
    R2_ACCOUNT_ID: str | None = None  # not used but allowed

    # Refund policy
    REFUND_COOL_OFF_HOURS: int = 24

    # Document conversion (Gotenberg)
    GOTENBERG_URL: str | None = None
    GOTENBERG_TIMEOUT_SECONDS: int = 45
    GOTENBERG_MAX_FILE_SIZE_MB: int | None = 40
    GOTENBERG_SKIP_TLS_VERIFY: bool = False
    GOTENBERG_HEALTHCHECK_PATH: str = "health"


def _normalize_settings(settings: Settings) -> None:
    """Normalize alternative environment variable names into canonical ones."""
    # Bucket
    if not settings.S3_BUCKET_NAME and settings.S3_BUCKET:
        settings.S3_BUCKET_NAME = settings.S3_BUCKET
    # Endpoint
    if not settings.S3_ENDPOINT_URL and settings.S3_ENDPOINT:
        settings.S3_ENDPOINT_URL = settings.S3_ENDPOINT
    # Access key
    if not settings.S3_ACCESS_KEY_ID and settings.AWS_ACCESS_KEY_ID:
        settings.S3_ACCESS_KEY_ID = settings.AWS_ACCESS_KEY_ID
    # Secret key
    if not settings.S3_SECRET_ACCESS_KEY and settings.AWS_SECRET_ACCESS_KEY:
        settings.S3_SECRET_ACCESS_KEY = settings.AWS_SECRET_ACCESS_KEY
    # Public base URL
    if not settings.S3_PUBLIC_BASE_URL and settings.R2_PUBLIC_BASE_URL:
        settings.S3_PUBLIC_BASE_URL = settings.R2_PUBLIC_BASE_URL


def _validate_settings(settings: Settings) -> None:
    """Validate critical application settings."""
    if not settings.DATABASE_URL:
        raise ValueError("DATABASE_URL is required")
    if not settings.GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY is required")
    
    # Environment-specific validations
    if settings.ENVIRONMENT == "production" and settings.DEBUG:
        print("WARNING: DEBUG is enabled in production. Consider setting DEBUG=False.")
    # In production, require explicit FRONTEND_APP_URL so we never fall back to localhost
    if settings.ENVIRONMENT == "production" and not settings.FRONTEND_APP_URL:
        raise ValueError("FRONTEND_APP_URL is required in production")


# Initialize settings with error handling
try:
    settings = Settings()
    _normalize_settings(settings)
    _validate_settings(settings)
except Exception as e:
    print(f"Error initializing settings: {e}")
    raise
