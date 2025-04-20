# app/core/response.py
from typing import Any, Optional, Literal
from pydantic import BaseModel
from fastapi.responses import JSONResponse


class ResponseModel(BaseModel):
    status: Literal["success", "error"]
    msg: str
    data: Optional[Any] = None


def success_response(
    msg: str = "OK", data: Any = None, status_code: int = 200
) -> JSONResponse:
    payload = ResponseModel(status="success", msg=msg, data=data).model_dump(
        exclude_none=True
    )
    return JSONResponse(status_code=status_code, content=payload)


def error_response(msg: str, data: Any = None, status_code: int = 400) -> JSONResponse:
    # include data if provided
    payload = ResponseModel(status="error", msg=msg, data=data).model_dump(
        exclude_none=True
    )
    return JSONResponse(status_code=status_code, content=payload)
