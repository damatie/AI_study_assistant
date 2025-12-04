"""Helpers for sending payment lifecycle emails via Resend."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.services.mail_handler_service import mailer_resend
from app.services.mail_handler_service.mailer_resend import EmailError

APP_NAME = "knoledg"
TEMPLATE_DIR = os.path.join(
    os.path.dirname(__file__),
    "templates",
    "payments",
)

env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def _render_template(template_name: str, context: Dict[str, Any]) -> str:
    template = env.get_template(template_name)
    return template.render(**context)


def _base_context(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = {
        "app_name": APP_NAME,
        "logo_url": settings.LOGO,
        "support_email": settings.SUPPORT_EMAIL,
    }
    if extra:
        base.update(extra)
    return base


def _build_subject(prefix: str, plan_name: str) -> str:
    return f"{prefix} · {plan_name}"


def _success_text(context: Dict[str, Any]) -> str:
    return (
        f"Hi {context['name']},\n\n"
        f"We processed {context['amount']} {context['currency']} for your {context['plan_name']} plan."
        f" Coverage now runs through {context['period_end']}."
        + (
            f" Manage billing: {context['manage_url']}"
            if context.get("manage_url")
            else ""
        )
        + f"\n\n— {APP_NAME}"
    )


def _failure_text(context: Dict[str, Any]) -> str:
    return (
        f"Heads up {context['name']} — attempt {context['attempt_number']} of {context['max_attempts']} failed for the"
        f" {context['plan_name']} plan."
        f" Next retry: {context['next_retry_date']}."
        + (
            f" Update billing here: {context['update_payment_url']}"
            if context.get("update_payment_url")
            else ""
        )
        + f"\n\nWe'll keep trying, but the plan downgrades if we exhaust retries."
    )


def _retry_success_text(context: Dict[str, Any]) -> str:
    return (
        f"Hi {context['name']}, we successfully charged your account and extended access through {context['period_end']}."
        + (
            f" Billing portal: {context['manage_url']}"
            if context.get("manage_url")
            else ""
        )
    )


def _downgrade_text(context: Dict[str, Any]) -> str:
    return (
        f"Your {context['plan_name']} plan was downgraded on {context['downgrade_date']} after retries failed."
        f" {context['plan_limit_summary']}"
        + (
            f" Reactivate: {context['reactivate_url']}"
            if context.get("reactivate_url")
            else ""
        )
    )


def _cancellation_text(context: Dict[str, Any]) -> str:
    return (
        f"Your {context['plan_name']} plan will end on {context['effective_date']}."
        + (
            f" Reactivate anytime: {context['reactivate_url']}"
            if context.get("reactivate_url")
            else ""
        )
    )


async def send_payment_success_email(
    *,
    email: str,
    name: str,
    plan_name: str,
    billing_interval: str,
    amount: str,
    currency: str,
    period_start: str,
    period_end: str,
    manage_url: Optional[str] = None,
    provider: str = "unknown",
) -> Dict[str, Any]:
    context = _base_context(
        {
            "name": name,
            "plan_name": plan_name,
            "billing_interval": billing_interval,
            "amount": amount,
            "currency": currency,
            "period_start": period_start,
            "period_end": period_end,
            "manage_url": manage_url,
        }
    )
    context["currency"] = currency.upper()
    html = _render_template("payment_success.html", context)
    text = _success_text(context)
    return await mailer_resend.send_email(
        subject=_build_subject("Payment confirmed", plan_name),
        recipient=email,
        html_content=html,
        text_content=text,
        tags={"type": "payment-success", "provider": provider},
    )


async def send_payment_failure_email(
    *,
    email: str,
    name: str,
    plan_name: str,
    billing_interval: str,
    attempt_number: int,
    max_attempts: int,
    next_retry_date: str,
    update_payment_url: Optional[str] = None,
    provider: str = "unknown",
) -> Dict[str, Any]:
    context = _base_context(
        {
            "name": name,
            "plan_name": plan_name,
            "billing_interval": billing_interval,
            "attempt_number": attempt_number,
            "max_attempts": max_attempts,
            "next_retry_date": next_retry_date,
            "update_payment_url": update_payment_url,
        }
    )
    html = _render_template("payment_failure_retry.html", context)
    text = _failure_text(context)
    return await mailer_resend.send_email(
        subject=_build_subject("Payment issue", plan_name),
        recipient=email,
        html_content=html,
        text_content=text,
        tags={"type": "payment-failed", "provider": provider},
    )


async def send_retry_success_email(
    *,
    email: str,
    name: str,
    plan_name: str,
    period_end: str,
    manage_url: Optional[str] = None,
    provider: str = "unknown",
) -> Dict[str, Any]:
    context = _base_context(
        {
            "name": name,
            "plan_name": plan_name,
            "period_end": period_end,
            "manage_url": manage_url,
        }
    )
    html = _render_template("payment_retry_success.html", context)
    text = _retry_success_text(context)
    return await mailer_resend.send_email(
        subject=_build_subject("Billing restored", plan_name),
        recipient=email,
        html_content=html,
        text_content=text,
        tags={"type": "payment-retry-success", "provider": provider},
    )


async def send_downgrade_email(
    *,
    email: str,
    name: str,
    plan_name: str,
    downgrade_date: str,
    plan_limit_summary: str,
    reactivate_url: Optional[str] = None,
    provider: str = "unknown",
) -> Dict[str, Any]:
    context = _base_context(
        {
            "name": name,
            "plan_name": plan_name,
            "downgrade_date": downgrade_date,
            "plan_limit_summary": plan_limit_summary,
            "reactivate_url": reactivate_url,
        }
    )
    html = _render_template("payment_downgrade.html", context)
    text = _downgrade_text(context)
    return await mailer_resend.send_email(
        subject=_build_subject("Plan downgraded", plan_name),
        recipient=email,
        html_content=html,
        text_content=text,
        tags={"type": "payment-downgrade", "provider": provider},
    )


async def send_cancellation_email(
    *,
    email: str,
    name: str,
    plan_name: str,
    effective_date: str,
    reactivate_url: Optional[str] = None,
    provider: str = "unknown",
) -> Dict[str, Any]:
    context = _base_context(
        {
            "name": name,
            "plan_name": plan_name,
            "effective_date": effective_date,
            "reactivate_url": reactivate_url,
        }
    )
    html = _render_template("payment_cancellation.html", context)
    text = _cancellation_text(context)
    return await mailer_resend.send_email(
        subject=_build_subject("Cancellation scheduled", plan_name),
        recipient=email,
        html_content=html,
        text_content=text,
        tags={"type": "payment-cancellation", "provider": provider},
    )


__all__ = [
    "send_payment_success_email",
    "send_payment_failure_email",
    "send_retry_success_email",
    "send_downgrade_email",
    "send_cancellation_email",
    "EmailError",
]
