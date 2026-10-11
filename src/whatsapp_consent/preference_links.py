"""Private links to a verified number's preferences page."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from whatsapp_consent.config import ConsentSettings
from whatsapp_consent.hashing import hash_token, new_token
from whatsapp_consent.models import PreferenceLink

PREFERENCE_LINK_TTL = timedelta(days=365)


def issue_preference_link(db: Session, fingerprint: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    raw, digest = new_token()
    db.add(
        PreferenceLink(
            phone_fingerprint=fingerprint, token_hash=digest, expires_at=now + PREFERENCE_LINK_TTL, created_at=now
        )
    )
    db.flush()
    return raw


def resolve_preference_link(db: Session, raw_token: str, now: datetime | None = None) -> PreferenceLink | None:
    now = now or datetime.now(UTC)
    link = db.scalar(select(PreferenceLink).where(PreferenceLink.token_hash == hash_token(raw_token)))
    if link is None or link.revoked_at is not None or link.expires_at <= now:
        return None
    return link


def revoke_preference_links(db: Session, fingerprint: str, now: datetime | None = None) -> None:
    db.execute(
        update(PreferenceLink)
        .where(PreferenceLink.phone_fingerprint == fingerprint, PreferenceLink.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )


def preference_url(settings: ConsentSettings, token: str) -> str:
    return f"{settings.public_base_url}/p/{token}"
