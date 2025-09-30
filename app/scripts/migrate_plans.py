"""
One-time Plans migration tool

Goals:
- Read plans from OLD DB and upsert into NEW DB
- Preserve NEW schema invariants (sku uniqueness, new fields)
- Update limits and enums on existing plans matched by name (case-insensitive)
- Insert any missing plans with generated SKU; set new-only fields to safe defaults

Notes:
- OLD had price_pence on plans; NEW uses plan_prices. We do NOT migrate price_pence here.
- NEW has extra fields monthly_flash_cards_limit, max_cards_per_deck.
- For existing plans, we do not modify sku/monthly_flash_cards_limit/max_cards_per_deck unless inserting a new plan.

Usage:
  python -m app.scripts.migrate_plans \
    --old-url "postgresql+psycopg2://.../olddb" \
    --new-url "postgresql+psycopg2://.../newdb" \
    --dry-run

Environment variables OLD_DATABASE_URL / NEW_DATABASE_URL are respected if args omitted.
"""

from __future__ import annotations

import argparse
import os
import re
from typing import Optional

from sqlalchemy import create_engine, text, select
from sqlalchemy.engine import Engine, Result
from sqlalchemy.orm import Session, sessionmaker

from app.models.plan import Plan, SummaryDetail, AIFeedbackLevel


def _normalize_sync_url(url: str) -> str:
    if not url:
        return url
    u = url.strip()
    if u.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + u.split("postgresql+asyncpg://", 1)[1]
    if u.startswith("postgresql+psycopg://"):
        return "postgresql+psycopg2://" + u.split("postgresql+psycopg://", 1)[1]
    return u


def connect(url: str) -> Engine:
    if not url:
        raise SystemExit("Database URL is required")
    return create_engine(_normalize_sync_url(url), future=True)


