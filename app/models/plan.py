import uuid
import enum
from sqlalchemy import Column, String, Integer, Enum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.db.deps import Base


class SummaryDetail(str, enum.Enum):
    limited_detail = "limited_detail"
    deep_insights = "deep_insights"


class AIFeedbackLevel(str, enum.Enum):
    basic = "basic"
    concise = "concise"
    full_in_depth = "full_in_depth"


class Plan(Base):
    __tablename__ = "plans"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)
    price_pence = Column(Integer, nullable=False)
    monthly_upload_limit = Column(Integer, nullable=False)
    pages_per_upload_limit = Column(Integer, nullable=False)
    monthly_assessment_limit = Column(Integer, nullable=False)
    questions_per_assessment = Column(Integer, nullable=False)
    summary_detail = Column(Enum(SummaryDetail), nullable=False)
    ai_feedback_level = Column(Enum(AIFeedbackLevel), nullable=False)

    users = relationship("User", back_populates="plan")
