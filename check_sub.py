import asyncio
from app.db.deps import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text('''
            SELECT id, user_id, plan_id, billing_interval, period_start, period_end, created_at 
            FROM subscriptions 
            ORDER BY created_at DESC 
            LIMIT 3
        '''))
        rows = result.fetchall()
        print('Recent subscriptions:')
        for r in rows:
            print(f'  ID: {r[0]}')
            print(f'  User: {r[1]}')
            print(f'  Plan: {r[2]}')
            print(f'  Billing Interval: {r[3]}')
            print(f'  Period: {r[4]} to {r[5]}')
            print(f'  Created: {r[6]}')
            print()

asyncio.run(check())
