# Testing Stripe Webhooks

## Problem Found
Your database has NO subscriptions with `stripe_subscription_id` set, which means:
- ❌ Webhooks are not being processed
- ❌ Or webhooks are failing silently

## How to Fix and Test

### Step 1: Start the Backend
```powershell
python -m app.main
```

### Step 2: Use Stripe CLI to Forward Webhooks
In a NEW terminal:
```powershell
stripe listen --forward-to http://localhost:8100/api/v1/payments/stripe/webhook
```

This will give you a webhook secret like: `whsec_...`

### Step 3: Update Your .env
Add this line to `.env`:
```
STRIPE_WEBHOOK_SECRET=whsec_...  # Use the secret from step 2
```

### Step 4: Restart Backend
Stop the backend (Ctrl+C) and start it again to load the new webhook secret.

### Step 5: Test an Annual Subscription
1. Go to your billing page
2. Select **Annual** plan
3. Click "Upgrade"
4. Use test card: `4242 4242 4242 4242`
5. Complete payment

### Step 6: Watch the Logs
You should see in the Stripe CLI terminal:
```
checkout.session.completed [evt_...]
  --> POST http://localhost:8100/api/v1/payments/stripe/webhook [200]
```

And in your backend terminal (app.log):
```
✅ Got billing_interval 'year' from Stripe subscription
```

### Step 7: Check Database
```powershell
python check_stripe_subs.py
```

You should now see a subscription with:
- ✅ `stripe_subscription_id`: `sub_...`
- ✅ `billing_interval`: `BillingInterval.year`

## If It Still Fails

Check the webhook response in Stripe CLI for errors. Common issues:
1. Wrong webhook URL
2. Webhook secret not set or incorrect
3. Database connection issues
4. Plan ID or User ID not in metadata

## Alternative: Use ngrok (If Stripe CLI doesn't work)
```powershell
ngrok http 8100
```

Then in Stripe Dashboard:
1. Go to Developers > Webhooks
2. Add endpoint: `https://YOUR_NGROK_URL.ngrok.io/api/v1/payments/stripe/webhook`
3. Select event: `checkout.session.completed`
4. Copy the signing secret to `.env`
