"""Admin dashboard endpoints."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import success_response
from app.core.security import require_roles
from app.db.deps import get_db
from app.models.assessment_session import AssessmentSession
from app.models.flash_card_set import FlashCardSet
from app.models.plan import Plan
from app.models.study_material import StudyMaterial
from app.models.subscription import Subscription
from app.models.transaction import Transaction
from app.models.usage_tracking import UsageTracking
from app.models.user import Role, User
from app.schemas.admin.broadcasts import (
    BroadcastCreateRequest,
    BroadcastListResponse,
    BroadcastOut,
    BroadcastTestRequest,
)
from app.services.admin.broadcast_service import BroadcastService
from app.utils.enums import BillingInterval, SubscriptionStatus, TransactionStatus

METRIC_LOOKBACK_DAYS = 30
RECENT_LIMIT = 10

admin_guard = require_roles(Role.admin)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(admin_guard)],
)


def _dt_to_iso(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.astimezone().isoformat()


def _safe_ratio(numerator: Optional[int], denominator: Optional[int]) -> float:
    if not numerator or not denominator:
        return 0.0
    if denominator == 0:
        return 0.0
    return min(float(numerator) / float(denominator), 1.0)


def _pence_to_major(amount_pence: int) -> float:
    return round(amount_pence / 100.0, 2)


def _percent_change(current: int, previous: int) -> float:
    if previous == 0:
        return 1.0 if current > 0 else 0.0
    return round((current - previous) / previous, 4)


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _build_day_range(start: datetime, end: datetime) -> List[date]:
    """Return a list of calendar days spanning start -> end (inclusive)."""

    days: List[date] = []
    cursor = start.date()
    end_date = end.date()
    while cursor <= end_date:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


async def _daily_count_map(
    db: AsyncSession,
    *,
    column,
    entity_id,
    start_at: datetime,
    extra_filters: Optional[List[Any]] = None,
):
    """Return a mapping of day -> count for the given column within the window."""

    bucket = func.date_trunc("day", column).label("bucket")
    stmt = select(bucket, func.count(entity_id)).where(column >= start_at)
    if extra_filters:
        for condition in extra_filters:
            stmt = stmt.where(condition)
    stmt = stmt.group_by(bucket).order_by(bucket)

    rows = (await db.execute(stmt)).all()
    result: Dict[date, int] = {}
    for bucket_value, count in rows:
        if bucket_value is None:
            continue
        result[bucket_value.date()] = int(count or 0)
    return result


async def _daily_sum_map(
    db: AsyncSession,
    *,
    column,
    value_column,
    start_at: datetime,
    extra_filters: Optional[List[Any]] = None,
):
    bucket = func.date_trunc("day", column).label("bucket")
    stmt = select(bucket, func.coalesce(func.sum(value_column), 0)).where(column >= start_at)
    if extra_filters:
        for condition in extra_filters:
            stmt = stmt.where(condition)
    stmt = stmt.group_by(bucket).order_by(bucket)

    rows = (await db.execute(stmt)).all()
    result: Dict[date, int] = {}
    for bucket_value, total in rows:
        if bucket_value is None:
            continue
        result[bucket_value.date()] = int(total or 0)
    return result


@router.get("/metrics")
async def get_admin_metrics(db: AsyncSession = Depends(get_db)):
    """Return aggregate metrics for the dashboard cards."""

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=METRIC_LOOKBACK_DAYS)

    total_users = await db.scalar(select(func.count(User.id))) or 0
    verified_users = await db.scalar(
        select(func.count(User.id)).where(User.is_email_verified.is_(True))
    ) or 0
    unverified_users = total_users - verified_users

    active_subscriptions = await db.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.active
        )
    ) or 0
    churned_subscriptions = await db.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status.in_([
                SubscriptionStatus.cancelled,
                SubscriptionStatus.expired,
            ]),
            Subscription.updated_at >= window_start,
        )
    ) or 0

    materials_last_30 = await db.scalar(
        select(func.count(StudyMaterial.id)).where(StudyMaterial.created_at >= window_start)
    ) or 0
    assessments_last_30 = await db.scalar(
        select(func.count(AssessmentSession.id)).where(
            AssessmentSession.created_at >= window_start
        )
    ) or 0
    flash_cards_last_30 = await db.scalar(
        select(func.count(FlashCardSet.id)).where(FlashCardSet.created_at >= window_start)
    ) or 0

    daily_signups = await _daily_count_map(
        db,
        column=User.created_at,
        entity_id=User.id,
        start_at=window_start,
    )
    daily_material_uploads = await _daily_count_map(
        db,
        column=StudyMaterial.created_at,
        entity_id=StudyMaterial.id,
        start_at=window_start,
    )
    daily_assessments = await _daily_count_map(
        db,
        column=AssessmentSession.created_at,
        entity_id=AssessmentSession.id,
        start_at=window_start,
    )
    daily_flash_cards = await _daily_count_map(
        db,
        column=FlashCardSet.created_at,
        entity_id=FlashCardSet.id,
        start_at=window_start,
    )
    daily_subscription_activations = await _daily_count_map(
        db,
        column=Subscription.created_at,
        entity_id=Subscription.id,
        start_at=window_start,
        extra_filters=[Subscription.status == SubscriptionStatus.active],
    )
    daily_subscription_cancellations = await _daily_count_map(
        db,
        column=Subscription.updated_at,
        entity_id=Subscription.id,
        start_at=window_start,
        extra_filters=[
            Subscription.status.in_(
                [SubscriptionStatus.cancelled, SubscriptionStatus.expired]
            )
        ],
    )

    daily_revenue_pence = await _daily_sum_map(
        db,
        column=Transaction.created_at,
        value_column=Transaction.amount_pence,
        start_at=window_start,
        extra_filters=[Transaction.status == TransactionStatus.success],
    )

    revenue_currency = await db.scalar(
        select(Transaction.currency)
        .where(Transaction.status == TransactionStatus.success)
        .order_by(Transaction.created_at.desc())
    )
    if not revenue_currency:
        revenue_currency = "USD"

    current_month_start = _month_start(now)
    previous_month_start = _month_start(current_month_start - timedelta(days=1))

    current_month_revenue_pence = await db.scalar(
        select(func.coalesce(func.sum(Transaction.amount_pence), 0)).where(
            Transaction.status == TransactionStatus.success,
            Transaction.created_at >= current_month_start,
        )
    )
    current_month_revenue_pence = int(current_month_revenue_pence or 0)

    previous_month_revenue_pence = await db.scalar(
        select(func.coalesce(func.sum(Transaction.amount_pence), 0)).where(
            Transaction.status == TransactionStatus.success,
            Transaction.created_at >= previous_month_start,
            Transaction.created_at < current_month_start,
        )
    )
    previous_month_revenue_pence = int(previous_month_revenue_pence or 0)

    revenue_change_pct = _percent_change(
        current_month_revenue_pence, previous_month_revenue_pence
    )

    # Plan utilization snapshot (top 5 users closest to any plan limit)
    usage_latest_period = (
        select(
            UsageTracking.user_id,
            func.max(UsageTracking.period_start).label("latest_period"),
        )
        .group_by(UsageTracking.user_id)
        .subquery()
    )

    latest_usage = (
        select(
            UsageTracking.user_id,
            UsageTracking.period_start,
            UsageTracking.uploads_count,
            UsageTracking.assessments_count,
            UsageTracking.asked_questions_count,
            UsageTracking.flash_card_sets_count,
        )
        .join(
            usage_latest_period,
            and_(
                UsageTracking.user_id == usage_latest_period.c.user_id,
                UsageTracking.period_start == usage_latest_period.c.latest_period,
            ),
        )
        .subquery()
    )

    usage_stmt = (
        select(
            User.id,
            User.first_name,
            User.last_name,
            User.email,
            Plan.name.label("plan_name"),
            Plan.monthly_upload_limit,
            Plan.monthly_assessment_limit,
            Plan.monthly_ask_question_limit,
            Plan.monthly_flash_cards_limit,
            latest_usage.c.uploads_count,
            latest_usage.c.assessments_count,
            latest_usage.c.asked_questions_count,
            latest_usage.c.flash_card_sets_count,
        )
        .join(Plan, User.plan_id == Plan.id)
        .outerjoin(latest_usage, latest_usage.c.user_id == User.id)
    )

    usage_rows = (await db.execute(usage_stmt)).all()
    utilization: List[Dict[str, Any]] = []
    for row in usage_rows:
        uploads_ratio = _safe_ratio(row.uploads_count, row.monthly_upload_limit)
        assessments_ratio = _safe_ratio(
            row.assessments_count, row.monthly_assessment_limit
        )
        questions_ratio = _safe_ratio(
            row.asked_questions_count, row.monthly_ask_question_limit
        )
        flash_cards_ratio = _safe_ratio(
            row.flash_card_sets_count, row.monthly_flash_cards_limit
        )
        top_ratio = max(uploads_ratio, assessments_ratio, questions_ratio, flash_cards_ratio)
        if top_ratio <= 0:
            continue
        utilization.append(
            {
                "user_id": str(row.id),
                "name": f"{row.first_name} {row.last_name}",
                "email": row.email,
                "plan": row.plan_name,
                "max_utilization": round(top_ratio, 3),
                "breakdown": {
                    "uploads": uploads_ratio,
                    "assessments": assessments_ratio,
                    "questions": questions_ratio,
                    "flash_cards": flash_cards_ratio,
                },
            }
        )

    utilization = sorted(
        utilization,
        key=lambda item: item["max_utilization"],
        reverse=True,
    )[:5]

    timeline_days = _build_day_range(window_start, now)
    time_series: List[Dict[str, Any]] = []
    revenue_time_series: List[Dict[str, Any]] = []
    revenue_last_30_total_pence = sum(daily_revenue_pence.values())
    for day in timeline_days:
        day_iso = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat()
        time_series.append(
            {
                "date": day_iso,
                "signups": daily_signups.get(day, 0),
                "materials": daily_material_uploads.get(day, 0),
                "assessments": daily_assessments.get(day, 0),
                "flash_cards": daily_flash_cards.get(day, 0),
                "activations": daily_subscription_activations.get(day, 0),
                "cancellations": daily_subscription_cancellations.get(day, 0),
            }
        )
        revenue_time_series.append(
            {
                "date": day_iso,
                "amount": _pence_to_major(daily_revenue_pence.get(day, 0)),
            }
        )

    plan_distribution_rows = (
        await db.execute(
            select(
                Plan.name.label("plan_name"),
                func.count(User.id).label("user_count"),
            )
            .select_from(User)
            .outerjoin(Plan, Plan.id == User.plan_id)
            .group_by(Plan.name)
            .order_by(func.count(User.id).desc())
        )
    ).all()
    total_plan_users = sum(row.user_count for row in plan_distribution_rows)
    plan_distribution: List[Dict[str, Any]] = []
    for row in plan_distribution_rows:
        plan_label = row.plan_name or "Unassigned"
        count_value = int(row.user_count or 0)
        percentage = (
            round(count_value / total_plan_users, 4) if total_plan_users else 0.0
        )
        plan_distribution.append(
            {
                "plan": plan_label,
                "users": count_value,
                "percentage": percentage,
            }
        )

    data = {
        "totals": {
            "users": total_users,
            "verified_users": verified_users,
            "unverified_users": unverified_users,
        },
        "subscriptions": {
            "active": active_subscriptions,
            "churned_last_30_days": churned_subscriptions,
        },
        "usage_last_30_days": {
            "materials": materials_last_30,
            "assessments": assessments_last_30,
            "flash_card_sets": flash_cards_last_30,
        },
        "plan_utilization": utilization,
        "plan_distribution": plan_distribution,
        "time_series": time_series,
        "revenue": {
            "currency": revenue_currency,
            "current_month_total": _pence_to_major(current_month_revenue_pence),
            "previous_month_total": _pence_to_major(previous_month_revenue_pence),
            "last_30_days_total": _pence_to_major(revenue_last_30_total_pence),
            "change_percentage": revenue_change_pct,
            "time_series": revenue_time_series,
        },
    }

    return success_response("Metrics retrieved", data=data)


@router.get("/activity")
async def get_admin_activity(db: AsyncSession = Depends(get_db)):
    """Return recent events for the activity feed."""

    recent_users_stmt = (
        select(
            User.id,
            User.first_name,
            User.last_name,
            User.email,
            User.is_email_verified,
            User.created_at,
        )
        .order_by(User.created_at.desc())
        .limit(RECENT_LIMIT)
    )
    recent_users = [
        {
            "user_id": str(row.id),
            "name": f"{row.first_name} {row.last_name}",
            "email": row.email,
            "verified": row.is_email_verified,
            "created_at": _dt_to_iso(row.created_at),
        }
        for row in (await db.execute(recent_users_stmt)).all()
    ]

    recent_materials_stmt = (
        select(
            StudyMaterial.id,
            StudyMaterial.title,
            StudyMaterial.created_at,
            StudyMaterial.user_id,
            User.first_name,
            User.last_name,
        )
        .join(User, User.id == StudyMaterial.user_id)
        .order_by(StudyMaterial.created_at.desc())
        .limit(RECENT_LIMIT)
    )
    recent_materials = [
        {
            "material_id": str(row.id),
            "title": row.title,
            "owner": f"{row.first_name} {row.last_name}",
            "created_at": _dt_to_iso(row.created_at),
        }
        for row in (await db.execute(recent_materials_stmt)).all()
    ]

    recent_subscription_stmt = (
        select(
            Subscription.id,
            Subscription.user_id,
            Subscription.status,
            Subscription.updated_at,
            User.first_name,
            User.last_name,
        )
        .join(User, User.id == Subscription.user_id)
        .order_by(Subscription.updated_at.desc())
        .limit(RECENT_LIMIT)
    )
    recent_subscriptions = [
        {
            "subscription_id": str(row.id),
            "user": f"{row.first_name} {row.last_name}",
            "status": row.status,
            "occurred_at": _dt_to_iso(row.updated_at),
        }
        for row in (await db.execute(recent_subscription_stmt)).all()
    ]

    data = {
        "recent_signups": recent_users,
        "recent_materials": recent_materials,
        "recent_subscription_events": recent_subscriptions,
    }
    return success_response("Activity retrieved", data=data)


@router.get("/users")
async def get_admin_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by name or email"),
    plan_sku: Optional[str] = Query(None, description="Filter by plan SKU"),
    verified: Optional[bool] = Query(None, description="Filter by email verification status"),
    subscription_status: Optional[SubscriptionStatus] = Query(
        None, description="Filter by subscription status"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated user directory for admins."""

    filters = []
    if verified is not None:
        filters.append(User.is_email_verified.is_(verified))

    if plan_sku:
        filters.append(func.lower(Plan.sku) == plan_sku.strip().lower())

    if search:
        pattern = f"%{search.strip().lower()}%"
        filters.append(
            or_(
                func.lower(User.email).like(pattern),
                func.lower(User.first_name).like(pattern),
                func.lower(User.last_name).like(pattern),
            )
        )

    if subscription_status:
        filters.append(
            exists()
            .where(
                Subscription.user_id == User.id,
                Subscription.status == subscription_status,
            )
        )

    active_subscription_subquery = (
        select(func.count(Subscription.id))
        .where(
            Subscription.user_id == User.id,
            Subscription.status == SubscriptionStatus.active,
        )
        .correlate(User)
        .scalar_subquery()
    )

    base_query = (
        select(
            User.id,
            User.first_name,
            User.last_name,
            User.email,
            User.is_email_verified,
            User.created_at,
            Plan.name.label("plan_name"),
            Plan.sku.label("plan_sku"),
            active_subscription_subquery.label("active_subscription_count"),
        )
        .join(Plan, User.plan_id == Plan.id)
        .where(*filters)
        .order_by(User.created_at.desc())
    )

    total = await db.scalar(
        select(func.count(User.id))
        .join(Plan, User.plan_id == Plan.id)
        .where(*filters)
    ) or 0

    result = await db.execute(
        base_query.offset((page - 1) * page_size).limit(page_size)
    )
    rows = result.all()

    users = [
        {
            "user_id": str(row.id),
            "name": f"{row.first_name} {row.last_name}",
            "email": row.email,
            "plan": {
                "name": row.plan_name,
                "sku": row.plan_sku,
            },
            "verified": row.is_email_verified,
            "created_at": _dt_to_iso(row.created_at),
            "has_active_subscription": (row.active_subscription_count or 0) > 0,
        }
        for row in rows
    ]

    data = {
        "results": users,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size if page_size else 0,
        },
    }

    return success_response("Users retrieved", data=data)


