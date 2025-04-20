from contextlib import asynccontextmanager
import logging
import os
from fastapi.exceptions import (
    HTTPException as StarletteHTTPException,
    RequestValidationError,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
import google.generativeai as genai
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import io
import tempfile
import uuid
import json
from datetime import datetime
from app.api.v1.routes.router import router as api_v1_router
from app.db.seed.plans import seed_all
from app.models.plan import Plan as PlanModel
from app.core.config import settings

#

from contextlib import asynccontextmanager
import logging, os, io, tempfile, uuid, json
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
from sqlalchemy import select
from app.db.deps import get_db
from app.core.response import success_response, error_response, ResponseModel
from app.api.v1.routes.auth.auth import get_current_user
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.models.assessment_session import AssessmentSession as SessionModel
from app.models.submission import Submission as SubmissionModel
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage


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


# Setup for Gemini
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=GOOGLE_API_KEY)

# Initialize Gemini model
model = genai.GenerativeModel("gemini-2.0-flash")

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
    difficulty: str = "medium"
    question_types: List[str] = [
        "multiple_choice",
        "true_false",
        "short_answer",
    ]
    num_questions: Optional[int] = 5  # Default to 5 questions per type

    @field_validator("topic")
    def topic_not_empty(cls, v):
        if v is None or v.strip() == "":
            raise ValueError("Topic cannot be empty")
        return v.strip()


class GradeAssessment(BaseModel):
    question_index: int
    student_answer: str
    question_type: str
    correct_answer: str = None
    explanation: Optional[str] = None


class BulkGradeAssessment(BaseModel):
    session_id: str
    answers: List[GradeAssessment]


# Helper functions
def extract_text_from_image(image):
    """Extract text from an image using OCR"""
    return pytesseract.image_to_string(image)


def process_pdf(pdf_path):
    """Convert PDF to images and extract text"""
    images = convert_from_path(pdf_path)
    text = ""
    for image in images:
        text += extract_text_from_image(image) + "\n\n"
    page_count = len(images)  # Get the page count from the number of images
    return text, page_count


