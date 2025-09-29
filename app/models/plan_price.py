import uuid
import enum
from sqlalchemy import (
    Column,
    String,
    Integer,
    Enum,
    Boolean,
    ForeignKey,
    DateTime,
    func,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from app.db.deps import Base
from app.utils.enums import PaymentProvider


class RegionScopeType(str, enum.Enum):
    global_scope = "global"
    continent = "continent"
    country = "country"


class PlanPrice(Base):
    __tablename__ = "plan_prices"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(PG_UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    currency = Column(String(3), nullable=False)  # NGN, USD, GBP
    provider = Column(Enum(PaymentProvider), nullable=False)
    price_minor = Column(Integer, nullable=False)  # minor units (kobo, cents, pence)
    provider_price_id = Column(String, nullable=True)  # Stripe price id (optional)
    scope_type = Column(
        Enum(RegionScopeType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=RegionScopeType.global_scope,
    )
    scope_value = Column(String, nullable=True)  # 'AF' or 'NG' when scope != global
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    plan = relationship("Plan", back_populates="prices")

    __table_args__ = (
        Index(
            "ix_plan_prices_lookup",
            "plan_id",
            "currency",
            "provider",
            "scope_type",
            "scope_value",
            "active",
        ),
    )
