"""What a verified number sees on its preferences page: every owner it consented to, separately."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatsapp_consent.events import current_consent
from whatsapp_consent.models import Subject


@dataclass(frozen=True)
class PreferenceEntry:
    subject: Subject
    business: str
    consent: dict[str, bool]


def preference_entries(
    db: Session, fingerprint: str, owner_lookup: Callable[[Session, Any], Any]
) -> list[PreferenceEntry]:
    subjects = db.scalars(
        select(Subject)
        .where(Subject.phone_fingerprint == fingerprint, Subject.phone_verified_at.is_not(None))
        .order_by(Subject.created_at)
    )
    entries = []
    for subject in subjects:
        info = owner_lookup(db, subject.owner_id)
        entries.append(
            PreferenceEntry(
                subject=subject, business=info.display_name if info else "", consent=current_consent(db, subject.id)
            )
        )
    return entries