# Function to process text with Gemini LLM
async def analyze_content(text, operation_type, num_questions=5):
    """Process content using Gemini based on operation type

    Args:
        text: The content to analyze
        operation_type: Type of analysis to perform
        num_questions: Number of questions to generate (default: 5)
    """
    prompts = {
        "summarize": f"""
            You are an expert educational content analyst. Your task is to thoroughly analyze the provided
            educational material and transform it into a structured, intuitive knowledge framework that enhances
            comprehension and retention.
            
            CONTENT TO ANALYZE:
            {text}
            
            ANALYSIS PROCESS:
            1. First, identify the core subject area and learning objectives
            2. Extract all key concepts with precise definitions and contextual relationships
            3. Isolate and explain technical terminology, formulas, theorems, and principles
            4. Recognize hierarchical relationships between topics and subtopics
            5. Identify knowledge dependencies and progression pathways
            6. Highlight real-world applications and practical significance
            7. Note areas of potential confusion or conceptual difficulty
            
            OUTPUT FORMAT:
            Return a JSON with the following structure:
            {{
                "title": "Suggested title based on content",
                "summary": "Comprehensive overview of the material including its significance and core learning objectives",
                "topics": [
                    {{
                        "name": "Topic name",
                        "description": "Brief explanation of this topic's importance",
                        "key_points": ["Key point 1", "Key point 2"],
                        "definitions": [
                            {{
                                "term": "Term",
                                "definition": "Clear definition with context"
                            }}
                        ],
                        "formulas": [
                            {{
                                "name": "Formula name",
                                "expression": "Formula expression",
                                "variables": {{"variable": "what it represents"}},
                                "explanation": "What the formula represents and how to apply it"
                            }}
                        ],
                        "common_misconceptions": [
                            {{
                                "misconception": "Common misunderstanding",
                                "correction": "Proper understanding"
                            }}
                        ],
                        "subtopics": [
                            {{
                                "name": "Subtopic name", 
                                "key_points": ["Point 1", "Point 2"]
                            }}
                        ]
                    }}
                ],
                "practical_applications": [
                    {{
                        "context": "Application area",
                        "example": "Specific example of how this knowledge applies"
                    }}
                ],
                "study_suggestions": [
                    {{
                        "technique": "Study method",
                        "implementation": "How to apply this technique to this material"
                    }}
                ],
                "assessment_questions": [
                    {{
                        "question": "Conceptual question to test understanding",
                        "topic": "Related topic from the content"
                    }}
                ]
            }}
            
            QUALITY GUIDELINES:
            - Ensure all definitions are clear, precise, and accessible
            - Focus on building conceptual understanding, not just memorization
            - Highlight relationships between concepts to create an integrated knowledge network
            - Include sufficient detail to make complex ideas understandable
            - Provide actionable study guidance that addresses different learning styles
        """,
        "generate_questions": f"""
        Based on the following educational content, create {num_questions} assessment questions to test 
        understanding of the key concepts. Include a mix of question types.
        
        CONTENT:
        {text}
        
        OUTPUT FORMAT:
        Return a JSON with the following structure:
        {{
            "questions": [
                {{
                    "question": "Question text",
                    "type": "multiple_choice|short_answer|true_false",
                    "options": ["Option A", "Option B", "Option C", "Option D"] (for multiple choice),
                    "correct_answer": "Correct answer",
                    "explanation": "Why this is the correct answer"
                }}
            ],
            "difficulty_level": "easy|medium|hard",
            "topics_covered": ["Topic 1", "Topic 2"]
        }}
        """,
        "generate_mc": f"""
        Create {num_questions} multiple-choice questions (with 4 options each, one correct answer + explanation)
        from the following content:
        {text}
        
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
        From the content below, generate {num_questions} flash cards—each with a 'term', a 'hint', and a 'definition'.
        The hint should provide a clue that helps the student remember the answer without giving it away completely.
        
        CONTENT:
        {text}
        
        OUTPUT FORMAT:
        Return a JSON with the following structure:
        {{
          "flash_cards": [
            {{"term":"…", "hint":"…", "definition":"…"}}, …
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
    You are a helpful AI study assistant. Answer the following question based ONLY on the given context.

    If the question is not directly related to the provided study material or topic, respond with:
    "I'm sorry, but that question is outside the scope of the current study material/topic."

    Do not provide any general information beyond the provided context. 

    Provide a clear, concise explanation when applicable, and use appropriate headers, paragraphs, bullet points, and emphasis for visual attractiveness. Ensure there are blank lines between sections for good readability.

    
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
@app.post(
    "/materials/upload",
    response_model=ResponseModel,
    status_code=status.HTTP_201_CREATED,
)
async def upload_material(
    title: str = Form(...),
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 0. Load plan & usage
    plan = await db.get(PlanModel, current_user.plan_id)
    if not plan:
        raise HTTPException(500, detail="Subscription plan not found")

    usage = await get_or_create_usage(current_user.id, db)
    if usage.uploads_count >= plan.monthly_upload_limit:
        return error_response(
            msg="You've reached your monthly upload limit. Please upgrade your plan to continue uploading files.",
            data={"error_type": "MONTHLY_UPLOAD_LIMIT_EXCEEDED", "current_plan": plan.name},
            status_code=status.HTTP_403_FORBIDDEN
    )

    try:
        # 1. Save file to temp and extract text + page_count
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        if file.content_type == "application/pdf":
            text, page_count = process_pdf(tmp_path)
        elif file.content_type.startswith("image/"):
            img = Image.open(io.BytesIO(content))
            text = extract_text_from_image(img)
            page_count = 1
        else:
            os.unlink(tmp_path)
            raise HTTPException(400, "Unsupported file type")
        os.unlink(tmp_path)

        # 1b. Enforce per‑upload page limit
        if page_count > plan.pages_per_upload_limit:
            return error_response(
                msg=f"This file has {page_count} pages, which exceeds your current plan limit of {plan.pages_per_upload_limit} pages per upload. Please upgrade your plan to upload larger files.",
                 data={"error_type": "PAGES_PER_UPLOAD_LIMIT_EXCEEDED", "current_plan": plan.name, "limit": plan.pages_per_upload_limit},
                status_code=status.HTTP_400_BAD_REQUEST
    )

        # 2. Summarize
        processed = await analyze_content(text, "summarize")

        # 3. Persist material
        mat = StudyMaterialModel(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            title=title,
            file_name=file.filename,
            file_path="",
            content=text,
            processed_content=processed,
            page_count=page_count,
            created_at=datetime.now(timezone.utc),
        )
        db.add(mat)
        await db.commit()
        await db.refresh(mat)

        # 4. Increment usage and save
        usage.uploads_count += 1
        db.add(usage)
        await db.commit()

        return success_response(
            msg="Material uploaded and summarized",
            data={"material_id": str(mat.id), "page_count": page_count},
            status_code=status.HTTP_201_CREATED,
        )

    except HTTPException:
        raise
    except Exception:
        logging.exception("Failed to upload material")
        raise HTTPException(status_code=500, detail="Processing failed")

# Get all study materials
@app.get(
    "/materials",
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
                "page_count": m.page_count,
            }
        )

    return success_response(msg="Materials fetched", data=data)


# Get  study material by id
@app.get(
    "/materials/{material_id}",
    response_model=ResponseModel,
)
async def get_material(
    material_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
        "created_at": mat.created_at.isoformat(),
    }
    return success_response(msg="Material retrieved", data=data)


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
    "/questions/ask",
    response_model=ResponseModel,
)
async def ask_question(
    request: QuestionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        # Initialize context
        ctx = ""

        # If context_id is provided, fetch the material
        if request.context_id:
            material_query = select(StudyMaterialModel).where(
                StudyMaterialModel.id == request.context_id,
                StudyMaterialModel.user_id == current_user.id,
            )
            result = await db.execute(material_query)
            material = result.scalars().first()

            if not material:
                return error_response(
                    msg="Study material not found or you don't have access to it",
                    data=None,
                    status_code=404,
                )

            ctx = material.content

        # Proceed with answering the question
        answer = await answer_question(request.question, ctx)
        return success_response(msg="Answer generated", data=answer)

    except Exception as e:
        logging.error(f"Error processing question: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process question")


# —————————————————————————————————————————————————————————————————————————

# —————————————————————————————————————————————————————————————————————————
# ASSESSMENTS


# Generate assessments
@app.post("/assessment/generate", response_model=ResponseModel)
async def generate_assessment(
    assessment_data: Assessment,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 0. Load plan & usage
    plan = await db.get(PlanModel, current_user.plan_id)
    if not plan:
        raise HTTPException(500, detail="Subscription plan not found")

    usage = await get_or_create_usage(current_user.id, db)
    if usage.assessments_count >= plan.monthly_assessment_limit:
        return error_response(
            msg="You've reached your monthly assessment generation limit. Please upgrade your plan to create more assessments.",
            data={"error_type": "MONTHLY_ASSESSMENT_LIMIT_EXCEEDED", "current_plan": plan.name},
            status_code=status.HTTP_403_FORBIDDEN
        )

    # 1. Authorize & load material
    mat = await db.get(StudyMaterialModel, assessment_data.material_id)
    if not mat or mat.user_id != current_user.id:
        raise HTTPException(404, "Study material not found")

    # 2. Enforce per‑assessment question limit
    num = assessment_data.num_questions or DEFAULT_MAX_QUESTIONS
    if num > plan.questions_per_assessment:
        return error_response(
            msg=f"You can request at most {plan.questions_per_assessment} questions per assessment on your current plan. Please upgrade to create larger assessments.",
            data={"error_type": "QUESTIONS_PER_ASSESSMENT_LIMIT_EXCEEDED", "current_plan": plan.name, "limit": plan.questions_per_assessment},
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # 3. Build content (optional topic filter)
    content = mat.content
    if assessment_data.topic and mat.processed_content:
        for t in mat.processed_content.get("topics", []):
            if assessment_data.topic.lower() in t["name"].lower():
                content = json.dumps(t)
                break

    # 4. LLM generate each type
    payload = {}
    if "multiple_choice" in assessment_data.question_types:
        r = await analyze_content(content, "generate_mc", num_questions=num)
        payload["multiple_choice"] = r["questions"]
    if "true_false" in assessment_data.question_types:
        r = await analyze_content(content, "generate_tf", num_questions=num)
        payload["true_false"] = r["questions"]
    if "short_answer" in assessment_data.question_types:
        r = await analyze_content(content, "generate_sa", num_questions=num)
        payload["short_answer"] = r["questions"]
    if "flash_cards" in assessment_data.question_types:
        r = await analyze_content(content, "generate_fc", num_questions=num)
        payload["flash_cards"] = r["flash_cards"]

    # 5. Persist the session
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
    "/assessments",
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
    "/assessments/{session_id}",
    response_model=ResponseModel,
)
async def get_assessment(
    session_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get one assessment session, its questions, and any submitted answers"""
    # 1. Load session and authorize
    sess = await db.get(SessionModel, session_id)
    if not sess or sess.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    # 2. Fetch any submissions for this session
    sub_q = await db.execute(
        select(SubmissionModel)
        .where(SubmissionModel.session_id == sess.id)
        .order_by(SubmissionModel.question_index)
    )
    subs = sub_q.scalars().all()

    submissions = [
        {
            "question_index": sub.question_index,
            "question_type": sub.question_type,
            "student_answer": sub.student_answer,
            "correct_answer": sub.correct_answer,
            "feedback": sub.feedback,
            "score": sub.score,
            "created_at": sub.created_at.isoformat(),
        }
        for sub in subs
    ]

    # 3. Assemble full payload
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
    }

    return success_response(msg="Assessment retrieved", data=data)


