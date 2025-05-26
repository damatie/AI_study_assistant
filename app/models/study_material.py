import uuid
from sqlalchemy import Column, String, Text, Integer, DateTime, JSON, ForeignKey,Enum, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.db.deps import Base
from app.utils.enums import MaterialStatus


class StudyMaterial(Base):
    __tablename__ = "study_materials"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    processed_content = Column(JSON, nullable=True)
    page_count = Column(Integer, nullable=False)
    status = Column(Enum(MaterialStatus), 
                    nullable=False, 
                    default=MaterialStatus.processing)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="study_materials")
    assessment_sessions = relationship("AssessmentSession", back_populates="material", cascade="all, delete-orphan")
