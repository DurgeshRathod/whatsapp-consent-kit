"""Recording consent. Every change is a new consent_events row; consent_state follows by trigger."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatsapp_consent.enums import ConsentAction, ConsentSource, GivenBy
from whatsapp_consent.models import ConsentEvent, ConsentEvidence, ConsentState, Notice, Purpose, Subject

USER_AGENT_MAX = 500


class UnknownPurpose(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    """What the person's device told us when they acted. Deleted on erasure."""

    ip: str | None
    user_agent: str | None


def active_purposes(db: Session) -> list[Purpose]:
    return list(db.scalars(select(Purpose).where(Purpose.is_active).order_by(Purpose.position)))


def purpose_codes(db: Session) -> list[str]:
    return [p.code for p in active_purposes(db)]


def current_consent(db: Session, subject_id: uuid.UUID) -> dict[str, bool]:
    consent = dict.fromkeys(purpose_codes(db), False)
    rows = db.execute(
        select(ConsentState.purpose_code, ConsentState.granted).where(ConsentState.subject_id == subject_id)
    )
    for purpose, granted in rows:
        if purpose in consent:
            consent[purpose] = granted
    return consent


def given_by_for(subject: Subject) -> GivenBy:
    """GUARDIAN only for a minor with guardian details on record; otherwise the person acting is SELF."""
    return GivenBy.GUARDIAN if subject.is_minor and subject.guardian_name else GivenBy.SELF


def record_event(
    db: Session,
    subject: Subject,
    *,
    purpose: str,
    action: ConsentAction,
    source: ConsentSource,
    given_by: GivenBy,
    notice: Notice | None = None,
    evidence: Evidence | None = None,
    now: datetime | None = None,
) -> ConsentEvent:
    event = ConsentEvent(
        owner_id=subject.owner_id,
        subject_id=subject.id,
        phone_fingerprint=subject.phone_fingerprint,
        purpose_code=purpose,
        action=action,
        source=source,
        given_by=given_by,
        notice_version=notice.version if notice else None,
        notice_language=notice.language if notice else None,
        verified_at_time=subject.phone_verified_at is not None,
        occurred_at=now or datetime.now(UTC),
    )
    db.add(event)
    db.flush()
    if evidence is not None:
        user_agent = (evidence.user_agent or "")[:USER_AGENT_MAX] or None
        db.add(ConsentEvidence(event_id=event.id, ip=evidence.ip, user_agent=user_agent))
        db.flush()
    return event


def set_consent(
    db: Session,
    subject: Subject,
    wanted: dict[str, bool],
    *,
    source: ConsentSource,
    given_by: GivenBy,
    notice: Notice | None = None,
    evidence: Evidence | None = None,
    now: datetime | None = None,
) -> list[ConsentEvent]:
    """Brings a subject's consent to `wanted`, recording only changes, except that a consent page submission
    records every ticked purpose again: it is a fresh statement against the notice shown."""
    current = current_consent(db, subject.id)
    events = []
    for purpose, granted in wanted.items():
        if purpose not in current:
            raise UnknownPurpose(purpose)
        if current[purpose] != granted or (granted and source == ConsentSource.CONSENT_PAGE):
            action = ConsentAction.GRANTED if granted else ConsentAction.WITHDRAWN
            events.append(
                record_event(
                    db,
                    subject,
                    purpose=purpose,
                    action=action,
                    source=source,
                    given_by=given_by,
                    notice=notice,
                    evidence=evidence,
                    now=now,
                )
            )
    return events
