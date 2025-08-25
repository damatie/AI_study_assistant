# Standard library imports
from contextlib import asynccontextmanager

# Third-party imports
from fastapi import FastAPI, Request
from fastapi.exceptions import (
    HTTPException as StarletteHTTPException,
    RequestValidationError,
)
from fastapi.middleware.cors import CORSMiddleware

# Local imports
from app.api.v1.routes.router import router as api_v1_router
from app.core.config import settings
from app.core.response import error_response, validation_error_response
from app.db.seed.plans import seed_all


# Initialize seeding plans table
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown operations."""
    # Startup: Run before the application starts accepting requests
    await seed_all()
    yield
    # Shutdown: Run when the application is shutting down


# Initialize FastAPI
app = FastAPI(
    title="AI Study Assistant API",
    description="API for AI-powered study assistant application",
    version="1.0.0",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# Include the router with prefix
app.include_router(
    api_v1_router,
    prefix="/api/v1",
)


# Custom exception handler
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions with logging"""
    # exc.detail might be a dict or str
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    
    # Log the error with context
    logger.warning(
        f"HTTP Exception: {exc.status_code} - {msg}",
        extra={
            "status_code": exc.status_code,
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else None
        }
    )
    
    return error_response(msg, status_code=exc.status_code)


# Pydantic validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Enhanced validation error handler with structured error details and logging"""
    
    # Log validation errors with context
    error_count = len(exc.errors())
    logger.warning(
        f"Validation Error: {error_count} field(s) failed validation",
        extra={
            "path": request.url.path,
            "method": request.method,
            "error_count": error_count,
            "errors": exc.errors()
        }
    )
    
    return validation_error_response(exc.errors(), status_code=422)


# Initialize centralized logger
from app.core.logging_config import get_logger
logger = get_logger("main")

# Add CORS middleware
logger.info(f"Configuring CORS middleware with origins: {settings.ALLOWED_ORIGINS}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=settings.ALLOW_CREDENTIALS,
    allow_methods=settings.ALLOWED_METHODS,
    allow_headers=settings.ALLOWED_HEADERS,
)
logger.info("CORS middleware configured successfully")


# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host=settings.HOST, 
        port=settings.PORT,
        reload=settings.DEBUG
    )
