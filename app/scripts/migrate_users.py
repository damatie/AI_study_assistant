"""
One-time User migration tool

Goals:
- Migrate only users from an OLD DB to the NEW DB
- Default every user to the FREE plan (SKU=FREEMIUM) in the new system
- Do NOT migrate materials, assessments, or any related content
- Idempotent by upserting on email (unique)

Usage (env vars or CLI):

  # Using environment variables
  set OLD_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/olddb
  set NEW_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/newdb
  python -m app.scripts.migrate_users --dry-run

  # Using CLI args
  python -m app.scripts.migrate_users \
    --old-url "postgresql+psycopg://.../olddb" \
    --new-url "postgresql+psycopg://.../newdb" \
    --free-plan-sku FREEMIUM \
    --batch-size 500

Notes:
- Expects an old users table named 'users' with columns: email, password_hash, first_name, last_name, role, is_active, is_email_verified, created_at (best effort, with defaulting if missing)
- If old roles differ (e.g., 'USER'/'ADMIN'), they will be normalized and mapped to {'user','admin'}
- Password hashes are copied as-is (assumes same hashing scheme). If not, you may need to force password resets later.
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from typing import Any, Dict

from sqlalchemy import create_engine, text, select
from sqlalchemy.engine import Engine, Result
from sqlalchemy.orm import Session, sessionmaker

# New system models
from app.models.user import User, Role
from app.models.usage_tracking import UsageTracking
from app.models.plan import Plan


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate users from old DB to new DB")
    parser.add_argument("--old-url", default=os.getenv("OLD_DATABASE_URL"), help="Old database URL")
    parser.add_argument("--new-url", default=os.getenv("NEW_DATABASE_URL"), help="New database URL")
    parser.add_argument("--free-plan-id", default=os.getenv("FREE_PLAN_ID"), help="Explicit Free plan UUID in the new DB (highest precedence)")
    parser.add_argument("--free-plan-sku", default=os.getenv("FREE_PLAN_SKU"), help="SKU for Free plan in the new DB")
    parser.add_argument("--free-plan-name", default=os.getenv("FREE_PLAN_NAME", "Free"), help="Name of Free plan fallback (defaults to 'Free')")
    parser.add_argument("--old-users-table", default=os.getenv("OLD_USERS_TABLE", "users"), help="Old users table name")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("BATCH_SIZE", 500)), help="Batch size for fetching/inserting")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes to the new DB")
    return parser.parse_args()


def _normalize_sync_url(url: str) -> str:
    """Turn async URLs (postgresql+asyncpg://) into sync (postgresql+psycopg2://).

    Also accepts bare postgresql:// and leaves psycopg/psycopg2 variants untouched.
    """
    if not url:
        return url
    u = url.strip()
    # Convert async driver to psycopg2 which we have installed
    if u.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + u.split("postgresql+asyncpg://", 1)[1]
    # Modern psycopg3 alias -> use psycopg2 for this sync script unless psycopg3 is installed
    if u.startswith("postgresql+psycopg://"):
        return "postgresql+psycopg2://" + u.split("postgresql+psycopg://", 1)[1]
    # Bare scheme: leave as-is (defaults to psycopg2 in our env)
    return u


def connect(url: str) -> Engine:
    if not url:
        raise SystemExit("Database URL is required")
    return create_engine(_normalize_sync_url(url), future=True)


def find_free_plan_id(sess: Session, *, plan_id: str | None, sku: str | None, name: str | None) -> Any:
    # 1) If explicit ID provided
    if plan_id:
        plan = sess.get(Plan, plan_id)
        if not plan:
            raise SystemExit(f"Free plan with id={plan_id} not found")
        return plan.id

    # 2) Try SKU if provided
    if sku:
        p = sess.execute(select(Plan).where(Plan.sku.ilike(sku))).scalars().first()
        if p:
            return p.id

    # 3) Try name (exact, then startswith)
    if name:
        p = sess.execute(select(Plan).where(Plan.name.ilike(name))).scalars().first()
        if p:
            return p.id
        p = sess.execute(select(Plan).where(Plan.name.ilike(f"{name}%"))).scalars().first()
        if p:
            return p.id

    raise SystemExit("Free plan not found in new DB. Provide --free-plan-id or --free-plan-sku or --free-plan-name.")


def normalize_role(value: Any) -> Role:
    try:
        v = (value or "user").strip().lower()
    except Exception:
        v = "user"
    if v in ("admin",):
        return Role.admin
    return Role.user


def upsert_user(sess: Session, free_plan_id: Any, row: Dict[str, Any], create_usage: bool = True) -> bool:
    """Insert or update by email. Returns True if inserted, False if updated/skipped."""
    email = (row.get("email") or "").strip().lower()
    if not email:
        return False

    u = sess.query(User).filter(User.email == email).one_or_none()
    inserted = False
    if u is None:
        u = User(
            email=email,
            first_name=(row.get("first_name") or "").strip() or "",
            last_name=(row.get("last_name") or "").strip() or "",
            password_hash=row.get("password_hash") or "",
            role=normalize_role(row.get("role")),
            plan_id=free_plan_id,
            is_active=bool(row.get("is_active", True)),
            is_email_verified=bool(row.get("is_email_verified", False)),
        )
        sess.add(u)
        inserted = True
    else:
        # Update essentials; always set plan to free as requested
        u.first_name = (row.get("first_name") or u.first_name or "").strip()
        u.last_name = (row.get("last_name") or u.last_name or "").strip()
        if row.get("password_hash"):
            u.password_hash = row["password_hash"]
        u.role = normalize_role(row.get("role"))
        u.plan_id = free_plan_id
        u.is_active = bool(row.get("is_active", u.is_active))
        u.is_email_verified = bool(row.get("is_email_verified", u.is_email_verified))

    # Optionally ensure a usage row exists (period_start=today) without touching counts
    if create_usage and u.id:
        existing = (
            sess.query(UsageTracking)
            .filter(UsageTracking.user_id == u.id)
            .order_by(UsageTracking.period_start.desc())
            .first()
        )
        if existing is None:
            sess.add(
                UsageTracking(
                    user_id=u.id,
                    period_start=date.today(),
                    uploads_count=0,
                    assessments_count=0,
                    asked_questions_count=0,
                    flash_card_sets_count=0,
                )
            )

    return inserted


def iterate_old_users(conn_old: Engine, table: str, batch_size: int):
    # Try to fetch a safe column subset; ignore missing columns via COALESCE/NULL
    cols = [
        "id",
        "email",
        "password_hash",
        "first_name",
        "last_name",
        "role",
        "is_active",
        "is_email_verified",
        "created_at",
    ]
    select_list = ", ".join([f"{c}" for c in cols if c])
    sql = text(f"SELECT {select_list} FROM {table}")

    with conn_old.connect() as c:
        result: Result = c.execution_options(stream_results=True).execute(sql)
        while True:
            rows = result.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                payload = {k: row._mapping.get(k) for k in row._mapping.keys()}
                yield payload


def main():
    args = get_args()

    eng_old = connect(args.old_url)
    eng_new = connect(args.new_url)
    NewSession = sessionmaker(bind=eng_new, autoflush=False, autocommit=False, future=True)

    total = 0
    inserts = 0
    updates = 0

    with NewSession() as sess:
        free_plan_id = find_free_plan_id(
            sess,
            plan_id=args.free_plan_id,
            sku=args.free_plan_sku,
            name=args.free_plan_name,
        )

    with NewSession() as sess:
        try:
            for row in iterate_old_users(eng_old, args.old_users_table, args.batch_size):
                total += 1
                inserted = upsert_user(sess, free_plan_id, row, create_usage=True)
                inserts += 1 if inserted else 0
                updates += 0 if inserted else 1

                # Commit periodically to keep memory/locks healthy
                if total % args.batch_size == 0 and not args.dry_run:
                    sess.commit()

            if args.dry_run:
                sess.rollback()
            else:
                sess.commit()
        finally:
            pass

    print("--- Migration summary ---")
    print(f"Total scanned: {total}")
    print(f"Inserted:      {inserts}")
    print(f"Updated:       {updates}")
    if args.dry_run:
        print("(Dry run: no changes were written)")


if __name__ == "__main__":
    main()
