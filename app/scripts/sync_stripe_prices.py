"""
Sync Stripe Prices Script

This script creates Stripe Products and Prices for all plan_prices in your database,
then updates the provider_price_id field with real Stripe Price IDs.

Database Status:
- Total prices in DB: 16 (verified)
- Stripe prices: 12 (will be synced by this script)
- Paystack prices: 4 (skipped - already have plan codes)

Usage:
    python -m app.scripts.sync_stripe_prices

What it does:
1. Creates Stripe Products (Standard, Premium, etc.)
2. Creates 12 Stripe Price objects (monthly & annual for GBP/USD)
3. Updates plan_prices table with real Stripe Price IDs
4. Skips Paystack/NGN prices (different provider)
5. Handles duplicates gracefully (won't recreate existing prices)
"""

import asyncio
import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.deps import AsyncSessionLocal
from app.models.plan import Plan
from app.models.plan_price import PlanPrice
from app.utils.enums import PaymentProvider, BillingInterval


# Color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_success(msg: str):
    print(f"{Colors.OKGREEN}✅ {msg}{Colors.ENDC}")


def print_info(msg: str):
    print(f"{Colors.OKCYAN}ℹ️  {msg}{Colors.ENDC}")


def print_warning(msg: str):
    print(f"{Colors.WARNING}⚠️  {msg}{Colors.ENDC}")


def print_error(msg: str):
    print(f"{Colors.FAIL}❌ {msg}{Colors.ENDC}")


def print_header(msg: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{msg}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}\n")


async def get_or_create_stripe_product(plan: Plan) -> stripe.Product:
    """Get existing Stripe product or create a new one."""
    
    # Search for existing product by name
    products = stripe.Product.list(limit=100)
    existing = next((p for p in products.data if p.name == plan.name), None)
    
    if existing:
        print_info(f"Found existing Stripe product: {plan.name} (ID: {existing.id})")
        return existing
    
    # Create new product
    product = stripe.Product.create(
        name=plan.name,
        description=f"{plan.name} subscription plan",
        metadata={
            "plan_id": str(plan.id),
            "plan_name": plan.name,
            "plan_sku": plan.sku or "",
        }
    )
    print_success(f"Created Stripe product: {plan.name} (ID: {product.id})")
    return product


async def find_existing_stripe_price(
    product_id: str,
    currency: str,
    amount: int,
    interval: str
) -> stripe.Price | None:
    """Find if a price already exists for this product/currency/amount/interval."""
    
    prices = stripe.Price.list(
        product=product_id,
        currency=currency.lower(),
        limit=100,
        active=True
    )
    
    for price in prices.data:
        if (price.unit_amount == amount and 
            price.recurring and 
            price.recurring.interval == interval):
            return price
    
    return None


