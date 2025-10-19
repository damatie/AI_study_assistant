"""
Remove demo flash card sets that were seeded for development (metadata->>'source' = 'seed:dev').

Usage:
  python -m app.scripts.prune_flash_card_sets --url postgresql+psycopg2://... --dry-run
  python -m app.scripts.prune_flash_card_sets --url postgresql+psycopg2://...
"""

from __future__ import annotations

import argparse
import os
from typing import List, Dict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


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
    p = argparse.ArgumentParser(description="Prune demo flash card sets (seed:dev)")
    p.add_argument("--url", default=os.getenv("DATABASE_URL") or os.getenv("NEW_DATABASE_URL"), help="Target DB URL")
    p.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    return p.parse_args()


def prune(eng: Engine, dry_run: bool) -> Dict:
    deleted_ids: List[str] = []
    with eng.begin() as conn:
        res = conn.execute(text("""
            SELECT id::text
            FROM flash_card_sets
            WHERE metadata->>'source' = :src
        """), {"src": "seed:dev"})
        ids = [r[0] for r in res.fetchall()]

        if not dry_run and ids:
            # Delete one by one to avoid array binding quirks across drivers
            for i in ids:
                conn.execute(text("DELETE FROM flash_card_sets WHERE id = :id"), {"id": i})
                deleted_ids.append(i)

    return {
        "matched": len(ids),
        "deleted": len(deleted_ids) if not dry_run else 0,
        "deleted_ids": deleted_ids if not dry_run else [],
        "dry_run": dry_run,
    }


def main():
    args = get_args()
    if not args.url:
        raise SystemExit("--url or NEW_DATABASE_URL/DATABASE_URL must be provided")
    eng = create_engine(_normalize_sync_url(args.url), future=True)
    out = prune(eng, args.dry_run)
    print("--- Prune flash_card_sets summary ---")
    for k, v in out.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
