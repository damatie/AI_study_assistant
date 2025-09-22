# Standard library imports
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Literal

# Third-party imports
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

# Local imports
from app.core.genai_client import get_gemini_model
from app.core.response import success_response, error_response, ResponseModel
from app.db.deps import get_db
from app.models.plan import Plan as PlanModel
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.models.assessment_session import AssessmentSession as SessionModel
from app.models.submission import Submission as SubmissionModel
from app.api.v1.routes.auth.auth import get_current_user
from app.services.track_subscription_service.handle_track_subscription import (
    renew_subscription_for_user,
)
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.services.ai_service.assessment_service import generate_assessment_questions
from app.utils.enums import SubscriptionStatus
from app.core.config import settings

# Initialize logger and model
logger = logging.getLogger(__name__)
model = get_gemini_model()

# Defaults and router
DEFAULT_MAX_QUESTIONS = settings.DEFAULT_MAX_QUESTIONS
router = APIRouter(prefix="/assessments", tags=["assessments"])


# Data models
class Assessment(BaseModel):
    material_id: uuid.UUID
    topic: Optional[str] = None
    difficulty: Optional[str] = None
    question_types: List[str] = Field(default_factory=list)
    num_questions: Optional[int] = None

    @field_validator("difficulty", mode="before")
    @classmethod
    def _norm_diff(cls, v):
        if v is None:
            return v
        s = str(v).lower()
        return s if s in {"easy", "medium", "hard"} else "medium"

    @field_validator("num_questions")
    @classmethod
    def _norm_num(cls, v):
        return int(v) if v is not None else v


class GradeAssessment(BaseModel):
    question_index: int
    question_type: Literal["multiple_choice", "true_false", "short_answer"]
    student_answer: Optional[str | bool] = None


class BulkGradeAssessment(BaseModel):
    session_id: uuid.UUID
    answers: List[GradeAssessment]


