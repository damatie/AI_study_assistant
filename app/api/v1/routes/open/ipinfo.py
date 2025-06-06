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
from fastapi import status, Request
from fastapi import APIRouter
from app.core.response import success_response, error_response, ResponseModel

router = APIRouter(tags=["Public API"])

def get_client_ip(request: Request) -> str:
    """
    Extract the real client IP address from the request.
    Handles various proxy headers and fallbacks.
    """
    # Check for forwarded headers (common with reverse proxies)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        return forwarded_for.split(",")[0].strip()
    
    # Check for real IP header (some proxies use this)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Check for Cloudflare connecting IP
    cf_connecting_ip = request.headers.get("CF-Connecting-IP")
    if cf_connecting_ip:
        return cf_connecting_ip.strip()
    
    # Fallback to direct client IP
    return request.client.host

@router.get(
    "/ipinfo",
    response_model=ResponseModel,
    status_code=status.HTTP_200_OK,
)
async def get_ip_info(request: Request):
    """
    Returns the caller's geolocation data based on their IP address
    """
    try:
        # Get the client's real IP address
        client_ip = get_client_ip(request)
        
        # Handle localhost/private IPs
        if client_ip in ["127.0.0.1", "::1"] or client_ip.startswith("192.168.") or client_ip.startswith("10."):
            return success_response(
                msg="Local IP detected",
                data={
                    "ip": client_ip,
                    "city": "Local",
                    "region": "Local",
                    "country": "Local",
                    "loc": "0,0",
                    "note": "Cannot determine location for local/private IP"
                }
            )
        
        # Query the IP geolocation service with the client's IP
        # ip-api.com returns: status, country, countryCode, region, regionName, 
        # city, zip, lat, lon, timezone, isp, org, as, query
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://ip-api.com/json/{client_ip}")
            resp.raise_for_status()
            data = resp.json()
            
            # Check if the API returned an error
            if data.get("status") == "fail":
                return error_response(
                    msg="IP geolocation lookup failed",
                    data={"error": data.get("message", "Unknown error"), "ip": client_ip},
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            # The response already contains all the fields:
            # status, country, countryCode, region, regionName, city, zip,
            # lat, lon, timezone, isp, org, as, query
            
    except httpx.HTTPError as e:
        return error_response(
            msg="Failed to fetch IP information",
            data={"error": str(e)},
            status_code=status.HTTP_502_BAD_GATEWAY
        )
    except Exception as e:
        return error_response(
            msg="Internal server error",
            data={"error": str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return success_response(
        msg="IP information retrieved",
        data=data
    )