def get_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate plans from OLD DB to NEW DB")
    p.add_argument("--old-url", default=os.getenv("OLD_DATABASE_URL"))
    p.add_argument("--new-url", default=os.getenv("NEW_DATABASE_URL"))
    p.add_argument("--old-plans-table", default=os.getenv("OLD_PLANS_TABLE", "plans"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--exclude-name",
        action="append",
        default=[s.strip() for s in (os.getenv("EXCLUDE_PLAN_NAMES", "").split(",")) if s.strip()],
        help="Plan name(s) to exclude from migration (can be provided multiple times)",
    )
    return p.parse_args()


def _sku_from_name(name: str) -> str:
    n = (name or "").strip()
    lower = n.lower()
    # Known mappings
    if lower in ("free", "freemium"):
        return "FREEMIUM"
    if lower == "standard":
        return "STANDARD"
    if lower == "premium":
        return "PREMIUM"
    # Generic: alnum+underscore, uppercased, max 64
    base = re.sub(r"[^0-9a-zA-Z]+", "_", n).strip("_")
    return (base.upper() or "PLAN")[0:64]


def _alias_name(name: str) -> Optional[str]:
    if not name:
        return None
    lower = name.strip().lower()
    if lower == "free":
        return "Freemium"
    return None


def _parse_enum(enum_cls, value: str):
    if value is None:
        return None
    s = str(value).strip().lower()
    for member in enum_cls:
        if member.value == s:
            return member
    # try name matching
    for member in enum_cls:
        if member.name.lower() == s:
            return member
    raise ValueError(f"Unknown enum value '{value}' for {enum_cls.__name__}")


def migrate_plans(old_eng: Engine, new_eng: Engine, table: str, dry_run: bool, exclude_names: list[str] | None = None) -> dict:
    OldRow = dict
    scanned = inserted = updated = 0

    NewSession = sessionmaker(bind=new_eng, autoflush=False, autocommit=False, future=True)
    with new_eng.connect() as _c:
        pass  # verify connectivity early

    # Read all OLD plans at once (small set); fallback if columns differ
    select_sql = text(
        f"""
        SELECT name, monthly_upload_limit, pages_per_upload_limit, monthly_assessment_limit,
               questions_per_assessment, monthly_ask_question_limit,
               summary_detail, ai_feedback_level
        FROM {table}
        """
    )

    with old_eng.connect() as oc:
        res: Result = oc.execute(select_sql)
        rows = [dict(r._mapping) for r in res.fetchall()]

    excludes = set([e.strip().lower() for e in (exclude_names or []) if e])

    with NewSession() as sess:
        for row in rows:
            scanned += 1
            name = (row.get("name") or "").strip()
            if not name:
                continue

            if name.lower() in excludes:
                # Skip excluded plan
                continue

            # Try direct name match
            plan = sess.execute(select(Plan).where(Plan.name.ilike(name))).scalars().first()
            # Try alias (e.g., "Free" -> "Freemium")
            if not plan:
                alias = _alias_name(name)
                if alias:
                    plan = sess.execute(select(Plan).where(Plan.name.ilike(alias))).scalars().first()

            if plan:
                # Update mutable fields only
                changed = False
                def set_if_diff(obj, attr, val):
                    nonlocal changed
                    cur = getattr(obj, attr)
                    if cur != val:
                        setattr(obj, attr, val)
                        changed = True

                set_if_diff(plan, "monthly_upload_limit", int(row["monthly_upload_limit"]))
                set_if_diff(plan, "pages_per_upload_limit", int(row["pages_per_upload_limit"]))
                set_if_diff(plan, "monthly_assessment_limit", int(row["monthly_assessment_limit"]))
                set_if_diff(plan, "questions_per_assessment", int(row["questions_per_assessment"]))
                set_if_diff(plan, "monthly_ask_question_limit", int(row["monthly_ask_question_limit"]))
                # Enums
                try:
                    sd = _parse_enum(SummaryDetail, row["summary_detail"])  # may raise
                    set_if_diff(plan, "summary_detail", sd)
                except Exception:
                    pass
                try:
                    afl = _parse_enum(AIFeedbackLevel, row["ai_feedback_level"])  # may raise
                    set_if_diff(plan, "ai_feedback_level", afl)
                except Exception:
                    pass

                if changed:
                    if not dry_run:
                        sess.add(plan)
                    updated += 1
            else:
                # Insert new plan with generated SKU and safe defaults for new-only fields
                sku = _sku_from_name(name)
                # Ensure SKU uniqueness
                base = sku
                i = 1
                while sess.execute(select(Plan).where(Plan.sku == sku)).scalars().first():
                    i += 1
                    sku = f"{base}_{i}"

                try:
                    sd = _parse_enum(SummaryDetail, row.get("summary_detail")) or SummaryDetail.limited_detail
                except Exception:
                    sd = SummaryDetail.limited_detail
                try:
                    afl = _parse_enum(AIFeedbackLevel, row.get("ai_feedback_level")) or AIFeedbackLevel.basic
                except Exception:
                    afl = AIFeedbackLevel.basic

                new_plan = Plan(
                    name=name,
                    sku=sku,
                    monthly_upload_limit=int(row.get("monthly_upload_limit", 0) or 0),
                    pages_per_upload_limit=int(row.get("pages_per_upload_limit", 0) or 0),
                    monthly_assessment_limit=int(row.get("monthly_assessment_limit", 0) or 0),
                    questions_per_assessment=int(row.get("questions_per_assessment", 0) or 0),
                    monthly_ask_question_limit=int(row.get("monthly_ask_question_limit", 0) or 0),
                    monthly_flash_cards_limit=0,
                    max_cards_per_deck=0,
                    summary_detail=sd,
                    ai_feedback_level=afl,
                )
                if not dry_run:
                    sess.add(new_plan)
                inserted += 1

        if dry_run:
            sess.rollback()
        else:
            sess.commit()

    return {
        "scanned": scanned,
        "inserted": inserted,
        "updated": updated,
        "dry_run": dry_run,
    }


def main() -> None:
    args = get_args()
    eng_old = connect(args.old_url)
    eng_new = connect(args.new_url)
    stats = migrate_plans(eng_old, eng_new, args.old_plans_table, args.dry_run, args.exclude_name)
    print("--- Plans migration summary ---")
    for k, v in stats.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
