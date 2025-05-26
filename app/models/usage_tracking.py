import uuid
from sqlalchemy import Column, Integer, Date, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.db.deps import Base


class UsageTracking(Base):
    __tablename__ = "usage_tracking"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    period_start = Column(Date, nullable=False)
    uploads_count = Column(Integer, default=0, nullable=False)
    assessments_count = Column(Integer, default=0, nullable=False)
    asked_questions_count = Column(Integer, default=0, nullable=False)

    user = relationship("User", back_populates="usage_tracking")
