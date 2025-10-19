"""Test GET /plans API to verify billing_interval is included"""
import requests
import json

try:
    r = requests.get('http://localhost:8101/api/v1/plans')
    data = r.json()
    
    print("=" * 80)
    print("Testing GET /plans API Response")
    print("=" * 80)
    
    if data['status'] == 'success':
        plans = data['data']
        print(f"\nFound {len(plans)} plans\n")
        
        # Check first paid plan (Premium or Standard)
        for plan in plans:
            if plan['sku'] in ['PREMIUM', 'STANDARD']:
                print(f"\n{plan['sku']} Plan Prices:")
                print("-" * 80)
                
                monthly_found = False
                annual_found = False
                
                for price in plan['prices']:
                    interval = price.get('billing_interval', 'MISSING')
                    currency = price['currency']
                    amount = price['price_minor'] / 100
                    
                    print(f"  {currency:4} {amount:10.2f}  [{interval:6}]  {price['scope_type']:10}  {price.get('scope_value', 'N/A')}")
                    
                    if interval == 'month':
                        monthly_found = True
                    elif interval == 'year':
                        annual_found = True
                
                print()
                if monthly_found and annual_found:
                    print(f"✅ {plan['sku']}: Both monthly and annual prices found!")
                else:
                    print(f"❌ {plan['sku']}: Missing {'monthly' if not monthly_found else 'annual'} prices!")
                
                break
    else:
        print("❌ API returned error:", data)
        
except requests.exceptions.ConnectionError:
    print("❌ Cannot connect to backend. Is it running on port 8101?")
except Exception as e:
    print(f"❌ Error: {e}")
