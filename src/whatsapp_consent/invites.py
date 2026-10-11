"""Consent links. Only token hashes are stored."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from whatsapp_consent.enums import InviteKind
from whatsapp_consent.hashing import derive_token, hash_token, new_token
from whatsapp_consent.models import Invite, Subject

PERSONAL_INVITE_TTL = timedelta(days=7)


@dataclass(frozen=True)
class IssuedLink:
    invite: Invite
    token: str


def issue_personal_invite(
    db: Session, subject: Subject, *, minor_only: bool, now: datetime | None = None
) -> IssuedLink:
    """A single-use link for one subject. Any earlier link for the subject stops working."""
    now = now or datetime.now(UTC)
    db.execute(
        update(Invite).where(Invite.subject_id == subject.id, Invite.revoked_at.is_(None)).values(revoked_at=now)
    )
    raw, digest = new_token()
    invite = Invite(
        owner_id=subject.owner_id,
        kind=InviteKind.PERSONAL,
        subject_id=subject.id,
        token_hash=digest,
        minor_only=minor_only,
        expires_at=now + PERSONAL_INVITE_TTL,
    )
    db.add(invite)
    db.flush()
    return IssuedLink(invite=invite, token=raw)


def practice_token(key: str, invite_id: uuid.UUID) -> str:
    return derive_token(key, "practice-invite", str(invite_id))


def _active_practice(db: Session, owner_id: uuid.UUID) -> Invite | None:
    return db.scalar(
        select(Invite).where(
            Invite.owner_id == owner_id, Invite.kind == InviteKind.PRACTICE, Invite.revoked_at.is_(None)
        )
    )


def get_practice_invite(db: Session, owner_id: uuid.UUID, *, key: str) -> IssuedLink:
    """The owner's reusable link, created on first use. Its token is derived, so it can be shown again."""
    invite = _active_practice(db, owner_id)
    if invite is None:
        invite_id = uuid.uuid4()
        invite = Invite(
            id=invite_id,
            owner_id=owner_id,
            kind=InviteKind.PRACTICE,
            token_hash=hash_token(practice_token(key, invite_id)),
        )
        db.add(invite)
        db.flush()
    return IssuedLink(invite=invite, token=practice_token(key, invite.id))


def rotate_practice_invite(db: Session, owner_id: uuid.UUID, *, key: str, now: datetime | None = None) -> IssuedLink:
    db.execute(
        update(Invite)
        .where(Invite.owner_id == owner_id, Invite.kind == InviteKind.PRACTICE, Invite.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )
    db.flush()
    return get_practice_invite(db, owner_id, key=key)


def resolve_invite(db: Session, raw_token: str, now: datetime | None = None) -> Invite | None:
    """The invite for a token unless revoked or expired. A used personal invite is returned; callers show
    "already confirmed"."""
    now = now or datetime.now(UTC)
    invite = db.scalar(select(Invite).where(Invite.token_hash == hash_token(raw_token)))
    if invite is None or invite.revoked_at is not None:
        return None
    if invite.expires_at is not None and invite.expires_at <= now:
        return None
    return invite
