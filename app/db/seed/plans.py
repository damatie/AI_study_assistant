# app/seed/plans.py

import uuid
from ...models.plan import Plan, SummaryDetail, AIFeedbackLevel
import asyncio
from sqlalchemy.future import select
from app.db.deps import AsyncSessionLocal
from app.models.plan import Plan
from app.models.plan_price import PlanPrice, RegionScopeType
from app.utils.enums import BillingInterval
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

        def ensure_price(plan: Plan, currency: str, provider: PaymentProvider, price_minor: int, scope_type: RegionScopeType | str, scope_value: str | None = None, billing: BillingInterval = BillingInterval.month, provider_price_id: str | None = None, interval_count: int = 1):
            return PlanPrice(
                plan_id=plan.id,
                currency=currency,
                provider=provider,
                price_minor=price_minor,
                billing_interval=billing,
                provider_price_id=provider_price_id,
                interval_count=interval_count,
                scope_type=_to_scope_enum(scope_type),
                scope_value=scope_value,
                active=True,
            )

        # Basic example prices (adjust as needed)
        rows: list[PlanPrice] = []
        if std:
            rows += [
                # NG → Paystack NGN (country-specific) monthly + annual
                ensure_price(std, "NGN", PaymentProvider.paystack, 200000, RegionScopeType.country, "NG", BillingInterval.month, "pln_std_ngn_month"),
                ensure_price(std, "NGN", PaymentProvider.paystack, 2000000, RegionScopeType.country, "NG", BillingInterval.year, "pln_std_ngn_year"),
                # GBP → Stripe global monthly + annual
                ensure_price(std, "GBP", PaymentProvider.stripe, 499, RegionScopeType.global_scope.value, billing=BillingInterval.month, provider_price_id="price_std_gbp_month"),
                ensure_price(std, "GBP", PaymentProvider.stripe, 4990, RegionScopeType.global_scope.value, billing=BillingInterval.year, provider_price_id="price_std_gbp_year"),
                # USD → Stripe global vs Africa tier monthly + annual
                ensure_price(std, "USD", PaymentProvider.stripe, 699, RegionScopeType.global_scope.value, billing=BillingInterval.month, provider_price_id="price_std_usd_month"),
                ensure_price(std, "USD", PaymentProvider.stripe, 6990, RegionScopeType.global_scope.value, billing=BillingInterval.year, provider_price_id="price_std_usd_year"),
                ensure_price(std, "USD", PaymentProvider.stripe, 499, RegionScopeType.continent, "AF", billing=BillingInterval.month, provider_price_id="price_std_usd_af_month"),
                ensure_price(std, "USD", PaymentProvider.stripe, 4990, RegionScopeType.continent, "AF", billing=BillingInterval.year, provider_price_id="price_std_usd_af_year"),
            ]
        if prem:
            rows += [
                ensure_price(prem, "NGN", PaymentProvider.paystack, 500000, RegionScopeType.country, "NG", BillingInterval.month, "pln_prem_ngn_month"),
                ensure_price(prem, "NGN", PaymentProvider.paystack, 5000000, RegionScopeType.country, "NG", BillingInterval.year, "pln_prem_ngn_year"),
                ensure_price(prem, "GBP", PaymentProvider.stripe, 799, RegionScopeType.global_scope.value, billing=BillingInterval.month, provider_price_id="price_prem_gbp_month"),
                ensure_price(prem, "GBP", PaymentProvider.stripe, 7990, RegionScopeType.global_scope.value, billing=BillingInterval.year, provider_price_id="price_prem_gbp_year"),
                ensure_price(prem, "USD", PaymentProvider.stripe, 999, RegionScopeType.global_scope.value, billing=BillingInterval.month, provider_price_id="price_prem_usd_month"),
                ensure_price(prem, "USD", PaymentProvider.stripe, 9990, RegionScopeType.global_scope.value, billing=BillingInterval.year, provider_price_id="price_prem_usd_year"),
                ensure_price(prem, "USD", PaymentProvider.stripe, 799, RegionScopeType.continent, "AF", billing=BillingInterval.month, provider_price_id="price_prem_usd_af_month"),
                ensure_price(prem, "USD", PaymentProvider.stripe, 7990, RegionScopeType.continent, "AF", billing=BillingInterval.year, provider_price_id="price_prem_usd_af_year"),
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
                    PlanPrice.billing_interval == getattr(r, "billing_interval", BillingInterval.month),
                )
            )
            existing = exists_q.scalars().first()
            if not existing:
                db.add(r)
                inserted += 1
            else:
                # Update price if different
                try:
                    changed = False
                    if getattr(existing, "price_minor", None) != r.price_minor:
                        existing.price_minor = r.price_minor
                        changed = True
                    # Only set provider_price_id if currently NULL (don't overwrite real Stripe IDs)
                    if getattr(r, "provider_price_id", None) and not getattr(existing, "provider_price_id", None):
                        existing.provider_price_id = r.provider_price_id
                        changed = True
                    if changed:
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
    # Ensure flash-card related plan limits are populated for existing plans
    try:
        await ensure_flashcard_limits()
    except Exception as e:
        print(f"Flash-card limits ensure skipped due to: {e}")


async def ensure_flashcard_limits():
    """Fill in monthly_flash_cards_limit and max_cards_per_deck for known SKUs if zero.

    Idempotent: Only updates rows where these values are 0 or NULL.
    """
    targets = {
        "FREEMIUM": {"monthly_flash_cards_limit": 3, "max_cards_per_deck": 25},
        "STANDARD": {"monthly_flash_cards_limit": 15, "max_cards_per_deck": 40},
        "PREMIUM":  {"monthly_flash_cards_limit": 50, "max_cards_per_deck": 80},
    }
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Plan))
        plans = result.scalars().all()
        changed = 0
        for p in plans:
            spec = targets.get(p.sku)
            if not spec:
                continue
            mfl = getattr(p, "monthly_flash_cards_limit", 0) or 0
            mcd = getattr(p, "max_cards_per_deck", 0) or 0
            did_change = False
            if mfl == 0 and spec["monthly_flash_cards_limit"] is not None:
                p.monthly_flash_cards_limit = spec["monthly_flash_cards_limit"]
                did_change = True
            if mcd == 0 and spec["max_cards_per_deck"] is not None:
                p.max_cards_per_deck = spec["max_cards_per_deck"]
                did_change = True
            if did_change:
                db.add(p)
                changed += 1
        if changed:
            await db.commit()
            print(f"Plans flash-card limits updated for {changed} plan(s).")
        else:
            print("Plans flash-card limits already set.")


def run_seeder():
    """Run the seeder."""
    asyncio.run(seed_all())


if __name__ == "__main__":
    run_seeder()
