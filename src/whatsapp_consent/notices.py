from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatsapp_consent.enums import Language
from whatsapp_consent.models import Notice


def latest_notice(db: Session, language: Language, today: date | None = None) -> Notice:
    """The newest notice in effect for a language, falling back to English."""
    today = today or date.today()
    notice = db.scalar(
        select(Notice)
        .where(Notice.language == language, Notice.effective_from <= today)
        .order_by(Notice.version.desc())
        .limit(1)
    )
    if notice is None and language != Language.EN:
        return latest_notice(db, Language.EN, today)
    if notice is None:
        raise LookupError("No consent notice is in effect: load one with whatsapp_consent.library.load_notices")
    return notice
