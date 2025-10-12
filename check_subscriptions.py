import asyncio
from app.db.deps import AsyncSessionLocal
from app.models.subscription import Subscription
from sqlalchemy import select

async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Subscription).order_by(Subscription.created_at.desc()).limit(5)
        )
        subs = result.scalars().all()
        
        if not subs:
            print("No subscriptions found")
            return
            
        print("\n=== Recent Subscriptions ===")
        for s in subs:
            print(f"\nID: {s.id}")
            print(f"  User ID: {s.user_id}")
            print(f"  Plan ID: {s.plan_id}")
            print(f"  Billing Interval: {s.billing_interval}")
            print(f"  Period: {s.period_start} to {s.period_end}")
            print(f"  Status: {s.status}")
            print(f"  Stripe Sub ID: {s.stripe_subscription_id}")

asyncio.run(check())
