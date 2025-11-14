from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class GotenbergError(Exception):
    """Base exception for Gotenberg conversion issues."""


class GotenbergNotConfigured(GotenbergError):
    """Raised when GOTENBERG_URL is missing."""


class GotenbergConversionError(GotenbergError):
    """Raised when the remote conversion request fails."""


@dataclass(frozen=True)
class GotenbergConversionResult:
    """Represents a successful conversion payload."""

    content: bytes
    filename: str
    content_type: str


_OFFICE_MIME_OVERRIDES = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _resolve_base_url() -> str:
    base_url = settings.GOTENBERG_URL
    if not base_url:
        raise GotenbergNotConfigured("GOTENBERG_URL is not configured.")
    return base_url.rstrip("/")


def _resolve_mime_type(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return _OFFICE_MIME_OVERRIDES.get(ext) or mimetypes.guess_type(filename or "")[0] or "application/octet-stream"


def _ensure_size_allowed(payload: bytes) -> None:
    limit_mb = settings.GOTENBERG_MAX_FILE_SIZE_MB
    if limit_mb is None:
        return
    max_bytes = limit_mb * 1024 * 1024
    if len(payload) > max_bytes:
        raise GotenbergConversionError(
            f"Document exceeds maximum allowed size of {limit_mb}MB for conversion."
        )


def _resolved_pdf_filename(filename: str) -> str:
    stem, _ = os.path.splitext(filename or "document")
    return f"{stem or 'document'}.pdf"


async def convert_office_document_to_pdf(
    *,
    document_bytes: bytes,
    filename: str,
    timeout: Optional[float] = None,
) -> GotenbergConversionResult:
    """Convert a DOC/DOCX payload to PDF via Gotenberg."""

    if not document_bytes:
        raise GotenbergConversionError("Cannot convert empty payload.")

    _ensure_size_allowed(document_bytes)
    base_url = _resolve_base_url()
    request_timeout = timeout or settings.GOTENBERG_TIMEOUT_SECONDS
    verify = not settings.GOTENBERG_SKIP_TLS_VERIFY

    files = {
        "files": (filename or "document", document_bytes, _resolve_mime_type(filename)),
    }
    data = {"output": _resolved_pdf_filename(filename)}

    url = f"{base_url}/forms/libreoffice/convert"
    try:
        async with httpx.AsyncClient(timeout=request_timeout, verify=verify) as client:
            response = await client.post(url, files=files, data=data)
    except httpx.HTTPError as exc:
        logger.error("Gotenberg conversion request failed: %s", exc)
        raise GotenbergConversionError("Conversion request failed.") from exc

    if response.status_code >= 400:
        snippet = response.text[:200]
        logger.error("Gotenberg returned %s: %s", response.status_code, snippet)
        raise GotenbergConversionError(
            f"Gotenberg responded with status {response.status_code}."
        )

    content_type = response.headers.get("content-type", "application/pdf")
    output_name = (
        response.headers.get("x-gotenberg-output-filename")
        or _resolved_pdf_filename(filename)
    )

    return GotenbergConversionResult(
        content=response.content,
        filename=output_name,
        content_type=content_type,
    )


async def healthcheck(timeout: Optional[float] = None) -> bool:
    """Check whether the Gotenberg endpoint is reachable."""

    try:
        base_url = _resolve_base_url()
    except GotenbergNotConfigured:
        return False

    health_path = settings.GOTENBERG_HEALTHCHECK_PATH.strip("/")
    url = f"{base_url}/{health_path}" if health_path else base_url
    request_timeout = timeout or settings.GOTENBERG_TIMEOUT_SECONDS
    verify = not settings.GOTENBERG_SKIP_TLS_VERIFY

    try:
        async with httpx.AsyncClient(timeout=request_timeout, verify=verify) as client:
            response = await client.get(url)
        return response.status_code < 500
    except httpx.HTTPError:
        return False
