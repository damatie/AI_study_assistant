"""Check annual prices in database"""
import asyncio
from app.db.deps import AsyncSessionLocal
from app.models.plan_price import PlanPrice
from app.models.plan import Plan
from sqlalchemy import select


async def check_prices():
    async with AsyncSessionLocal() as db:
        # Get all prices
        result = await db.execute(
            select(PlanPrice, Plan)
            .join(Plan, PlanPrice.plan_id == Plan.id)
            .order_by(Plan.sku, PlanPrice.currency, PlanPrice.billing_interval)
        )
        rows = result.all()
        
        print(f"\n{'='*80}")
        print(f"Found {len(rows)} price entries in database")
        print(f"{'='*80}\n")
        
        monthly_count = 0
        annual_count = 0
        
        for price, plan in rows:
            interval = price.billing_interval.value if hasattr(price.billing_interval, 'value') else price.billing_interval
            price_display = f"{price.currency} {price.price_minor/100:.2f}"
            
            print(f"{plan.sku:10} | {price_display:15} | {interval:6} | {price.scope_type.value if hasattr(price.scope_type, 'value') else price.scope_type:10} | {price.scope_value or 'N/A'}")
            
            if interval == 'month':
                monthly_count += 1
            elif interval == 'year':
                annual_count += 1
        
        print(f"\n{'='*80}")
        print(f"Summary: {monthly_count} monthly prices, {annual_count} annual prices")
        print(f"{'='*80}\n")
        
        if annual_count == 0:
            print("⚠️  WARNING: No annual prices found! Run seed script.")
        else:
            print(f"✅ Annual prices exist! Frontend should display them.")


if __name__ == "__main__":
    asyncio.run(check_prices())
