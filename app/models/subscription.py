import uuid
from sqlalchemy import (
    Column, Date, DateTime, Enum, ForeignKey, String, Boolean, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.deps import Base
from app.utils.enums import SubscriptionStatus, BillingInterval

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)

    period_start = Column(Date, nullable=False)   # e.g. 2025-04-01
    period_end   = Column(Date, nullable=False)   # e.g. 2025-05-01

    # Recurring subscription tracking (Stripe/Paystack IDs)
    stripe_subscription_id = Column(String, nullable=True, index=True)
    stripe_customer_id = Column(String, nullable=True, index=True)
    paystack_subscription_code = Column(String, nullable=True, index=True)
    paystack_customer_code = Column(String, nullable=True, index=True)
    
    # Billing interval (reuses existing 'billinginterval' enum from PlanPrice)
    billing_interval = Column(
        Enum(BillingInterval, name="billinginterval"),  # Reuse existing enum
        nullable=True,
        default=BillingInterval.month
    )
    
    # Auto-renewal control
    auto_renew = Column(Boolean, nullable=False, default=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(
        Enum(SubscriptionStatus),
        nullable=False,
        default=SubscriptionStatus.active
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


    # relationships
    plan = relationship("Plan")
    transactions = relationship("Transaction", back_populates="subscription")
    user = relationship("User", back_populates="subscriptions")
    
