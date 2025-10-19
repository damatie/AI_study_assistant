import asyncio
from datetime import datetime, timezone

from sqlalchemy import update

from app.core.logging_config import get_logger
from app.db.deps import AsyncSessionLocal
from app.models.transaction import Transaction
from app.utils.enums import TransactionStatus, TransactionStatusReason


logger = get_logger("ttl_expirer")


async def expire_stale_transactions_once() -> int:
    """Expire pending transactions whose expires_at has passed.

    Returns number of rows affected (best effort; depending on dialect).
    """
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        stmt = (
            update(Transaction)
            .where(Transaction.status == TransactionStatus.pending)
            .where(Transaction.expires_at.is_not(None))
            .where(Transaction.expires_at < now)
            .values(
                status=TransactionStatus.expired,
                status_reason=TransactionStatusReason.ttl_elapsed,
                status_message="Payment session expired",
            )
        )
        res = await db.execute(stmt)
        await db.commit()
        try:
            rowcount = res.rowcount if hasattr(res, "rowcount") else 0
        except Exception:
            rowcount = 0
        if rowcount:
            logger.info(f"TTL expirer: expired {rowcount} pending transactions")
        return rowcount


async def run_ttl_expirer_task(poll_seconds: int = 60):
    """Background loop: periodically expire stale pending transactions."""
    logger.info(f"Starting TTL expirer task (interval={poll_seconds}s)")
    try:
        while True:
            try:
                await expire_stale_transactions_once()
            except Exception as e:
                logger.exception(f"TTL expirer error: {e}")
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        logger.info("TTL expirer task cancelled; shutting down")
        raise
