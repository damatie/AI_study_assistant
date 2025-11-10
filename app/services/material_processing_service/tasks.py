import logging
from contextlib import redirect_stdout, redirect_stderr
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import AsyncSessionLocal
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.utils.enums import MaterialStatus
from app.utils.processed_payload import set_overview_env, set_detailed_env, set_suggestions_env
from app.services.material_processing_service.handle_material_processing import (
    process_pdf_via_gemini,
    process_image_via_gemini,
)
from app.services.material_processing_service.gemini_files import (
    encode_gemini_file_metadata,
    get_or_refresh_gemini_file,
    is_supported_file_type,
)
from app.services.ai_service.question_generator import generate_suggested_questions
from app.services.ai_service.notes_service import (
    NoteGenerationVariant,
    generate_notes_for_material,
)
from app.services.storage_service import get_storage_backend
import os
import tempfile

logger = logging.getLogger(__name__)


# Suppress stdout/stderr to avoid Windows pipe errors in background tasks
class _NullWriter:
    def write(self, msg):
        # Intentionally drop all writes
        return 0

    def flush(self):
        pass


def _is_pdf(name: str) -> bool:
    return name.lower().endswith(".pdf")


def _is_image(name: str) -> bool:
    return any(name.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp"))

async def _get_material(session: AsyncSession, material_id: str) -> Optional[StudyMaterialModel]:
    return await session.get(StudyMaterialModel, material_id)


async def generate_light_overview(material_id: str) -> None:
    """Async background task: generate overview and update DB envelope + overview_status-like state.

    Note: We leave `status` for detailed notes lifecycle. For overview, we reuse `status` only if needed.
    """
    null = _NullWriter()
    # Redirect any stray prints from libs or model SDKs to avoid WinError 233 on Windows
    with redirect_stdout(null), redirect_stderr(null):
        async with AsyncSessionLocal() as session:
            mat = await _get_material(session, material_id)
            if not mat:
                logger.error(f"Material {material_id} not found for overview generation")
                return

            # We don't have a separate overview_status column; keep status idle for notes
            # Optionally set a transient state by writing placeholder overview
            try:
                # Indicate processing (used by frontend to show loader on upload)
                await session.execute(
                    update(StudyMaterialModel)
                    .where(StudyMaterialModel.id == material_id)
                    .values(status=MaterialStatus.processing)
                )
                await session.commit()

                backend = get_storage_backend()
                obj_bytes = await backend.get_bytes(key=mat.file_path)
                md = "# Overview Processing Failed\n\nUnsupported file type."
                page_count = mat.page_count or 0

                gemini_metadata = None
                if is_supported_file_type(mat.file_name or ""):
                    try:
                        gemini_metadata = await get_or_refresh_gemini_file(
                            mat.content or "",
                            obj_bytes,
                            mat.file_name or "",
                        )
                        logger.info(
                            "Gemini Files reference ready for material %s (expires %s)",
                            mat.id,
                            gemini_metadata.expires_at,
                        )
                    except Exception as exc:
                        logger.error("Failed to obtain Gemini Files reference: %s", exc)
                        gemini_metadata = None

                # Write to temp file for processor
                ext = os.path.splitext(mat.file_name or "")[1].lower() or ".bin"
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_file:
                    tmp_file.write(obj_bytes)
                    tmp_path = tmp_file.name

                try:
                    if _is_pdf(mat.file_name or ""):
                        _, md, page_count = await process_pdf_via_gemini(
                            tmp_path, mode="overview", title=(mat.title or mat.file_name or "Overview")
                        )
                    elif _is_image(mat.file_name or ""):
                        _, md = await process_image_via_gemini(
                            tmp_path, mode="overview", title=(mat.title or mat.file_name or "Overview")
                        )
                    else:
                        md = "# Overview Processing Failed\n\nUnsupported file type."
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

                # Generate AI-powered suggested questions (seamlessly during overview)
                try:
                    questions = await generate_suggested_questions(
                        content=md,
                        title=mat.title or mat.file_name or "Material"
                    )
                    logger.info(f"Generated {len(questions)} questions for material {material_id}")
                except Exception as e:
                    logger.warning(f"Question generation failed, will use fallback: {e}")
                    questions = None

                # Update processed_content envelope with overview and questions
                new_payload = set_overview_env(mat.processed_content, md)
                if questions:
                    new_payload = set_suggestions_env(new_payload, questions)
                    
                await session.execute(
                    update(StudyMaterialModel)
                    .where(StudyMaterialModel.id == material_id)
                    .values(
                        processed_content=new_payload,
                        page_count=page_count or mat.page_count,
                        status=MaterialStatus.completed,
                        content=(
                            encode_gemini_file_metadata(
                                gemini_metadata.uri,
                                gemini_metadata.expires_at,
                                gemini_metadata.mime_type,
                            )
                            if gemini_metadata
                            else (mat.content or "")
                        ),
                    )
                )
                await session.commit()
                logger.info(f"Overview generated for material {material_id}")
            except Exception:
                logger.exception("Overview generation failed")
                # Write failure overview into envelope for transparency
                new_payload = set_overview_env(mat.processed_content, "# Overview Processing Failed\n\nAn error occurred.")
                await session.execute(
                    update(StudyMaterialModel)
                    .where(StudyMaterialModel.id == material_id)
                    .values(processed_content=new_payload, status=MaterialStatus.failed)
                )
                await session.commit()


async def generate_detailed_notes(material_id: str) -> None:
    """Async background task: generate detailed notes and update `status` lifecycle."""
    null = _NullWriter()
    with redirect_stdout(null), redirect_stderr(null):
        async with AsyncSessionLocal() as session:
            mat = await _get_material(session, material_id)
            if not mat:
                logger.error(f"Material {material_id} not found for notes generation")
                return

            # Set processing
            await session.execute(
                update(StudyMaterialModel)
                .where(StudyMaterialModel.id == material_id)
                .values(status=MaterialStatus.processing)
            )
            await session.commit()

            try:
                md = "# Processing Failed\n\nUnsupported file type."
                page_count = mat.page_count or 0

                backend = get_storage_backend()
                obj_bytes = await backend.get_bytes(key=mat.file_path)

                gemini_metadata = None
                if _is_pdf(mat.file_name or "") or _is_image(mat.file_name or ""):
                    if is_supported_file_type(mat.file_name or ""):
                        try:
                            gemini_metadata = await get_or_refresh_gemini_file(
                                mat.content or "",
                                obj_bytes,
                                mat.file_name or "",
                            )
                        except Exception as exc:
                            logger.warning("Unable to prepare Gemini Files reference: %s", exc)
                            gemini_metadata = None

                    notes_result = await generate_notes_for_material(
                        file_bytes=obj_bytes,
                        filename=mat.file_name or "",
                        title=mat.title or mat.file_name or "Study Notes",
                        variant=NoteGenerationVariant.detailed,
                        gemini_file=gemini_metadata,
                        page_count=mat.page_count,
                    )
                    md = notes_result.markdown
                    if notes_result.page_count:
                        page_count = notes_result.page_count
                else:
                    md = "# Processing Failed\n\nUnsupported file type."

                new_payload = set_detailed_env(mat.processed_content, md)

                update_values = {
                    "processed_content": new_payload,
                    "page_count": page_count or mat.page_count,
                    "status": MaterialStatus.completed if not md.startswith("# Processing Failed") else MaterialStatus.failed,
                }

                if gemini_metadata:
                    update_values["content"] = encode_gemini_file_metadata(
                        gemini_metadata.uri,
                        gemini_metadata.expires_at,
                        gemini_metadata.mime_type,
                    )

                await session.execute(
                    update(StudyMaterialModel)
                    .where(StudyMaterialModel.id == material_id)
                    .values(**update_values)
                )
                await session.commit()
                logger.info(f"Detailed notes generated for material {material_id}")
            except Exception:
                logger.exception("Detailed notes generation failed")
                new_payload = set_detailed_env(mat.processed_content, "# Processing Failed\n\nAn error occurred.")
                await session.execute(
                    update(StudyMaterialModel)
                    .where(StudyMaterialModel.id == material_id)
                    .values(
                        processed_content=new_payload,
                        status=MaterialStatus.failed,
                    )
                )
                await session.commit()
