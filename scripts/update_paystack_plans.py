"""Update Paystack plan codes in the database."""
import asyncio
from app.db.deps import AsyncSessionLocal
from app.models.plan_price import PlanPrice
from app.models.plan import Plan
from sqlalchemy import select


async def update_paystack_plans():
    """Update Paystack plan codes based on the screenshot."""
    
    # Mapping from your Paystack dashboard
    paystack_plans = {
        # Premium Annual - NGN 50,000.00
        ('Premium', 'year', 'NGN'): 'PLN_8lbm4pr08m547zd',
        
        # Premium Monthly - NGN 5,000.00
        ('Premium', 'month', 'NGN'): 'PLN_ddwdcx2mr3ubs2u',
        
        # Standard Annual - NGN 20,000.00
        ('Standard', 'year', 'NGN'): 'PLN_ey4hyg31c6yicvq',
        
        # Standard Monthly - NGN 2,000.00
        ('Standard', 'month', 'NGN'): 'PLN_utcjce782gisiks',
    }
    
    async with AsyncSessionLocal() as db:
        # Get all plans
        plans_result = await db.execute(select(Plan))
        plans = {plan.name: plan for plan in plans_result.scalars().all()}
        
        print(f"\nFound {len(plans)} plans: {list(plans.keys())}\n")
        
        # Get all Paystack prices
        prices_result = await db.execute(
            select(PlanPrice).where(PlanPrice.provider == 'paystack')
        )
        prices = prices_result.scalars().all()
        
        print(f"Found {len(prices)} Paystack prices\n")
        
        updated = 0
        for price in prices:
            # Find plan by ID
            plan_name = None
            for p in plans.values():
                if p.id == price.plan_id:
                    plan_name = p.name
                    break
            
            if not plan_name:
                print(f"⚠️  Could not find plan for price {price.id}")
                continue
            
            key = (plan_name, price.billing_interval.value, price.currency)
            
            if key in paystack_plans:
                old_code = price.provider_price_id
                new_code = paystack_plans[key]
                
                if old_code != new_code:
                    price.provider_price_id = new_code
                    db.add(price)
                    print(f"✅ Updated {plan_name} {price.billing_interval.value} {price.currency}")
                    print(f"   Old: {old_code}")
                    print(f"   New: {new_code}\n")
                    updated += 1
                else:
                    print(f"✓  {plan_name} {price.billing_interval.value} {price.currency} already correct\n")
            else:
                print(f"⚠️  No Paystack plan found for {plan_name} {price.billing_interval.value} {price.currency}\n")
        
        if updated > 0:
            await db.commit()
            print(f"\n🎉 Updated {updated} Paystack plan codes!")
        else:
            print(f"\n✓  All Paystack plan codes already up to date!")


if __name__ == "__main__":
    asyncio.run(update_paystack_plans())
