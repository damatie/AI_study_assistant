"""Quick sanity check for new payment system."""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def check_imports():
    """Test all imports work correctly."""
    print("🔍 Checking imports...")
    
    try:
        from app.services.payments.stripe_client import StripeClient
        print("  ✅ StripeClient imported")
    except Exception as e:
        print(f"  ❌ StripeClient import failed: {e}")
        return False
    
    try:
        from app.services.payments.paystack_client import PaystackClient
        print("  ✅ PaystackClient imported")
    except Exception as e:
        print(f"  ❌ PaystackClient import failed: {e}")
        return False
    
    try:
        from app.services.payments.subscription_service import SubscriptionService
        print("  ✅ SubscriptionService imported")
    except Exception as e:
        print(f"  ❌ SubscriptionService import failed: {e}")
        return False
    
    try:
        from app.api.v1.routes.payments.checkout import router as checkout_router
        print("  ✅ Checkout router imported")
    except Exception as e:
        print(f"  ❌ Checkout router import failed: {e}")
        return False
    
    try:
        from app.api.v1.routes.payments.stripe_webhooks import router as stripe_router
        print("  ✅ Stripe webhooks router imported")
    except Exception as e:
        print(f"  ❌ Stripe webhooks router import failed: {e}")
        return False
    
    try:
        from app.api.v1.routes.payments.paystack_webhooks import router as paystack_router
        print("  ✅ Paystack webhooks router imported")
    except Exception as e:
        print(f"  ❌ Paystack webhooks router import failed: {e}")
        return False
    
    return True


def check_enums():
    """Test enum usage."""
    print("\n🔍 Checking enums...")
    
    try:
        from app.utils.enums import TransactionStatus, PaymentProvider, BillingInterval
        print("  ✅ TransactionStatus imported")
        print(f"     - Values: {[s.value for s in TransactionStatus]}")
        print("  ✅ PaymentProvider imported")
        print(f"     - Values: {[p.value for p in PaymentProvider]}")
        print("  ✅ BillingInterval imported")
        print(f"     - Values: {[b.value for b in BillingInterval]}")
    except Exception as e:
        print(f"  ❌ Enum import failed: {e}")
        return False
    
    return True


def check_models():
    """Test model field names."""
    print("\n🔍 Checking models...")
    
    try:
        from app.models.transaction import Transaction
        print("  ✅ Transaction model imported")
        print(f"     - Has 'reference' field: {hasattr(Transaction, 'reference')}")
        print(f"     - Has 'status' field: {hasattr(Transaction, 'status')}")
    except Exception as e:
        print(f"  ❌ Transaction model check failed: {e}")
        return False
    
    try:
        from app.models.subscription import Subscription
        print("  ✅ Subscription model imported")
        print(f"     - Has 'stripe_subscription_id': {hasattr(Subscription, 'stripe_subscription_id')}")
        print(f"     - Has 'paystack_subscription_code': {hasattr(Subscription, 'paystack_subscription_code')}")
        print(f"     - Has 'billing_interval': {hasattr(Subscription, 'billing_interval')}")
        print(f"     - Has 'auto_renew': {hasattr(Subscription, 'auto_renew')}")
    except Exception as e:
        print(f"  ❌ Subscription model check failed: {e}")
        return False
    
    return True


def check_router():
    """Test router registration."""
    print("\n🔍 Checking router registration...")
    
    try:
        from app.api.v1.routes.router import router
        route_paths = [route.path for route in router.routes]
        print(f"  ✅ Main router imported ({len(router.routes)} routes)")
        
        # Check if our new routes are registered
        checkout_found = any('/checkout' in path for path in route_paths)
        stripe_webhook_found = any('/payments/stripe/webhook' in path for path in route_paths)
        paystack_webhook_found = any('/payments/paystack/webhook' in path for path in route_paths)
        
        print(f"     - /checkout: {'✅' if checkout_found else '❌'}")
        print(f"     - /payments/stripe/webhook: {'✅' if stripe_webhook_found else '❌'}")
        print(f"     - /payments/paystack/webhook: {'✅' if paystack_webhook_found else '❌'}")
        
        return checkout_found and stripe_webhook_found and paystack_webhook_found
    except Exception as e:
        print(f"  ❌ Router check failed: {e}")
        return False


def main():
    """Run all checks."""
    print("=" * 60)
    print("🔧 Payment System Sanity Check")
    print("=" * 60)
    
    checks = [
        ("Imports", check_imports),
        ("Enums", check_enums),
        ("Models", check_models),
        ("Router", check_router),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} check crashed: {e}")
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("📊 Results Summary")
    print("=" * 60)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{name}: {status}")
    
    all_passed = all(result for _, result in results)
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ ALL CHECKS PASSED! System is ready for testing.")
    else:
        print("❌ SOME CHECKS FAILED! Review errors above.")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
