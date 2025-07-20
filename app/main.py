import asyncio
from contextlib import asynccontextmanager
import logging
import os
from fastapi.exceptions import (
    HTTPException as StarletteHTTPException,
    RequestValidationError,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal
import pytesseract
import uuid
import json
from datetime import datetime
from app.api.v1.routes.router import router as api_v1_router
from app.core.genai_client import get_gemini_model
from app.db.seed.plans import seed_all
from app.models.plan import Plan as PlanModel
from app.core.config import settings

#

from contextlib import asynccontextmanager
import logging, os, uuid, json
from datetime import datetime, timezone

from fastapi import (
    FastAPI,
    Depends,
    File,
    UploadFile,
    Form,
    Request,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update
from app.db.deps import AsyncSessionLocal, get_db
from app.core.response import success_response, error_response, ResponseModel
from app.api.v1.routes.auth.auth import get_current_user
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.models.assessment_session import AssessmentSession as SessionModel
from app.models.submission import Submission as SubmissionModel
from app.services.material_processing_service.handle_material_processing import  get_pdf_page_count, process_image_via_gemini,process_pdf_via_gemini
from app.services.track_subscription_service.handle_track_subscription import (
    renew_subscription_for_user,
)
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.utils.enums import MaterialStatus, SubscriptionStatus


# Initialize seeding plans table
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Run before the application starts accepting requests
    await seed_all()
    yield
    # Shutdown: Run when the application is shutting down
    pass


# Initialize FastAPI
app = FastAPI(
    title="AI Study Assistant API",
    description="API for AI-powered study assistant application",
    version="1.0.0",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# Include the router with prefix
app.include_router(
    api_v1_router,
    prefix="/api/v1",
)

pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD


# Custom exception handler
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # exc.detail might be a dict or str
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return error_response(msg, status_code=exc.status_code)


# Pydantic validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Format each error into a { field, message } dict
    errors = []
    for err in exc.errors():
        # skip the "body" prefix in loc, join the rest by dot
        loc = err.get("loc", [])
        field = ".".join(str(x) for x in loc if x != "body")
        errors.append({"field": field or loc[-1], "message": err.get("msg")})

    return error_response(
        msg="Invalid request parameters", data=errors, status_code=422
    )


logger = logging.getLogger(__name__)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# Initialize Gemini model

model = get_gemini_model()

# Per‑assessment question limit for Freemium
DEFAULT_MAX_QUESTIONS = 5


# Data models
class StudyMaterial(BaseModel):
    id: str
    user_id: str
    title: str
    content: str
    processed_content: Optional[dict] = None
    created_at: datetime


class Question(BaseModel):
    question: str
    context_id: Optional[str] = None


class Assessment(BaseModel):
    material_id: Optional[uuid.UUID] = Field(
        None,
        description="UUID of the study material for context",
        example="3fa85f64-5717-4562-b3fc-2c963f66afa6",
    )
    topic: str = Field(
        ..., description="The topic of your choice", example="Photosynthesis"
    )
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    question_types: List[
        Literal["multiple_choice", "true_false", "short_answer", "flash_cards"]
    ] = [
        "multiple_choice"
    ]
    num_questions: Optional[int] = 5  # Default to 5 questions per type

    @field_validator("difficulty", mode="before")
    def normalize_difficulty(cls, v):
        # accept “Easy”, “EASY”, etc., but store as lowercase
        if isinstance(v, str):
            return v.lower()
        return v
    
    @field_validator("topic")
    def topic_not_empty(cls, v):
        if v is None or v.strip() == "":
            raise ValueError("Topic cannot be empty")
        return v.strip()

    @field_validator("num_questions")
    def validate_num_questions(cls, v):
        if v is not None and v < 5:
            raise ValueError("Number of questions must be at least 5")
        return v


class GradeAssessment(BaseModel):
    question_index: int
    question_type: Literal["multiple_choice","true_false","short_answer"]
    student_answer: Optional[str| bool]
class BulkGradeAssessment(BaseModel):
    session_id: str
    answers: List[GradeAssessment]


# Function to process text with Gemini LLM
async def analyze_content(text, operation_type, num_questions=5, difficulty="medium"):

    

    """Process content using Gemini based on operation type

    Args:
        text: The content to analyze
        operation_type: Type of analysis to perform
        num_questions: Number of questions to generate (default: 5)
    """

    Critical = f"""

        CRITICAL GUIDELINES
            - For any mathematical expression, formula, equation, matrix, or symbol convert them to LaTeX format:
                * Wrap it using display-math delimiters $$ ... $$
                * Escape every backslash (\\) and replace it with double (\\\\)
                * Example:(eg. $$C_6H_{{12}}O_6$$  +  $$6O_2$$  -->  $$6CO_2$$  +  $$6H_2O$$  + ATP), (convert them to LaTeX format,Escape every backslash (\\) and replace it with double (\\\\),Example: Convert $$\\frac{{x}}{{y}}$$ to $$\\\\frac{{x}}{{y}}$$)
                * This should be appilcable to all questions, options, answers, explantions, feedbacks etc...
                * For multiple choice questions dont use options eg A, B, C, D as the  correct answers alone for clarity
                
                """

    prompts = {
        
        "generate_mc": f"""
        Create {num_questions} multiple-choice questions (with 4 options each, one correct answer + explanation)
        from the following content:
        {text}

        Difficulty Level: {difficulty}

       {Critical}
        
        OUTPUT FORMAT:
        Return a JSON with the following structure:
        {{
            "questions": [
                {{
                    "question": "Question text",
                    "options": ["Option A", "Option B", "Option C", "Option D"],
                    "correct_answer": "Correct option",
                    "explanation": "Why this is the correct answer"
                }}
            ]
        }}
        """,
        "generate_tf": f"""
        Create {num_questions} true/false questions (with correct answer + explanation)
        from the following content:
        {text}

        Difficulty Level: {difficulty}

        {Critical}
        
        
        OUTPUT FORMAT:
        Return a JSON with the following structure:
        {{
            "questions": [
                {{
                    "question": "Question text",
                    "correct_answer": true or false,
                    "explanation": "Why this is the correct answer"
                }}
            ]
        }}
        """,
        "generate_sa": f"""
        Generate {num_questions} short-answer (essay) questions only—do NOT provide answers—
        based on the following content:
        {text}

        Difficulty Level: {difficulty}

        {Critical}

        
        OUTPUT FORMAT:
        Return a JSON with the following structure:
        {{
            "questions": [
                {{
                    "question": "Question text"
                }}
            ]
        }}
        """,
        "generate_fc": f"""
        From the content below, generate {num_questions} flash cards, each designed for effective learning and recall.

        {Critical}

        Each flashcard should contain the following key-value pairs:

        * "prompt": This side presents a concise question, term, or cue derived from the content. It should encourage active recall.
        * "correspondingInformation": This side provides the direct answer, definition, or explanation related to the prompt.
        * "hint": Offer a brief clue or partial information that guides the learner towards the 'correspondingInformation' without revealing it entirely. The hint should aid memory retrieval.

        Ensure the 'prompt' and 'correspondingInformation' pairings are clear and directly related to the provided content. The 'hint' should be distinct from both and serve as a helpful intermediary.

        CONTENT:
        {text}

        Difficulty Level: {difficulty}

        OUTPUT FORMAT:
        Return a JSON object with the following structure:
        {{
          "flash_cards": [
            {{
              "prompt": "...",
              "correspondingInformation": "...",
              "hint": "..."
            }},
            ...
          ]
        }}
        """,
    }

    # Check if the operation_type exists in the prompts dictionary
    if operation_type not in prompts:
        raise ValueError(f"Invalid operation type: {operation_type}")

    # Call Gemini with the appropriate prompt
    response = await model.generate_content_async(prompts[operation_type])

    # Parse the JSON response
    try:
        # Extract JSON from response
        response_text = response.text
        json_str = response_text
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            # Handle case where JSON is wrapped in ``` without language specifier
            json_str = response_text.split("```")[1].strip()

        result = json.loads(json_str)

        # Validate essential structure based on operation_type
        if operation_type == "generate_fc" and "flash_cards" not in result:
            raise ValueError("Response missing 'flash_cards' key")
        elif (
            operation_type != "generate_fc"
            and operation_type != "summarize"
            and "questions" not in result
        ):
            raise ValueError("Response missing 'questions' key")

        return result
    except json.JSONDecodeError as e:
        print(f"JSON decode error: {e}")
        print(f"Response was: {response.text}")
        raise Exception(f"Failed to parse JSON from LLM response: {e}")
    except Exception as e:
        print(f"Error processing response: {e}")
        print(f"Response was: {response.text}")
        raise Exception(f"Failed to process LLM response: {e}")


async def answer_question(question, context):
    """Answer a question based on provided context"""
    prompt = f"""
    You are an exceptional AI study tutor with a gift for making complex concepts crystal clear and engaging. Your mission is to help students truly understand and master the material within the given context.

        ## CORE TEACHING PRINCIPLES:
        - **Clarity First**: Break down complex ideas into digestible, logical steps
        - **Engagement**: Use analogies, real-world connections, and intuitive explanations when they relate to the study material
        - **Active Learning**: Encourage deeper thinking with guiding questions and connections between concepts
        - **Visual Learning**: Structure responses with clear formatting, examples, and step-by-step breakdowns

        ## STRICT BOUNDARY RULE:
        **You MUST stay within the scope of the provided study material/topic.** 

        If a question falls outside the given context (e.g., asking about HTML when studying Thermodynamics, or current affairs when studying Mathematics), respond with:

        *"I understand you're curious about that topic, but I'm here to help you master [current subject/topic]. Let's focus on [specific area from the context] - is there anything about [relevant concept] you'd like to explore further?"*

        ## INTERNAL RESPONSE STRATEGY:

        ### When Explaining Concepts (structure your response to include):
        - Brief connection to what they already know from the material
        - Clear, simple definition of the concept
        - Deeper explanation of 'why' and 'how' with examples from study material
        - Links to related concepts within the same topic
        - End with a thought-provoking question or summary

        ### When Solving Problems (organize your response to include):
        - Identification of which method/principle from the material applies
        - Clear, step-by-step solution with reasoning
        - Highlighting of crucial concepts being used
        - Mention of where else in the material this approach applies

        **Note: These are structural guidelines for YOU to follow internally - do NOT use these as literal headers in your responses. Create natural, flowing explanations that incorporate these elements seamlessly.**

        ## FORMATTING REQUIREMENTS:

        ### Mathematical Content:
        - **ALL** mathematical expressions, formulas, equations, matrices, or symbols MUST use LaTeX format
        - Use display-math delimiters: `$$ ... $$`
        - Escape backslashes: Replace every `\\` with `\\\\`
        - Example: Convert `$$\\frac{{x}}{{y}}$$` to `$$\\\\frac{{x}}{{y}}$$`
        - **CRITICAL**: Write complete formulas, not fragments
        - ✅ CORRECT: `$$C_{{6}}H_{{12}}O_{{6}} + 6O_{{2}} \\\\longrightarrow 6CO_{{2}} + 6H_{{2}}O + ATP$$`
        - ❌ WRONG: `$$C_6H_{{12}}O_6$$` + `$$6O_2$$` → `$$6CO_2$$` + `$$6H_2O$$` + ATP

        ### Visual Structure:
        - Use **headers** for main sections
        - Apply *emphasis* for key terms and concepts
        - Create **bullet points** for lists and key features
        - Include **blank lines** between sections for readability
        - Use **examples** and **analogies** when they relate to the study material

        ## YOUR TEACHING VOICE:
        - Be enthusiastic but not overwhelming
        - Use encouraging language that builds confidence
        - Anticipate common misconceptions and address them
        - Make connections that deepen understanding
        - Always relate back to the core concepts in the study material

        Remember: You're not just answering questions - you're helping students build genuine understanding and mastery of the subject matter within the given context.

    
    CONTEXT:
    {context}
    
    QUESTION:
    {question}
    
    Your answer should be educational, accurate, and easy to understand. Include relevant examples 
    if they would help clarify the concept.
    """

    response = await model.generate_content_async(prompt)
    return {"answer": response.text}


# API Endpoints
# —————————————————————————————————————————————————————————————————————————
# MATERIALS


# Upload materials
# Upload materials
@app.post(
    "/api/v1/materials/upload",
    response_model=ResponseModel,
    status_code=status.HTTP_201_CREATED,
)
async def upload_material(
    title: str = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
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
        
        # Optional: Store the task reference somewhere if you need to track it
        # app.state.background_tasks = getattr(app.state, "background_tasks", {})
        # app.state.background_tasks[mat.id] = background_task

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

# Get all study materials
@app.get(
    "/api/v1/materials",
    response_model=ResponseModel,
)
async def list_materials(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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


# Get  study material by id
@app.get(
    "/api/v1/materials/{material_id}",
    response_model=ResponseModel,
)
async def get_material(
    material_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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

# Delete material
@app.delete(
    "/api/v1/materials/{material_id}",
    response_model=ResponseModel,
)
async def delete_material(
    material_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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

# —————————————————————————————————————————————————————————————————————————

# —————————————————————————————————————————————————————————————————————————
# QUESTIONS


class QuestionRequest(BaseModel):
    question: str = Field(
        ...,
        description="The question to be answered",
        example="What is photosynthesis?",
    )
    context_id: Optional[uuid.UUID] = Field(
        None,
        description="UUID of the study material for context",
        example="3fa85f64-5717-4562-b3fc-2c963f66afa6",
    )

    @field_validator("question")
    def question_not_empty(cls, v):
        if v is None or v.strip() == "":
            raise ValueError("Question cannot be empty")
        return v.strip()


# Ask questions
@app.post(
    "/api/v1/questions/ask",
    response_model=ResponseModel,
)
async def ask_question(
    request: QuestionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Ensure subscription is current
    sub = await renew_subscription_for_user(current_user, db)
    if sub.status != SubscriptionStatus.active:
        return error_response("Your subscription is not active", 403)

    # 2. Load plan & usage
    plan = await db.get(PlanModel, current_user.plan_id)
    usage = await get_or_create_usage(current_user, db)
    if usage.asked_questions_count >= plan.monthly_ask_question_limit:
        return error_response(
            msg="You've reached your monthly question‑asking limit. Upgrade to ask more.",
            data={"error_type":"MONTHLY_QUESTION_LIMIT_EXCEEDED","current_plan":plan.name},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 3. Build context if provided
    ctx = ""
    if request.context_id:
        material = await db.get(StudyMaterialModel, request.context_id)
        if not material or material.user_id != current_user.id:
            return error_response(
                msg="Study material not found or access denied", status_code=404
            )
        ctx = material.content

    # 4. Generate the answer
    answer = await answer_question(request.question, ctx)

    # 5. Increment the asked_questions_count and save
    usage.asked_questions_count += 1
    db.add(usage)
    await db.commit()

    # 6. Return the response
    return success_response(msg="Answer generated", data=answer)

# —————————————————————————————————————————————————————————————————————————

# —————————————————————————————————————————————————————————————————————————
# ASSESSMENTS


# Generate assessments
@app.post("/api/v1/assessment/generate", response_model=ResponseModel)
async def generate_assessment(
    assessment_data: Assessment,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Ensure subscription is current
    sub = await renew_subscription_for_user(current_user, db)
    if sub.status != SubscriptionStatus.active:
        return error_response("Your subscription is not active", 403)

    # 2. Load plan & usage
    plan = await db.get(PlanModel, current_user.plan_id)
    usage = await get_or_create_usage(current_user, db)
    if usage.assessments_count >= plan.monthly_assessment_limit:
        return error_response(
            msg="You've reached your monthly assessment‑generation limit. Upgrade to continue.",
            data={"error_type":"MONTHLY_ASSESSMENT_LIMIT_EXCEEDED","current_plan":plan.name},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 3. Check if flash_cards or short_answer is mixed with other question types
    has_flash_cards = "flash_cards" in assessment_data.question_types
    has_short_answer = "short_answer" in assessment_data.question_types
    has_other_types = any(qt not in ["flash_cards", "short_answer"] for qt in assessment_data.question_types)

    if has_flash_cards and (has_short_answer or has_other_types):
        return error_response(
            msg="Flash cards cannot be combined with other question types. Please select either flash cards or other question types.",
            data={"error_type": "INCOMPATIBLE_QUESTION_TYPES"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    
    if has_short_answer and (has_flash_cards or has_other_types):
        return error_response(
            msg="Short answer questions cannot be combined with other question types. Please select either short answer or other question types.",
            data={"error_type": "INCOMPATIBLE_QUESTION_TYPES"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 4. Authorize & load material
    mat = await db.get(StudyMaterialModel, assessment_data.material_id)
    if not mat or mat.user_id != current_user.id:
        raise HTTPException(404, "Study material not found")

    # 5. Enforce per‑assessment question limit
    num = assessment_data.num_questions or DEFAULT_MAX_QUESTIONS
    if num > plan.questions_per_assessment:
        return error_response(
            msg=f"You can request at most {plan.questions_per_assessment} questions per assessment on your current plan. Please upgrade to create larger assessments.",
            data={
                "error_type": "QUESTIONS_PER_ASSESSMENT_LIMIT_EXCEEDED",
                "current_plan": plan.name,
                "limit": plan.questions_per_assessment,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 7. Build content (optional topic filter)
    content = mat.content
    if assessment_data.topic and mat.processed_content:
        for t in mat.processed_content.get("topics", []):
            if assessment_data.topic.lower() in t["name"].lower():
                content = json.dumps(t)
                break

    # 8. LLM generate each type
    payload = {}

    # Strategy for distributing questions among types
    selected_types = [qt for qt in assessment_data.question_types]

    if "flash_cards" in selected_types:
        # Flash cards don't mix, so use all questions for them
        r = await analyze_content(content, "generate_fc", num_questions=num, difficulty=assessment_data.difficulty )
        payload["flash_cards"] = r["flash_cards"]
    elif "short_answer" in selected_types:
        # Short answer questions don't mix either, use all questions for them
        r = await analyze_content(content, "generate_sa", num_questions=num, difficulty=assessment_data.difficulty)
        payload["short_answer"] = r["questions"]
    else:
        # For multiple question types, distribute questions
        num_types = len(selected_types)
        base_questions_per_type = num // num_types  # Integer division
        extra_questions = num % num_types  # Remainder

        question_distribution = {}

        # Distribute base questions to all types
        for qt in selected_types:
            question_distribution[qt] = base_questions_per_type

        # Distribute extra questions one by one to types until used up
        for i in range(extra_questions):
            question_distribution[selected_types[i % len(selected_types)]] += 1

        # Generate questions according to the distribution
        if "multiple_choice" in selected_types:
            mc_count = question_distribution["multiple_choice"]
            if mc_count > 0:
                r = await analyze_content(
                    content, "generate_mc", num_questions=mc_count, difficulty=assessment_data.difficulty
                )
                payload["multiple_choice"] = r["questions"]

        if "true_false" in selected_types:
            tf_count = question_distribution["true_false"]
            if tf_count > 0:
                r = await analyze_content(
                    content, "generate_tf", num_questions=tf_count, difficulty=assessment_data.difficulty
                )
                payload["true_false"] = r["questions"]

    # 9. Persist the session
    sess = SessionModel(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        material_id=mat.id,
        topic=assessment_data.topic,
        difficulty=assessment_data.difficulty,
        question_types=assessment_data.question_types,
        questions_payload=payload,
        current_index=0,
        status="in_progress",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)

    # 6. Increment usage
    usage.assessments_count += 1
    db.add(usage)
    await db.commit()

    return success_response(
        msg="Assessment generated successfully",
        data={"session_id": str(sess.id), "questions": payload},
    )


# Get all assessments
@app.get(
    "/api/v1/assessments",
    response_model=ResponseModel,
)
async def list_assessments(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all assessment sessions with question counts and material titles."""
    # 1. Load all sessions for this user
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.user_id == current_user.id)
        .order_by(SessionModel.created_at.desc())
    )
    sessions = result.scalars().all()

    data = []
    for sess in sessions:
        # 2. Compute total questions
        q_payload = sess.questions_payload or {}
        total_questions = sum(len(v) for v in q_payload.values())

        # 3. Fetch material title
        mat = await db.get(StudyMaterialModel, sess.material_id)
        title = mat.title if mat else None

        data.append(
            {
                "session_id": str(sess.id),
                "material_id": str(sess.material_id),
                "material_title": title,
                "topic": sess.topic,
                "difficulty": sess.difficulty,
                "question_types": sess.question_types,
                "number_of_questions": total_questions,
                "current_index": sess.current_index,
                "status": sess.status,
                "created_at": sess.created_at.isoformat(),
                "updated_at": sess.updated_at.isoformat(),
            }
        )

    return success_response(msg="Assessments fetched", data=data)


# Get one assessment
@app.get(
    "/api/v1/assessments/{session_id}",
    response_model=ResponseModel,
)
async def get_assessment(
    session_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        # 1) Load session and authorize
        sess = await db.get(SessionModel, session_id)
        if not sess or sess.user_id != current_user.id:
            return error_response("Assessment session not found", status_code=status.HTTP_404_NOT_FOUND)

        # 2) Fetch submissions
        sub_q = await db.execute(
            select(SubmissionModel)
            .where(SubmissionModel.session_id == sess.id)
            .order_by(SubmissionModel.question_index)
        )
        subs = sub_q.scalars().all()

        # 3) Build submissions list and compute final_score
        submissions = []
        correct = total = 0

        for s in subs:
            submissions.append({
                "question_index": s.question_index,
                "question_type": s.question_type,
                "student_answer": s.student_answer,
                "correct_answer": s.correct_answer,
                "feedback": s.feedback,
                "score": s.score,
                "created_at": s.created_at.isoformat(),
            })
            if s.question_type in ["multiple_choice", "true_false"]:
                total += 1
                if s.student_answer == s.correct_answer:
                    correct += 1

        final_score = f"{int((correct / total * 100) if total else 0)}%"

        # 4) Assemble payload
        data = {
            "session_id": str(sess.id),
            "material_id": str(sess.material_id),
            "topic": sess.topic,
            "difficulty": sess.difficulty,
            "question_types": sess.question_types,
            "questions_payload": sess.questions_payload,
            "current_index": sess.current_index,
            "status": sess.status,
            "created_at": sess.created_at.isoformat(),
            "updated_at": sess.updated_at.isoformat(),
            "submissions": submissions,
            "final_score": final_score,
        }

        return success_response(msg="Assessment retrieved", data=data)

    except HTTPException:
        # Re-raise our controlled 404
        raise
    except Exception:
        # Any other error becomes a 404
        return error_response("Assessment session not found", status_code=status.HTTP_404_NOT_FOUND)


# Submit assessment
@app.post(
    "/api/v1/assessment/submit",
    response_model=ResponseModel,
)
async def submit_assessment_bulk(
    bulk: BulkGradeAssessment,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Load & authorize session
    sess = await db.get(SessionModel, bulk.session_id)
    if not sess or sess.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    # 2. Validate count
    total_expected = sum(len(v) for v in sess.questions_payload.values())
    if len(bulk.answers) != total_expected:
        raise HTTPException(
            status_code=400,
            detail=f"Expected {total_expected} answers, got {len(bulk.answers)}",
        )

    results = []

    # 3. Grade each answer
    for ans in bulk.answers:
        q_list = sess.questions_payload.get(ans.question_type)
        if not q_list or not (0 <= ans.question_index < len(q_list)):
            raise HTTPException(status_code=400, detail="Invalid question index/type")

        meta = q_list[ans.question_index]
        question_text  = meta["question"]
        correct_answer = meta.get("correct_answer")
        explanation    = meta.get("explanation", "")

        feedback = None
        score    = None
        is_correct = None

        # --- OBJECTIVE (MC/TF) ---
        if ans.question_type in ("multiple_choice", "true_false"):
            if correct_answer is None:
                raise HTTPException(status_code=500, detail="Stored question missing correct_answer")

            # Handle different student answer types
            if isinstance(ans.student_answer, bool):
                # If student_answer is already boolean
                student_bool = ans.student_answer
            elif ans.student_answer is None:
                # Handle None case
                student_bool = False
            else:
                # If student_answer is string, normalize it
                student_raw = str(ans.student_answer).strip().lower()
                student_bool = student_raw in ("true", "1", "t", "yes")

            if isinstance(correct_answer, bool):
                # compare booleans
                is_correct = (student_bool == correct_answer)
            else:
                # compare strings (when correct_answer is not bool)
                if isinstance(ans.student_answer, bool):
                    # Can't directly compare bool with non-bool answer
                    is_correct = False
                else:
                    # Only compare strings if student answer is also string-like
                    correct_raw = str(correct_answer).strip().lower()
                    student_raw = str(ans.student_answer).strip().lower() if ans.student_answer is not None else ""
                    is_correct = (student_raw == correct_raw)

            score = 100 if is_correct else 0
            feedback = (
                f"Correct! {explanation}"
                if is_correct
                else f"Incorrect. The correct answer is {correct_answer}. {explanation}"
            )

        # --- SHORT ANSWER via LLM ---
        elif ans.question_type == "short_answer":
            # Make sure we have a string to work with
            student_answer = str(ans.student_answer) if ans.student_answer is not None else ""
            
            mat = await db.get(StudyMaterialModel, sess.material_id)
            context = mat.content if mat else ""

            prompt = f"""
                You are an AI tutor. The student answered:

                QUESTION:
                {question_text}

                STUDENT ANSWER:
                {student_answer}

                REFERENCE CONTEXT:
                {context}

                Evaluate their answer: 
                - Acknowledge what they got right,
                - Point out missing depth or points,
                - Cite the importance of those points from the context,
                - Suggest how to improve.
                - Rate their answer out of 10 (e.g., "2/10", "7/10").

                OUTPUT FORMAT:
                Return JSON with keys:
                "feedback", "score", "key_points_addressed", "missing_points", "improvement_suggestions"
            """
            resp = await model.generate_content_async(prompt)
            text = resp.text
            try:
                payload = text
                if "```json" in text:
                    payload = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    payload = text.split("```")[1].strip()
                grading = json.loads(payload)
            except Exception:
                grading = {"feedback": text, "score": None}

            feedback = grading.get("feedback")
            # We're getting the score but not using it for the database
            # Instead, we'll keep it in the grading dictionary

        else:
            raise HTTPException(status_code=400, detail="Unsupported question type")

        # 4. Persist
        sub = SubmissionModel(
            id=str(uuid.uuid4()),
            session_id=sess.id,
            question_index=ans.question_index,
            question_type=ans.question_type,
            student_answer=str(ans.student_answer) if ans.student_answer is not None else "",
            correct_answer=str(correct_answer) if correct_answer is not None else "",
            feedback=grading if ans.question_type == "short_answer" else feedback,
            score=score if ans.question_type != "short_answer" else None,  # Only store score for non-short-answer questions
            created_at=datetime.now(timezone.utc),
        )
        db.add(sub)

        results.append({
            "question_index": ans.question_index,
            "question_type": ans.question_type,
            "is_correct": is_correct,
            "score": score,
            "feedback": feedback,
        })

    # 5. Complete session
    sess.current_index = total_expected
    sess.status = "completed"
    sess.updated_at = datetime.now(timezone.utc)

    await db.commit()

    return success_response(
        msg="All answers submitted and graded",
        data={"session_id": str(sess.id), "results": results, "session_status": sess.status},
    )

# Retake assessment
@app.post(
    "/api/v1/assessments/{session_id}/restart",
    response_model=ResponseModel,
)
async def restart_assessment(
    session_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1) Load and authorize the session
    sess = await db.get(SessionModel, session_id)
    if not sess or sess.user_id != current_user.id:
        return error_response("Assessment session not found", status_code=status.HTTP_404_NOT_FOUND)

    # 2) Delete all prior submissions for this session
    await db.execute(
        delete(SubmissionModel)
        .where(SubmissionModel.session_id == session_id)
    )

    # 3) Reset the session pointer and status
    sess.current_index = 0
    sess.status = "in_progress"
    sess.updated_at = datetime.now(timezone.utc)

    # 4) Persist changes
    db.add(sess)
    await db.commit()

    return success_response(
        msg="Assessment restarted. All previous answers cleared.",
        data={
            "session_id": session_id,
            "current_index": sess.current_index,
            "status": sess.status,
        }
    )


# Delete assessment
@app.delete(
    "/api/v1/assessments/{session_id}",
    response_model=ResponseModel,
)
async def delete_assessment(
    session_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Load and authorize
    sess = await db.get(SessionModel, session_id)
    if not sess or sess.user_id != current_user.id:
        return error_response("Assessment session not found", status_code=status.HTTP_404_NOT_FOUND)

    # 2. Delete submissions first (if not cascade)
    await db.execute(
        delete(SubmissionModel).where(SubmissionModel.session_id == session_id)
    )

    # 3. Delete the session
    await db.execute(
        delete(SessionModel).where(SessionModel.id == session_id)
    )
    await db.commit()

    return success_response(msg="Assessment session deleted")

# Run the app
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8100)
