"""What happens when a person submits the consent page, and when their number is then verified.

Nothing about an existing subject changes when the form is submitted: the submission is held on the verification
row(s) it starts (`Verification.pending`) and applied by `complete_consent_verification` once the number is proven.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from whatsapp_consent.enums import ConsentSource, GivenBy, InviteKind, Language, SubjectSource
from whatsapp_consent.events import Evidence, purpose_codes, set_consent
from whatsapp_consent.hashing import phone_fingerprint
from whatsapp_consent.models import Invite, Notice, Subject, Verification
from whatsapp_consent.subjects import find_subject


class MinorNeedsGuardian(Exception):
    """An adult ("me") submission for a subject recorded as under 18: a parent or guardian must fill it in."""


class DuplicateSubject(Exception):
    """The submitted name and number already belong to another of the owner's subjects."""


class InviteAlreadyUsed(LookupError):
    """The personal invite a held submission came through has been completed meanwhile."""


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

    def to_pending(self) -> dict[str, Any]:
        return {
            "for_child": self.for_child,
            "display_name": self.display_name,
            "phone": self.phone,
            "birthday_day": self.birthday_day,
            "birthday_month": self.birthday_month,
            "guardian_name": self.guardian_name,
            "guardian_relation": self.guardian_relation,
            "purposes": sorted(self.purposes),
            "language": self.language.value,
        }

    @classmethod
    def from_pending(cls, data: dict[str, Any]) -> "ConsentSubmission":
        return cls(
            for_child=bool(data["for_child"]),
            display_name=data["display_name"],
            phone=data["phone"],
            birthday_day=data["birthday_day"],
            birthday_month=data["birthday_month"],
            guardian_name=data["guardian_name"],
            guardian_relation=data["guardian_relation"],
            purposes=frozenset(data["purposes"]),
            language=Language(data["language"]),
        )


def pending_payload(
    *, invite: Invite, submission: ConsentSubmission, notice: Notice, evidence: Evidence, key: str
) -> dict[str, Any]:
    """What a verification row carries until it succeeds (deleted with the row after 2 days).

    Each call is a new hold with a fresh `hold_id`; the typed and click rows of one submission share it.
    """
    return {
        "hold_id": str(uuid.uuid4()),
        "invite_id": str(invite.id),
        "submission": submission.to_pending(),
        "phone_fingerprint": phone_fingerprint(key, submission.phone),
        "notice_version": notice.version,
        "notice_language": notice.language.value,
        "ip": evidence.ip,
        "user_agent": evidence.user_agent,
    }


def hold_consent(
    db: Session,
    *,
    invite: Invite,
    submission: ConsentSubmission,
    notice: Notice,
    evidence: Evidence,
    key: str,
    now: datetime | None = None,
) -> Subject:
    """Finds the subject a submission is for, creating a bare unverified practice-link sign-up if there is none.

    Changes nothing on an existing subject. The caller holds `pending_payload(...)` on the verifications it starts.
    Raises `MinorNeedsGuardian` for an adult submission on a minor and `DuplicateSubject` when a personal invite's
    new name and number already belong to another subject.
    """
    if invite.kind == InviteKind.PERSONAL:
        subject = db.get(Subject, invite.subject_id)
        if subject is None:
            raise LookupError("The invite's subject no longer exists")
        if subject.is_minor and not submission.for_child:
            raise MinorNeedsGuardian
        other = find_subject(db, invite.owner_id, submission.phone, submission.display_name)
        if other is not None and other.id != subject.id:
            raise DuplicateSubject
        return subject
    subject = find_subject(db, invite.owner_id, submission.phone, submission.display_name)
    if subject is not None:
        if subject.is_minor and not submission.for_child:
            raise MinorNeedsGuardian
        return subject
    subject = Subject(
        owner_id=invite.owner_id,
        source=SubjectSource.PRACTICE_LINK,
        needs_review=True,
        display_name=submission.display_name,
        phone_e164=submission.phone,
        phone_fingerprint=phone_fingerprint(key, submission.phone),
    )
    db.add(subject)
    db.flush()
    return subject


