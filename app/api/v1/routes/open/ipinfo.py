# import httpx
# from fastapi import status
# from fastapi import APIRouter
# from app.core.response import success_response, error_response, ResponseModel

# router = APIRouter( tags=["Public API"])

# @router.get(
#     "/ipinfo",
#     response_model=ResponseModel,
#     status_code=status.HTTP_200_OK,
# )
# async def get_ip_info():
#     """
#     Proxy endpoint that returns the caller’s geolocation data
#     from http://ip-api.com/json
#     """
#     try:
#         async with httpx.AsyncClient(timeout=5.0) as client:
#             resp = await client.get("http://ip-api.com/json")
#             resp.raise_for_status()
#             data = resp.json()
#     except httpx.HTTPError as e:
#         return error_response(
#             msg="Failed to fetch IP information",
#             data={"error": str(e)},
#             status_code=status.HTTP_502_BAD_GATEWAY
#         )

#     return success_response(
#         msg="IP information retrieved",
#         data=data
#     )


import httpx
from fastapi import status
from fastapi import APIRouter
from app.core.response import success_response, error_response, ResponseModel

router = APIRouter( tags=["Public API"])

@router.get(
    "/ipinfo",
    response_model=ResponseModel,
    status_code=status.HTTP_200_OK,
)
async def get_ip_info():
    """
    Proxy endpoint that returns the caller's geolocation data
    from https://ipapi.co/json
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://ipapi.co/json/")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return error_response(
            msg="Failed to fetch IP information",
            data={"error": str(e)},
            status_code=status.HTTP_502_BAD_GATEWAY
        )

    return success_response(
        msg="IP information retrieved",
        data=data
    )