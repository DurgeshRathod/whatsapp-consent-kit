"""Answering "where does this person stand?" for one subject, many, or a phone number."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from whatsapp_consent.enums import ConsentAction, Language, SendBlocker, SubjectStatus
from whatsapp_consent.events import purpose_codes
from whatsapp_consent.gate import send_blockers
from whatsapp_consent.hashing import phone_fingerprint
from whatsapp_consent.models import ConsentEvent, ConsentState, Subject
from whatsapp_consent.phones import normalise_phone


@dataclass(frozen=True)
class SubjectState:
    subject_id: uuid.UUID
    owner_id: uuid.UUID
    display_name: str
    status: SubjectStatus
    verified: bool
    consents: dict[str, bool]
    blockers: dict[str, list[SendBlocker]]
    last_changed_at: datetime | None
    notice_version: int | None  # the notice they last agreed against
    notice_language: Language | None

    def can_send(self, purpose: str) -> bool:
        return not self.blockers[purpose]


def subject_status(subject: Subject, consent: dict[str, bool], has_events: bool) -> SubjectStatus:
    if subject.needs_review:
        return SubjectStatus.NEEDS_REVIEW
    if subject.minor_suspended:
        return SubjectStatus.SUSPENDED
    if not has_events:
        return SubjectStatus.INVITED
    if subject.phone_verified_at is None:
        return SubjectStatus.UNVERIFIED
    return SubjectStatus.ACTIVE if any(consent.values()) else SubjectStatus.WITHDRAWN


def states_of(db: Session, subjects: list[Subject]) -> list[SubjectState]:
    """A few queries for any number of subjects."""
    if not subjects:
        return []
    ids = [s.id for s in subjects]
    codes = purpose_codes(db)
    consent = {subject_id: dict.fromkeys(codes, False) for subject_id in ids}
    rows = db.execute(
        select(ConsentState.subject_id, ConsentState.purpose_code, ConsentState.granted).where(
            ConsentState.subject_id.in_(ids)
        )
    )
    for subject_id, purpose, granted in rows:
        if purpose in consent[subject_id]:
            consent[subject_id][purpose] = granted
    latest = {
        row.subject_id: row.occurred_at
        for row in db.execute(
            select(ConsentEvent.subject_id, ConsentEvent.occurred_at)
            .where(ConsentEvent.subject_id.in_(ids))
            .order_by(ConsentEvent.subject_id, ConsentEvent.occurred_at.desc())
            .ext(distinct_on(ConsentEvent.subject_id))
        )
    }
    agreed = {
        row.subject_id: (row.notice_version, row.notice_language)
        for row in db.execute(
            select(ConsentEvent.subject_id, ConsentEvent.notice_version, ConsentEvent.notice_language)
            .where(
                ConsentEvent.subject_id.in_(ids),
                ConsentEvent.action == ConsentAction.GRANTED,
                ConsentEvent.notice_version.is_not(None),
            )
            .order_by(ConsentEvent.subject_id, ConsentEvent.occurred_at.desc())
            .ext(distinct_on(ConsentEvent.subject_id))
        )
    }
    states = []
    for subject in subjects:
        subject_consent = consent[subject.id]
        notice_version, notice_language = agreed.get(subject.id, (None, None))
        states.append(
            SubjectState(
                subject_id=subject.id,
                owner_id=subject.owner_id,
                display_name=subject.display_name,
                status=subject_status(subject, subject_consent, subject.id in latest),
                verified=subject.phone_verified_at is not None,
                consents=subject_consent,
                blockers={p: send_blockers(subject, granted) for p, granted in subject_consent.items()},
                last_changed_at=latest.get(subject.id),
                notice_version=notice_version,
                notice_language=notice_language,
            )
        )
    return states


def state_of(db: Session, subject: Subject) -> SubjectState:
    return states_of(db, [subject])[0]


def subjects_by_phone(db: Session, owner_id: uuid.UUID, phone: str, *, key: str) -> list[Subject]:
    """An owner's subjects on a number (a parent may have several children). Raises InvalidPhone."""
    fingerprint = phone_fingerprint(key, normalise_phone(phone))
    query = select(Subject).where(Subject.owner_id == owner_id, Subject.phone_fingerprint == fingerprint)
    return list(db.scalars(query.order_by(Subject.created_at)))


def status_by_phone(db: Session, owner_id: uuid.UUID, phone: str, *, key: str) -> list[SubjectState]:
    return states_of(db, subjects_by_phone(db, owner_id, phone, key=key))
