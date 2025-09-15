# Standard library imports
import logging
import uuid
from typing import Optional, List, Tuple, Literal

# Third-party imports
from fastapi import APIRouter, Depends, status, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

# Local imports
from app.core.response import success_response, error_response, ResponseModel
from app.db.deps import get_db
from app.models.plan import Plan as PlanModel
from app.models.study_material import StudyMaterial as StudyMaterialModel
from app.api.v1.routes.auth.auth import get_current_user
from app.services.track_subscription_service.handle_track_subscription import (
    renew_subscription_for_user,
)
from app.services.track_usage_service.handle_usage_cycle import get_or_create_usage
from app.services.ai_service.tutoring_service import chat_with_ai
from app.utils.enums import SubscriptionStatus

# Initialize logger
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/questions", tags=["tutoring"])


# Data models
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
    tone: Literal['academic', 'conversational'] = Field(
        default='academic',
        description="Response tone: 'academic' (default) or 'conversational'",
        examples=['academic', 'conversational']
    )

    @field_validator("question")
    def question_not_empty(cls, v):
        if v is None or v.strip() == "":
            raise ValueError("Question cannot be empty")
        return v.strip()


# Ask questions
@router.post(
    "/ask",
    response_model=ResponseModel,
)
async def ask_question(
    request: QuestionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask a question to the AI tutor with markdown response"""
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
        # Prefer processed markdown content (detailed then overview) from envelope
        from app.utils.processed_payload import get_detailed, get_overview
        detailed_md = get_detailed(material.processed_content)
        overview_md = get_overview(material.processed_content)
        raw_md = detailed_md or overview_md or material.content or ""

        # Clean markdown for better AI context
        from app.services.material_processing_service.markdown_parser import (
            clean_markdown_for_context,
        )
        ctx = clean_markdown_for_context(raw_md)

    # 4. Generate the answer
    answer = await chat_with_ai(request.question, ctx, request.tone)

    # 5. Increment the asked_questions_count and save
    usage.asked_questions_count += 1
    db.add(usage)
    await db.commit()

    # 6. Return the response
    return success_response(msg="Answer generated", data=answer)


# Hint endpoint: provide a brief context hint and suggested questions
@router.get(
    "/hint",
    response_model=ResponseModel,
)
async def get_chat_hint(
    context_id: uuid.UUID = Query(..., description="UUID of the study material for context"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a short hint and exactly 4 content‑aware suggested questions derived from the material."""
    # Authorize & load material
    material = await db.get(StudyMaterialModel, context_id)
    if not material or material.user_id != current_user.id:
        return error_response("Study material not found or access denied", 404)

    # Pull best available markdown
    from app.utils.processed_payload import get_detailed, get_overview
    from app.services.material_processing_service.markdown_parser import clean_markdown_for_context

    title = material.title or "Material"
    md = get_detailed(material.processed_content) or get_overview(material.processed_content) or (material.content or "")
    md = clean_markdown_for_context(md)

    # Build hint and suggestions
    hint, suggestions = _derive_hint_and_questions(md, title)

    return success_response(
        msg="Hint generated",
        data={
            "hint": hint,
            "suggestions": suggestions,
        },
    )


def _derive_hint_and_questions(markdown: str, title: str) -> Tuple[str, List[str]]:
    """Create a concise hint (1–2 sentences) and exactly 4 content‑aware questions.

    Important: Never include the material title in the hint or questions. Use
    neutral phrases like "this material" or "the document" so suggestions
    generalize and don't sound repetitive.

    Approach (no LLM):
    - Use the first meaningful paragraph as the hint.
    - Extract section headings and key keywords from the document.
    - Build varied question templates (how/why/compare/limitations/example) using those signals.
    """
    import re
    from collections import Counter

    text = markdown or ""
    lines = [ln.strip() for ln in text.splitlines()]

    # Remove an H1 at the beginning
    if lines and lines[0].startswith("# "):
        lines = lines[1:]

    # First non-empty paragraph
    para: List[str] = []
    for ln in lines:
        if ln == "":
            if para:
                break
            continue
        # skip images/tables code fences
        if ln.startswith("!") or ln.startswith("|") or ln.startswith("```"):
            continue
        para.append(ln)
    first_para = " ".join(para).strip()
    sentences = re.split(r"(?<=[.!?])\s+", first_para) if first_para else []
    if sentences:
        hint = " ".join(sentences[:2])
    else:
        # Use neutral wording; do not inject the actual title
        hint = "Explore the core ideas, methods, and results presented in this material."

    # Collect headings (## / ###) in order and filter meta/boilerplate
    headings: List[str] = []
    meta_headings = {
        "abstract",
        "introduction",
        "conclusion",
        "results",
        "discussion",
        "references",
        "concise overview",
        "overview",
        "overview & learning objectives",
        "learning objectives",
        "high-yield summary",
        "summary",
        "table of contents",
        "contents",
        "quality checklist",
        "examples & applications",
        "visual elements policy",
        "interactive process visualization",
        "stepsjson format specification",
        "stepsjson rules",
        "good stepsjson examples",
        "poor stepsjson examples",
    }

    # Title tokens to help filter headings repeating the title
    def norm_tokens(s: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 2}

    title_tokens = norm_tokens(title or "")

    for m in re.finditer(r"^##+\s+(.+)$", text, flags=re.MULTILINE):
        h = m.group(1).strip().strip('#').strip()
        # drop leading numbering like "1.", "2.1)" etc.
        h = re.sub(r"^\s*\d+(?:[.)]|(?:\.\d+)*[.)]?)[\s-]*", "", h)
        hl = h.lower()
        if hl in meta_headings:
            continue
        # Skip if heading starts with any meta keyword
        if any(hl.startswith(mh) for mh in meta_headings):
            continue
        # Skip if heading is largely the title (>=60% token overlap)
        htoks = norm_tokens(hl)
        overlap = len(htoks & title_tokens)
        if title_tokens and htoks and (overlap / max(1, len(htoks)) >= 0.6):
            continue
        # Skip if heading contains 'introduction' even when prefixed with numbers
        if "introduction" in hl:
            continue
        headings.append(h)
    # Deduplicate while preserving order
    seen = set()
    topics = [h for h in headings if not (h in seen or seen.add(h))]

    # Quick keyword extraction (frequency of content words)
    stop = {
        'the','and','for','that','with','from','this','have','has','are','was','were','will','can','into','using','use','used','their','our','your','its','between','over','under','about','than','then','also','such','may','might','more','most','less','least','each','other','within','without','across','based','on','of','in','to','a','an','by','is','it','as','at','or','be','we','you','they','he','she','them','his','her','which','who','whom'
    }
    # Add meta words that shouldn't influence question focus
    stop.update({
        'concise','overview','learning','objectives','summary','high','yield','highyield',
        'figure','figures','table','tables','contents','quality','checklist','policy',
        'process','visualization','stepsjson','examples','applications','example','application'
    })
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text.lower())
    keywords = [w for w in words if w not in stop]
    key_counts = Counter(keywords)
    key_terms = [w for w, _ in key_counts.most_common(12)]

    # Prefer more specific topics for questions
    focus_terms: List[str] = []
    focus_terms.extend([t for t in topics[:4]])
    focus_terms.extend([w for w in key_terms if w not in {t.lower() for t in topics}][:4])

    # Signals
    has_math = "$" in text or "\\(" in text or "\\)" in text or "\\[" in text
    has_steps = "```stepsjson" in text

    suggestions: List[str] = []
    base = "this material"

    # Prioritize question styles aligned with study flow
    if topics:
        suggestions.append(f"How does the '{topics[0]}' section relate to the main goal of {base}?")

    # If preprocessing or feature terms appear, suggest targeted questions
    def contains_any(words: list[str]) -> bool:
        s = text.lower()
        return any(w in s for w in words)

    if contains_any(["pre-process", "preprocess", "pre-processing", "preprocessing", "normalization", "stemming", "lemmatiz", "html", "tag removal", "stopword"]):
        suggestions.append("Describe the purpose of the main preprocessing steps and how they affect the results.")

    if contains_any(["tf-idf", "tfidf", "term frequency", "inverse document frequency"]):
        suggestions.append("Explain how TF‑IDF helps in feature extraction and when it works best.")

    if contains_any(["feature", "manual feature", "n-gram", "ngram", "regex", "url", "domain", "header"]):
        suggestions.append("Name three specific features used and briefly explain why each is indicative of the target outcome.")

    if contains_any(["random forest", "logistic regression", "svm", "xgboost", "neural", "transformer", "bert", "distilbert"]):
        suggestions.append("What is the role of the primary model described, and what is its key strength in this context?")

    if has_math:
        suggestions.append("Explain the key equation or derivation and when it applies.")

    # Metrics and validation patterns
    if contains_any(["accuracy", "precision", "recall", "f1", "auc", "roc"]):
        suggestions.append("Define Accuracy, Precision, and Recall in the context of this material.")
    if contains_any(["cross-validation", "k-fold", "fold", "train-test", "validation"]):
        suggestions.append("Why is k‑fold cross‑validation considered more robust than a single train‑test split?")

    # Trim duplicates and empties, preserve order
    seenq = set()
    suggestions = [q for q in suggestions if q and not (q in seenq or seenq.add(q))]

    # Ensure exactly 4 suggestions with focused fallbacks
    fallbacks = [
        "What is the primary goal addressed, and what sensitive inputs/outputs are involved?",
        "List reasons why this problem matters in practice.",
        "What are the main limitations of traditional approaches mentioned here?",
        "Summarize the practical steps in the core method described.",
    ]
    fi = 0
    while len(suggestions) < 4 and fi < len(fallbacks):
        # Avoid duplicates
        if fallbacks[fi] not in suggestions:
            suggestions.append(fallbacks[fi])
        fi += 1

    # Trim to exactly 4
    return hint, suggestions[:4]
