import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, DateTime, Enum, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.deps import Base
from app.utils.enums import TransactionStatus

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True)
    amount_pence = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default="GBP")
    status = Column(Enum(TransactionStatus), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default="now()", onupdate="now()")

    user = relationship("User", back_populates="transactions")
    subscription = relationship("Subscription", back_populates="transactions")
