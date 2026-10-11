"""The single check every outgoing message must pass."""

import uuid

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from whatsapp_consent.enums import SendBlocker, TemplateCategory
from whatsapp_consent.events import UnknownPurpose, active_purposes, current_consent, purpose_codes
from whatsapp_consent.models import ConsentState, Subject


class AmbiguousCategory(ValueError):
    pass


def send_blockers(subject: Subject, granted: bool) -> list[SendBlocker]:
    blockers: list[SendBlocker] = []
    if not granted:
        blockers.append(SendBlocker.NO_CONSENT)
    if subject.phone_verified_at is None:
        blockers.append(SendBlocker.UNVERIFIED)
    if subject.needs_review:
        blockers.append(SendBlocker.NEEDS_REVIEW)
    if subject.minor_suspended:
        blockers.append(SendBlocker.MINOR_SUSPENDED)
    return blockers


def can_send(db: Session, subject_id: uuid.UUID, purpose: str) -> bool:
    subject = db.get(Subject, subject_id)
    if subject is None:
        return False
    consent = current_consent(db, subject_id)
    if purpose not in consent:
        raise UnknownPurpose(purpose)
    return not send_blockers(subject, consent[purpose])


def can_send_category(db: Session, subject_id: uuid.UUID, category: TemplateCategory) -> bool:
    """By Meta category. AUTHENTICATION (codes the person asked for) is always allowed for a known subject."""
    if category == TemplateCategory.AUTHENTICATION:
        return db.get(Subject, subject_id) is not None
    codes = [p.code for p in active_purposes(db) if p.category == category]
    if len(codes) != 1:
        raise AmbiguousCategory(f"{len(codes)} active purposes use {category}; check by purpose instead")
    return can_send(db, subject_id, codes[0])


def sendable_subjects(db: Session, owner_id: uuid.UUID, purpose: str) -> list[uuid.UUID]:
    """Everyone an owner may message for a purpose right now (a campaign audience), in one query."""
    if purpose not in purpose_codes(db):
        raise UnknownPurpose(purpose)
    granted = and_(
        ConsentState.subject_id == Subject.id, ConsentState.purpose_code == purpose, ConsentState.granted.is_(True)
    )
    query = (
        select(Subject.id)
        .join(ConsentState, granted)
        .where(
            Subject.owner_id == owner_id,
            Subject.phone_verified_at.is_not(None),
            Subject.needs_review.is_(False),
            Subject.minor_suspended.is_(False),
        )
        .order_by(Subject.created_at, Subject.id)
    )
    return list(db.scalars(query))
