"""Full consent history, for answering a complaint. By phone it still works after erasure, within retention."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from whatsapp_consent.enums import ConsentAction, ConsentSource, DataRequestSource, GivenBy, Language
from whatsapp_consent.hashing import phone_fingerprint
from whatsapp_consent.models import ConsentEvent, DataRequest, Subject
from whatsapp_consent.phones import normalise_phone


@dataclass(frozen=True)
class AuditEvent:
    subject_id: uuid.UUID | None  # None once the subject was erased
    purpose: str
    action: ConsentAction
    source: ConsentSource
    given_by: GivenBy
    notice_version: int | None
    notice_language: Language | None
    verified_at_time: bool
    occurred_at: datetime


@dataclass(frozen=True)
class AuditRequest:
    source: DataRequestSource
    requested_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class AuditRecord:
    owner_id: uuid.UUID
    phone_fingerprint: str
    events: list[AuditEvent]
    erasure_requests: list[AuditRequest]


def _record(db: Session, owner_id: uuid.UUID, fingerprint: str, *event_filter: ColumnElement[bool]) -> AuditRecord:
    events = db.scalars(
        select(ConsentEvent).where(ConsentEvent.owner_id == owner_id, *event_filter).order_by(ConsentEvent.occurred_at)
    )
    requests = db.scalars(
        select(DataRequest)
        .where(DataRequest.owner_id == owner_id, DataRequest.phone_fingerprint == fingerprint)
        .order_by(DataRequest.requested_at)
    )
    return AuditRecord(
        owner_id=owner_id,
        phone_fingerprint=fingerprint,
        events=[
            AuditEvent(
                subject_id=e.subject_id,
                purpose=e.purpose_code,
                action=e.action,
                source=e.source,
                given_by=e.given_by,
                notice_version=e.notice_version,
                notice_language=e.notice_language,
                verified_at_time=e.verified_at_time,
                occurred_at=e.occurred_at,
            )
            for e in events
        ],
        erasure_requests=[AuditRequest(r.source, r.requested_at, r.completed_at) for r in requests],
    )


def audit_subject(db: Session, subject_id: uuid.UUID) -> AuditRecord | None:
    subject = db.get(Subject, subject_id)
    if subject is None:
        return None
    return _record(db, subject.owner_id, subject.phone_fingerprint, ConsentEvent.subject_id == subject_id)


def audit_phone(db: Session, owner_id: uuid.UUID, phone: str, *, key: str) -> AuditRecord:
    """Every event for this owner and number, including erased subjects (raises InvalidPhone)."""
    fingerprint = phone_fingerprint(key, normalise_phone(phone))
    return _record(db, owner_id, fingerprint, ConsentEvent.phone_fingerprint == fingerprint)
