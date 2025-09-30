import uuid
from sqlalchemy import Column, String, DateTime, JSON, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from app.db.deps import Base
from app.models.assessment_session import Difficulty
from app.utils.enums import FlashCardStatus


class FlashCardSet(Base):
    __tablename__ = "flash_card_sets"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # Optional link to a study material for provenance/context
    material_id = Column(PG_UUID(as_uuid=True), ForeignKey("study_materials.id"), nullable=True)

    title = Column(String, nullable=False)
    topic = Column(String, nullable=True)
    difficulty = Column(Enum(Difficulty, name="difficulty"), nullable=False)

    # Cards payload: list[{prompt, correspondingInformation, hint?}]
    cards_payload = Column(JSON, nullable=False)

    # Avoid using reserved attribute name 'metadata' in SQLAlchemy mappers
    extra_metadata = Column("metadata", JSON, nullable=True)

    # Generation job status for async pipeline
    status = Column(
        Enum(FlashCardStatus, name="flash_card_status"),
        nullable=False,
        default=FlashCardStatus.processing,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
