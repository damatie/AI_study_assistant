import uuid
import enum
from sqlalchemy import Column, String, Integer, DateTime, JSON, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.db.deps import Base


class Difficulty(str, enum.Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class SessionStatus(str, enum.Enum):
    in_progress = "in_progress"
    completed = "completed"


class AssessmentSession(Base):
    __tablename__ = "assessment_sessions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    material_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("study_materials.id"), nullable=False
    )
    topic = Column(String, nullable=True)
    difficulty = Column(Enum(Difficulty), nullable=False)
    question_types = Column(JSON, nullable=False)
    questions_payload = Column(JSON, nullable=False)
    current_index = Column(Integer, default=0, nullable=False)
    status = Column(
        Enum(SessionStatus), default=SessionStatus.in_progress, nullable=False
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="assessment_sessions")
    material = relationship("StudyMaterial", back_populates="assessment_sessions")
    submissions = relationship("Submission", back_populates="session")
