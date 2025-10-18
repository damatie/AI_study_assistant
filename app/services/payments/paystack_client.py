"""Paystack API client - Clean wrapper for Paystack operations."""

from __future__ import annotations

from typing import Optional, Dict, Any
import httpx

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class PaystackClient:
    """Clean wrapper for Paystack API calls."""
    
    def __init__(self):
        """Initialize Paystack client."""
        if not settings.PAYSTACK_SECRET_KEY:
            raise RuntimeError("PAYSTACK_SECRET_KEY not configured")
        self.secret_key = settings.PAYSTACK_SECRET_KEY
        self.base_url = "https://api.paystack.co"
        logger.info("Paystack client initialized")
    
    async def initialize_transaction(
        self,
        email: str,
        amount: int,
        currency: str,
        plan_code: str,
        callback_url: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Initialize Paystack transaction with subscription plan.
        
        Args:
            email: Customer email
            amount: Amount in kobo (NGN minor units)
            currency: Currency code (NGN)
            plan_code: Paystack plan code (from provider_price_id)
            callback_url: URL to redirect after payment
            metadata: Dictionary with user_id, plan_id, billing_interval, etc.
        
        Returns:
            Dictionary with authorization_url and reference
        
        Raises:
            httpx.HTTPStatusError: If Paystack API call fails
        """
        url = f"{self.base_url}/transaction/initialize"
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "email": email,
            "amount": amount,
            "currency": currency,
            "plan": plan_code,  # This makes it a subscription!
            "callback_url": callback_url,
            "metadata": metadata,
        }
        
        try:
            logger.info(f"Initializing Paystack transaction: email={email}, plan={plan_code}, amount={amount}")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                
                if not data.get("status"):
                    raise RuntimeError(f"Paystack API returned status=false: {data}")
                
                result = data.get("data", {})
                authorization_url = result.get("authorization_url")
                reference = result.get("reference")
                
                if not authorization_url or not reference:
                    raise RuntimeError(f"Paystack response missing required fields: {data}")
                
                logger.info(f"Paystack transaction initialized: reference={reference}")
                
                return {
                    "authorization_url": authorization_url,
                    "reference": reference,
                    "access_code": result.get("access_code"),
                }
        
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack transaction initialization failed: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Paystack transaction initialization error: {e}")
            raise
    
    async def verify_transaction(self, reference: str) -> Dict[str, Any]:
        """Verify a Paystack transaction by reference.
        
        Args:
            reference: Transaction reference
        
        Returns:
            Transaction data dictionary
        
        Raises:
            httpx.HTTPStatusError: If Paystack API call fails
        """
        url = f"{self.base_url}/transaction/verify/{reference}"
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
        }
        
        try:
            logger.info(f"Verifying Paystack transaction: reference={reference}")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                
                if not data.get("status"):
                    raise RuntimeError(f"Paystack verification returned status=false: {data}")
                
                result = data.get("data", {})
                logger.info(f"Paystack transaction verified: reference={reference}, status={result.get('status')}")
                
                return result
        
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack transaction verification failed: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Paystack transaction verification error: {e}")
            raise
    
    async def get_subscription(self, subscription_code: str) -> Dict[str, Any]:
        """Fetch subscription details from Paystack.
        
        Args:
            subscription_code: Paystack subscription code (e.g., SUB_xxx)
        
        Returns:
            Dictionary with subscription details including next_payment_date
        
        Raises:
            httpx.HTTPStatusError: If Paystack API call fails
        """
        url = f"{self.base_url}/subscription/{subscription_code}"
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=30.0)
                response.raise_for_status()
                result = response.json()
            
            if not result.get("status"):
                raise ValueError(f"Paystack subscription fetch failed: {result.get('message')}")
            
            logger.info(f"Subscription fetched: {subscription_code}")
            return result["data"]
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack get_subscription failed: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Paystack get_subscription error: {e}")
            raise
    
    async def disable_subscription(self, subscription_code: str, email_token: Optional[str] = None) -> bool:
        """Disable a Paystack subscription.
        
        For merchant-initiated cancellations, email_token can be omitted.
        For customer-initiated cancellations, email_token is required.
        
        Args:
            subscription_code: Paystack subscription code
            email_token: Optional email token for customer-initiated cancellations
        
        Returns:
            True if successful
        
        Raises:
            httpx.HTTPStatusError: If Paystack API call fails
        """
        url = f"{self.base_url}/subscription/disable"
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "code": subscription_code,
        }
        
        # Add token only if provided (for customer-initiated cancellations)
        if email_token:
            payload["token"] = email_token
        
        try:
            logger.info(f"Disabling Paystack subscription: code={subscription_code}")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                
                if not data.get("status"):
                    raise RuntimeError(f"Paystack disable returned status=false: {data}")
                
                logger.info(f"Paystack subscription disabled: code={subscription_code}")
                return True
        
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack subscription disable failed: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Paystack subscription disable error: {e}")
            raise
    
    async def get_manage_link(self, subscription_code: str) -> str:
        """Get subscription management link containing email_token.
        
        Args:
            subscription_code: Paystack subscription code
        
        Returns:
            Management link URL with JWT containing email_token
        
        Raises:
            httpx.HTTPStatusError: If Paystack API call fails
        """
        url = f"{self.base_url}/subscription/{subscription_code}/manage/link"
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
        }
        
        try:
            logger.info(f"Getting manage link for subscription: {subscription_code}")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                
                if not data.get("status"):
                    raise RuntimeError(f"Paystack API returned status=false: {data}")
                
                link = data.get("data", {}).get("link")
                if not link:
                    raise RuntimeError(f"Paystack response missing link: {data}")
                
                logger.info(f"Manage link retrieved for subscription: {subscription_code}")
                return link
        
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack get manage link failed: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Paystack get manage link error: {e}")
            raise
    
    def extract_email_token_from_link(self, link: str) -> str:
        """Extract email_token from subscription management link.
        
        The link contains a JWT with email_token embedded.
        Example: https://paystack.com/manage/subscriptions/xxx?subscription_token=JWT
        
        Args:
            link: Management link URL
        
        Returns:
            Email token string
        
        Raises:
            ValueError: If link format is invalid or token cannot be extracted
        """
        import json
        import base64
        from urllib.parse import urlparse, parse_qs
        
        try:
            # Parse URL and extract JWT from query parameter
            parsed = urlparse(link)
            params = parse_qs(parsed.query)
            jwt_token = params.get('subscription_token', [None])[0]
            
            if not jwt_token:
                raise ValueError("No subscription_token found in link")
            
            # JWT has 3 parts: header.payload.signature
            # We need the payload (middle part)
            parts = jwt_token.split('.')
            if len(parts) != 3:
                raise ValueError("Invalid JWT format")
            
            # Decode base64 payload (add padding if needed)
            payload_b64 = parts[1]
            # Add padding if needed
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += '=' * padding
            
            payload_json = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_json)
            
            email_token = payload.get('email_token')
            if not email_token:
                raise ValueError("No email_token found in JWT payload")
            
            logger.info(f"Successfully extracted email_token from manage link")
            return email_token
        
        except Exception as e:
            logger.error(f"Failed to extract email_token from link: {e}")
            raise ValueError(f"Could not extract email_token: {e}")
