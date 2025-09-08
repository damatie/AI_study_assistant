"""Deprecated: Mermaid support removed. This module retained only to avoid import errors if any lingering references exist.
All functions now no-op.
"""

from typing import Any

__all__ = ["sanitize_markdown_mermaid"]

def sanitize_markdown_mermaid(markdown: str) -> str:  # pragma: no cover
    return markdown
