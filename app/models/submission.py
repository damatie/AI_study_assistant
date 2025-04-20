import uuid
import enum
from sqlalchemy import Column, Integer, Text, DateTime, JSON, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.db.deps import Base


class QuestionType(str, enum.Enum):
    multiple_choice = "multiple_choice"
    true_false = "true_false"
    short_answer = "short_answer"


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("assessment_sessions.id"), nullable=False
    )
    question_index = Column(Integer, nullable=False)
    question_type = Column(Enum(QuestionType), nullable=False)
    student_answer = Column(Text, nullable=False)
    correct_answer = Column(Text, nullable=True)
    feedback = Column(JSON, nullable=True)
    score = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("AssessmentSession", back_populates="submissions")
