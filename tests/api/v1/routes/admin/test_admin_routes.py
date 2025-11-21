from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest
from sqlalchemy import select

from app.models.admin_broadcast import AdminBroadcast
from app.models.assessment_session import AssessmentSession, Difficulty, SessionStatus
from app.models.flash_card_set import FlashCardSet
from app.models.plan import AIFeedbackLevel, Plan, SummaryDetail
from app.models.study_material import StudyMaterial
from app.models.subscription import Subscription
from app.models.transaction import Transaction
from app.models.usage_tracking import UsageTracking
from app.models.user import Role, User
from app.utils.enums import (
	BroadcastAudienceType,
	BroadcastStatus,
	FlashCardStatus,
	MaterialStatus,
	SubscriptionStatus,
	TransactionStatus,
	TransactionType,
	PaymentProvider,
)


pytestmark = pytest.mark.anyio


async def seed_admin_dataset(session) -> Dict[str, Any]:
	now = datetime.now(timezone.utc)

	plan = Plan(
		name="Pro",
		sku="PRO_MONTH",
		monthly_upload_limit=10,
		pages_per_upload_limit=25,
		monthly_assessment_limit=10,
		questions_per_assessment=5,
		monthly_ask_question_limit=20,
		monthly_flash_cards_limit=5,
		max_cards_per_deck=40,
		summary_detail=SummaryDetail.deep_insights,
		ai_feedback_level=AIFeedbackLevel.full_in_depth,
	)
	session.add(plan)
	await session.flush()

	admin_user = User(
		first_name="Admin",
		last_name="User",
		email="admin@example.com",
		password_hash="hashed",
		role=Role.admin,
		plan_id=plan.id,
		is_email_verified=True,
		created_at=now - timedelta(days=3),
	)
	verified_user = User(
		first_name="Victor",
		last_name="Verified",
		email="verified@example.com",
		password_hash="hashed",
		role=Role.user,
		plan_id=plan.id,
		is_email_verified=True,
		created_at=now - timedelta(days=2),
	)
	pending_user = User(
		first_name="Una",
		last_name="Verified",
		email="pending@example.com",
		password_hash="hashed",
		role=Role.user,
		plan_id=plan.id,
		is_email_verified=False,
		created_at=now - timedelta(days=1),
	)
	session.add_all([admin_user, verified_user, pending_user])
	await session.flush()

	active_sub = Subscription(
		user_id=verified_user.id,
		plan_id=plan.id,
		period_start=now - timedelta(days=15),
		period_end=now + timedelta(days=15),
		status=SubscriptionStatus.active,
		updated_at=now - timedelta(days=1),
	)
	churned_sub = Subscription(
		user_id=pending_user.id,
		plan_id=plan.id,
		period_start=now - timedelta(days=45),
		period_end=now - timedelta(days=15),
		status=SubscriptionStatus.cancelled,
		updated_at=now - timedelta(days=5),
	)
	session.add_all([active_sub, churned_sub])

	material_one = StudyMaterial(
		user_id=verified_user.id,
		title="Biology Notes",
		file_name="bio.pdf",
		file_path="/tmp/bio.pdf",
		content="Plants",
		processed_content=None,
		page_count=10,
		status=MaterialStatus.completed,
		created_at=now - timedelta(days=3),
	)
	material_two = StudyMaterial(
		user_id=pending_user.id,
		title="History Notes",
		file_name="history.pdf",
		file_path="/tmp/history.pdf",
		content="History",
		processed_content=None,
		page_count=8,
		status=MaterialStatus.completed,
		created_at=now - timedelta(days=1),
	)
	session.add_all([material_one, material_two])
	await session.flush()

	assessment = AssessmentSession(
		user_id=verified_user.id,
		material_id=material_one.id,
		topic="Photosynthesis",
		difficulty=Difficulty.easy,
		question_types=["multiple_choice"],
		questions_payload=[{"question": "Q1"}],
		status=SessionStatus.completed,
		created_at=now - timedelta(days=1),
	)
	session.add(assessment)

	flash_cards = FlashCardSet(
		user_id=verified_user.id,
		material_id=material_one.id,
		title="Bio Cards",
		topic="Cells",
		difficulty=Difficulty.easy,
		cards_payload=[{"prompt": "What is ATP?", "correspondingInformation": "Energy"}],
		status=FlashCardStatus.completed,
		created_at=now - timedelta(days=1),
	)
	session.add(flash_cards)

	usage_verified = UsageTracking(
		user_id=verified_user.id,
		period_start=(now - timedelta(days=5)).date(),
		uploads_count=8,
		assessments_count=6,
		asked_questions_count=9,
		flash_card_sets_count=4,
	)
	usage_pending = UsageTracking(
		user_id=pending_user.id,
		period_start=(now - timedelta(days=5)).date(),
		uploads_count=1,
		assessments_count=0,
		asked_questions_count=1,
		flash_card_sets_count=0,
	)
	session.add_all([usage_verified, usage_pending])

	transaction_recent = Transaction(
		user_id=verified_user.id,
		subscription_id=active_sub.id,
		reference="txn-recent",
		amount_pence=4500,
		currency="GBP",
		status=TransactionStatus.success,
		transaction_type=TransactionType.initial,
		provider=PaymentProvider.stripe,
		created_at=now - timedelta(days=3),
	)
	transaction_previous = Transaction(
		user_id=verified_user.id,
		subscription_id=active_sub.id,
		reference="txn-prev",
		amount_pence=3000,
		currency="GBP",
		status=TransactionStatus.success,
		transaction_type=TransactionType.recurring,
		provider=PaymentProvider.stripe,
		created_at=now - timedelta(days=40),
	)
	session.add_all([transaction_recent, transaction_previous])

	await session.commit()

	return {
		"plan": plan,
		"users": {
			"admin": admin_user,
			"verified": verified_user,
			"pending": pending_user,
		},
	}


