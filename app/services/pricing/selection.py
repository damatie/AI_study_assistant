from __future__ import annotations

from typing import Iterable, Optional


def pick_price_row(
    rows: Iterable,
    *,
    country_code: str | None = None,
    continent_code: str | None = None,
    resolved_currency: str | None = None,
    billing_interval: str | None = None,
):
    """Select the best matching price row given region hints and billing interval.

    Precedence within the resolved currency and billing interval:
    - country match
    - USD + continent AF match
    - global
    - cheapest active row in currency
    """
    rows = list(rows or [])
    if not rows:
        return None

    country_code = (country_code or "").upper()
    continent_code = (continent_code or "").upper()
    resolved_currency = (resolved_currency or "").upper()
    billing_interval = (billing_interval or "month").lower()
    
    # Filter by billing_interval first if specified
    if billing_interval:
        rows = [
            pr for pr in rows 
            if getattr(pr, "billing_interval", None) and 
               getattr(getattr(pr, "billing_interval", None), "value", str(pr.billing_interval)).lower() == billing_interval
        ]
        if not rows:
            return None

    # country
    if country_code and resolved_currency:
        for pr in rows:
            st = getattr(pr, "scope_type", None)
            if (
                getattr(pr, "currency", None) == resolved_currency
                and st and (getattr(st, "value", st) == "country")
                and getattr(pr, "scope_value", None) == country_code
                and getattr(pr, "active", False)
            ):
                return pr

    # USD + Africa continent tier
    if resolved_currency == "USD" and continent_code == "AF":
        for pr in rows:
            st = getattr(pr, "scope_type", None)
            if (
                getattr(pr, "currency", None) == "USD"
                and st and (getattr(st, "value", st) == "continent")
                and getattr(pr, "scope_value", None) == "AF"
                and getattr(pr, "active", False)
            ):
                return pr

    # global
    if resolved_currency:
        for pr in rows:
            st = getattr(pr, "scope_type", None)
            if (
                getattr(pr, "currency", None) == resolved_currency
                and st and (getattr(st, "value", st) == "global")
                and getattr(pr, "active", False)
            ):
                return pr

    # Fallback: cheapest in currency
    candidates = [
        pr
        for pr in rows
        if getattr(pr, "currency", None) == resolved_currency and getattr(pr, "active", False)
    ]
    if candidates:
        return sorted(candidates, key=lambda r: getattr(r, "price_minor", 1_000_000_000))[0]

    return None