async def sync_prices_to_stripe(db: AsyncSession, dry_run: bool = False):
    """
    Create Stripe Price objects and update database with real Price IDs.
    
    Args:
        db: Database session
        dry_run: If True, only shows what would be done without making changes
    """
    
    print_header("Stripe Price Sync Script")
    
    # Initialize Stripe
    if not settings.STRIPE_SECRET_KEY:
        print_error("STRIPE_SECRET_KEY not configured in environment!")
        return
    
    stripe.api_key = settings.STRIPE_SECRET_KEY
    print_info(f"Using Stripe API Key: {settings.STRIPE_SECRET_KEY[:7]}...")
    
    if dry_run:
        print_warning("DRY RUN MODE - No changes will be made")
    
    # Get database stats
    total_result = await db.execute(select(PlanPrice))
    total_prices = len(total_result.scalars().all())
    
    paystack_result = await db.execute(
        select(PlanPrice).where(PlanPrice.provider == PaymentProvider.paystack)
    )
    paystack_count = len(paystack_result.scalars().all())
    
    print_info(f"Total prices in database: {total_prices}")
    print_info(f"Paystack prices (will skip): {paystack_count}")
    print_info(f"Stripe prices to sync: {total_prices - paystack_count}")
    print()
    
    # Get all Stripe prices that need syncing
    result = await db.execute(
        select(PlanPrice, Plan)
        .join(Plan, PlanPrice.plan_id == Plan.id)
        .where(PlanPrice.provider == PaymentProvider.stripe)
        .where(PlanPrice.active == True)
        .order_by(Plan.name, PlanPrice.currency, PlanPrice.billing_interval)
    )
    
    price_plan_pairs = result.all()
    
    if not price_plan_pairs:
        print_warning("No Stripe prices found in database!")
        return
    
    print_info(f"Found {len(price_plan_pairs)} price rows to sync\n")
    
    # Group by plan to create products first
    plans_dict = {}
    for price_row, plan in price_plan_pairs:
        if plan.id not in plans_dict:
            plans_dict[plan.id] = {
                "plan": plan,
                "prices": []
            }
        plans_dict[plan.id]["prices"].append(price_row)
    
    # Process each plan
    total_created = 0
    total_updated = 0
    total_skipped = 0
    
    for plan_id, data in plans_dict.items():
        plan = data["plan"]
        prices = data["prices"]
        
        print(f"\n{Colors.BOLD}Processing Plan: {plan.name}{Colors.ENDC}")
        print(f"  SKU: {plan.sku}")
        print(f"  Prices to sync: {len(prices)}")
        
        if dry_run:
            print_info("  [DRY RUN] Would create/update Stripe product")
        else:
            # Get or create Stripe product
            try:
                stripe_product = await get_or_create_stripe_product(plan)
            except Exception as e:
                print_error(f"  Failed to create product: {e}")
                continue
        
        # Process each price
        for price_row in prices:
            currency = price_row.currency
            amount = price_row.price_minor
            interval = price_row.billing_interval.value if price_row.billing_interval else "month"
            scope = price_row.scope_type.value if price_row.scope_type else "global"
            scope_val = price_row.scope_value or ""
            
            # Format display
            amount_display = f"{currency} {amount/100:.2f}"
            scope_display = f"{scope}" + (f":{scope_val}" if scope_val else "")
            
            print(f"\n  → {amount_display} / {interval} ({scope_display})")
            
            # Check if already has a valid provider_price_id
            if price_row.provider_price_id and price_row.provider_price_id.startswith("price_"):
                print_info(f"    Already has Price ID: {price_row.provider_price_id}")
                
                # Verify it exists in Stripe
                if not dry_run:
                    try:
                        existing = stripe.Price.retrieve(price_row.provider_price_id)
                        print_success(f"    Verified in Stripe ✓")
                        total_skipped += 1
                        continue
                    except stripe.error.InvalidRequestError:
                        print_warning(f"    Price ID not found in Stripe, will recreate")
                        # Clear the invalid price ID so it will be recreated
                        price_row.provider_price_id = None
            
            if dry_run:
                print_info(f"    [DRY RUN] Would create Stripe price")
                continue
            
            try:
                # Check if price already exists in Stripe
                existing_price = await find_existing_stripe_price(
                    stripe_product.id,
                    currency,
                    amount,
                    interval
                )
                
                if existing_price:
                    print_info(f"    Found existing: {existing_price.id}")
                    stripe_price = existing_price
                    action = "updated"
                else:
                    # Build description
                    interval_display = "monthly" if interval == "month" else "annual"
                    scope_desc = ""
                    if scope == "continent" and scope_val:
                        scope_desc = f" (Africa)"
                    elif scope == "country" and scope_val:
                        scope_desc = f" ({scope_val})"
                    elif scope == "global":
                        scope_desc = " (Global)"
                    
                    description = f"{plan.name} Plan - {interval_display} subscription{scope_desc}"
                    
                    # Create new Stripe price with description
                    stripe_price = stripe.Price.create(
                        product=stripe_product.id,
                        currency=currency.lower(),
                        unit_amount=amount,
                        nickname=f"{plan.name} - {currency} {interval}",  # Short name for Stripe Dashboard
                        recurring={
                            "interval": interval,
                            "interval_count": 1,
                        },
                        metadata={
                            "plan_id": str(plan.id),
                            "plan_name": plan.name,
                            "plan_sku": plan.sku or "",
                            "billing_interval": interval,
                            "scope_type": scope,
                            "scope_value": scope_val,
                            "db_price_id": str(price_row.id),
                            "description": description,
                        }
                    )
                    print_success(f"    Created: {stripe_price.id}")
                    print_info(f"    Description: {description}")
                    action = "created"
                
                # Update database with Stripe Price ID
                price_row.provider_price_id = stripe_price.id
                db.add(price_row)
                
                if action == "created":
                    total_created += 1
                else:
                    total_updated += 1
                    
            except Exception as e:
                print_error(f"    Failed: {e}")
                continue
    
    # Commit all changes
    if not dry_run:
        await db.commit()
        print_header("Sync Complete!")
        print_success(f"Created: {total_created} new prices")
        print_success(f"Updated: {total_updated} existing prices")
        print_info(f"Skipped: {total_skipped} already synced")
        print(f"\n{Colors.BOLD}Total processed: {total_created + total_updated + total_skipped}{Colors.ENDC}\n")
    else:
        print_header("Dry Run Complete!")
        print_info("No changes were made. Run without --dry-run to apply changes.")


