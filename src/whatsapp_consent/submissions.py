"""What happens when a person submits the consent page, and when their number is then verified."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from whatsapp_consent.enums import ConsentSource, GivenBy, InviteKind, Language, SubjectSource
from whatsapp_consent.events import Evidence, purpose_codes, set_consent
from whatsapp_consent.hashing import phone_fingerprint
from whatsapp_consent.models import Invite, Notice, Subject
from whatsapp_consent.subjects import find_subject


@dataclass(frozen=True)
class ConsentSubmission:
    for_child: bool
    display_name: str  # the adult's name, or the child's first name
    phone: str  # E.164; the parent's number for a child
    birthday_day: int | None
    birthday_month: int | None
    guardian_name: str | None
    guardian_relation: str | None
    purposes: frozenset[str]
    language: Language


def submit_consent(
    db: Session,
    *,
    invite: Invite,
    submission: ConsentSubmission,
    notice: Notice,
    evidence: Evidence,
    key: str,
    now: datetime | None = None,
) -> Subject:
    """Creates or updates the subject and records consent, unverified until the number is verified."""
    fingerprint = phone_fingerprint(key, submission.phone)
    if invite.kind == InviteKind.PERSONAL:
        subject = db.get(Subject, invite.subject_id)
        if subject is None:
            raise LookupError("The invite's subject no longer exists")
    else:
        subject = find_subject(db, invite.owner_id, submission.phone, submission.display_name)
        if subject is None:
            subject = Subject(
                owner_id=invite.owner_id,
                source=SubjectSource.PRACTICE_LINK,
                needs_review=True,
                display_name=submission.display_name,
                phone_e164=submission.phone,
                phone_fingerprint=fingerprint,
            )
            db.add(subject)
    if subject.phone_e164 != submission.phone:
        subject.phone_e164 = submission.phone
        subject.phone_fingerprint = fingerprint
        subject.phone_verified_at = None  # a different number must be verified again
    subject.display_name = submission.display_name
    subject.birthday_day = submission.birthday_day
    subject.birthday_month = submission.birthday_month
    subject.is_minor = submission.for_child
    subject.guardian_name = submission.guardian_name if submission.for_child else None
    subject.guardian_relation = submission.guardian_relation if submission.for_child else None
    subject.language = submission.language
    db.flush()
    set_consent(
        db,
        subject,
        {code: code in submission.purposes for code in purpose_codes(db)},
        source=ConsentSource.CONSENT_PAGE,
        given_by=GivenBy.GUARDIAN if submission.for_child else GivenBy.SELF,
        notice=notice,
        evidence=evidence,
        now=now,
    )
    return subject


def complete_consent_verification(db: Session, subject: Subject, now: datetime | None = None) -> None:
    """Called after a typed code or a click-to-chat message proves the number."""
    now = now or datetime.now(UTC)
    subject.phone_verified_at = now
    if subject.is_minor and subject.guardian_name is not None:
        subject.minor_suspended = False  # a verified guardian has now consented
    db.execute(
        update(Invite)
        .where(
            Invite.subject_id == subject.id,
            Invite.kind == InviteKind.PERSONAL,
            Invite.used_at.is_(None),
            Invite.revoked_at.is_(None),
        )
        .values(used_at=now)
    )
    db.flush()
