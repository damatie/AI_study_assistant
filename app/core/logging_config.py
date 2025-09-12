# app/core/logging_config.py
import logging
import logging.config
import sys
from typing import Dict, Any
from app.core.config import settings


def setup_logging() -> None:
    """Setup centralized logging configuration"""
    # Prevent logging handler failures (e.g., broken pipe) from raising exceptions
    # that could interrupt request/background task processing on Windows.
    logging.raiseExceptions = False

    # Define log format
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Define logging configuration
    logging_config: Dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": log_format,
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "detailed": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": "DEBUG" if settings.DEBUG else "INFO",
                "formatter": "detailed" if settings.DEBUG else "default",
                "stream": sys.stdout,
            },
            "file": {
                "class": "logging.FileHandler",
                "level": "INFO",
                "formatter": "detailed",
                "filename": "app.log",
                "mode": "a",
            },
        },
        "loggers": {
            "": {  # Root logger
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
            "app": {  # Application logger
                "handlers": ["console", "file"],
                "level": "DEBUG" if settings.DEBUG else "INFO",
                "propagate": False,
            },
            "uvicorn": {  # Uvicorn logger
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
            "sqlalchemy": {  # Database logger
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
        },
    }
    
    # Apply configuration
    logging.config.dictConfig(logging_config)
    
    # Set specific logger levels based on environment
    if settings.ENVIRONMENT == "production":
        logging.getLogger("app").setLevel(logging.INFO)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
    
    # Log the setup
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured for {settings.ENVIRONMENT} environment")
    logger.info(f"Debug mode: {settings.DEBUG}")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name"""
    return logging.getLogger(f"app.{name}")


# Initialize logging when module is imported
setup_logging()
