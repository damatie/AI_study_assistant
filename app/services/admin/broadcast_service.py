from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple
from urllib.parse import urlencode

from fastapi import HTTPException, status
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_broadcast import AdminBroadcast
from app.models.plan import Plan
from app.models.user import User
from app.schemas.admin.broadcasts import (
    BroadcastAudience,
    BroadcastCreateRequest,
    BroadcastTestRequest,
)
from app.core.config import settings
from app.services.mail_handler_service.mailer_resend import EmailError, send_email
from app.utils.enums import BroadcastAudienceType, BroadcastStatus


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "mail_handler_service" / "templates"
TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


TOKEN_PATTERN = re.compile(r"{{\s*(?P<name>[a-zA-Z0-9_]+)\s*}}")


@dataclass
class PreparedBroadcastContent:
    html: str | None
    text: str | None
    template_name: str | None
    template_variables: Dict[str, Any]


class BroadcastService:
    MAX_BATCH_SIZE = 50

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_broadcasts(self, limit: int = 25) -> List[AdminBroadcast]:
        stmt = (
            select(AdminBroadcast)
            .order_by(AdminBroadcast.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def send_test(self, payload: BroadcastTestRequest) -> Dict[str, Any]:
        content = self._prepare_content(payload)
        await self._deliver_batch(
            subject=payload.subject,
            recipients=[payload.test_recipient],
            content=content,
            tags={"type": "admin-broadcast-test"},
        )
        return {"recipient": payload.test_recipient}

    async def send_broadcast(
        self, payload: BroadcastCreateRequest, admin_user: User
    ) -> AdminBroadcast:
        content = self._prepare_content(payload)
        recipients = await self._resolve_recipients(payload.audience)
        if not recipients:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No recipients matched the selected audience.",
            )

        broadcast = AdminBroadcast(
            subject=payload.subject,
            html_content=content.html,
            text_content=content.text,
            template_name=payload.template_name,
            audience_type=payload.audience.type,
            audience_filters=payload.audience.model_dump(exclude_none=True),
            total_recipients=len(recipients),
            sent_by_id=admin_user.id,
            status=BroadcastStatus.pending,
        )
        self.db.add(broadcast)
        await self.db.flush()

        try:
            sent_total = await self._deliver_in_batches(
                subject=payload.subject,
                recipients=recipients,
                content=content,
                broadcast_id=str(broadcast.id),
            )
        except EmailError as exc:
            broadcast.status = BroadcastStatus.failed
            broadcast.error_message = str(exc)
            await self.db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to send broadcast.",
            ) from exc

        broadcast.status = BroadcastStatus.sent
        broadcast.sent_count = sent_total
        broadcast.sent_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(broadcast)
        return broadcast

    async def _deliver_in_batches(
        self,
        *,
        subject: str,
        recipients: Sequence[str],
        content: PreparedBroadcastContent,
        broadcast_id: str,
    ) -> int:
        total_sent = 0
        for chunk in self._chunked(recipients, self.MAX_BATCH_SIZE):
            await self._deliver_batch(
                subject=subject,
                recipients=chunk,
                content=content,
                tags={"type": "admin-broadcast", "broadcast_id": broadcast_id},
            )
            total_sent += len(chunk)
        return total_sent

    async def _deliver_batch(
        self,
        *,
        subject: str,
        recipients: Sequence[str],
        content: PreparedBroadcastContent,
        tags: Dict[str, str],
    ) -> None:
        for recipient in recipients:
            personalized_html, personalized_text = self._personalize_content(
                recipient, content
            )
            await send_email(
                subject=subject,
                recipient=[recipient],
                html_content=personalized_html,
                text_content=personalized_text,
                tags=tags,
            )

    async def _resolve_recipients(self, audience: BroadcastAudience) -> List[str]:
        if audience.type == BroadcastAudienceType.custom:
            return list(audience.custom_emails or [])

        stmt = select(User.email).where(User.is_active.is_(True))

        if audience.type == BroadcastAudienceType.verified:
            stmt = stmt.where(User.is_email_verified.is_(True))
        elif audience.type == BroadcastAudienceType.unverified:
            stmt = stmt.where(User.is_email_verified.is_(False))
        elif audience.type == BroadcastAudienceType.plan:
            sku = audience.plan_sku.strip().lower()
            stmt = (
                stmt.join(Plan, Plan.id == User.plan_id)
                .where(func.lower(Plan.sku) == sku)
            )

        result = await self.db.execute(stmt)
        return self._dedupe(result.scalars().all())

    def _dedupe(self, emails: Iterable[str]) -> List[str]:
        seen: Dict[str, str] = {}
        for email in emails:
            if not email:
                continue
            key = email.lower()
            if key not in seen:
                seen[key] = email
        return list(seen.values())

    def _prepare_content(
        self, payload: BroadcastCreateRequest | BroadcastTestRequest
    ) -> PreparedBroadcastContent:
        template_name = payload.template_name.strip() if payload.template_name else None
        html = payload.html_body
        text = payload.text_body
        template_variables = dict(payload.template_variables or {})
        if template_name:
            html = self._render_template(template_name, template_variables)
        if not html and not text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to determine email content.",
            )
        return PreparedBroadcastContent(
            html=html,
            text=text,
            template_name=template_name,
            template_variables=template_variables,
        )

    def _render_template(
        self,
        template_name: str,
        variables: Dict[str, Any],
        *,
        extra_context: Dict[str, Any] | None = None,
        token_replacements: Dict[str, str] | None = None,
    ) -> str:
        normalized = template_name.strip()
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="template_name cannot be empty.",
            )
        template_path = Path(normalized)
        if template_path.is_absolute() or ".." in template_path.parts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid template_name provided.",
            )
        template_key = template_path.as_posix()
        if not template_key.endswith(".html"):
            template_key = f"{template_key}.html"
        try:
            template = TEMPLATE_ENV.get_template(template_key)
        except TemplateNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template '{template_name}' was not found.",
            ) from exc

        base_context = dict(variables or {})
        if token_replacements:
            base_context = self._apply_token_substitutions(base_context, token_replacements)
        context = base_context
        if extra_context:
            context.update(extra_context)
        if not context.get("logo_url") and settings.LOGO:
            context["logo_url"] = settings.LOGO
        if not context.get("support_email") and settings.RESEND_FROM_EMAIL:
            context["support_email"] = settings.RESEND_FROM_EMAIL
        if not context.get("app_name"):
            context["app_name"] = "knoledg"
        if not context.get("current_year"):
            context["current_year"] = str(datetime.now().year)
        return template.render(**context)

    def _personalize_content(
        self, recipient: str, content: PreparedBroadcastContent
    ) -> Tuple[str | None, str | None]:
        replacements = self._build_token_replacements(recipient)

        html_content = content.html
        if content.template_name:
            html_content = self._render_template(
                content.template_name,
                content.template_variables,
                extra_context=replacements,
                token_replacements=replacements,
            )
        elif html_content:
            html_content = self._apply_token_substitutions(html_content, replacements)

        text_content = content.text
        if text_content:
            text_content = self._apply_token_substitutions(text_content, replacements)

        return html_content, text_content

    def _build_token_replacements(self, email: str) -> Dict[str, str]:
        verify_url = self._build_verify_url(email)
        return {
            "email": email,
            "verify_url": verify_url,
        }

    def _build_verify_url(self, email: str) -> str:
        frontend_base = settings.FRONTEND_APP_URL or settings.APP_URL
        if not frontend_base:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="FRONTEND_APP_URL is not configured.",
            )
        base = frontend_base.rstrip("/")
        query = urlencode({"email": email})
        return f"{base}/verify-email?{query}"

    def _apply_token_substitutions(self, value: Any, replacements: Dict[str, str]) -> Any:
        if isinstance(value, str):
            def _replace(match: re.Match[str]) -> str:
                key = match.group("name")
                return replacements.get(key, match.group(0))

            return TOKEN_PATTERN.sub(_replace, value)
        if isinstance(value, list):
            return [self._apply_token_substitutions(item, replacements) for item in value]
        if isinstance(value, dict):
            return {
                key: self._apply_token_substitutions(item, replacements)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _chunked(sequence: Sequence[str], size: int) -> Iterable[Sequence[str]]:
        for idx in range(0, len(sequence), size):
            yield sequence[idx : idx + size]
