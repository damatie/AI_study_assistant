from fastapi import status

from app.core.response import error_response


def plan_limit_error(
    *,
    message: str,
    error_type: str,
    current_plan: str,
    metric: str,
    limit: int,
    used: int | None = None,
    actual: int | None = None,
    status_code: int = status.HTTP_403_FORBIDDEN,
):
    """Return a standardized plan-limit error response.

    Payload shape:
    {
      status: "error",
      msg: message,
      error_code: "PLAN_LIMIT_EXCEEDED",
      data: {
        error_type,           # e.g., MONTHLY_UPLOAD_LIMIT_EXCEEDED
        current_plan,         # plan name
        limit: {
          metric,             # e.g., monthly_uploads, pages_per_upload
          limit,              # numeric plan limit
          used?,              # numeric used so far (monthly caps)
          actual?,            # numeric actual for this action (per-action caps)
        }
      }
    }
    """
    limit_obj: dict[str, int | str] = {"metric": metric, "limit": limit}
    if used is not None:
        limit_obj["used"] = used
    if actual is not None:
        limit_obj["actual"] = actual

    return error_response(
        msg=message,
        data={
            "error_type": error_type,
            "current_plan": current_plan,
            "limit": limit_obj,
        },
        status_code=status_code,
        error_code="PLAN_LIMIT_EXCEEDED",
    )
