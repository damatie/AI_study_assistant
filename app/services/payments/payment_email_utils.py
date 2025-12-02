"""Utility helpers for formatting payment email content."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union

from app.core.config import settings
from app.models.plan import Plan
from app.models.user import User

ZERO_DECIMAL_CURRENCIES = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "JPY",
    "KMF",
    "KRW",
    "MGA",
    "PYG",
    "RWF",
    "UGX",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}


def format_amount_minor(amount_minor: Optional[int], currency: Optional[str]) -> str:
    """Convert minor units (cents, kobo) into a human string."""
    if amount_minor is None or currency is None:
        return "—"
    currency_code = currency.upper()
    decimals = 0 if currency_code in ZERO_DECIMAL_CURRENCIES else 2
    divisor = Decimal(1) if decimals == 0 else (Decimal(10) ** decimals)
    quantize_exp = Decimal("1") if decimals == 0 else Decimal("0.01")
    value = (Decimal(amount_minor) / divisor).quantize(quantize_exp, rounding=ROUND_HALF_UP)
    return f"{value:,.{decimals}f}"


def format_period(value: Optional[Union[str, datetime]]) -> str:
    """Return a friendly date (e.g., Nov 26, 2025) from ISO strings or datetimes."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.strftime("%b %d, %Y")


def build_billing_dashboard_url() -> Optional[str]:
    """Return the dashboard billing page URL that users can visit any time."""
    base = settings.FRONTEND_APP_URL or settings.APP_URL
    if not base:
        return None
    return f"{base.rstrip('/')}/dashboard/settings"


def user_display_name(user: Optional[User]) -> str:
    """Compose a friendly display name for email greetings."""
    if not user:
        return "there"
    return f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip() or user.email


def describe_plan_limits(plan: Optional[Plan]) -> str:
    """Summarize the key limits of a plan for downgrade emails."""
    if not plan:
        return "Freemium includes a limited number of uploads, flash-card sets, and assessments each month."
    return (
        f"Up to {plan.monthly_upload_limit} uploads/mo | "
        f"{plan.monthly_assessment_limit} assessments/mo | "
        f"{plan.monthly_flash_cards_limit} flash-card sets/mo"
    )