async def test_get_admin_metrics_returns_expected_aggregates(client, db_session):
	await seed_admin_dataset(db_session)

	response = await client.get("/api/v1/admin/metrics")
	assert response.status_code == 200
	payload = response.json()

	assert payload["status"] == "success"
	data = payload["data"]

	assert data["totals"] == {
		"users": 3,
		"verified_users": 2,
		"unverified_users": 1,
	}
	assert data["subscriptions"]["active"] == 1
	assert data["subscriptions"]["churned_last_30_days"] == 1
	assert data["usage_last_30_days"]["materials"] == 2
	assert data["usage_last_30_days"]["assessments"] == 1
	assert data["usage_last_30_days"]["flash_card_sets"] == 1
	assert data["plan_utilization"][0]["email"] == "verified@example.com"
	assert data["plan_distribution"][0]["plan"] == "Pro"
	assert data["plan_distribution"][0]["users"] >= 1
	time_series = data["time_series"]
	assert time_series, "expected time series data for charts"
	assert any(point["signups"] >= 0 for point in time_series)
	revenue = data["revenue"]
	assert revenue["currency"] == "GBP"
	assert revenue["last_30_days_total"] >= 45
	assert len(revenue["time_series"]) == len(time_series)


async def test_get_admin_activity_lists_recent_events(client, db_session):
	await seed_admin_dataset(db_session)

	response = await client.get("/api/v1/admin/activity")
	assert response.status_code == 200
	payload = response.json()
	data = payload["data"]

	assert any(item["email"] == "verified@example.com" for item in data["recent_signups"])
	assert any(item["title"] == "Biology Notes" for item in data["recent_materials"])
	assert any(item["status"] == SubscriptionStatus.active for item in data["recent_subscription_events"])


