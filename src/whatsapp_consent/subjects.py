import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whatsapp_consent.enums import Language, SubjectSource
from whatsapp_consent.hashing import phone_fingerprint
from whatsapp_consent.models import Subject


class DuplicateSubject(Exception):
    pass


def find_subject(db: Session, owner_id: uuid.UUID, phone_e164: str, display_name: str) -> Subject | None:
    """A subject is identified by owner, number and name, so a parent can register several children."""
    return db.scalar(
        select(Subject).where(
            Subject.owner_id == owner_id,
            Subject.phone_e164 == phone_e164,
            func.lower(Subject.display_name) == display_name.lower(),
        )
    )


def create_subject(
    db: Session,
    *,
    owner_id: uuid.UUID,
    display_name: str,
    phone_e164: str,
    birthday_day: int | None,
    birthday_month: int | None,
    is_minor: bool,
    language: Language,
    key: str,
) -> Subject:
    if find_subject(db, owner_id, phone_e164, display_name) is not None:
        raise DuplicateSubject(display_name)
    subject = Subject(
        owner_id=owner_id,
        display_name=display_name,
        phone_e164=phone_e164,
        phone_fingerprint=phone_fingerprint(key, phone_e164),
        birthday_day=birthday_day,
        birthday_month=birthday_month,
        is_minor=is_minor,
        source=SubjectSource.PERSONAL_LINK,
        language=language,
    )
    db.add(subject)
    db.flush()
    return subject
