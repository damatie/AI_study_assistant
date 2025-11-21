from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

from app.utils.enums import BroadcastAudienceType, BroadcastStatus


SubjectStr = Annotated[str, StringConstraints(min_length=3, max_length=200, strip_whitespace=True)]
TemplateNameStr = Annotated[str, StringConstraints(min_length=1, max_length=255, strip_whitespace=True)]


class BroadcastAudience(BaseModel):
    type: BroadcastAudienceType = Field(description="Audience selection strategy")
    plan_sku: Optional[str] = Field(
        default=None,
        description="Plan SKU required when type is 'plan'",
    )
    custom_emails: Optional[List[EmailStr]] = Field(
        default=None,
        description="Explicit list of recipients when type is 'custom'",
    )

    @model_validator(mode="after")
    def validate_requirements(self) -> "BroadcastAudience":
        if self.type == BroadcastAudienceType.plan and not (self.plan_sku and self.plan_sku.strip()):
            raise ValueError("plan_sku is required when targeting a specific plan")
        if self.type == BroadcastAudienceType.custom:
            if not self.custom_emails:
                raise ValueError("Provide at least one email when using a custom audience")
            deduped: Dict[str, EmailStr] = {}
            for email in self.custom_emails:
                key = email.lower()
                if key not in deduped:
                    deduped[key] = email
            self.custom_emails = list(deduped.values())
        return self


class BroadcastContentBase(BaseModel):
    subject: SubjectStr
    html_body: Optional[str] = Field(None, description="Full HTML version of the email")
    text_body: Optional[str] = Field(None, description="Plain-text fallback body")
    template_name: Optional[TemplateNameStr] = Field(
        default=None,
        description="Optional template path relative to mail_handler_service/templates",
    )
    template_variables: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_content(self) -> "BroadcastContentBase":
        has_content = any([self.html_body, self.text_body, self.template_name])
        if not has_content:
            raise ValueError("Provide html_body/text_body or a template_name")
        return self


class BroadcastCreateRequest(BroadcastContentBase):
    audience: BroadcastAudience


class BroadcastTestRequest(BroadcastContentBase):
    test_recipient: EmailStr


class BroadcastOut(BaseModel):
    id: UUID
    subject: str
    status: BroadcastStatus
    audience_type: BroadcastAudienceType
    audience_filters: Dict[str, Any]
    total_recipients: int
    sent_count: int
    template_name: Optional[str] = None
    error_message: Optional[str] = None
    sent_by_id: Optional[UUID] = None
    created_at: datetime
    sent_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BroadcastListResponse(BaseModel):
    items: List[BroadcastOut]
    count: int
