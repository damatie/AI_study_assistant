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
    TESSERACT_CMD: str = (
        r"C:/Users/Edafe Maxwell/AppData/Local/Programs/Tesseract-OCR/tesseract.exe"
    )

    # App Logo
    LOGO: str =  'https://res.cloudinary.com/webmataz/image/upload/v1748798978/Assets/logo_hkakvj.png'

    # JWT settings
    JWT_SECRET: str
    JWT_ALGORITHM: str
    JWT_EXPIRE_MINUTES: int
    JWT_REFRESH_SECRET: str
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int
    EMAIL_TOKEN_EXPIRE_MINUTES: int
    VALID_WINDOW: int

    # Email sender settings
    # Sendgrid mailer
    EMAIL_USERNAME: str
    EMAIL_PASSWORD: str
    SMTP_SERVER: str
    SMTP_PORT: int
    FROM_EMAIL: str

    # Resender mailer
    RESEND_API_KEY: str
    RESEND_FROM_EMAIL: str

    # Payment gateway settings
    PAYSTACK_SECRET_KEY:str


# Try to instantiate with error handling
try:
    settings = Settings()
    print("Settings initialized successfully!")
except Exception as e:
    print(f"Error initializing settings: {e}")
    raise
