# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

# Debug to check if .env file is found
env_path = Path(".env")
print(f".env file exists: {env_path.exists()}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,  # Set to True when using uppercase variables
    )

    # Database and API settings
    DATABASE_URL: str
    GOOGLE_API_KEY: str
    
    # Application settings
    HOST: str = "0.0.0.0"
    PORT: int = 8100
    DEBUG: bool = False
    
    # CORS settings
    ALLOWED_ORIGINS: list = ["*"]  # In production, specify actual origins
    ALLOW_CREDENTIALS: bool = True
    ALLOWED_METHODS: list = ["*"]
    ALLOWED_HEADERS: list = ["*"]
    
    # Assessment settings
    DEFAULT_MAX_QUESTIONS: int = 5
    
    # Environment
    ENVIRONMENT: str = "development"

    # App Logo
    LOGO: str =  'https://res.cloudinary.com/webmataz/image/upload/v1748798978/Assets/logo_hkakvj.png'

    # JWT settings
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 43200  # 30 days
    JWT_REFRESH_SECRET: str
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    EMAIL_TOKEN_EXPIRE_MINUTES: int = 120
    VALID_WINDOW: int = 20

    # Email sender settings
    # Sendgrid mailer
    EMAIL_USERNAME: str
    EMAIL_PASSWORD: str
    SMTP_SERVER: str
    SMTP_PORT: int = 587
    FROM_EMAIL: str

    # Resend mailer
    RESEND_API_KEY: str
    RESEND_FROM_EMAIL: str

    APP_URL: str

    # Payment gateway settings
    PAYSTACK_SECRET_KEY: str
    PAYSTACK_PUBLIC_KEY: str
    PAYSTACK_WEBHOOK_SECRET: str
    STRIPE_SECRET_KEY: str
    STRIPE_PUBLIC_KEY: str
    STRIPE_WEBHOOK_SECRET: str


# Try to instantiate with error handling
try:
    settings = Settings()
    
    # Validate critical settings
    if not settings.DATABASE_URL:
        raise ValueError("DATABASE_URL is required")
    if not settings.GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY is required")
    
    # Environment-specific validations
    if settings.ENVIRONMENT == "production":
        if settings.DEBUG:
            print("WARNING: DEBUG is enabled in production. Consider setting DEBUG=False.")
    
    print(f"Settings initialized successfully for {settings.ENVIRONMENT} environment!")
    print(f"Server will run on {settings.HOST}:{settings.PORT}")
    
except Exception as e:
    print(f"Error initializing settings: {e}")
    raise
