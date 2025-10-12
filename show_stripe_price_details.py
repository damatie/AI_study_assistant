"""Show detailed Stripe Price information including nicknames/descriptions"""
import asyncio
import stripe
from app.db.deps import AsyncSessionLocal
from app.models.plan_price import PlanPrice
from app.models.plan import Plan
from app.core.config import settings
from sqlalchemy import select

async def show_stripe_price_details():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PlanPrice, Plan)
            .join(Plan)
            .where(PlanPrice.provider == "stripe")
            .order_by(Plan.name, PlanPrice.currency, PlanPrice.billing_interval)
        )
        
        print("\n🎯 Stripe Price Details with Descriptions:")
        print("=" * 120)
        
        for price_row, plan in result:
            try:
                # Fetch full price details from Stripe
                stripe_price = stripe.Price.retrieve(price_row.provider_price_id)
                
                interval = price_row.billing_interval.value if price_row.billing_interval else "month"
                scope = f"{price_row.scope_type.value}"
                if price_row.scope_value:
                    scope += f" ({price_row.scope_value})"
                amount = price_row.price_minor / 100
                
                nickname = stripe_price.get('nickname', 'No nickname')
                description = stripe_price.get('metadata', {}).get('description', 'No description')
                
                print(f"\n📋 {plan.name} - {price_row.currency} {amount:.2f}/{interval} ({scope})")
                print(f"   Price ID: {price_row.provider_price_id}")
                print(f"   Nickname: {nickname}")
                print(f"   Description: {description}")
                
            except Exception as e:
                print(f"\n❌ Error fetching {price_row.provider_price_id}: {e}")
        
        print("\n" + "=" * 120)
        print("\n✅ All Stripe price details displayed!")

if __name__ == "__main__":
    asyncio.run(show_stripe_price_details())
