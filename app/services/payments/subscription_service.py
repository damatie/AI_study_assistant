"""Subscription service - Unified business logic for Stripe and Paystack."""

from __future__ import annotations

import uuid
from datetime import date, timedelta, datetime, timezone
from typing import Optional, Dict, Any, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging_config import get_logger
from app.utils.datetime_utils import get_current_utc_datetime
from app.models.subscription import Subscription
from app.models.transaction import Transaction
from app.models.plan import Plan
from app.models.plan_price import PlanPrice
from app.models.user import User
from app.utils.enums import (
    PaymentProvider,
    BillingInterval,
    SubscriptionStatus,
    TransactionStatus,
    TransactionStatusReason,
)
from app.services.mail_handler_service import payment_notifications
from app.services.mail_handler_service.mailer_resend import EmailError
from app.services.payments.payment_email_utils import (
    build_billing_dashboard_url,
    format_period,
    describe_plan_limits,
    user_display_name,
)
from app.services.payments.stripe_client import StripeClient
from app.services.payments.paystack_client import PaystackClient

logger = get_logger(__name__)
BILLING_DASHBOARD_URL = build_billing_dashboard_url()


class SubscriptionService:
    """Unified subscription service for Stripe and Paystack."""
    
    def __init__(self):
        """Initialize service with payment clients."""
        self.stripe_client = StripeClient()
        self.paystack_client = PaystackClient()
        logger.info("SubscriptionService initialized")
    
    async def create_checkout(
        self,
        db: AsyncSession,
        user: User,
        plan_id: uuid.UUID,
        billing_interval: str,
        country_code: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create checkout session (Stripe or Paystack based on location).
        
        Args:
            db: Database session
            user: Current user
            plan_id: Plan UUID
            billing_interval: 'month' or 'year'
            country_code: Optional country code (e.g., 'NG', 'US')
            success_url: Optional custom success URL
            cancel_url: Optional custom cancel URL
        
        Returns:
            Dictionary with provider, checkout_url, reference
        """
        # Validate billing_interval
        if billing_interval not in ("month", "year"):
            raise ValueError("billing_interval must be 'month' or 'year'")
        
        # Load plan with prices
        result = await db.execute(
            select(Plan).where(Plan.id == plan_id)
        )
        plan = result.scalars().first()
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")
        
        # Get prices for this plan
        prices_result = await db.execute(
            select(PlanPrice).where(
                PlanPrice.plan_id == plan_id,
                PlanPrice.billing_interval == BillingInterval(billing_interval),
                PlanPrice.active == True,
            )
        )
        prices = prices_result.scalars().all()
        
        if not prices:
            raise ValueError(f"No active prices found for plan {plan.name} with {billing_interval} billing")
        
        # Determine provider and currency based on location
        provider, currency = self._determine_provider_and_currency(country_code)
        
        logger.info(f"Selected provider: {provider}, currency: {currency} for country: {country_code}")
        
        # Find matching price
        chosen_price = None
        for price in prices:
            if price.provider == provider and price.currency == currency:
                chosen_price = price
                break
        
        if not chosen_price:
            raise ValueError(f"No {provider} price found for {currency} currency")
        
        # Build URLs
        api_base = settings.APP_URL.rstrip('/')
        frontend_base = (settings.FRONTEND_APP_URL or settings.APP_URL).rstrip('/')
        
        if not success_url:
            if provider == PaymentProvider.stripe:
                success_url = f"{api_base}/api/v1/payments/stripe/verify-redirect?session_id={{CHECKOUT_SESSION_ID}}"
            else:
                success_url = f"{api_base}/api/v1/payments/paystack/verify-redirect"
        
        if not cancel_url:
            cancel_url = f"{frontend_base}/dashboard/settings"
        
        # Create metadata
        metadata = {
            "user_id": str(user.id),
            "plan_id": str(plan.id),
            "plan_name": plan.name,
            "plan_sku": plan.sku,
            "billing_interval": billing_interval,
        }
        
        # Call appropriate provider
        if provider == PaymentProvider.stripe:
            return await self._create_stripe_checkout(
                db, user, plan, chosen_price, metadata, success_url, cancel_url
            )
        else:
            return await self._create_paystack_checkout(
                db, user, plan, chosen_price, metadata, success_url
            )
    
    def _determine_provider_and_currency(
        self,
        country_code: Optional[str]
    ) -> Tuple[PaymentProvider, str]:
        """Determine payment provider and currency based on location.
        
        Args:
            country_code: ISO country code (e.g., 'NG', 'US', 'GB')
        
        Returns:
            Tuple of (provider, currency)
        """
        country_code = (country_code or "").upper()
        
        # Nigeria → Paystack (NGN only)
        if country_code == "NG":
            return (PaymentProvider.paystack, "NGN")
        
        # UK/EU → Stripe (GBP)
        elif country_code in ("GB", "UK") or country_code.startswith("EU"):
            return (PaymentProvider.stripe, "GBP")
        
        # Everyone else → Stripe (USD)
        else:
            return (PaymentProvider.stripe, "USD")
    
    async def _create_stripe_checkout(
        self,
        db: AsyncSession,
        user: User,
        plan: Plan,
        price: PlanPrice,
        metadata: Dict[str, str],
        success_url: str,
        cancel_url: str,
    ) -> Dict[str, Any]:
        """Create Stripe checkout session."""
        if not price.provider_price_id:
            raise ValueError(f"Stripe price missing provider_price_id")
        
        # Create checkout session
        session = self.stripe_client.create_checkout_session(
            price_id=price.provider_price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata,
        )
        
        # Save transaction record
        txn = Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            reference=session.id,
            authorization_url=session.url,
            provider=PaymentProvider.stripe,
            amount_pence=price.price_minor,
            currency=price.currency,
            status=TransactionStatus.pending,
            status_reason=TransactionStatusReason.awaiting_payment,
            expires_at=datetime.fromtimestamp(session.expires_at, tz=timezone.utc) if session.expires_at else None,
            status_message="Awaiting payment",
        )
        db.add(txn)
        await db.commit()
        
        logger.info(f"Stripe checkout created: session={session.id}, user={user.id}, plan={plan.name}")
        
        return {
            "provider": "stripe",
            "checkout_url": session.url,
            "reference": session.id,
        }
    
    async def _create_paystack_checkout(
        self,
        db: AsyncSession,
        user: User,
        plan: Plan,
        price: PlanPrice,
        metadata: Dict[str, Any],
        callback_url: str,
    ) -> Dict[str, Any]:
        """Create Paystack checkout session."""
        if not price.provider_price_id:
            raise ValueError(f"Paystack price missing provider_price_id (plan_code)")
        
        # Initialize transaction
        result = await self.paystack_client.initialize_transaction(
            email=user.email,
            amount=price.price_minor,
            currency=price.currency,
            plan_code=price.provider_price_id,  # This is the Paystack plan code
            callback_url=callback_url,
            metadata=metadata,
        )
        
        # Save transaction record
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)  # 24-hour expiration
        
        txn = Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            reference=result["reference"],
            authorization_url=result["authorization_url"],
            provider=PaymentProvider.paystack,
            amount_pence=price.price_minor,
            currency=price.currency,
            status=TransactionStatus.pending,
            status_reason=TransactionStatusReason.awaiting_payment,
            expires_at=expires_at,
            status_message="Awaiting payment",
        )
        db.add(txn)
        await db.commit()
        
        logger.info(f"Paystack checkout created: reference={result['reference']}, user={user.id}, plan={plan.name}")
        
        return {
            "provider": "paystack",
            "checkout_url": result["authorization_url"],
            "reference": result["reference"],
        }
    
    async def activate_subscription(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        plan_id: uuid.UUID,
        billing_interval: str,
        provider: PaymentProvider,
        provider_subscription_id: Optional[str] = None,
        provider_customer_id: Optional[str] = None,
        period_start_str: Optional[str] = None,
        period_end_str: Optional[str] = None,
    ) -> Subscription:
        """Activate a subscription after successful payment.
        
        Args:
            db: Database session
            user_id: User UUID
            plan_id: Plan UUID
            billing_interval: 'month' or 'year'
            provider: Payment provider (stripe or paystack)
            provider_subscription_id: Stripe subscription ID or Paystack subscription code
            provider_customer_id: Stripe customer ID or Paystack customer code
            period_start_str: Optional ISO date string from provider (e.g., "2025-10-13T00:00:00Z")
            period_end_str: Optional ISO date string from provider (e.g., "2025-11-13T00:00:00Z")
        
        Returns:
            Created subscription object
        """
        # Deactivate any existing active subscriptions
        now = get_current_utc_datetime()
        existing_result = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.active,
                Subscription.period_start <= now,
                Subscription.period_end > now,
            )
        )
        existing_sub = existing_result.scalars().first()
        if existing_sub:
            existing_sub.status = SubscriptionStatus.cancelled
            existing_sub.period_end = now
            db.add(existing_sub)
            logger.info(f"Deactivated existing subscription: {existing_sub.id}")
        
        # Use exact dates from payment provider (REQUIRED)
        if not period_start_str or not period_end_str:
            logger.error(f"Missing provider dates! period_start_str={period_start_str}, period_end_str={period_end_str}")
            raise ValueError("Provider dates (period_start_str, period_end_str) are required")
        
        # Parse provider datetimes (keep full datetime with timezone, not just date)
        period_start = datetime.fromisoformat(period_start_str.replace('Z', '+00:00'))
        period_end = datetime.fromisoformat(period_end_str.replace('Z', '+00:00'))
        logger.info(f"✅ Using provider datetimes: start={period_start.isoformat()}, end={period_end.isoformat()}")
        
        # Create new subscription
        subscription = Subscription(
            id=uuid.uuid4(),
            user_id=user_id,
            plan_id=plan_id,
            period_start=period_start,
            period_end=period_end,
            status=SubscriptionStatus.active,
            billing_interval=BillingInterval(billing_interval),
            auto_renew=True,
        )
        
        # Set provider-specific fields
        if provider == PaymentProvider.stripe:
            subscription.stripe_subscription_id = provider_subscription_id
            subscription.stripe_customer_id = provider_customer_id
        else:
            subscription.paystack_subscription_code = provider_subscription_id
            subscription.paystack_customer_code = provider_customer_id
        
        db.add(subscription)
        
        # Update user's plan_id
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalars().first()
        if user:
            user.plan_id = plan_id
            db.add(user)
        
        await db.commit()
        await db.refresh(subscription)
        
        logger.info(f"Subscription activated: id={subscription.id}, user={user_id}, plan={plan_id}, interval={billing_interval}, period_end={period_end}")
        
        return subscription
    
    async def extend_subscription(
        self,
        db: AsyncSession,
        subscription: Subscription,
    ) -> Subscription:
        """Extend subscription period after recurring payment using provider dates.
        
        IMPORTANT: This method fetches exact dates from provider APIs (Stripe or Paystack).
        Do NOT calculate dates manually with timedelta!
        
        Args:
            db: Database session
            subscription: Subscription object (must have provider subscription ID)
        
        Returns:
            Updated subscription object
        """
        # Route to appropriate provider
        if subscription.stripe_subscription_id:
            return await self._extend_stripe_subscription(db, subscription)
        elif subscription.paystack_subscription_code:
            return await self._extend_paystack_subscription(db, subscription)
        else:
            raise ValueError(f"Subscription {subscription.id} has no provider subscription ID")
    
    async def _extend_stripe_subscription(
        self,
        db: AsyncSession,
        subscription: Subscription,
    ) -> Subscription:
        """Extend Stripe subscription using Stripe API dates."""
        import stripe
        
        stripe.api_key = settings.STRIPE_SECRET_KEY
        
        try:
            # Fetch exact dates from Stripe API (source of truth)
            stripe_subscription = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
            first_item = stripe_subscription['items']['data'][0]
            period_start_unix = first_item['current_period_start']
            period_end_unix = first_item['current_period_end']
            
            # Convert to datetime
            period_start_dt = datetime.fromtimestamp(period_start_unix, tz=timezone.utc)
            period_end_dt = datetime.fromtimestamp(period_end_unix, tz=timezone.utc)
            
            # Update subscription with exact provider dates
            subscription.period_start = period_start_dt
            subscription.period_end = period_end_dt
            
            db.add(subscription)
            await db.commit()
            await db.refresh(subscription)
            
            logger.info(f"✅ Stripe subscription extended: id={subscription.id}, period_start={period_start_dt.isoformat()}, period_end={period_end_dt.isoformat()}")
            
        except Exception as e:
            logger.error(f"Failed to fetch Stripe subscription dates for {subscription.id}: {e}")
            raise
        
        return subscription
    
    async def _extend_paystack_subscription(
        self,
        db: AsyncSession,
        subscription: Subscription,
    ) -> Subscription:
        """Extend Paystack subscription using Paystack API dates."""
        try:
            # Fetch exact dates from Paystack API (source of truth)
            paystack_subscription = await self.paystack_client.get_subscription(
                subscription.paystack_subscription_code
            )
            
            # Extract dates from Paystack response
            # createdAt format: "2024-10-13T00:00:00.000Z"
            # next_payment_date format: "2024-11-13T00:00:00.000Z"
            created_at_str = paystack_subscription.get("createdAt")
            next_payment_date_str = paystack_subscription.get("next_payment_date")
            
            if not created_at_str or not next_payment_date_str:
                raise ValueError(f"Missing dates in Paystack response: createdAt={created_at_str}, next_payment_date={next_payment_date_str}")
            
            # Parse dates
            period_start_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
            period_end_dt = datetime.fromisoformat(next_payment_date_str.replace('Z', '+00:00'))
            
            # Update subscription with exact provider dates
            subscription.period_start = period_start_dt
            subscription.period_end = period_end_dt
            
            db.add(subscription)
            await db.commit()
            await db.refresh(subscription)
            
            logger.info(f"✅ Paystack subscription extended: id={subscription.id}, period_start={period_start_dt.isoformat()}, period_end={period_end_dt.isoformat()}")
            
        except Exception as e:
            logger.error(f"Failed to fetch Paystack subscription dates for {subscription.id}: {e}")
            raise
        
        return subscription
    
    async def cancel_subscription(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        immediate: bool = False,
    ) -> Subscription:
        """Cancel user's active subscription.
        
        Args:
            db: Database session
            user_id: User UUID
            immediate: If True, cancel now; if False, cancel at period_end
        
        Returns:
            Updated subscription object
        """
        # Find active subscription
        now = get_current_utc_datetime()
        result = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.active,
                Subscription.period_start <= now,
                Subscription.period_end > now,
            )
        )
        subscription = result.scalars().first()
        if not subscription:
            raise ValueError("No active subscription found")
        
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalars().first()
        
        # Cancel via provider API
        if subscription.stripe_subscription_id:
            try:
                self.stripe_client.cancel_subscription(
                    subscription.stripe_subscription_id,
                    at_period_end=(not immediate)
                )
            except Exception as e:
                logger.error(f"Failed to cancel Stripe subscription: {e}")
                # Continue with local cancellation even if API call fails
        
        if subscription.paystack_subscription_code:
            try:
                # Get email_token dynamically from Paystack manage link
                logger.info(f"Getting email_token for Paystack subscription {subscription.paystack_subscription_code}")
                manage_link = await self.paystack_client.get_manage_link(subscription.paystack_subscription_code)
                email_token = self.paystack_client.extract_email_token_from_link(manage_link)
                
                # Now disable subscription with the token
                await self.paystack_client.disable_subscription(
                    subscription.paystack_subscription_code,
                    email_token=email_token
                )
                logger.info(f"✅ Paystack subscription {subscription.paystack_subscription_code} disabled successfully")
            except Exception as e:
                logger.error(f"❌ Failed to cancel Paystack subscription: {e}")
                # Continue with local cancellation even if API call fails
        
        # Update subscription in database
        subscription.auto_renew = False
        subscription.canceled_at = datetime.now(timezone.utc)
        
        if immediate:
            subscription.status = SubscriptionStatus.cancelled
            subscription.period_end = now
            
            # Downgrade user to Freemium
            if user:
                plan_result = await db.execute(select(Plan).where(Plan.sku == "FREEMIUM"))
                free_plan = plan_result.scalars().first()
                if free_plan:
                    user.plan_id = free_plan.id
                    db.add(user)
        
        db.add(subscription)
        await db.commit()
        await db.refresh(subscription)
        
        cancel_type = "immediately" if immediate else "at period end"
        logger.info(f"Subscription cancelled {cancel_type}: id={subscription.id}, user={user_id}")
        plan_result = await db.execute(select(Plan).where(Plan.id == subscription.plan_id))
        plan = plan_result.scalars().first()
        provider_label = "stripe" if subscription.stripe_subscription_id else "paystack" if subscription.paystack_subscription_code else "unknown"
        if user:
            try:
                await payment_notifications.send_cancellation_email(
                    email=user.email,
                    name=user_display_name(user),
                    plan_name=plan.name if plan else "Your plan",
                    effective_date=format_period(subscription.period_end),
                    reactivate_url=BILLING_DASHBOARD_URL,
                    provider=provider_label,
                )
            except EmailError as exc:
                logger.error("Failed to send cancellation email for subscription %s: %s", subscription.id, exc)
        
        return subscription
    
    async def downgrade_to_freemium(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        reason: str = "retries_exhausted",
    ) -> Subscription:
        """Downgrade user to Freemium plan after payment retry period exhausted.
        
        Args:
            db: Database session
            user_id: User UUID
            reason: Reason for downgrade (for logging)
        
        Returns:
            New Freemium subscription object
        """
        logger.info(f"Downgrading user {user_id} to Freemium. Reason: {reason}")
        
        # 1. Get user's current subscription
        now = get_current_utc_datetime()
        result = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.active,
            ).order_by(Subscription.created_at.desc()).limit(1)
        )
        old_subscription = result.scalars().first()
        
        # 2. Mark old subscription as cancelled and exit retry period
        old_plan = None
        if old_subscription:
            old_subscription.status = SubscriptionStatus.cancelled
            old_subscription.is_in_retry_period = False
            old_subscription.auto_renew = False
            db.add(old_subscription)
            logger.info(f"Cancelled old subscription {old_subscription.id}")
            plan_lookup = await db.execute(select(Plan).where(Plan.id == old_subscription.plan_id))
            old_plan = plan_lookup.scalars().first()
        
        # 3. Get Freemium plan
        plan_result = await db.execute(select(Plan).where(Plan.sku == "FREEMIUM"))
        freemium_plan = plan_result.scalars().first()
        if not freemium_plan:
            raise ValueError("Freemium plan not found in database")
        
        # 4. Create new free subscription (30 days)
        period_start = now
        period_end = now + timedelta(days=30)
        
        new_subscription = Subscription(
            id=uuid.uuid4(),
            user_id=user_id,
            plan_id=freemium_plan.id,
            period_start=period_start,
            period_end=period_end,
            status=SubscriptionStatus.active,
            billing_interval=BillingInterval.month,
            auto_renew=True,
            is_in_retry_period=False,
            retry_attempt_count=0,
        )
        db.add(new_subscription)
        
        # 5. Update user's plan_id
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalars().first()
        if user:
            user.plan_id = freemium_plan.id
            db.add(user)
        
        await db.commit()
        await db.refresh(new_subscription)
        
        logger.info(f"✅ User {user_id} downgraded to Freemium. New subscription: {new_subscription.id}, expires: {period_end}")
        user = user or (await db.execute(select(User).where(User.id == user_id))).scalars().first()
        if user:
            try:
                await payment_notifications.send_downgrade_email(
                    email=user.email,
                    name=user_display_name(user),
                    plan_name=old_plan.name if old_plan else "Your previous plan",
                    downgrade_date=format_period(now),
                    plan_limit_summary=describe_plan_limits(freemium_plan),
                    reactivate_url=BILLING_DASHBOARD_URL,
                    provider="stripe" if old_subscription and old_subscription.stripe_subscription_id else "paystack" if old_subscription and old_subscription.paystack_subscription_code else "unknown",
                )
            except EmailError as exc:
                logger.error("Failed to send downgrade email for user %s: %s", user_id, exc)
        
        return new_subscription
    
    async def get_payment_portal_url(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        return_url: str,
    ) -> str:
        """Get payment management portal URL for user's provider.
        
        Routes to provider-hosted portal:
        - Stripe: Customer Portal (update card, view invoices)
        - Paystack: Manage subscription link
        
        Args:
            db: Database session
            user_id: User UUID
            return_url: URL to redirect after portal session
        
        Returns:
            Portal URL string
        
        Raises:
            ValueError: If user has no active subscription or provider unsupported
        """
        # Get user's active subscription
        result = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.cancelled]),
            ).order_by(Subscription.created_at.desc()).limit(1)
        )
        subscription = result.scalars().first()
        
        if not subscription:
            logger.warning(f"No active subscription for user {user_id}")
            raise ValueError("No active subscription found")
        
        # Route based on which provider IDs are populated
        if subscription.stripe_subscription_id:
            if not subscription.stripe_customer_id:
                raise ValueError("Missing Stripe customer ID")
            
            portal_url = self.stripe_client.create_customer_portal_session(
                customer_id=subscription.stripe_customer_id,
                return_url=return_url,
            )
            logger.info(f"Generated Stripe portal for user {user_id}")
            return portal_url
        
        elif subscription.paystack_subscription_code:
            if not subscription.paystack_subscription_code:
                raise ValueError("Missing Paystack subscription code")
            
            portal_url = await self.paystack_client.get_manage_link(
                subscription_code=subscription.paystack_subscription_code
            )
            logger.info(f"Generated Paystack portal for user {user_id}")
            return portal_url
        
        else:
            raise ValueError("Unable to determine payment provider for subscription")
