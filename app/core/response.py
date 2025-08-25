# app/core/response.py
from typing import Any, Optional, Literal, Dict
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse
import traceback
from app.core.config import settings


class ErrorDetail(BaseModel):
    """Detailed error information for debugging"""
    field: Optional[str] = None
    message: str
    code: Optional[str] = None


class ResponseModel(BaseModel):
    status: Literal["success", "error"]
    msg: str
    data: Optional[Any] = None


class ErrorResponseModel(BaseModel):
    """Enhanced error response with more details"""
    status: Literal["error"] = "error"
    msg: str
    error_code: Optional[str] = None
    details: Optional[list[ErrorDetail]] = None
    data: Optional[Any] = None
    # Only include debug info in development
    debug_info: Optional[Dict[str, Any]] = None


def success_response(
    msg: str = "OK", data: Any = None, status_code: int = 200
) -> JSONResponse:
    """Create a success response - maintains existing frontend compatibility"""
    payload = ResponseModel(status="success", msg=msg, data=data).model_dump(
        exclude_none=True
    )
    return JSONResponse(status_code=status_code, content=payload)


def error_response(
    msg: str, 
    data: Any = None, 
    status_code: int = 400,
    error_code: Optional[str] = None,
    details: Optional[list[ErrorDetail]] = None
) -> JSONResponse:
    """Enhanced error response with optional error codes and details"""
    # For backward compatibility, if no details provided, use the old format
    if not details and not error_code:
        payload = ResponseModel(status="error", msg=msg, data=data).model_dump(
            exclude_none=True
        )
    else:
        # Enhanced error response
        debug_info = None
        if settings.DEBUG:
            debug_info = {
                "traceback": traceback.format_exc(),
                "environment": settings.ENVIRONMENT
            }
        
        payload = ErrorResponseModel(
            status="error",
            msg=msg,
            error_code=error_code,
            details=details,
            data=data,
            debug_info=debug_info
        ).model_dump(exclude_none=True)
    
    return JSONResponse(status_code=status_code, content=payload)


def validation_error_response(
    errors: list[Dict[str, Any]], 
    status_code: int = 422
) -> JSONResponse:
    """Create a standardized validation error response"""
    details = []
    for err in errors:
        loc = err.get("loc", [])
        field = ".".join(str(x) for x in loc if x != "body")
        details.append(ErrorDetail(
            field=field or str(loc[-1]) if loc else None,
            message=err.get("msg", "Validation error"),
            code="VALIDATION_ERROR"
        ))
    
    return error_response(
        msg="Invalid request parameters",
        details=details,
        status_code=status_code,
        error_code="VALIDATION_ERROR"
    )
