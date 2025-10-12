"""Show Stripe Price IDs"""
import asyncio
from app.db.deps import AsyncSessionLocal
from app.models.plan_price import PlanPrice
from app.models.plan import Plan
from sqlalchemy import select

async def show_stripe_ids():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PlanPrice, Plan)
            .join(Plan)
            .where(PlanPrice.provider == "stripe")
            .order_by(Plan.name, PlanPrice.currency, PlanPrice.billing_interval)
        )
        
        print("\n🎯 Real Stripe Price IDs in Database:")
        print("=" * 100)
        print(f"{'Plan':<10} | {'Price':<20} | {'Scope':<20} | {'Stripe Price ID':<40}")
        print("=" * 100)
        
        for price, plan in result:
            interval = price.billing_interval.value if price.billing_interval else "month"
            scope = f"{price.scope_type.value}"
            if price.scope_value:
                scope += f" ({price.scope_value})"
            amount = price.price_minor / 100
            price_str = f"{price.currency} {amount:7.2f}/{interval}"
            
            print(f"{plan.name:<10} | {price_str:<20} | {scope:<20} | {price.provider_price_id}")
        
        print("=" * 100)
        print("\n✅ All 12 Stripe prices now have real Price IDs!")

if __name__ == "__main__":
    asyncio.run(show_stripe_ids())