async def list_stripe_products():
    """List all Stripe products and their prices."""
    
    print_header("Stripe Products & Prices")
    
    if not settings.STRIPE_SECRET_KEY:
        print_error("STRIPE_SECRET_KEY not configured!")
        return
    
    stripe.api_key = settings.STRIPE_SECRET_KEY
    
    products = stripe.Product.list(limit=100, active=True)
    
    if not products.data:
        print_warning("No products found in Stripe")
        return
    
    for product in products.data:
        print(f"\n{Colors.BOLD}Product: {product.name}{Colors.ENDC}")
        print(f"  ID: {product.id}")
        
        # Get prices for this product
        prices = stripe.Price.list(product=product.id, limit=100, active=True)
        
        if prices.data:
            print(f"  Prices:")
            for price in prices.data:
                currency = price.currency.upper()
                amount = price.unit_amount / 100
                interval = price.recurring.interval if price.recurring else "one-time"
                print(f"    • {currency} {amount:.2f} / {interval}")
                print(f"      ID: {Colors.OKCYAN}{price.id}{Colors.ENDC}")
        else:
            print(f"  {Colors.WARNING}No prices{Colors.ENDC}")


async def delete_all_stripe_prices(db: AsyncSession):
    """Delete all existing Stripe prices and clear provider_price_id in database."""
    
    print_header("Deleting All Existing Stripe Prices")
    
    if not settings.STRIPE_SECRET_KEY:
        print_error("STRIPE_SECRET_KEY not configured!")
        return
    
    stripe.api_key = settings.STRIPE_SECRET_KEY
    
    # Get all Stripe prices from database
    result = await db.execute(
        select(PlanPrice)
        .where(PlanPrice.provider == PaymentProvider.stripe)
        .where(PlanPrice.provider_price_id.isnot(None))
    )
    
    price_rows = result.scalars().all()
    deleted_count = 0
    
    print_info(f"Found {len(price_rows)} prices in database to check")
    
    for price_row in price_rows:
        if price_row.provider_price_id and price_row.provider_price_id.startswith("price_"):
            try:
                # Try to retrieve and archive the price in Stripe
                stripe.Price.modify(price_row.provider_price_id, active=False)
                print_success(f"Archived: {price_row.provider_price_id}")
                deleted_count += 1
            except stripe.error.InvalidRequestError:
                print_warning(f"Price not found in Stripe: {price_row.provider_price_id}")
            except Exception as e:
                print_warning(f"Could not archive {price_row.provider_price_id}: {e}")
            
            # Clear the provider_price_id from database
            price_row.provider_price_id = None
            db.add(price_row)
    
    await db.commit()
    
    print_success(f"\n✅ Archived {deleted_count} prices in Stripe")
    print_success(f"✅ Cleared all provider_price_id values in database")
    print_info("Ready to recreate prices with uniform descriptions\n")


async def main():
    """Main entry point."""
    import sys
    
    # Parse arguments
    dry_run = "--dry-run" in sys.argv
    list_only = "--list" in sys.argv
    clean = "--clean" in sys.argv
    
    async with AsyncSessionLocal() as db:
        if list_only:
            await list_stripe_products()
        elif clean:
            # Delete all prices and recreate
            await delete_all_stripe_prices(db)
            print_info("Now running sync to recreate all prices with descriptions...\n")
            await sync_prices_to_stripe(db, dry_run=False)
        else:
            await sync_prices_to_stripe(db, dry_run=dry_run)


if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║           Stripe Price Sync Script                      ║
    ║                                                          ║
    ║  This will create Stripe Products & Prices and update   ║
    ║  your database with real Stripe Price IDs               ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    import sys
    if "--help" in sys.argv or "-h" in sys.argv:
        print("""
Usage:
  python -m app.scripts.sync_stripe_prices              # Run sync
  python -m app.scripts.sync_stripe_prices --dry-run    # Preview changes
  python -m app.scripts.sync_stripe_prices --list       # List Stripe products

Options:
  --dry-run    Show what would be done without making changes
  --list       List all Stripe products and their prices
  --help, -h   Show this help message
        """)
        sys.exit(0)
    
    asyncio.run(main())
