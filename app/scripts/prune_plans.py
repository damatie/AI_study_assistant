"""
Prune (delete) unwanted plans by name or SKU from a target DB.

Safety:
- Checks for referencing users/subscriptions before deletion.
- Aborts delete if references exist unless --force is provided.
- Supports dry-run to preview actions.

Usage:
  python -m app.scripts.prune_plans --url postgresql+psycopg2://... \
    --name Basic --dry-run
  python -m app.scripts.prune_plans --url postgresql+psycopg2://... \
    --name Basic --force
"""

from __future__ import annotations

import argparse
from typing import List, Dict
import os

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from app.models.plan import Plan
from app.models.user import User
from app.models.subscription import Subscription


def _normalize_sync_url(url: str) -> str:
    if not url:
        return url
    u = url.strip()
    if u.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + u.split("postgresql+asyncpg://", 1)[1]
    if u.startswith("postgresql+psycopg://"):
        return "postgresql+psycopg2://" + u.split("postgresql+psycopg://", 1)[1]
    return u


def get_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prune (delete) plans by name or SKU")
    p.add_argument("--url", default=os.getenv("DATABASE_URL") or os.getenv("NEW_DATABASE_URL"), help="Target DB URL")
    p.add_argument("--name", action="append", default=[], help="Plan name to delete (can be provided multiple times)")
    p.add_argument("--sku", action="append", default=[], help="Plan SKU to delete (can be provided multiple times)")
    p.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    p.add_argument("--force", action="store_true", help="Delete even if references exist (users/subscriptions)")
    return p.parse_args()


def prune(url: str, names: List[str], skus: List[str], dry_run: bool, force: bool) -> Dict:
    eng = create_engine(_normalize_sync_url(url), future=True)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)

    targets = []
    with SessionLocal() as sess:
        for nm in names:
            p = sess.execute(select(Plan).where(Plan.name.ilike(nm))).scalars().first()
            if p:
                targets.append(p)
        for s in skus:
            p = sess.execute(select(Plan).where(Plan.sku == s)).scalars().first()
            if p and p not in targets:
                targets.append(p)

        deleted = []
        skipped = []
        details = []
        for plan in targets:
            user_cnt = sess.execute(select(func.count()).select_from(User).where(User.plan_id == plan.id)).scalar_one()
            sub_cnt = sess.execute(select(func.count()).select_from(Subscription).where(Subscription.plan_id == plan.id)).scalar_one()
            info = {
                "plan": {"id": str(plan.id), "name": plan.name, "sku": plan.sku},
                "user_refs": user_cnt,
                "subscription_refs": sub_cnt,
            }
            can_delete = force or (user_cnt == 0 and sub_cnt == 0)
            if can_delete:
                if not dry_run:
                    sess.delete(plan)  # cascades plan.prices via ORM
                deleted.append(plan.name)
            else:
                skipped.append(plan.name)
            details.append(info)

        if dry_run:
            sess.rollback()
        else:
            sess.commit()

        return {"deleted": deleted, "skipped": skipped, "details": details, "dry_run": dry_run}


def main():
    args = get_args()
    if not args.url:
        raise SystemExit("--url or NEW_DATABASE_URL must be provided")
    out = prune(args.url, args.name, args.sku, args.dry_run, args.force)
    print("--- Prune summary ---")
    for k, v in out.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
