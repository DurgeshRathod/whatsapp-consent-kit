from sqlalchemy.orm import Session

from whatsapp_consent.api.schemas import HistoryOut, SubjectDetailOut, SubjectOut
from whatsapp_consent.audit import audit_subject
from whatsapp_consent.models import Subject
from whatsapp_consent.status import SubjectState, states_of


def _subject_out(subject: Subject, state: SubjectState) -> SubjectOut:
    return SubjectOut(
        id=subject.id,
        display_name=subject.display_name,
        phone=subject.phone_e164,
        birthday_day=subject.birthday_day,
        birthday_month=subject.birthday_month,
        is_minor=subject.is_minor,
        guardian_name=subject.guardian_name,
        guardian_relation=subject.guardian_relation,
        source=subject.source,
        status=state.status,
        verified=state.verified,
        needs_review=subject.needs_review,
        minor_suspended=subject.minor_suspended,
        consents=state.consents,
        can_send={purpose: state.can_send(purpose) for purpose in state.consents},
        blockers=state.blockers,
        created_at=subject.created_at,
    )


def subjects_out(db: Session, subjects: list[Subject]) -> list[SubjectOut]:
    return [_subject_out(s, state) for s, state in zip(subjects, states_of(db, subjects), strict=True)]


def subject_out(db: Session, subject: Subject) -> SubjectOut:
    return subjects_out(db, [subject])[0]


def subject_detail_out(db: Session, subject: Subject) -> SubjectDetailOut:
    record = audit_subject(db, subject.id)
    history = [
        HistoryOut(
            purpose=e.purpose,
            action=e.action,
            source=e.source,
            given_by=e.given_by,
            notice_version=e.notice_version,
            notice_language=e.notice_language,
            verified_at_time=e.verified_at_time,
            occurred_at=e.occurred_at,
        )
        for e in (record.events if record else [])
    ]
    return SubjectDetailOut(**subject_out(db, subject).model_dump(), history=history)
