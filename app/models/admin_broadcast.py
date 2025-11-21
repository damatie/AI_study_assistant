from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from app.db.deps import Base
from app.utils.enums import BroadcastAudienceType, BroadcastStatus


class AdminBroadcast(Base):
    __tablename__ = "admin_broadcasts"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject = Column(String(255), nullable=False)
    html_content = Column(Text, nullable=True)
    text_content = Column(Text, nullable=True)
    template_name = Column(String(255), nullable=True)
    audience_type = Column(Enum(BroadcastAudienceType), nullable=False)
    audience_filters = Column(JSON, nullable=False, default=dict)
    total_recipients = Column(Integer, nullable=False, default=0)
    sent_count = Column(Integer, nullable=False, default=0)
    status = Column(Enum(BroadcastStatus), nullable=False, default=BroadcastStatus.pending)
    error_message = Column(Text, nullable=True)
    sent_by_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    sent_by = relationship("User", back_populates="broadcasts_sent", foreign_keys=[sent_by_id])

    def mark_sent(self, sent_total: int) -> None:
        self.status = BroadcastStatus.sent
        self.sent_count = sent_total
        self.sent_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        self.status = BroadcastStatus.failed
        self.error_message = error
