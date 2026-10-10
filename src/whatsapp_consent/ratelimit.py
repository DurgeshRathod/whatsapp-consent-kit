"""Rate limits stored in Postgres, so they hold across replicas. Keys (IP addresses) are stored hashed."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whatsapp_consent.hashing import keyed_hash
from whatsapp_consent.models import RateLimitEvent


def allow(
    db: Session, *, bucket: str, key: str, limit: int, window: timedelta, hash_key: str, now: datetime | None = None
) -> bool:
    """Records a hit and returns True, or returns False if `limit` hits already happened within `window`."""
    now = now or datetime.now(UTC)
    key_hash = keyed_hash(hash_key, f"ratelimit:{bucket}", key)
    count = db.scalar(
        select(func.count())
        .select_from(RateLimitEvent)
        .where(
            RateLimitEvent.bucket == bucket,
            RateLimitEvent.key_hash == key_hash,
            RateLimitEvent.created_at > now - window,
        )
    )
    if (count or 0) >= limit:
        return False
    db.add(RateLimitEvent(bucket=bucket, key_hash=key_hash, created_at=now))
    db.flush()
    return True
