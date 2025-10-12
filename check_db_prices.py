"""Check actual database prices"""
import asyncio
from app.db.deps import AsyncSessionLocal
from app.models.plan_price import PlanPrice
from app.models.plan import Plan
from sqlalchemy import select, func

async def check_prices():
    async with AsyncSessionLocal() as db:
        # Total count
        result = await db.execute(select(func.count()).select_from(PlanPrice))
        total = result.scalar()
        print(f"✅ Total prices in database: {total}")
        print("=" * 60)
        
        # Get plans
        plans_result = await db.execute(select(Plan))
        plans = {p.id: p.name for p in plans_result.scalars().all()}
        
        # Breakdown by currency and provider
        result2 = await db.execute(
            select(
                PlanPrice.currency,
                PlanPrice.provider,
                PlanPrice.billing_interval,
                func.count()
            ).group_by(
                PlanPrice.currency,
                PlanPrice.provider,
                PlanPrice.billing_interval
            )
        )
        
        print("\n📊 Breakdown by currency/provider/interval:")
        for currency, provider, interval, count in result2:
            interval_val = interval.value if interval else "month"
            print(f"  {currency:3} | {provider.value:8} | {interval_val:5} → {count} price(s)")
        
        print("\n" + "=" * 60)
        
        # List all prices with details
        all_prices = await db.execute(
            select(PlanPrice).order_by(PlanPrice.plan_id, PlanPrice.currency, PlanPrice.billing_interval)
        )
        
        print("\n📋 All prices in database:")
        for price in all_prices.scalars().all():
            plan_name = plans.get(price.plan_id, "Unknown")
            interval = price.billing_interval.value if price.billing_interval else "month"
            scope = f"{price.scope_type.value}"
            if price.scope_value:
                scope += f" ({price.scope_value})"
            amount = price.price_minor / 100
            
            print(f"  {plan_name:8} | {price.currency:3} | {price.provider.value:8} | "
                  f"{interval:5} | {scope:15} | {price.currency} {amount:8.2f}")

if __name__ == "__main__":
    asyncio.run(check_prices())
