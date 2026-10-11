"""Erasure and retention. Personal data goes at once; anonymised evidence stays for the retention period."""

import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Executable, and_, delete, exists, func, or_, select, text
from sqlalchemy.orm import Session

from whatsapp_consent.enums import ConsentSource, DataRequestSource, InviteKind, SubjectSource
from whatsapp_consent.events import given_by_for, purpose_codes, set_consent
from whatsapp_consent.models import (
    ConsentEvent,
    ConsentEvidence,
    DataRequest,
    Invite,
    PreferenceLink,
    RateLimitEvent,
    Subject,
    Verification,
)
from whatsapp_consent.preference_links import revoke_preference_links

ErasureHook = Callable[[Session, uuid.UUID, uuid.UUID], None]

EVENT_SOURCE = {
    DataRequestSource.SUBJECT: ConsentSource.PREFERENCES_PAGE,
    DataRequestSource.OWNER: ConsentSource.OWNER_RECORDED,
    DataRequestSource.SYSTEM: ConsentSource.SYSTEM,
}
UNVERIFIED_SIGNUP_TTL = timedelta(days=30)
SHORT_LIVED_TTL = timedelta(days=2)  # verification codes and rate-limit hits
SPENT_INVITE_TTL = timedelta(days=30)


def erase_subject(
    db: Session,
    subject: Subject,
    *,
    source: DataRequestSource,
    on_erased: ErasureHook | None = None,
    now: datetime | None = None,
) -> DataRequest:
    """Withdraws all consent, lets the host delete its own data, deletes the subject and its device evidence, and
    keeps the anonymised history. One transaction (the caller commits). Expires the session afterwards."""
    now = now or datetime.now(UTC)
    subject_id, owner_id = subject.id, subject.owner_id
    phone, fingerprint = subject.phone_e164, subject.phone_fingerprint
    request = DataRequest(
        owner_id=owner_id, subject_id=subject_id, phone_fingerprint=fingerprint, source=source, requested_at=now
    )
    db.add(request)
    db.flush()
    set_consent(
        db,
        subject,
        dict.fromkeys(purpose_codes(db), False),
        source=EVENT_SOURCE[source],
        given_by=given_by_for(subject),
        now=now,
    )
    if on_erased is not None:
        on_erased(db, owner_id, subject_id)

    subject_events = select(ConsentEvent.id).where(ConsentEvent.subject_id == subject_id)
    db.execute(delete(ConsentEvidence).where(ConsentEvidence.event_id.in_(subject_events)))
    # Verification rows can hold a held submission (raw IP and user agent in `pending`): none may outlive the person.
    db.execute(delete(Verification).where(Verification.subject_id == subject_id))
    db.expunge(subject)
    # ON DELETE SET NULL clears subject_id on consent_events and data_requests; consent_state and invites cascade.
    db.execute(delete(Subject).where(Subject.id == subject_id))
    if not db.scalar(select(func.count()).select_from(Subject).where(Subject.phone_e164 == phone)):
        # Phone-only rows (no subject) belong to nobody now, unless another owner's subject has the same number.
        db.execute(delete(Verification).where(Verification.subject_id.is_(None), Verification.phone_e164 == phone))
    if not db.scalar(select(func.count()).select_from(Subject).where(Subject.phone_fingerprint == fingerprint)):
        revoke_preference_links(db, fingerprint, now)
    request.completed_at = now
    db.flush()
    db.expire_all()
    return request


def _erase_all(
    db: Session,
    subject_ids: list[uuid.UUID],
    source: DataRequestSource,
    on_erased: ErasureHook | None,
    now: datetime | None,
) -> int:
    for subject_id in subject_ids:
        subject = db.get(Subject, subject_id)
        if subject is not None:
            erase_subject(db, subject, source=source, on_erased=on_erased, now=now)
    return len(subject_ids)


def erase_owner(
    db: Session, owner_id: uuid.UUID, *, on_erased: ErasureHook | None = None, now: datetime | None = None
) -> int:
    """For when an owner closes their account."""
    ids = list(db.scalars(select(Subject.id).where(Subject.owner_id == owner_id)))
    return _erase_all(db, ids, DataRequestSource.OWNER, on_erased, now)


def cleanup_unverified(db: Session, *, on_erased: ErasureHook | None = None, now: datetime | None = None) -> int:
    """Practice-link sign-ups never verified within 30 days are probably mistakes or spam."""
    now = now or datetime.now(UTC)
    ids = list(
        db.scalars(
            select(Subject.id).where(
                Subject.source == SubjectSource.PRACTICE_LINK,
                Subject.phone_verified_at.is_(None),
                Subject.created_at < now - UNVERIFIED_SIGNUP_TTL,
                ~exists().where(Verification.subject_id == Subject.id, Verification.expires_at > now),
            )
        )
    )
    return _erase_all(db, ids, DataRequestSource.SYSTEM, on_erased, now)


@dataclass
class PurgeResult:
    consent_events: int
    data_requests: int
    verifications: int
    rate_limit_events: int
    invites: int
    preference_links: int


def _deleted(db: Session, statement: Executable) -> int:
    return db.execute(statement).rowcount  # pyright: ignore[reportAttributeAccessIssue]


def purge(db: Session, *, retention_days: int, now: datetime | None = None) -> PurgeResult:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)
    db.execute(text("SELECT set_config('whatsapp_consent.allow_purge', 'on', true)"))
    # An event stays while the erasure request that anonymised it is itself within the retention period.
    recent_request = exists().where(
        DataRequest.owner_id == ConsentEvent.owner_id,
        DataRequest.phone_fingerprint == ConsentEvent.phone_fingerprint,
        DataRequest.completed_at >= cutoff,
    )
    events = _deleted(
        db,
        delete(ConsentEvent).where(
            ConsentEvent.subject_id.is_(None), ConsentEvent.occurred_at < cutoff, ~recent_request
        ),
    )
    requests = _deleted(
        db, delete(DataRequest).where(DataRequest.subject_id.is_(None), DataRequest.completed_at < cutoff)
    )
    db.execute(text("SELECT set_config('whatsapp_consent.allow_purge', 'off', true)"))
    verifications = _deleted(db, delete(Verification).where(Verification.created_at < now - SHORT_LIVED_TTL))
    rate_limit_events = _deleted(db, delete(RateLimitEvent).where(RateLimitEvent.created_at < now - SHORT_LIVED_TTL))
    spent = now - SPENT_INVITE_TTL
    invites = _deleted(
        db,
        delete(Invite).where(
            or_(
                Invite.revoked_at < spent,
                Invite.used_at < spent,
                and_(Invite.kind == InviteKind.PERSONAL, Invite.expires_at < spent),
            )
        ),
    )
    links = _deleted(
        db, delete(PreferenceLink).where(or_(PreferenceLink.revoked_at.is_not(None), PreferenceLink.expires_at < now))
    )
    db.flush()
    return PurgeResult(events, requests, verifications, rate_limit_events, invites, links)


def run_maintenance(
    db: Session, *, retention_days: int, on_erased: ErasureHook | None = None, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    removed = cleanup_unverified(db, on_erased=on_erased, now=now)
    return {"unverified_signups_removed": removed} | asdict(purge(db, retention_days=retention_days, now=now))


def run_purge_only(db: Session, *, retention_days: int, now: datetime | None = None) -> dict[str, int]:
    return asdict(purge(db, retention_days=retention_days, now=now))