# Generate assessments
@router.post("/generate", response_model=ResponseModel)
async def generate_assessment(
    assessment_data: Assessment,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate assessment questions based on study material"""
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

    # 7. Build content (content-only; no topic slicing)
    # Prefer processed markdown envelope (detailed → overview), falling back to raw content
    from app.utils.processed_payload import get_detailed, get_overview
    detailed = get_detailed(mat.processed_content)
    overview = None if detailed else get_overview(mat.processed_content)
    base_md = detailed or overview or (mat.content or "")
    source_label = "processed.detailed" if detailed else ("processed.overview" if overview else "raw")

    # Clean and truncate for AI consumption to keep latency and cost predictable
    from app.services.material_processing_service.markdown_parser import (
        clean_markdown_for_context,
        smart_truncate_markdown,
    )
    pre_len = len(base_md or "")
    content = clean_markdown_for_context(base_md)
    cleaned_len = len(content)
    # Scale budget with requested number of questions (approx 4k chars per Q), capped for safety
    budget = min(4000 * num, 60000)
    content = smart_truncate_markdown(content, budget_chars=budget)
    final_len = len(content)

    # Observability: log selection and sizes (debug level)
    logger.debug(
        "assessment_content_source=%s pre_len=%d cleaned_len=%d final_len=%d budget=%d user_id=%s material_id=%s",
        source_label,
        pre_len,
        cleaned_len,
        final_len,
        budget,
        str(current_user.id),
        str(mat.id),
    )

    # 8. LLM generate each type
    payload = {}

    # Strategy for distributing questions among types
    selected_types = [qt for qt in assessment_data.question_types]

    if "flash_cards" in selected_types:
        # Flash cards don't mix, so use all questions for them
        r = await generate_assessment_questions(content, "generate_fc", num_questions=num, difficulty=assessment_data.difficulty )
        payload["flash_cards"] = r["flash_cards"]
    elif "short_answer" in selected_types:
        # Short answer questions don't mix either, use all questions for them
        r = await generate_assessment_questions(content, "generate_sa", num_questions=num, difficulty=assessment_data.difficulty)
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
                r = await generate_assessment_questions(
                    content, "generate_mc", num_questions=mc_count, difficulty=assessment_data.difficulty
                )
                payload["multiple_choice"] = r["questions"]

        if "true_false" in selected_types:
            tf_count = question_distribution["true_false"]
            if tf_count > 0:
                r = await generate_assessment_questions(
                    content, "generate_tf", num_questions=tf_count, difficulty=assessment_data.difficulty
                )
                payload["true_false"] = r["questions"]

    # 9. Persist the session (let DB generate UUID id)
    sess = SessionModel(
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
@router.get(
    "",
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
@router.get(
    "/{session_id}",
    response_model=ResponseModel,
)
async def get_assessment(
    session_id: str,  # Accept as string first, then convert
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific assessment session by ID"""
    try:
        # 1) Convert string to UUID and load session
        try:
            session_uuid = uuid.UUID(session_id)
        except ValueError as e:
            logger.warning(f"Invalid UUID format for session_id={session_id}: {e}")
            return error_response("Invalid session ID format", status_code=status.HTTP_400_BAD_REQUEST)
            
        sess = await db.get(SessionModel, session_uuid)
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
            qtype = s.question_type.value if hasattr(s.question_type, "value") else str(s.question_type)
            item = {
                "question_index": s.question_index,
                "question_type": qtype,
                "student_answer": s.student_answer,
                "correct_answer": s.correct_answer,
                "created_at": s.created_at.isoformat() if getattr(s, "created_at", None) else None,
            }

            # objective correctness & scoring
            if s.question_type in ["multiple_choice", "true_false"]:
                total += 1
                is_correct = s.student_answer == s.correct_answer
                if is_correct:
                    correct += 1
                if s.score is not None:
                    item["score"] = s.score
                # Add calculated is_correct field
                item["is_correct"] = is_correct

            # feedback: pass-through; DB is already normalized to {score, details} for short_answer
            fb = s.feedback
            if fb is not None:
                # Optional guard: warn if short_answer feedback isn't the expected v2 dict shape
                if s.question_type == "short_answer":
                    if not (isinstance(fb, dict) and {"score", "details"}.issubset(set(fb.keys()))):
                        logger.warning(
                            "short_answer_feedback_unexpected_shape session_id=%s qidx=%s type=%s shape=%s",
                            str(sess.id), s.question_index, s.question_type, type(fb).__name__,
                        )
                item["feedback"] = fb

            submissions.append(item)

        final_score = f"{int((correct / total * 100) if total else 0)}%"

        # 4) Assemble payload
        # Fetch material title for a better UX on detail page
        mat = await db.get(StudyMaterialModel, sess.material_id)
        material_title = mat.title if mat else None
        # Normalize enums to plain strings for JSON safety
        diff = sess.difficulty.value if hasattr(sess.difficulty, "value") else str(sess.difficulty)
        stat = sess.status.value if hasattr(sess.status, "value") else str(sess.status)

        data = {
            "session_id": str(sess.id),
            "material_id": str(sess.material_id),
            "topic": sess.topic,
            "material_title": material_title,
            "difficulty": diff,
            "question_types": sess.question_types,
            "questions_payload": sess.questions_payload,
            "current_index": sess.current_index,
            "status": stat,
            "created_at": sess.created_at.isoformat() if getattr(sess, "created_at", None) else None,
            "updated_at": sess.updated_at.isoformat() if getattr(sess, "updated_at", None) else None,
            "submissions": submissions,
            "final_score": final_score,
        }

        return success_response(msg="Assessment retrieved", data=data)

    except HTTPException:
        # Re-raise our controlled 404
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_assessment for session {session_id}: {e}", exc_info=True)
        return error_response("Assessment session not found", status_code=status.HTTP_404_NOT_FOUND)


# Submit assessment
@router.post(
    "/submit",
    response_model=ResponseModel,
)
async def submit_assessment_bulk(
    bulk: BulkGradeAssessment,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit and grade assessment answers"""
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
            # Simple context, ensure it's a string before slicing (processed_content may be a dict)
            raw_ctx = ""
            if mat:
                # Prefer raw content if present; otherwise processed_content
                raw_ctx = mat.content or mat.processed_content or ""
            if not isinstance(raw_ctx, str):
                try:
                    raw_ctx = json.dumps(raw_ctx, ensure_ascii=False)
                except Exception:
                    raw_ctx = str(raw_ctx)
            # Keep a generous cap to avoid huge prompts while minimizing information loss
            context_text = raw_ctx[:12000]

            # Prompt: strict JSON only with markdown details (bold, hyphen bullets, blockquotes), max 200 words
            prompt = f"""
You are an expert educator grading strictly against the provided course material. Speak directly to the student using "you" language.

QUESTION: {question_text}
STUDENT ANSWER: {student_answer}
COURSE MATERIAL: {context_text}

Instructions:
- Evaluate only against the course material. Do not invent facts.
- Adapt tone based on the student's answer (encouraging, guiding, corrective as needed).
- Return ONLY valid JSON with exactly these keys and no extra text, no backticks, no code fences:
    - "score": "X/10" where X is an integer from 0 to 10
    - "details": clean markdown (MAX 200 words) that may include:
        - **bold** for emphasis when it helps
        - hyphen bullet points (-) or short lists when clearer
        - > blockquotes when directly referencing the course material
        - supportive tone if unsure, constructive tone for misconceptions, affirming tone for partial credit
    - natural references to the course material (not formulaic)
"""

            resp = await model.generate_content_async(prompt)
            text = resp.text

            def _clean_details(s: str, max_words: int = 200) -> str:
                """Preserve helpful markdown (bold, bullets, blockquotes), remove disallowed wrappers, and cap word count.

                - Removes code fences and inline backticks.
                - Removes leading labels like 'score:' or 'details:' (case-insensitive).
                - Preserves newlines to keep bullet structure; collapses 3+ newlines to 2.
                - Caps total words across lines to max_words without forcing trailing punctuation.
                """
                import re as _re
                if not s:
                    return ""
                # Strip surrounding code fences and inline code markers
                s = _re.sub(r"```[\s\S]*?```", " ", s)
                s = s.replace("`", "")
                # Remove explicit label lines we never want
                s = _re.sub(r"(?im)^(\s*)(score|details|model\s*answer|why|study\s*next)\s*:\s*", r"\1", s)
                # Trim outer whitespace but keep internal newlines
                s = s.strip()
                # Collapse excessive blank lines (3+ to 2)
                s = _re.sub(r"\n{3,}", "\n\n", s)
                # Enforce word cap while keeping line breaks
                def _cap_words(text: str, limit: int) -> str:
                    parts = []
                    count = 0
                    for line in text.splitlines():
                        tokens = line.split()
                        if not tokens:
                            parts.append("")
                            continue
                        if count >= limit:
                            break
                        remaining = limit - count
                        if len(tokens) > remaining:
                            tokens = tokens[:remaining]
                        parts.append(" ".join(tokens))
                        count += len(tokens)
                    return "\n".join(parts).rstrip()

                s = _cap_words(s, max_words)
                return s

            def _fallback_compact_feedback(obj: dict) -> dict:
                """Return a minimal {score, details} object as a single helpful paragraph."""
                score_str = obj.get("score") or "0/10"
                # Prefer a direct feedback string if available
                fb = str(obj.get("feedback") or obj.get("details") or "").strip()

                def _first_sentence(text_: str) -> str:
                    t = text_.replace("\n", " ").strip()
                    return t if t else "Focus on the core concept asked."

                details_ = _clean_details(_first_sentence(fb), max_words=200)
                return {"score": str(score_str), "details": details_}

            def _normalize_score(raw_val) -> str:
                """Normalize score to 'X/10' string with X in [0,10]."""
                import re as _re
                try:
                    if isinstance(raw_val, (int, float)):
                        x = int(round(float(raw_val)))
                        x = max(0, min(10, x))
                        return f"{x}/10"
                    s = str(raw_val).strip()
                    # Extract first number
                    m = _re.search(r"(\d+(?:\.\d+)?)", s)
                    if m:
                        x = int(round(float(m.group(1))))
                        x = max(0, min(10, x))
                        return f"{x}/10"
                except Exception:
                    pass
                return "0/10"

            try:
                payload = text
                if "```json" in text:
                    payload = text.split("```json")[1].split("```", 1)[0].strip()
                elif "```" in text:
                    payload = text.split("```", 1)[1].strip()
                parsed = json.loads(payload)
                if isinstance(parsed, dict) and {"score", "details"}.issubset(parsed.keys()):
                    det = _clean_details(str(parsed.get("details", "")), max_words=200)
                    grading = {"score": _normalize_score(parsed.get("score")), "details": det}
                else:
                    # Build from whatever we got back
                    built = _fallback_compact_feedback(parsed if isinstance(parsed, dict) else {"feedback": text})
                    built["details"] = _clean_details(built.get("details", ""), max_words=200)
                    grading = built
            except Exception:
                # Fallback: compress raw text to a single concise sentence/paragraph
                raw = _clean_details(str(text or "").strip(), max_words=200)
                model_line = raw or "Review the core concept asked and answer directly."
                grading = {"score": "0/10", "details": model_line}

            # Persist the full grading dict in feedback for short_answer
            feedback = grading

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

        # Omit null fields to avoid "score": null style responses
        result_item = {
            "question_index": ans.question_index,
            "question_type": ans.question_type,
            "feedback": feedback,
        }
        if is_correct is not None:
            result_item["is_correct"] = is_correct
        if score is not None:
            result_item["score"] = score
        results.append(result_item)

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
@router.post(
    "/{session_id}/restart",
    response_model=ResponseModel,
)
async def restart_assessment(
    session_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restart an assessment session, clearing all previous answers"""
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
@router.delete(
    "/{session_id}",
    response_model=ResponseModel,
)
async def delete_assessment(
    session_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an assessment session and all related submissions"""
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