@router.get("/subscriptions")
async def get_admin_subscriptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    plan_sku: Optional[str] = Query(None, description="Filter by plan SKU"),
    status: Optional[SubscriptionStatus] = Query(
        None, description="Filter by subscription status"
    ),
    billing_interval: Optional[BillingInterval] = Query(
        None, description="Filter by billing interval"
    ),
    search: Optional[str] = Query(None, description="Search by name or email"),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if plan_sku:
        filters.append(func.lower(Plan.sku) == plan_sku.strip().lower())
    if status:
        filters.append(Subscription.status == status)
    if billing_interval:
        filters.append(Subscription.billing_interval == billing_interval)
    if search:
        pattern = f"%{search.strip().lower()}%"
        filters.append(
            or_(
                func.lower(User.email).like(pattern),
                func.lower(User.first_name).like(pattern),
                func.lower(User.last_name).like(pattern),
            )
        )

    latest_payment_ref = (
        select(
            Transaction.subscription_id.label("lp_subscription_id"),
            func.max(Transaction.created_at).label("lp_latest_created_at"),
        )
        .where(Transaction.status == TransactionStatus.success)
        .group_by(Transaction.subscription_id)
        .subquery()
    )

    latest_payment = (
        select(
            Transaction.subscription_id.label("lp_subscription_id"),
            Transaction.amount_pence.label("lp_amount_pence"),
            Transaction.currency.label("lp_currency"),
            Transaction.created_at.label("lp_created_at"),
        )
        .join(
            latest_payment_ref,
            and_(
                Transaction.subscription_id
                == latest_payment_ref.c.lp_subscription_id,
                Transaction.created_at == latest_payment_ref.c.lp_latest_created_at,
            ),
        )
        .subquery()
    )

    base_query = (
        select(
            Subscription.id,
            Subscription.status,
            Subscription.period_start,
            Subscription.period_end,
            Subscription.billing_interval,
            Subscription.auto_renew,
            Subscription.created_at,
            User.first_name,
            User.last_name,
            User.email,
            Plan.name.label("plan_name"),
            Plan.sku.label("plan_sku"),
            latest_payment.c.lp_amount_pence,
            latest_payment.c.lp_currency,
            latest_payment.c.lp_created_at,
        )
        .join(User, Subscription.user_id == User.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .outerjoin(
            latest_payment,
            latest_payment.c.lp_subscription_id == Subscription.id,
        )
        .where(*filters)
        .order_by(Subscription.created_at.desc())
    )

    total = await db.scalar(
        select(func.count(Subscription.id))
        .join(User, Subscription.user_id == User.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(*filters)
    ) or 0

    result = await db.execute(
        base_query.offset((page - 1) * page_size).limit(page_size)
    )
    rows = result.all()

    subscriptions: List[Dict[str, Any]] = []
    for row in rows:
        full_name = f"{row.first_name} {row.last_name}".strip()
        last_payment_block = None
        if row.lp_amount_pence is not None and row.lp_currency:
            last_payment_block = {
                "amount": _pence_to_major(int(row.lp_amount_pence)),
                "currency": row.lp_currency,
                "collected_at": _dt_to_iso(row.lp_created_at),
            }
        subscriptions.append(
            {
                "subscription_id": str(row.id),
                "status": row.status,
                "period_start": _dt_to_iso(row.period_start),
                "period_end": _dt_to_iso(row.period_end),
                "billing_interval": row.billing_interval,
                "auto_renew": bool(row.auto_renew),
                "plan": {
                    "name": row.plan_name,
                    "sku": row.plan_sku,
                },
                "subscriber": {
                    "name": full_name,
                    "email": row.email,
                },
                "last_payment": last_payment_block,
            }
        )

    data = {
        "results": subscriptions,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size if page_size else 0,
        },
    }

    return success_response("Subscriptions retrieved", data=data)


@router.get("/broadcasts")
async def list_admin_broadcasts(
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    service = BroadcastService(db)
    records = await service.list_broadcasts(limit=limit)
    payload = BroadcastListResponse(
        items=[BroadcastOut.model_validate(record) for record in records],
        count=len(records),
    )
    return success_response("Broadcast history", data=payload.model_dump())


@router.post("/broadcasts/test")
async def send_admin_broadcast_test(
    request: BroadcastTestRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(admin_guard),
):
    service = BroadcastService(db)
    result = await service.send_test(request)
    return success_response("Test email sent", data=result)


@router.post("/broadcasts")
async def send_admin_broadcast(
    request: BroadcastCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(admin_guard),
):
    service = BroadcastService(db)
    broadcast = await service.send_broadcast(request, current_admin)
    data = BroadcastOut.model_validate(broadcast).model_dump()
    return success_response("Broadcast sent", data=data, status_code=201)
