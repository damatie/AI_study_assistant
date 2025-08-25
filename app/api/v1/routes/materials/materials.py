# Standard library imports
import asyncio
import json
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
from app.services.material_processing_service.handle_material_processing import (
    get_pdf_page_count, 
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
        # 2) Save upload to a temp file
        content_bytes = await file.read()
        
        # Create a permanent directory for uploads if it doesn't exist
        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "uploads")
        upload_dir = os.path.normpath(upload_dir)
        os.makedirs(upload_dir, exist_ok=True)
        
        # Create a permanent file path with UUID to avoid collisions
        file_uuid = str(uuid.uuid4())
        file_ext = os.path.splitext(file.filename)[1] if file.filename else ".pdf"
        permanent_path = os.path.join(upload_dir, f"{file_uuid}{file_ext}")
        
        # Write content to the permanent file
        with open(permanent_path, "wb") as f:
            f.write(content_bytes)
        
        tmp_path = permanent_path  # For error handling compatibility
        
        # 3) Validate the file is actually a PDF if claimed to be
        is_pdf = False
        page_count = 1
        
        if file.content_type == "application/pdf" or file_ext.lower() == ".pdf":
            try:
                page_count = get_pdf_page_count(permanent_path)
                is_pdf = True
                if page_count > plan.pages_per_upload_limit:
                    os.unlink(permanent_path)
                    return error_response(
                        msg=f"This file has {page_count} pages, exceeding your plan limit of {plan.pages_per_upload_limit}.",
                        data={"error_type":"PAGES_PER_UPLOAD_LIMIT_EXCEEDED","current_plan":plan.name},
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
            except ValueError as e:
                logger.warning(f"Could not verify PDF: {str(e)}")
                # If it's not a PDF, we'll handle it differently but still continue
                is_pdf = False
        
        # 4) Create DB row with status=processing
        material_id = file_uuid  # Use string UUID for database
        mat = StudyMaterialModel(
            id=material_id,
            user_id=current_user.id,
            title=title,
            file_name=file.filename,
            file_path="",  # Store the path to the permanent file
            content="",              # will be populated in background
            processed_content=None,  # will be populated in background
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

        # 6) Background pipeline
        async def summarize_in_background(material_id: str, file_path: str, is_pdf: bool):
            try:
                logger.info(f"Starting background processing for material {material_id}, file: {file_path}, is_pdf: {is_pdf}")
                
                # Check if file exists before processing
                if not os.path.exists(file_path):
                    raise FileNotFoundError(f"File not found at path: {file_path}")
                    
                # Handle both PDF and image files
                if is_pdf:
                    raw_text, processed_json, actual_count = await process_pdf_via_gemini(file_path)
                else:
                    # Process as a single image if not a PDF
                    logger.info(f"Processing as image: {file_path}")
                    raw_text, processed_json = await process_image_via_gemini(file_path)
                    actual_count = 1
                
                logger.info(f"Processing complete for {material_id}, serializing results")
                
                # Ensure processed_json is not None
                if processed_json is None:
                    processed_json = {"pages": [{"summary": "", "topics": [], "equations": []}]}
                
                
                # Ensure processed_json is properly serialized
                if not isinstance(processed_json, str):

                    try:
                        processed_json_data = processed_json
                    except Exception as e:
                        logger.error(f"JSON serialization error: {str(e)}")
                        # Create a safe fallback
                        processed_json_data = json.dumps({"pages": [{"summary": f"Error: {str(e)}", "topics": [], "equations": []}]})
                else:
                    processed_json_data = processed_json

                logger.info(f"Updating database for material {material_id}")
                
                # update the material record
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(StudyMaterialModel)
                        .where(StudyMaterialModel.id == material_id)
                        .values(
                            content=raw_text,
                            processed_content=processed_json_data,
                            page_count=actual_count,
                            status=MaterialStatus.completed
                        )
                    )
                    await session.commit()
                    
                logger.info(f"Successfully processed material {material_id}")
                    
            except Exception as e:
                logger.exception(f"Processing failed for material {material_id}: {str(e)}")
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(StudyMaterialModel)
                        .where(StudyMaterialModel.id == material_id)
                        .values(
                            status=MaterialStatus.failed,
                            # Store error message in processed_content
                            processed_content=json.dumps({
                                "error": str(e),
                                "pages": [{"summary": f"Error: {str(e)}", "topics": [], "equations": []}]
                            })
                        )
                    )
                    await session.commit()

        # fire-and-forget
        background_task = asyncio.create_task(summarize_in_background(material_id, permanent_path, is_pdf))

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

        # Count topics in processed_content if available
        topic_count = 0
        if m.processed_content and isinstance(m.processed_content, dict):
            topics = m.processed_content.get("topics", [])
            topic_count = len(topics) if isinstance(topics, list) else 0

        data.append(
            {
                "id": str(m.id),
                "title": m.title,
                "created_at": m.created_at.isoformat(),
                "file_extension": file_extension,
                "topic_count": topic_count,
                "status": m.status.value, 
                "page_count": m.page_count,
            }
        )

    return success_response(msg="Materials fetched", data=data)


@router.get(
    "/{material_id}",
    response_model=ResponseModel,
)
async def get_material(
    material_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific study material by ID"""
    try:
        mat = await db.get(StudyMaterialModel, material_id)
        if not mat or mat.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Material not found")
        
        data = {
            "id": str(mat.id),
            "title": mat.title,
            "content": mat.content,
            "file_name": mat.file_name,
            "file_path": mat.file_path,
            "processed_content": mat.processed_content,
            "page_count": mat.page_count,
            "status": mat.status.value,
            "created_at": mat.created_at.isoformat(),
        }
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
    if not mat or mat.user_id != current_user.id:
        return error_response("Material not found", status_code=status.HTTP_404_NOT_FOUND)

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

    return success_response(msg="Material deleted")
