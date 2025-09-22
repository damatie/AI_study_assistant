from typing import List, Optional, Annotated
from pydantic import BaseModel, Field
from uuid import UUID
from app.models.assessment_session import Difficulty


class FlashCardItem(BaseModel):
    prompt: Annotated[str, Field(strip_whitespace=True, min_length=1, description="Front side text/question")]
    correspondingInformation: Annotated[str, Field(strip_whitespace=True, min_length=1, description="Back side detailed info/answer")]
    hint: Optional[str] = Field(None, description="Optional brief hint")


class FlashCardSetBase(BaseModel):
    title: Annotated[str, Field(strip_whitespace=True, min_length=1)]
    topic: Optional[str] = None
    difficulty: Difficulty


class FlashCardSetCreate(FlashCardSetBase):
    material_id: Optional[UUID] = Field(None, description="Optional source study material")
    cards: List[FlashCardItem]


class FlashCardSetOut(FlashCardSetBase):
    id: UUID
    user_id: UUID
    material_id: Optional[UUID] = None
    cards: List[FlashCardItem]

    class Config:
        from_attributes = True


class FlashCardSetListItem(BaseModel):
    id: UUID
    title: str
    topic: Optional[str] = None
    difficulty: Difficulty
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
