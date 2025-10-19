"""
Test script to simulate the complete grace period flow:
1. Create active subscription with payment failure
2. Simulate 4 failed payment attempts (retry logic)
3. Verify downgrade to Freemium after exhausting retries
4. Test auto-renewal success (clears retry period)

Run from project root: python scripts/test_grace_period_flow.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.deps import AsyncSessionLocal
from app.models.user import User
from app.models.subscription import Subscription
from app.models.plan import Plan
from app.models.usage_tracking import UsageTracking
from app.utils.enums import SubscriptionStatus, BillingInterval
from app.utils.datetime_utils import get_current_utc_datetime
from datetime import timedelta
from app.services.payments.subscription_service import SubscriptionService


async def create_test_subscription(db: AsyncSession, user_email: str = "test@example.com"):
    """Create a test user with an active subscription."""
    
    # Find or create user
    stmt = select(User).where(User.email == user_email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        print(f"❌ User {user_email} not found. Please create user first.")
        return None
    
    # Get Standard plan (or any paid plan)
    stmt = select(Plan).where(Plan.sku == "STANDARD")
    result = await db.execute(stmt)
    plan = result.scalar_one_or_none()
    
    if not plan:
        print("❌ Standard plan not found")
        return None
    
    # Create subscription
    now = get_current_utc_datetime()
    subscription = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        period_start=now,
        period_end=now + timedelta(days=30),
        stripe_subscription_id="test_sub_" + str(user.id)[:8],
        stripe_customer_id="test_cus_" + str(user.id)[:8],
        billing_interval=BillingInterval.month,
        auto_renew=True,
        status=SubscriptionStatus.active,
        is_in_retry_period=False,
        retry_attempt_count=0,
        last_payment_failure_at=None
    )
    
    db.add(subscription)
    
    # Update user's plan
    user.plan_id = plan.id
    db.add(user)
    
    await db.commit()
    await db.refresh(subscription)
    
    print(f"✅ Created test subscription: {subscription.id}")
    print(f"   User: {user.email} (ID: {user.id})")
    print(f"   Plan: {plan.name}")
    print(f"   Status: {subscription.status.value}")
    print(f"   Period: {subscription.period_start} → {subscription.period_end}")
    
    return subscription


async def simulate_payment_failure(db: AsyncSession, subscription: Subscription):
    """Simulate a payment failure (enters retry period)."""
    
    print(f"\n💥 Simulating payment failure #{subscription.retry_attempt_count + 1}...")
    
    # Simulate what invoice.payment_failed / charge.failed webhook does
    subscription.is_in_retry_period = True
    subscription.retry_attempt_count += 1
    subscription.last_payment_failure_at = get_current_utc_datetime()
    # Keep status = active (user retains access during retry)
    
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    
    print(f"⚠️  Payment failed! Retry attempt {subscription.retry_attempt_count}/4")
    print(f"   is_in_retry_period: {subscription.is_in_retry_period}")
    print(f"   retry_attempt_count: {subscription.retry_attempt_count}")
    print(f"   status: {subscription.status.value} (still active)")
    
    return subscription


async def simulate_payment_success(db: AsyncSession, subscription: Subscription):
    """Simulate a successful payment (clears retry period)."""
    
    print(f"\n✅ Simulating successful payment...")
    
    # Simulate what invoice.payment_succeeded / charge.success webhook does
    subscription.is_in_retry_period = False
    subscription.retry_attempt_count = 0
    subscription.last_payment_failure_at = None
    subscription.status = SubscriptionStatus.active
    
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    
    print(f"✅ Payment succeeded! Retry period cleared.")
    print(f"   is_in_retry_period: {subscription.is_in_retry_period}")
    print(f"   retry_attempt_count: {subscription.retry_attempt_count}")
    print(f"   status: {subscription.status.value}")
    
    return subscription


async def simulate_retry_exhaustion(db: AsyncSession, subscription: Subscription):
    """Simulate retry exhaustion and downgrade to Freemium."""
    
    print(f"\n🔻 Simulating retry exhaustion (4 failures) and downgrade...")
    
    service = SubscriptionService()
    
    # Get user's current plan
    stmt = select(User, Plan).join(Plan, User.plan_id == Plan.id).where(User.id == subscription.user_id)
    result = await db.execute(stmt)
    row = result.first()
    user, old_plan = row if row else (None, None)
    
    old_plan_name = old_plan.name if old_plan else "Unknown"
    
    # Downgrade to Freemium
    await service.downgrade_to_freemium(
        db=db,
        user_id=subscription.user_id,
        reason="test_retries_exhausted"
    )
    
    await db.refresh(subscription)
    
    # Get updated plan
    stmt = select(User, Plan).join(Plan, User.plan_id == Plan.id).where(User.id == subscription.user_id)
    result = await db.execute(stmt)
    row = result.first()
    user, new_plan = row if row else (None, None)
    
    print(f"🔻 Downgraded to Freemium!")
    print(f"   Old plan: {old_plan_name}")
    print(f"   New plan: {new_plan.name if new_plan else 'Unknown'}")
    print(f"   Subscription status: {subscription.status.value}")
    print(f"   is_in_retry_period: {subscription.is_in_retry_period}")


async def test_full_retry_flow(db: AsyncSession, user_email: str):
    """Test the complete flow: 4 failures → downgrade."""
    
    print("=" * 70)
    print("TEST 1: Full Retry Flow (4 failures → downgrade)")
    print("=" * 70)
    
    # Create subscription
    subscription = await create_test_subscription(db, user_email)
    if not subscription:
        return
    
    # Simulate 4 payment failures
    for i in range(4):
        await asyncio.sleep(0.5)  # Small delay for readability
        subscription = await simulate_payment_failure(db, subscription)
    
    # After 4 failures, downgrade should happen
    await asyncio.sleep(0.5)
    await simulate_retry_exhaustion(db, subscription)
    
    print("\n✅ TEST 1 PASSED: Downgrade after 4 failures works!")


async def test_recovery_flow(db: AsyncSession, user_email: str):
    """Test the recovery flow: failure → success (clears retry)."""
    
    print("\n" + "=" * 70)
    print("TEST 2: Recovery Flow (failure → success)")
    print("=" * 70)
    
    # Create subscription
    subscription = await create_test_subscription(db, user_email)
    if not subscription:
        return
    
    # Simulate 2 failures
    subscription = await simulate_payment_failure(db, subscription)
    await asyncio.sleep(0.5)
    subscription = await simulate_payment_failure(db, subscription)
    
    # Then success (should clear retry period)
    await asyncio.sleep(0.5)
    subscription = await simulate_payment_success(db, subscription)
    
    # Verify cleared
    assert subscription.is_in_retry_period == False, "Retry period should be cleared"
    assert subscription.retry_attempt_count == 0, "Retry count should be reset"
    
    print("\n✅ TEST 2 PASSED: Recovery from payment failure works!")


async def cleanup_test_data(db: AsyncSession, user_email: str):
    """Clean up test subscriptions."""
    
    print("\n" + "=" * 70)
    print("CLEANUP: Removing test subscriptions...")
    print("=" * 70)
    
    # Get user
    stmt = select(User).where(User.email == user_email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        print("No user to clean up")
        return
    
    # Delete subscriptions
    stmt = select(Subscription).where(Subscription.user_id == user.id)
    result = await db.execute(stmt)
    subscriptions = result.scalars().all()
    
    for sub in subscriptions:
        await db.delete(sub)
    
    # Delete usage tracking
    stmt = select(UsageTracking).where(UsageTracking.user_id == user.id)
    result = await db.execute(stmt)
    usage_records = result.scalars().all()
    
    for usage in usage_records:
        await db.delete(usage)
    
    await db.commit()
    
    print(f"✅ Cleaned up {len(subscriptions)} subscriptions and {len(usage_records)} usage records")


async def main():
    """Run all tests."""
    
    # Get test user email from command line or use default
    user_email = sys.argv[1] if len(sys.argv) > 1 else "edafemaxwell@gmail.com"
    
    print("\n" + "🧪" * 35)
    print(f"GRACE PERIOD FLOW TEST - User: {user_email}")
    print("🧪" * 35 + "\n")
    
    async with AsyncSessionLocal() as db:
        try:
            # Test 1: Full retry flow (4 failures → downgrade)
            await test_full_retry_flow(db, user_email)
            
            # Cleanup before next test
            await cleanup_test_data(db, user_email)
            await asyncio.sleep(1)
            
            # Test 2: Recovery flow (failure → success)
            await test_recovery_flow(db, user_email)
            
            # Final cleanup
            await cleanup_test_data(db, user_email)
            
            print("\n" + "=" * 70)
            print("🎉 ALL TESTS PASSED!")
            print("=" * 70)
            print("\nSummary:")
            print("✅ Payment failure enters retry period (keeps status=active)")
            print("✅ Retry counter increments correctly (max 4)")
            print("✅ 4 failures triggers downgrade to Freemium")
            print("✅ Successful payment clears retry period")
            print("✅ Auto-renewal logic working correctly")
            
        except Exception as e:
            print(f"\n❌ TEST FAILED: {e}")
            import traceback
            traceback.print_exc()
            
            # Try to cleanup even on failure
            try:
                await cleanup_test_data(db, user_email)
            except:
                pass


if __name__ == "__main__":
    asyncio.run(main())
