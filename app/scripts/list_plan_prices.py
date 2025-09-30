"""List plan prices grouped by plan, including billing interval and scope.

Run:
  python -m app.scripts.list_plan_prices
"""
import asyncio
from sqlalchemy import select
from app.db.deps import AsyncSessionLocal
from app.models.plan import Plan
from app.models.plan_price import PlanPrice


async def main() -> None:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Plan))
        plans = res.scalars().all()
        print(f"Plans: {len(plans)}")
        total = 0
        annual = 0
        for p in plans:
            q = await db.execute(select(PlanPrice).where(PlanPrice.plan_id == p.id))
            rows = q.scalars().all()
            if not rows:
                continue
            print(f"\n{p.name} ({p.sku}) -> {len(rows)} prices:")
            for r in sorted(rows, key=lambda x: (x.currency, x.provider.value if hasattr(x.provider, 'value') else x.provider, (x.billing_interval.value if hasattr(x.billing_interval, 'value') else x.billing_interval) or 'month', x.scope_type.value if hasattr(x.scope_type, 'value') else x.scope_type, x.scope_value or '')):
                ival = getattr(r, 'billing_interval', None)
                ival_s = ival.value if hasattr(ival, 'value') else (ival or 'month')
                prov = r.provider.value if hasattr(r.provider, 'value') else r.provider
                scopet = r.scope_type.value if hasattr(r.scope_type, 'value') else r.scope_type
                print(f"  - {r.currency} {prov} {ival_s} scope={scopet}:{r.scope_value or '-'} price_minor={r.price_minor} price_id={r.provider_price_id or '-'}")
                total += 1
                if ival_s == 'year':
                    annual += 1
        print(f"\nSummary: total prices={total}, annual entries={annual}")


if __name__ == "__main__":
    asyncio.run(main())
