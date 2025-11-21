from typing import Annotated, List, Optional
from pydantic import BaseModel, Field, ConfigDict, StringConstraints
from uuid import UUID
from app.models.assessment_session import Difficulty
from app.utils.enums import FlashCardStatus


StrippedShortStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class FlashCardItem(BaseModel):
    prompt: StrippedShortStr = Field(
        ..., description="Front side text/question"
    )
    correspondingInformation: StrippedShortStr = Field(
        ..., description="Back side detailed info/answer"
    )
    hint: Optional[str] = Field(None, description="Optional brief hint")


class FlashCardSetBase(BaseModel):
    title: StrippedShortStr
    topic: Optional[str] = None
    difficulty: Difficulty


class FlashCardSetCreate(FlashCardSetBase):
    material_id: Optional[UUID] = Field(None, description="Optional source study material")
    cards: List[FlashCardItem]


class FlashCardSetOut(FlashCardSetBase):
    id: UUID
    user_id: UUID
    material_id: Optional[UUID] = None
    status: Optional[FlashCardStatus] = None
    cards: List[FlashCardItem]

    model_config = ConfigDict(from_attributes=True)


class FlashCardSetListItem(BaseModel):
    id: UUID
    title: str
    topic: Optional[str] = None
    difficulty: Difficulty
    status: Optional[FlashCardStatus] = None
    count: int


class FlashCardGenerateRequest(BaseModel):
    material_id: Optional[UUID] = None
    title: Optional[str] = None
    topic: Optional[str] = None
    difficulty: Difficulty = Difficulty.medium
    num_cards: int = Field(12, ge=3, le=40)


class FlashCardGenerateResponse(BaseModel):
    title: str
    topic: Optional[str] = None
    difficulty: Difficulty
    cards: List[FlashCardItem]
