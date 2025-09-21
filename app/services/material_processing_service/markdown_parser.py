# app/services/material_processing_service/markdown_parser.py

import re
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

"""Markdown cleaning and truncation utilities for AI context preparation."""


def clean_markdown_for_context(markdown_content: Any) -> str:
    """
    Clean markdown content for use as context in AI services
    
    Args:
        markdown_content: The full markdown content; can be a string or an
            envelope-like dict containing 'overview'/'detailed'
        
    Returns:
        Cleaned content suitable for AI context
    """
    try:
        # Normalize input into a markdown string first
        if isinstance(markdown_content, dict):
            # Attempt to extract from our envelope shape
            md = markdown_content.get("detailed") or markdown_content.get("overview")
            markdown_content = md if isinstance(md, str) else ""
        elif markdown_content is None:
            markdown_content = ""
        elif not isinstance(markdown_content, str):
            # Fallback: stringify unknown types
            markdown_content = str(markdown_content)

        # Remove HTML comments
        content = re.sub(r'<!--.*?-->', '', markdown_content, flags=re.DOTALL)
        
        # Remove collapsible section tags but keep content
        content = re.sub(r'<details>\s*<summary>.*?</summary>', '', content, flags=re.DOTALL)
        content = re.sub(r'</details>', '', content)
        
        # Clean up extra whitespace
        content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
        content = content.strip()
        
        return content
        
    except Exception as e:
        logger.exception(f"Error cleaning markdown: {str(e)}")
        return markdown_content


def smart_truncate_markdown(markdown_content: str, budget_chars: int = 20000) -> str:
    """
    Truncate markdown to a character budget while trying to preserve section boundaries.

    Strategy:
    - If content length <= budget, return as-is.
    - Split by ATX headings (lines starting with 1-6 '#' chars).
    - Accumulate sections in order until reaching the budget.
    - Ensure at least the first section is included; if it's longer than the
      budget, return its leading slice up to budget.
    """
    try:
        if not isinstance(markdown_content, str):
            markdown_content = str(markdown_content or "")
        if len(markdown_content) <= budget_chars:
            return markdown_content

        # Split on headings but keep the delimiter by using a lookahead
        parts = re.split(r"(?=^#{1,6}\s)", markdown_content, flags=re.MULTILINE)
        if not parts or len(parts) == 1:
            # No headings found; hard truncate
            return markdown_content[:budget_chars]

        out: list[str] = []
        total = 0
        for idx, section in enumerate(parts):
            sec_len = len(section)
            if total + sec_len <= budget_chars:
                out.append(section)
                total += sec_len
            else:
                # If nothing has been added yet, fall back to slicing the first section
                if not out:
                    return section[:budget_chars]
                break

        merged = "".join(out)
        # Safety check if empty due to unexpected parsing; fall back to head slice
        return merged or markdown_content[:budget_chars]
    except Exception as e:
        logger.exception(f"Error smart-truncating markdown: {str(e)}")
        # Fallback: naive truncate
        return (markdown_content or "")[:budget_chars]
