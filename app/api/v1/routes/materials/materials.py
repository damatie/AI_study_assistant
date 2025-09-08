# Standard library imports
import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

# Third-party imports
from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Local imports
from app.core.response import success_response, error_response, ResponseModel
from app.db.deps import AsyncSessionLocal, get_db
from app.models.plan import Plan as PlanModel
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.models.assessment_session import AssessmentSession as SessionModel
from app.models.submission import Submission as SubmissionModel
from app.api.v1.routes.auth.auth import get_current_user
from app.services.storage_service import store_bytes, generate_access_url
from app.services.material_processing_service.handle_material_processing import (
    get_pdf_page_count_from_bytes,
    process_image_via_gemini,
    process_pdf_via_gemini
)
from app.services.track_subscription_service.handle_track_subscription import (
    renew_subscription_for_user,
)
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.utils.enums import MaterialStatus, SubscriptionStatus

# Initialize logger
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/materials", tags=["materials"])


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
    """Upload and process study materials (PDF, JPG, JPEG, PNG)"""
    # 0) Ensure subscription is active
    sub = await renew_subscription_for_user(current_user, db)
    if sub.status != SubscriptionStatus.active:
        return error_response("Your subscription is not active", 403)

    # 1) Load plan & usage
    plan = await db.get(PlanModel, current_user.plan_id)
    usage = await get_or_create_usage(current_user, db)
    if usage.uploads_count >= plan.monthly_upload_limit:
        return error_response(
            msg="You've reached your monthly upload limit. Upgrade to upload more.",
            data={"error_type":"MONTHLY_UPLOAD_LIMIT_EXCEEDED","current_plan":plan.name},
            status_code=status.HTTP_403_FORBIDDEN,
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
        file_uuid = str(uuid.uuid4())
        file_ext = os.path.splitext(file.filename)[1] if file.filename else ".pdf"
        object_key = await store_bytes(data=content_bytes, filename=file.filename or f"upload{file_ext}", content_type=file.content_type)
        
        logger.info(f"User {current_user.id} uploading file {file.filename} to storage key: {object_key}")
        
        # 3) Validate the file is actually a PDF if claimed to be
        is_pdf = False
        page_count = 1
        
        if file.content_type == "application/pdf" or file_ext.lower() == ".pdf":
            try:
                # Use centralized PDF page count function
                page_count = get_pdf_page_count_from_bytes(content_bytes)
                is_pdf = True
                if page_count > plan.pages_per_upload_limit:
                    return error_response(
                        msg=f"This file has {page_count} pages, exceeding your plan limit of {plan.pages_per_upload_limit}.",
                        data={"error_type":"PAGES_PER_UPLOAD_LIMIT_EXCEEDED","current_plan":plan.name},
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
            except Exception as e:
                logger.warning(f"Could not verify PDF pages in memory: {e}")
                is_pdf = False
        
        # 4) Create DB row with status=processing
        material_id = file_uuid  # Use string UUID for database
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

        # 6) Background processing task
        async def process_material_content(material_id: str, object_key: str, is_pdf: bool, file_bytes: bytes):
            """Process uploaded material and generate study content."""
            try:
                logger.info(f"Starting background processing for material {material_id}")
                
                # Use centralized processing functions
                if is_pdf:
                    # For PDF processing, we need to save bytes to temp file since process_pdf_via_gemini expects file path
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
                        tmp_file.write(file_bytes)
                        tmp_path = tmp_file.name
                    
                    try:
                        _, markdown_content, actual_page_count = await process_pdf_via_gemini(tmp_path)
                    finally:
                        os.unlink(tmp_path)  # Clean up temp file
                else:
                    # For image processing, save to temp file
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
                        tmp_file.write(file_bytes)
                        tmp_path = tmp_file.name
                    
                    try:
                        _, markdown_content = await process_image_via_gemini(tmp_path)
                        actual_page_count = 1
                    finally:
                        os.unlink(tmp_path)  # Clean up temp file
                
                if not markdown_content:
                    markdown_content = "# Processing Failed\n\nNo content could be extracted from the material."
                
                # Update database
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(StudyMaterialModel)
                        .where(StudyMaterialModel.id == material_id)
                        .values(
                            content="",  # Raw text no longer stored
                            processed_content=markdown_content,
                            page_count=actual_page_count,
                            status=MaterialStatus.completed
                        )
                    )
                    await session.commit()
                
                logger.info(f"Successfully processed material {material_id}")
                
            except Exception as e:
                logger.exception(f"Processing failed for material {material_id}: {str(e)}")
                # Store error as markdown
                error_markdown = f"# Processing Failed\n\nError: {str(e)}"
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(StudyMaterialModel)
                        .where(StudyMaterialModel.id == material_id)
                        .values(
                            status=MaterialStatus.failed,
                            processed_content=error_markdown
                        )
                    )
                    await session.commit()

        # Start background processing with the file bytes we already have
        asyncio.create_task(process_material_content(material_id, object_key, is_pdf, content_bytes))

        return success_response(
            msg="Material uploaded. Summarization is in progress.",
            data={"material_id": material_id, "page_count": page_count},
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

        data.append(
            {
                "id": str(m.id),
                "title": m.title,
                "created_at": m.created_at.isoformat(),
                "file_extension": file_extension,
                "status": m.status.value, 
                "page_count": m.page_count,
                "file_name": m.file_name,
                "has_content": bool(m.processed_content),
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
        mat = await db.get(StudyMaterialModel, material_id)
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
            "processed_content": mat.processed_content,
            "page_count": mat.page_count,
            "status": mat.status.value,
            "created_at": mat.created_at.isoformat(),
        }
        
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

    # 2. Delete any related submissions/sessions? 
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

    # 3. Delete the material
    await db.execute(
        delete(StudyMaterialModel).where(StudyMaterialModel.id == material_id)
    )
    await db.commit()

    logger.info(f"User {current_user.id} successfully deleted material {material_id} ({mat.title})")
    return success_response(msg="Material deleted")