# Submit assessment
@app.post(
    "/assessment/submit",
    response_model=ResponseModel,
)
async def submit_assessment_bulk(
    bulk: BulkGradeAssessment,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Load and authorize session
    sess = await db.get(SessionModel, bulk.session_id)
    if not sess or sess.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    # 2. Prepare for grading
    total_expected = sum(len(v) for v in sess.questions_payload.values())
    if len(bulk.answers) != total_expected:
        raise HTTPException(
            status_code=400,
            detail=f"Expected {total_expected} answers, got {len(bulk.answers)}",
        )

    results = []

    # 3. Loop through each answer
    for ans in bulk.answers:
        # a) Validate index & type
        questions = sess.questions_payload.get(ans.question_type)
        if (
            not questions
            or ans.question_index < 0
            or ans.question_index >= len(questions)
        ):
            raise HTTPException(status_code=400, detail="Invalid question index/type")

        feedback = None
        score = None
        is_correct = None

        # b) Objective grading
        if ans.question_type in ["multiple_choice", "true_false"]:
            if ans.correct_answer is None:
                raise HTTPException(status_code=400, detail="Missing correct_answer")
            is_correct = (
                ans.student_answer.strip().lower() == ans.correct_answer.strip().lower()
            )
            score = 100 if is_correct else 0
            feedback = (
                f"Correct! {ans.explanation or ''}"
                if is_correct
                else f"Incorrect. The correct answer is {ans.correct_answer}. {ans.explanation or ''}"
            )

        # c) Short‐answer via LLM
        elif ans.question_type == "short_answer":
            # fetch context
            mat = await db.get(StudyMaterialModel, sess.material_id)
            context = mat.content if mat else ""

            prompt = f"""
                You are an AI tutor. The student answered:

                QUESTION:
                {ans.question}

                STUDENT ANSWER:
                {ans.student_answer}

                REFERENCE CONTEXT:
                {context}

                Evaluate their answer: 
                - Acknowledge what they got right,
                - Point out missing depth or points,
                - Cite the importance of those points from the context,
                - Suggest how to improve.

                OUTPUT FORMAT:
                Return a JSON with the following structure:
                {{
                    "feedback": "Detailed constructive feedback for the student",
                    "score": A number between 0 and 100 representing your assessment of their answer,
                    "key_points_addressed": ["Point 1", "Point 2"], 
                    "missing_points": ["Missing point 1", "Missing point 2"],
                    "improvement_suggestions": ["Suggestion 1", "Suggestion 2"]
                }}

                Respond in a friendly, constructive tone.
                """
            resp = await model.generate_content_async(prompt)
            text = resp.text

            # extract JSON
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
            score = grading.get("score")

        else:
            raise HTTPException(status_code=400, detail="Unsupported question type")

        # d) Persist submission
        sub = SubmissionModel(
            id=str(uuid.uuid4()),
            session_id=sess.id,
            question_index=ans.question_index,
            question_type=ans.question_type,
            student_answer=ans.student_answer,
            correct_answer=ans.correct_answer,
            feedback=grading if ans.question_type == "short_answer" else feedback,
            score=score,
            created_at=datetime.now(timezone.utc),
        )
        db.add(sub)

        # e) Collect result
        results.append(
            {
                "question_index": ans.question_index,
                "question_type": ans.question_type,
                "is_correct": is_correct,
                "score": score,
                "feedback": feedback,
            }
        )

    # 4. Advance session to completed
    sess.current_index = len(bulk.answers)
    sess.status = "completed"
    sess.updated_at = datetime.now(timezone.utc)

    await db.commit()

    # 5. Return aggregated results
    return success_response(
        msg="All answers submitted and graded",
        data={
            "session_id": sess.id,
            "results": results,
            "session_status": sess.status,
        },
    )


# Run the app
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8100)