async def test_get_admin_users_supports_filters_and_pagination(client, db_session):
	seeded = await seed_admin_dataset(db_session)
	plan = seeded["plan"]

	base_response = await client.get("/api/v1/admin/users", params={"page": 1, "page_size": 2})
	assert base_response.status_code == 200
	base_payload = base_response.json()
	assert base_payload["data"]["pagination"]["total"] == 3
	assert len(base_payload["data"]["results"]) == 2

	verified_filter = await client.get("/api/v1/admin/users", params={"verified": "false"})
	verified_payload = verified_filter.json()
	assert verified_payload["data"]["pagination"]["total"] == 1
	assert verified_payload["data"]["results"][0]["email"] == "pending@example.com"

	search_response = await client.get(
		"/api/v1/admin/users",
		params={"search": "victor"},
	)
	search_payload = search_response.json()
	assert search_payload["data"]["pagination"]["total"] == 1
	assert search_payload["data"]["results"][0]["email"] == "verified@example.com"

	plan_filter = await client.get(
		"/api/v1/admin/users",
		params={"plan_sku": plan.sku},
	)
	plan_payload = plan_filter.json()
	assert plan_payload["data"]["pagination"]["total"] == 3

	active_only = await client.get(
		"/api/v1/admin/users",
		params={"subscription_status": SubscriptionStatus.active.value},
	)
	active_payload = active_only.json()
	assert active_payload["data"]["pagination"]["total"] == 1
	assert active_payload["data"]["results"][0]["email"] == "verified@example.com"


async def test_get_admin_subscriptions_returns_expected_payload(client, db_session):
	await seed_admin_dataset(db_session)

	response = await client.get("/api/v1/admin/subscriptions")
	assert response.status_code == 200
	data = response.json()["data"]
	assert data["pagination"]["total"] == 2
	first = data["results"][0]
	assert "subscription_id" in first
	assert first["subscriber"]["email"]
	assert first["plan"]["name"] == "Pro"

	active_only = await client.get(
		"/api/v1/admin/subscriptions",
		params={"status": SubscriptionStatus.active.value},
	)
	active_payload = active_only.json()["data"]
	assert active_payload["pagination"]["total"] == 1
	assert active_payload["results"][0]["status"] == SubscriptionStatus.active.value


async def test_admin_broadcast_test_endpoint_sends_email(monkeypatch, client, db_session):
	await seed_admin_dataset(db_session)
	sent_payload = {}

	async def fake_send_email(**kwargs):
		sent_payload.update(kwargs)
		return {"id": "test"}

	monkeypatch.setattr(
		"app.services.admin.broadcast_service.send_email",
		fake_send_email,
	)

	body = {
		"subject": "Test Broadcast",
		"text_body": "Hello world",
		"test_recipient": "admin@example.com",
	}

	response = await client.post("/api/v1/admin/broadcasts/test", json=body)
	assert response.status_code == 200
	assert sent_payload["recipient"] == ["admin@example.com"]
	assert sent_payload["text_content"] == "Hello world"
	assert sent_payload["tags"]["type"] == "admin-broadcast-test"


async def test_admin_broadcast_send_endpoint_creates_record(monkeypatch, client, db_session):
	await seed_admin_dataset(db_session)
	captured: list[list[str]] = []

	async def fake_send_email(**kwargs):
		captured.append(kwargs["recipient"])
		return {"id": "batch"}

	monkeypatch.setattr(
		"app.services.admin.broadcast_service.send_email",
		fake_send_email,
	)

	body = {
		"subject": "New Feature",
		"text_body": "We shipped something",
		"audience": {"type": BroadcastAudienceType.verified.value},
	}

	response = await client.post("/api/v1/admin/broadcasts", json=body)
	assert response.status_code == 201
	data = response.json()["data"]
	assert data["status"] == BroadcastStatus.sent.value
	assert data["total_recipients"] == 2
	assert data["sent_count"] == 2
	assert captured and set(captured[0]) == {"verified@example.com", "admin@example.com"}

	# Ensure record persisted
	record = await db_session.scalar(select(AdminBroadcast))
	assert record is not None
	assert record.subject == "New Feature"
	assert record.total_recipients == 2


async def test_admin_broadcast_list_returns_history(client, db_session):
	seeded = await seed_admin_dataset(db_session)
	admin = seeded["users"]["admin"]
	broadcast = AdminBroadcast(
		subject="A",
		total_recipients=2,
		sent_count=2,
		audience_type=BroadcastAudienceType.all,
		audience_filters={"type": "all"},
		status=BroadcastStatus.sent,
		sent_by_id=admin.id,
	)
	db_session.add(broadcast)
	await db_session.commit()

	response = await client.get("/api/v1/admin/broadcasts")
	assert response.status_code == 200
	items = response.json()["data"]["items"]
	assert len(items) >= 1
	assert items[0]["subject"] == "A"
