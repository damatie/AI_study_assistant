# app/seed/plans.py

import uuid
from ...models.plan import Plan, SummaryDetail, AIFeedbackLevel
import asyncio
from sqlalchemy.future import select
from app.db.deps import AsyncSessionLocal
from app.models.plan import Plan
from app.models.plan_price import PlanPrice, RegionScopeType
import enum as _enum
from app.utils.enums import PaymentProvider

# A list of Plan instances ready to be bulk‑inserted
default_plans = [
    Plan(
        id=uuid.uuid4(),
        name="Freemium",
        sku="FREEMIUM",
        monthly_upload_limit=3,
        pages_per_upload_limit=5,
        monthly_assessment_limit=5,
        questions_per_assessment=5,
        monthly_ask_question_limit=30,
        summary_detail=SummaryDetail.limited_detail,
        ai_feedback_level=AIFeedbackLevel.basic,
    ),
    Plan(
        id=uuid.uuid4(),
        name="Standard",
        sku="STANDARD",
        monthly_upload_limit=15,
        pages_per_upload_limit=15,
        monthly_assessment_limit=999999,
        questions_per_assessment=20,
        monthly_ask_question_limit=100,
        summary_detail=SummaryDetail.deep_insights,
        ai_feedback_level=AIFeedbackLevel.concise,
    ),
    Plan(
        id=uuid.uuid4(),
        name="Premium",
        sku="PREMIUM",
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


async def seed_plan_prices():
    """Seed regional/provider pricing for Standard and Premium plans if absent."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Plan))
        plans = {p.sku: p for p in result.scalars().all()}
        std = plans.get("STANDARD")
        prem = plans.get("PREMIUM")
        if not std and not prem:
            print("No paid plans found for pricing seed.")
            return

        def _to_scope_enum(st: RegionScopeType | str) -> RegionScopeType:
            if isinstance(st, _enum.Enum):
                return st  # already enum
            s = str(st).lower()
            if s in ("global", "global_scope"):
                return RegionScopeType.global_scope
            if s == "continent":
                return RegionScopeType.continent
            if s == "country":
                return RegionScopeType.country
            # default to global to be safe
            return RegionScopeType.global_scope

        def ensure_price(plan: Plan, currency: str, provider: PaymentProvider, price_minor: int, scope_type: RegionScopeType | str, scope_value: str | None = None):
            return PlanPrice(
                plan_id=plan.id,
                currency=currency,
                provider=provider,
                price_minor=price_minor,
                scope_type=_to_scope_enum(scope_type),
                scope_value=scope_value,
                active=True,
            )

        # Basic example prices (adjust as needed)
        rows: list[PlanPrice] = []
        if std:
            rows += [
                # NG → Paystack NGN (country-specific)
                ensure_price(std, "NGN", PaymentProvider.paystack, 200000, RegionScopeType.country, "NG"),
                # GBP → Stripe global
                ensure_price(std, "GBP", PaymentProvider.stripe, 499, RegionScopeType.global_scope.value),
                # USD → Stripe global vs Africa tier
                ensure_price(std, "USD", PaymentProvider.stripe, 699, RegionScopeType.global_scope.value),
                ensure_price(std, "USD", PaymentProvider.stripe, 499, RegionScopeType.continent, "AF"),
            ]
        if prem:
            rows += [
                ensure_price(prem, "NGN", PaymentProvider.paystack, 500000, RegionScopeType.country, "NG"),
                ensure_price(prem, "GBP", PaymentProvider.stripe, 799, RegionScopeType.global_scope.value),
                ensure_price(prem, "USD", PaymentProvider.stripe, 999, RegionScopeType.global_scope.value),
                ensure_price(prem, "USD", PaymentProvider.stripe, 799, RegionScopeType.continent, "AF"),
            ]

        # Insert only missing (plan_id, currency, provider, scope_type, scope_value)
        inserted = 0
        updated = 0
        for r in rows:
            exists_q = await db.execute(
                select(PlanPrice).where(
                    PlanPrice.plan_id == r.plan_id,
                    PlanPrice.currency == r.currency,
                    PlanPrice.provider == r.provider,
                    PlanPrice.scope_type == _to_scope_enum(r.scope_type),
                    PlanPrice.scope_value == r.scope_value,
                )
            )
            existing = exists_q.scalars().first()
            if not existing:
                db.add(r)
                inserted += 1
            else:
                # Update price if different
                try:
                    if getattr(existing, "price_minor", None) != r.price_minor:
                        existing.price_minor = r.price_minor
                        db.add(existing)
                        updated += 1
                except Exception:
                    pass
        if inserted or updated:
            await db.commit()
            print(f"Plan prices seeded. Inserted: {inserted}, Updated: {updated}.")
        else:
            print("Plan prices already up-to-date.")


async def seed_all():
    """Run all seed functions."""
    await seed_plans()
    await seed_plan_prices()


def run_seeder():
    """Run the seeder."""
    asyncio.run(seed_all())


if __name__ == "__main__":
    run_seeder()
