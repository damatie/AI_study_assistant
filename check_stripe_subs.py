import asyncio
from app.db.deps import AsyncSessionLocal
from app.models.subscription import Subscription
from sqlalchemy import select

async def check():
    async with AsyncSessionLocal() as db:
        # Check subscriptions with Stripe IDs
        result = await db.execute(
            select(Subscription)
            .where(Subscription.stripe_subscription_id.isnot(None))
            .order_by(Subscription.created_at.desc())
            .limit(10)
        )
        subs = result.scalars().all()
        
        if not subs:
            print("❌ No Stripe subscriptions found in database!")
            print("This means either:")
            print("  1. Webhooks aren't being called")
            print("  2. Webhook handler is failing")
            print("  3. You haven't completed a Stripe checkout yet")
            return
            
        print("\n=== Stripe Subscriptions ===")
        for s in subs:
            print(f"\n✅ Subscription {s.id}")
            print(f"  User ID: {s.user_id}")
            print(f"  Plan ID: {s.plan_id}")
            print(f"  Billing Interval: {s.billing_interval}")
            print(f"  Period: {s.period_start} to {s.period_end}")
            print(f"  Status: {s.status}")
            print(f"  Stripe Sub ID: {s.stripe_subscription_id}")
            print(f"  Created: {s.created_at}")

asyncio.run(check())
