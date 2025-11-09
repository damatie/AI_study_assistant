"""
Helper utilities for using Gemini Files API across AI features.
Provides DRY, scalable functions to handle file URI retrieval and refresh.
"""
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.study_material import StudyMaterial
from app.services.storage_service import get_storage_backend
from app.services.material_processing_service.gemini_files import (
    GeminiFileMetadata,
    encode_gemini_file_metadata,
    get_or_refresh_gemini_file,
    is_supported_file_type,
)

logger = logging.getLogger(__name__)


async def get_gemini_file_reference_for_material(
    material: StudyMaterial,
    session: AsyncSession,
) -> Optional[GeminiFileMetadata]:
    """Return a valid Gemini Files reference for the given study material."""

    filename = (material.file_name or "").strip()
    if not filename or not is_supported_file_type(filename):
        logger.debug(
            "Material %s is not a supported Gemini Files type (filename=%s); skipping upload",
            material.id,
            filename,
        )
        return None

    backend = get_storage_backend()

    try:
        file_bytes = await backend.get_bytes(key=material.file_path)

        metadata = await get_or_refresh_gemini_file(
            material.content or "",
            file_bytes,
            filename,
        )

        encoded = encode_gemini_file_metadata(
            metadata.uri,
            metadata.expires_at,
            metadata.mime_type,
        )

        if encoded != (material.content or ""):
            material.content = encoded
            session.add(material)
            await session.commit()
            logger.info(
                "Stored Gemini file metadata for material %s (expires %s)",
                material.id,
                metadata.expires_at,
            )

        return metadata
    except Exception as exc:
        logger.error(
            "Failed to get Gemini file reference for material %s: %s",
            material.id,
            exc,
        )
        return None


async def get_gemini_file_uri_for_material(
    material: StudyMaterial,
    session: AsyncSession,
) -> Optional[str]:
    """Backward-compatible wrapper returning only the file URI string."""

    reference = await get_gemini_file_reference_for_material(material, session)
    return reference.uri if reference else None
