"""Stripe API client - Clean wrapper for Stripe operations."""

from __future__ import annotations

from typing import Optional, Dict, Any
import stripe

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class StripeClient:
    """Clean wrapper for Stripe API calls."""
    
    def __init__(self):
        """Initialize Stripe with API key."""
        if not settings.STRIPE_SECRET_KEY:
            raise RuntimeError("STRIPE_SECRET_KEY not configured")
        stripe.api_key = settings.STRIPE_SECRET_KEY
        logger.info("Stripe client initialized")
    
    def create_checkout_session(
        self,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: Dict[str, str],
    ) -> stripe.checkout.Session:
        """Create a Stripe checkout session for subscription.
        
        Args:
            price_id: Stripe Price ID (e.g., price_1234...)
            success_url: URL to redirect after successful payment
            cancel_url: URL to redirect if user cancels
            metadata: Dictionary with user_id, plan_id, billing_interval, etc.
        
        Returns:
            Stripe checkout session object
        
        Raises:
            stripe.error.StripeError: If Stripe API call fails
        """
        try:
            logger.info(f"Creating Stripe checkout: price_id={price_id}, metadata={metadata}")
            
            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[
                    {
                        "price": price_id,
                        "quantity": 1,
                    }
                ],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata=metadata,
            )
            
            logger.info(f"Stripe checkout created: session_id={session.id}, url={session.url}")
            return session
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe checkout creation failed: {e}")
            raise
    
    def retrieve_session(self, session_id: str) -> stripe.checkout.Session:
        """Retrieve a checkout session by ID.
        
        Args:
            session_id: Stripe session ID
        
        Returns:
            Stripe checkout session object
        """
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            logger.info(f"Retrieved Stripe session: {session_id}, status={session.status}")
            return session
        except stripe.error.StripeError as e:
            logger.error(f"Failed to retrieve Stripe session {session_id}: {e}")
            raise
    
    def cancel_subscription(
        self,
        subscription_id: str,
        at_period_end: bool = True
    ) -> stripe.Subscription:
        """Cancel a Stripe subscription.
        
        Args:
            subscription_id: Stripe subscription ID
            at_period_end: If True, cancel at period end; if False, cancel immediately
        
        Returns:
            Updated Stripe subscription object
        """
        try:
            if at_period_end:
                # Schedule cancellation at period end
                subscription = stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True
                )
                logger.info(f"Stripe subscription {subscription_id} will cancel at period end")
            else:
                # Cancel immediately
                subscription = stripe.Subscription.cancel(subscription_id)
                logger.info(f"Stripe subscription {subscription_id} cancelled immediately")
            
            return subscription
            
        except stripe.error.StripeError as e:
            logger.error(f"Failed to cancel Stripe subscription {subscription_id}: {e}")
            raise
    
    def create_customer_portal_session(self, customer_id: str, return_url: str) -> str:
        """Create a Stripe Customer Portal session for payment management.
        
        Allows customers to:
        - Update payment methods
        - View invoices and payment history
        - Download receipts
        - Update billing information
        
        Args:
            customer_id: Stripe customer ID
            return_url: URL to redirect after portal session
        
        Returns:
            Customer Portal URL
        """
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
            logger.info(f"Created Stripe Customer Portal session for customer: {customer_id}")
            return session.url
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Customer Portal session for {customer_id}: {e}")
            raise
    
    def retrieve_subscription(self, subscription_id: str) -> stripe.Subscription:
        """Retrieve a Stripe subscription by ID.
        
        Args:
            subscription_id: Stripe subscription ID
        
        Returns:
            Stripe subscription object
        """
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            logger.info(f"Retrieved Stripe subscription: {subscription_id}, status={subscription.status}")
            return subscription
        except stripe.error.StripeError as e:
            logger.error(f"Failed to retrieve Stripe subscription {subscription_id}: {e}")
            raise
