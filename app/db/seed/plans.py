# app/seed/plans.py

import uuid
from ...models.plan import Plan, SummaryDetail, AIFeedbackLevel
import asyncio
from sqlalchemy.future import select
from app.db.deps import AsyncSessionLocal
from app.models.plan import Plan

# A list of Plan instances ready to be bulk‑inserted
default_plans = [
    Plan(
        id=uuid.uuid4(),
        name="Freemium",
        price_pence=0,
        monthly_upload_limit=2,
        pages_per_upload_limit=5,
        monthly_assessment_limit=2,
        questions_per_assessment=5,
        monthly_ask_question_limit=10,
        summary_detail=SummaryDetail.limited_detail,
        ai_feedback_level=AIFeedbackLevel.basic,
    ),
    Plan(
        id=uuid.uuid4(),
        name="Basic",
        price_pence=499,
        monthly_upload_limit=10,
        pages_per_upload_limit=20,
        monthly_assessment_limit=999999,
        questions_per_assessment=25,
        monthly_ask_question_limit=100,
        summary_detail=SummaryDetail.deep_insights,
        ai_feedback_level=AIFeedbackLevel.concise,
    ),
    Plan(
        id=uuid.uuid4(),
        name="Premium",
        price_pence=799,
        monthly_upload_limit=999999,
        pages_per_upload_limit=999999,
        monthly_assessment_limit=999999,
        questions_per_assessment=50,
        monthly_ask_question_limit=999999,
        summary_detail=SummaryDetail.deep_insights,
        ai_feedback_level=AIFeedbackLevel.full_in_depth,
    ),
]


async def seed_plans():
    """Seed plans if they don't exist."""
    async with AsyncSessionLocal() as db:
        # Check if plans exist
        result = await db.execute(select(Plan))
        existing_plans = result.scalars().all()

        if not existing_plans:
            print("No plans found. Adding default plans...")
            # Add default plans
            for plan in default_plans:
                db.add(plan)
            await db.commit()
            print(f"Added {len(default_plans)} default plans.")
        else:
            print(f"Found {len(existing_plans)} existing plans. Skipping seed.")


async def seed_all():
    """Run all seed functions."""
    await seed_plans()
    # Add more seeding functions here as needed


def run_seeder():
    """Run the seeder."""
    asyncio.run(seed_all())


if __name__ == "__main__":
    run_seeder()
