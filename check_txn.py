import asyncio
from app.db.deps import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as db:
        # Check recent transactions
        result = await db.execute(text('''
            SELECT id, user_id, subscription_id, provider, status, reference, 
                   stripe_invoice_id, created_at 
            FROM transactions 
            ORDER BY created_at DESC 
            LIMIT 5
        '''))
        rows = result.fetchall()
        print('Recent transactions:')
        for r in rows:
            print(f'  ID: {r[0]}')
            print(f'  User ID: {r[1]}')
            print(f'  Subscription ID: {r[2]}')
            print(f'  Provider: {r[3]}')
            print(f'  Status: {r[4]}')
            print(f'  Reference: {r[5]}')
            print(f'  Stripe Invoice ID: {r[6]}')
            print(f'  Created: {r[7]}')
            print()

asyncio.run(check())
