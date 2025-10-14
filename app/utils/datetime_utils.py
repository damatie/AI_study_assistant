"""Datetime utility functions for consistent timezone handling."""

from datetime import datetime, timezone


def get_current_utc_datetime() -> datetime:
    """
    Get current datetime in UTC timezone.
    
    Returns:
        datetime: Current UTC datetime with timezone info
        
    Example:
        >>> now = get_current_utc_datetime()
        >>> now.tzinfo
        datetime.timezone.utc
    """
    return datetime.now(timezone.utc)
