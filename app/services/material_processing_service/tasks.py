import logging
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import AsyncSessionLocal
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.utils.enums import MaterialStatus
from app.utils.processed_payload import set_overview_env, set_detailed_env
from app.services.material_processing_service.handle_material_processing import (
    process_pdf_via_gemini,
    process_image_via_gemini,
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

                # Update processed_content envelope, page_count and mark overall status completed (overview ready)
                new_payload = set_overview_env(mat.processed_content, md)
                await session.execute(
                    update(StudyMaterialModel)
                    .where(StudyMaterialModel.id == material_id)
                    .values(
                        processed_content=new_payload,
                        page_count=page_count or mat.page_count,
                        status=MaterialStatus.completed,
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
                ext = os.path.splitext(mat.file_name or "")[1].lower() or ".bin"
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_file:
                    tmp_file.write(obj_bytes)
                    tmp_path = tmp_file.name

                try:
                    if _is_pdf(mat.file_name or ""):
                        _, md, page_count = await process_pdf_via_gemini(
                            tmp_path, mode="detailed", title=(mat.title or mat.file_name or "Study Notes")
                        )
                    elif _is_image(mat.file_name or ""):
                        _, md = await process_image_via_gemini(
                            tmp_path, mode="detailed", title=(mat.title or mat.file_name or "Study Notes")
                        )
                    else:
                        md = "# Processing Failed\n\nUnsupported file type."
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

                new_payload = set_detailed_env(mat.processed_content, md)
                await session.execute(
                    update(StudyMaterialModel)
                    .where(StudyMaterialModel.id == material_id)
                    .values(
                        processed_content=new_payload,
                        page_count=page_count or mat.page_count,
                        status=MaterialStatus.completed if not md.startswith("# Processing Failed") else MaterialStatus.failed,
                    )
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
