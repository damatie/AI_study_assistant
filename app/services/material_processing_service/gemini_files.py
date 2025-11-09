"""Gemini Files API integration for study material assets (PDFs, images)."""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import google.generativeai as genai

from app.core.config import settings
from app.core.genai_client import get_gemini_model

logger = logging.getLogger(__name__)

# Configure Gemini API
genai.configure(api_key=settings.GOOGLE_API_KEY)

SUPPORTED_FILE_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


GEMINI_FILE_TTL = timedelta(hours=48)


@dataclass(frozen=True)
class GeminiFileMetadata:
    uri: str
    expires_at: datetime
    mime_type: Optional[str] = None


def is_supported_file_type(filename: str) -> bool:
    """Return True if the filename has an extension supported by Gemini Files."""
    ext = os.path.splitext(filename or "")[1].lower()
    return ext in SUPPORTED_FILE_MIME_TYPES


def _mime_type_for_filename(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in SUPPORTED_FILE_MIME_TYPES:
        raise ValueError(f"Unsupported file type for Gemini Files: {ext or 'unknown'}")
    return SUPPORTED_FILE_MIME_TYPES[ext]


async def upload_file_to_gemini(
    file_bytes: bytes,
    filename: str,
    display_name: Optional[str] = None,
) -> GeminiFileMetadata:
    """Upload a supported study material asset (PDF/image) to Gemini Files."""
    mime_type = _mime_type_for_filename(filename)

    try:
        logger.info("Uploading asset to Gemini Files API: %s (%s)", filename, mime_type)

        # The SDK expects a file path or file-like object. Persist bytes to a temp file.
        import tempfile

        suffix = os.path.splitext(filename or "")[1] or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            uploaded_file = genai.upload_file(
                path=tmp_path,
                mime_type=mime_type,
                display_name=display_name or filename,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        # Calculate expiration (48 hours from now)
        expiration_time = datetime.utcnow() + GEMINI_FILE_TTL

        logger.info("Asset uploaded successfully: %s", uploaded_file.uri)
        logger.info("File expires at: %s", expiration_time)

        return GeminiFileMetadata(
            uri=uploaded_file.uri,
            expires_at=expiration_time,
            mime_type=mime_type,
        )

    except Exception as e:
        logger.error("Failed to upload asset to Gemini Files: %s", e)
        raise


async def generate_from_gemini_file(
    *,
    file_uri: str,
    prompt: str,
    mime_type: str,
    generation_config: Optional[dict] = None,
) -> str:
    """
    Generate content using a Gemini file URI.

    Args:
        file_uri: Gemini file URI (e.g., "https://generativelanguage.googleapis.com/v1beta/files/abc123")
        prompt: Generation prompt
        mime_type: MIME type associated with the uploaded Gemini file
        generation_config: Optional Gemini generation config overrides

    Returns:
        Generated text content

    Raises:
        Exception: If generation fails
    """
    try:
        logger.info("Generating content from Gemini file: %s", file_uri)

        model = get_gemini_model()

        parts = [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"file_data": {"file_uri": file_uri, "mime_type": mime_type}},
                ],
            }
        ]

        response = await model.generate_content_async(
            parts,
            generation_config=generation_config,
        )

        if not response or not getattr(response, "text", ""):
            raise ValueError("Empty response from Gemini")

        text = response.text.strip()
        logger.info("Generated %s characters", len(text))
        return text

    except Exception as e:
        logger.error("Failed to generate from Gemini file: %s", e)
        raise


async def get_or_refresh_gemini_file(
    material_content: str,
    file_bytes: bytes,
    filename: str,
) -> GeminiFileMetadata:
    """Return a valid Gemini Files reference, uploading a fresh copy when needed."""

    expected_mime = _mime_type_for_filename(filename)
    metadata = decode_gemini_file_metadata(material_content)
    now = datetime.utcnow()

    if metadata:
        current_mime = metadata.mime_type or expected_mime
        if metadata.expires_at > now and current_mime == expected_mime:
            logger.info(
                "Using existing Gemini file URI (expires %s)",
                metadata.expires_at,
            )
            return GeminiFileMetadata(
                uri=metadata.uri,
                expires_at=metadata.expires_at,
                mime_type=current_mime,
            )

        logger.info(
            "Gemini file reference expired or mismatched (expires %s, mime %s -> %s); re-uploading",
            metadata.expires_at,
            current_mime,
            expected_mime,
        )
    else:
        logger.info("No Gemini file metadata found; uploading material: %s", filename)

    return await upload_file_to_gemini(file_bytes, filename)


def encode_gemini_file_metadata(
    uri: str,
    expires_at: datetime,
    mime_type: Optional[str] = None,
) -> str:
    """Encode Gemini file metadata for storage inside `StudyMaterial.content`."""

    parts = [
        f"gemini_file_uri:{uri}",
        f"expires:{expires_at.isoformat()}",
    ]
    if mime_type:
        parts.append(f"mime:{mime_type}")
    return "|".join(parts)


def decode_gemini_file_metadata(content: str) -> Optional[GeminiFileMetadata]:
    """Decode stored Gemini file metadata (handles legacy formats gracefully)."""

    if not content or not content.startswith("gemini_file_uri:"):
        return None

    try:
        entries = {}
        for section in content.split("|"):
            if ":" not in section:
                continue
            key, value = section.split(":", 1)
            entries[key] = value

        uri = entries.get("gemini_file_uri")
        expires_raw = entries.get("expires")
        if not uri or not expires_raw:
            return None

        expires_at = datetime.fromisoformat(expires_raw)
        mime_type = entries.get("mime")

        return GeminiFileMetadata(uri=uri, expires_at=expires_at, mime_type=mime_type)
    except Exception:
        return None
