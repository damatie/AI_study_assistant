# Standard library imports
import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

# Third-party imports
from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

# Local imports
from app.core.response import success_response, error_response, ResponseModel
from app.core.plan_limits import plan_limit_error
from app.db.deps import get_db
from app.models.plan import Plan as PlanModel
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.models.assessment_session import AssessmentSession as SessionModel
from app.models.submission import Submission as SubmissionModel
from app.api.v1.routes.auth.auth import get_current_user
from app.services.storage_service import store_bytes, generate_access_url, delete_bytes
from app.services.material_processing_service.handle_material_processing import (
    get_pdf_page_count_from_bytes,
    process_pdf_via_gemini,
    process_image_via_gemini,
)
from app.services.material_processing_service.tasks import generate_light_overview, generate_detailed_notes
from app.utils.processed_payload import get_overview, get_detailed, set_overview_env
from app.services.subscription_access import get_active_subscription
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.utils.enums import MaterialStatus, SubscriptionStatus

# Initialize logger
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/materials", tags=["materials"])

# Simple in-memory throttle so we don't requeue the same material too often
_overview_requeue_cache: dict[str, datetime] = {}


@router.post(
    "/upload",
    response_model=ResponseModel,
    status_code=status.HTTP_201_CREATED,
)
async def upload_material(
    title: str = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload and start light overview generation (PDF, JPG, JPEG, PNG). Detailed notes are generated on-demand."""
    # 0) Check if user has active subscription
    sub = await get_active_subscription(current_user, db)
    if not sub:
        return error_response("No active subscription found. Please subscribe to upload materials.", 403)

    # 1) Load plan & usage
    plan = await db.get(PlanModel, current_user.plan_id)
    usage = await get_or_create_usage(current_user, db)
    if usage.uploads_count >= plan.monthly_upload_limit:
        return plan_limit_error(
            message="You've reached your monthly upload limit. Upgrade to upload more.",
            error_type="MONTHLY_UPLOAD_LIMIT_EXCEEDED",
            current_plan=plan.name,
            metric="monthly_uploads",
            used=usage.uploads_count,
            limit=plan.monthly_upload_limit,
        )

    accepted_exts = {".pdf", ".jpg", ".jpeg", ".png"}
    _, ext = os.path.splitext(file.filename or "")
    ext = ext.lower()
    if ext not in accepted_exts:
        return error_response(
            msg="Unsupported file type. Please upload a PDF, JPG, JPEG or PNG file.",
            data={"error_type": "UNSUPPORTED_FILE_TYPE", "accepted_types": list(accepted_exts)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    tmp_path = None
    try:
        # 2) Read upload bytes and push to storage backend
        content_bytes = await file.read()
        # Generate a UUID for DB primary key as a UUID object (not string)
        material_uuid = uuid.uuid4()
        file_ext = os.path.splitext(file.filename)[1] if file.filename else ".pdf"
        object_key = await store_bytes(data=content_bytes, filename=file.filename or f"upload{file_ext}", content_type=file.content_type)

        logger.info(f"User {current_user.id} uploading file {file.filename} to storage key: {object_key}")

        # 3) Validate the file is actually a PDF if claimed to be
        page_count = 1

        if file.content_type == "application/pdf" or file_ext.lower() == ".pdf":
            try:
                # Use centralized PDF page count function
                page_count = get_pdf_page_count_from_bytes(content_bytes)
                if page_count > plan.pages_per_upload_limit:
                    return plan_limit_error(
                        message=f"This file has {page_count} pages, exceeding your plan limit of {plan.pages_per_upload_limit}.",
                        error_type="PAGES_PER_UPLOAD_LIMIT_EXCEEDED",
                        current_plan=plan.name,
                        metric="pages_per_upload",
                        actual=page_count,
                        limit=plan.pages_per_upload_limit,
                    )
            except Exception as e:
                logger.warning(f"Could not verify PDF pages in memory: {e}")

        # 4) Create DB row. Set status=processing while we synchronously generate overview.
        material_id = material_uuid  # Use UUID object for database PK
        mat = StudyMaterialModel(
            id=material_id,
            user_id=current_user.id,
            title=title,
            file_name=file.filename,
            file_path=object_key,  # store object key
            content="",
            processed_content=None,
            page_count=page_count,
            status=MaterialStatus.processing,
            created_at=datetime.now(timezone.utc),
        )
        db.add(mat)
        await db.commit()
        await db.refresh(mat)

        # 5) Increment usage
        usage.uploads_count += 1
        db.add(usage)
        await db.commit()

        # 6) Synchronously generate light overview and update DB
        try:
            # Write uploaded bytes to a temp file for the processor
            with open(os.path.join(os.getenv("TMP", os.getenv("TEMP", ".")), f"upload_{material_uuid}{file_ext}"), "wb") as fp:
                fp.write(content_bytes)
                tmp_path = fp.name

            md = "# Overview Processing Failed\n\nUnsupported file type."
            new_page_count = page_count

            if (file.content_type == "application/pdf") or (file_ext.lower() == ".pdf"):
                _, md, new_page_count = await process_pdf_via_gemini(tmp_path, mode="overview")
            elif file_ext.lower() in (".png", ".jpg", ".jpeg"):
                _, md = await process_image_via_gemini(tmp_path, mode="overview")
            else:
                md = "# Overview Processing Failed\n\nUnsupported file type."

            # Update envelope and status
            updated_payload = set_overview_env(mat.processed_content, md)
            mat.processed_content = updated_payload
            # Prefer new_page_count if returned
            mat.page_count = new_page_count or mat.page_count
            # Mark as completed on success, else failed
            mat.status = MaterialStatus.completed if not md.startswith("# Overview Processing Failed") else MaterialStatus.failed

            db.add(mat)
            await db.commit()
            await db.refresh(mat)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        return success_response(
            msg="Material uploaded.",
            data={"material_id": str(material_uuid), "page_count": mat.page_count},
            status_code=status.HTTP_201_CREATED,
        )

    except HTTPException:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    except Exception as e:
        logger.exception(f"Failed to upload material: {e}")
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


@router.get(
    "",
    response_model=ResponseModel,
)
async def list_materials(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all study materials for the current user"""
    q = await db.execute(
        select(StudyMaterialModel)
        .where(StudyMaterialModel.user_id == current_user.id)
        .order_by(StudyMaterialModel.created_at.desc())
    )
    mats = q.scalars().all()

    data = []
    for m in mats:
        # Extract file extension from file_name
        file_extension = (
            os.path.splitext(m.file_name)[1].lstrip(".") if m.file_name else ""
        )

        # Unpack envelope for frontend: quick overview availability & notes status
        env_overview = get_overview(m.processed_content)
        env_detailed = get_detailed(m.processed_content)
        # Derive status: if overview exists but detailed not yet and DB says processing, present idle
        status_out = m.status.value
        if status_out == "processing" and env_overview and not env_detailed:
            status_out = "idle"
        data.append(
            {
                "id": str(m.id),
                "title": m.title,
                "created_at": m.created_at.isoformat(),
                "file_extension": file_extension,
                "status": status_out,
                "page_count": m.page_count,
                "file_name": m.file_name,
                # Back-compat: has_content means detailed exists
                "has_content": bool(env_detailed),
                "has_overview": bool(env_overview),
                "has_detailed": bool(env_detailed),
            }
        )

    logger.info(f"User {current_user.id} fetched {len(data)} materials")
    return success_response(msg="Materials fetched", data=data)


@router.get(
    "/{material_id}",
    response_model=ResponseModel,
)
async def get_material(
    material_id: str,
    include_src_url: bool = False,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific study material by ID with optional source file URL for study interface"""
    try:
        import uuid as _uuid
        try:
            lookup_id = _uuid.UUID(material_id)
        except Exception:
            lookup_id = material_id  # fallback; DB may coerce
        mat = await db.get(StudyMaterialModel, lookup_id)
        if not mat:
            logger.warning(f"Material {material_id} not found")
            raise HTTPException(status_code=404, detail="Material not found")
        
        if mat.user_id != current_user.id:
            logger.warning(f"Access denied: User {current_user.id} tried to access material {material_id} owned by {mat.user_id}")
            raise HTTPException(status_code=403, detail="Access denied: You don't own this material")
        
        # Base data always included
        data = {
            "id": str(mat.id),
            "title": mat.title,
            "file_name": mat.file_name,
            # Backward compat: keep processed_content, but also expose envelope-unpacked fields
            "processed_content": get_detailed(mat.processed_content),
            "light_overview": get_overview(mat.processed_content),
            "page_count": mat.page_count,
            "status": mat.status.value,
            "created_at": mat.created_at.isoformat(),
        }
        # If overview is still missing and it's been > 20s since creation, requeue once per 60s
        try:
            env_overview = get_overview(mat.processed_content)
            env_detailed = get_detailed(mat.processed_content)
            # Do NOT derive idle here so the client can reflect processing during detailed generation
            if (
                data["status"] == "processing"
                and not env_overview
                and (datetime.now(timezone.utc) - mat.created_at) > timedelta(seconds=20)
            ):
                key = str(mat.id)
                last = _overview_requeue_cache.get(key)
                if not last or (datetime.now(timezone.utc) - last) > timedelta(seconds=60):
                    asyncio.create_task(generate_light_overview(key))
                    _overview_requeue_cache[key] = datetime.now(timezone.utc)
                    logger.info(f"Re-queued overview generation for material {key}")
        except Exception:
            pass
        
        # Only generate source file URL if requested (for study interface)
        if include_src_url and mat.file_path:
            try:
                src_url = await generate_access_url(key=mat.file_path)
                data["src_url"] = src_url
                logger.info(f"Generated access URL for user {current_user.id} material {material_id}")
            except Exception as e:
                logger.warning(f"Unable to generate access URL for {material_id}: {e}")
                data["src_url"] = None
        elif include_src_url:
            # Requested source URL but no file path
            data["src_url"] = None
            
        return success_response(msg="Material retrieved", data=data)
    except Exception as e:
        # Handle case when material_id doesn't exist
        raise HTTPException(status_code=404, detail="Material not found")


class UpdateMaterialRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


@router.patch(
    "/{material_id}",
    response_model=ResponseModel,
)
async def update_material(
    material_id: str,
    payload: UpdateMaterialRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update fields on a study material (currently supports title)."""
    import uuid as _uuid
    try:
        lookup_id = _uuid.UUID(material_id)
    except Exception:
        lookup_id = material_id

    mat = await db.get(StudyMaterialModel, lookup_id)
    if not mat:
        return error_response("Material not found", status_code=status.HTTP_404_NOT_FOUND)

    if mat.user_id != current_user.id:
        return error_response(
            "Access denied: You don't own this material", status_code=status.HTTP_403_FORBIDDEN
        )

    # Update allowed fields
    updated = False
    new_title = payload.title.strip()
    if new_title and new_title != mat.title:
        mat.title = new_title
        updated = True

    if not updated:
        return success_response(msg="No changes", data={"id": str(mat.id), "title": mat.title})

    db.add(mat)
    await db.commit()
    await db.refresh(mat)
    logger.info(f"User {current_user.id} renamed material {material_id} to '{mat.title}'")
    return success_response(msg="Material updated", data={"id": str(mat.id), "title": mat.title})


@router.post(
    "/{material_id}/generate-notes",
    response_model=ResponseModel,
)
async def trigger_generate_detailed_notes(
    material_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger background task to generate detailed study notes for a material."""
    import uuid as _uuid
    try:
        lookup_id = _uuid.UUID(material_id)
    except Exception:
        lookup_id = material_id
    mat = await db.get(StudyMaterialModel, lookup_id)
    if not mat:
        return error_response("Material not found", status_code=status.HTTP_404_NOT_FOUND)
    if mat.user_id != current_user.id:
        return error_response("Access denied: You don't own this material", status_code=status.HTTP_403_FORBIDDEN)

    # Avoid duplicate triggers only if detailed already exists or status is completed
    env_detailed = get_detailed(mat.processed_content)
    if env_detailed:
        return success_response(msg="Detailed notes already exist.", data={"status": mat.status.value})
    # Allow triggering even if DB status is processing (post-overview) when detailed is missing

    asyncio.create_task(generate_detailed_notes(material_id))
    return success_response(msg="Notes generation started.", data={"status": MaterialStatus.processing.value})


@router.post(
    "/{material_id}/regenerate-overview",
    response_model=ResponseModel,
)
async def regenerate_overview(
    material_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate the light overview using the improved structured prompt."""
    import uuid as _uuid
    try:
        lookup_id = _uuid.UUID(material_id)
    except Exception:
        lookup_id = material_id
    mat = await db.get(StudyMaterialModel, lookup_id)
    if not mat:
        return error_response("Material not found", status_code=status.HTTP_404_NOT_FOUND)
    if mat.user_id != current_user.id:
        return error_response("Access denied: You don't own this material", status_code=status.HTTP_403_FORBIDDEN)

    # Kick off background regeneration of overview
    asyncio.create_task(generate_light_overview(material_id))
    return success_response(msg="Overview regeneration started.", data={"status": MaterialStatus.processing.value})


@router.delete(
    "/{material_id}",
    response_model=ResponseModel,
)
async def delete_material(
    material_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a study material and all related assessments"""
    # 1. Load and authorize
    mat = await db.get(StudyMaterialModel, material_id)
    if not mat:
        logger.warning(f"Delete attempt: Material {material_id} not found")
        return error_response("Material not found", status_code=status.HTTP_404_NOT_FOUND)
    
    if mat.user_id != current_user.id:
        logger.warning(f"Delete denied: User {current_user.id} tried to delete material {material_id} owned by {mat.user_id}")
        return error_response("Access denied: You don't own this material", status_code=status.HTTP_403_FORBIDDEN)

    # 2. Delete the file from storage (S3/R2) before deleting DB records
    if mat.file_path:
        try:
            deleted = await delete_bytes(key=mat.file_path)
            if deleted:
                logger.info(f"Successfully deleted storage file: {mat.file_path}")
            else:
                logger.warning(f"Storage file not found (may have been already deleted): {mat.file_path}")
        except Exception as e:
            logger.error(f"Failed to delete storage file {mat.file_path}: {e}")
            # Continue with DB deletion anyway - don't let storage errors block user action
    else:
        logger.warning(f"Material {material_id} has no file_path - skipping storage cleanup")

    # 3. Delete any related submissions/sessions? 
    #    (If you have a FK ON DELETE CASCADE you can skip these)
    # For example, to delete sessions & submissions tied to this material:
    
    await db.execute(
        delete(SubmissionModel)
        .where(SubmissionModel.session_id.in_(
            select(SessionModel.id).where(SessionModel.material_id == material_id)
        ))
    )
    await db.execute(
        delete(SessionModel)
        .where(SessionModel.material_id == material_id)
    )

    # 4. Delete the material from database
    await db.execute(
        delete(StudyMaterialModel).where(StudyMaterialModel.id == material_id)
    )
    await db.commit()

    logger.info(f"User {current_user.id} successfully deleted material {material_id} ({mat.title})")
    return success_response(msg="Material deleted")