def complete_consent_verification(db: Session, verification: Verification, now: datetime | None = None) -> Subject:
    """Called after a typed code or a click-to-chat message proves `verification.phone_e164`.

    Applies the submission held on the row (if any). Raises `InviteAlreadyUsed` when its personal invite was
    completed meanwhile, and `LookupError` when the subject, invite or notice is gone or no longer matches.
    """
    # Locks the subject with populate_existing, which reloads it from the database: a caller must flush its own
    # edits to the subject before calling this, or they are discarded.
    now = now or datetime.now(UTC)
    # Completions for one subject run one at a time: lock the subject, then re-read this row's held submission
    # inside the lock, so a typed and a click-to-chat completion racing each other apply it at most once.
    subject = (
        db.get(Subject, verification.subject_id, with_for_update=True, populate_existing=True)
        if verification.subject_id
        else None
    )
    if subject is None:
        raise LookupError("The verification's subject no longer exists")
    db.refresh(verification, with_for_update=True)
    held = verification.pending
    if held is not None and _already_applied(db, verification, held):
        verification.pending = held = None
    if held is None:
        if subject.phone_e164 != verification.phone_e164:
            raise LookupError("The verified number is not the subject's number")
        subject.phone_verified_at = now
    else:
        _apply_held(db, subject, verification, held, now)
        # Only the invite this submission came through is spent; a newer link the owner issued stays usable.
        db.execute(
            update(Invite)
            .where(
                Invite.id == uuid.UUID(held["invite_id"]),
                Invite.kind == InviteKind.PERSONAL,
                Invite.used_at.is_(None),
                Invite.revoked_at.is_(None),
            )
            .values(used_at=now)
        )
    db.flush()
    return subject


def _already_applied(db: Session, verification: Verification, held: dict[str, Any]) -> bool:
    """This very submission was already applied through its sibling row (the typed or the click-to-chat one)."""
    hold_id = held.get("hold_id")
    if hold_id is None:
        return False
    applied = db.scalar(
        select(Verification.id)
        .where(
            Verification.subject_id == verification.subject_id,
            Verification.applied_hold_id == uuid.UUID(hold_id),
        )
        .limit(1)
    )
    return applied is not None


def _apply_held(db: Session, subject: Subject, verification: Verification, held: dict[str, Any], now: datetime) -> None:
    submission = ConsentSubmission.from_pending(held["submission"])
    invite = db.get(Invite, uuid.UUID(held["invite_id"]))
    if invite is None or invite.revoked_at is not None or invite.owner_id != subject.owner_id:
        raise LookupError("The invite is no longer valid")
    if invite.kind == InviteKind.PERSONAL:
        if invite.subject_id != subject.id:
            raise LookupError("The invite is for another subject")
        if invite.used_at is not None:
            raise InviteAlreadyUsed("The invite has already been completed")
    if submission.phone != verification.phone_e164:
        raise LookupError("The held submission is for another number")
    if subject.is_minor and not submission.for_child:
        raise LookupError("The subject is recorded as a minor; a guardian must consent")
    notice = db.scalar(
        select(Notice).where(
            Notice.version == held["notice_version"], Notice.language == Language(held["notice_language"])
        )
    )
    if notice is None:
        raise LookupError("The notice the person was shown no longer exists")

    if subject.phone_e164 != submission.phone:
        subject.phone_e164 = submission.phone
        subject.phone_fingerprint = held["phone_fingerprint"]
    subject.display_name = submission.display_name
    subject.birthday_day = submission.birthday_day
    subject.birthday_month = submission.birthday_month
    subject.language = submission.language
    subject.is_minor = subject.is_minor or submission.for_child  # public pages never clear is_minor
    if submission.for_child:
        subject.guardian_name = submission.guardian_name
        subject.guardian_relation = submission.guardian_relation
    if subject.is_minor:
        # Lifted only by a guardian's submission verified now; otherwise the minor stays suspended.
        subject.minor_suspended = not (submission.for_child and subject.guardian_name is not None)
    subject.phone_verified_at = now
    db.flush()
    set_consent(
        db,
        subject,
        {code: code in submission.purposes for code in purpose_codes(db)},
        source=ConsentSource.CONSENT_PAGE,
        given_by=GivenBy.GUARDIAN if submission.for_child else GivenBy.SELF,
        notice=notice,
        evidence=Evidence(held.get("ip"), held.get("user_agent")),
        now=now,
    )
    # A proven submission supersedes every other held one for the subject. A row locked by a completion running
    # right now is skipped (waiting could deadlock); that completion finds this hold_id applied once it gets the lock.
    others = db.scalars(
        select(Verification)
        .where(
            Verification.subject_id == subject.id,
            Verification.purpose == verification.purpose,
            Verification.id != verification.id,
            Verification.verified_at.is_(None),
            Verification.pending.is_not(None),
        )
        .with_for_update(skip_locked=True)
    )
    for row in others:
        row.pending = None
    verification.pending = None
    verification.applied_hold_id = uuid.UUID(held["hold_id"])
