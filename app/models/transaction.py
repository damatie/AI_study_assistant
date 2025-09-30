import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, DateTime, Enum, ForeignKey, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.deps import Base
from app.utils.enums import TransactionStatus, PaymentProvider, TransactionStatusReason

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True)
    reference         = Column(String, nullable=False, unique=True)
    authorization_url = Column(String, nullable=True)
    provider = Column(Enum(PaymentProvider), nullable=True)
    amount_pence = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default="GBP")
    status = Column(Enum(TransactionStatus), nullable=False)
    # Optional lifecycle metadata
    expires_at = Column(DateTime(timezone=True), nullable=True)
    # Keep DB type name stable as 'statusreason' to match migration
    status_reason = Column(Enum(TransactionStatusReason, name="statusreason"), nullable=True)
    status_message = Column(String, nullable=True)
    failure_code = Column(String, nullable=True)
    # Use SQL functions for defaults to avoid passing raw strings as parameters
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # updated_at should be NULL until the first actual update; filled via onupdate
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    user = relationship("User", back_populates="transactions")
    subscription = relationship("Subscription", back_populates="transactions")
