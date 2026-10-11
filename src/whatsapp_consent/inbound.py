"""Acting on what people send to an owner's WhatsApp number: STOP/START words, button taps, click-to-chat codes and
Meta's marketing opt-out signal. Text is matched in memory and never stored."""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from whatsapp_consent.config import ConsentSettings
from whatsapp_consent.enums import ConsentSource, InboundEventType, InboundOutcomeType, TemplateCategory
from whatsapp_consent.events import active_purposes, given_by_for, purpose_codes, set_consent
from whatsapp_consent.meta import InboundEvent
from whatsapp_consent.models import Subject
from whatsapp_consent.preference_links import issue_preference_link, preference_url
from whatsapp_consent.submissions import complete_consent_verification
from whatsapp_consent.verification import verify_click_code

STOP_WORDS = frozenset({"stop", "unsubscribe", "stop all", "रोकें", "बंद", "बंद करें"})
START_WORDS = frozenset({"start", "subscribe"})
CLICK_CODE = re.compile(r"^START-([A-HJ-NP-Z2-9]{6})$")
STOP_BUTTON_PREFIX = "whatsapp_consent:stop:"
MANAGE_BUTTON = "whatsapp_consent:manage"
# Meta's "this person stopped marketing messages" failure. Confirm the code(s) against Meta's documentation before
# relying on it in production (spec §15, open item 1).
MARKETING_OPT_OUT_ERROR_CODES = frozenset({131050})


@dataclass(frozen=True)
class InboundOutcome:
    type: InboundOutcomeType
    event: InboundEvent
    subject_ids: tuple[uuid.UUID, ...] = ()
    # For the host's reply inside the free 24-hour window ("You're unsubscribed. Manage: <link>").
    preferences_url: str | None = None


def normalise_words(text: str) -> str:
    """Lower-cased words with punctuation, symbols, emoji and invisible format characters treated as separators."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    spaced = "".join(
        " " if ch.isspace() or unicodedata.category(ch)[0] in "PS" or unicodedata.category(ch) == "Cf" else ch
        for ch in folded
    )
    return " ".join(spaced.split())


def _subjects(db: Session, owner_id: uuid.UUID, phone: str) -> list[Subject]:
    query = select(Subject).where(Subject.owner_id == owner_id, Subject.phone_e164 == phone)
    return list(db.scalars(query.order_by(Subject.created_at)))


def _complete_click(
    db: Session, owner_id: uuid.UUID, phone: str, code: str, settings: ConsentSettings, now: datetime
) -> Subject | None:
    """Verifies a click-to-chat code and applies its held submission, all or nothing (None: ignore the message)."""
    savepoint = db.begin_nested()
    try:
        verification = verify_click_code(db, phone=phone, code=code, key=settings.fingerprint_key, now=now)
        subject = db.get(Subject, verification.subject_id) if verification and verification.subject_id else None
        if verification is None or subject is None or subject.owner_id != owner_id:
            savepoint.rollback()
            return None
        subject = complete_consent_verification(db, verification, now)
    except (LookupError, IntegrityError):  # LookupError includes InviteAlreadyUsed
        savepoint.rollback()
        return None
    except BaseException:
        savepoint.rollback()
        raise
    savepoint.commit()
    return subject


def _withdraw(db: Session, subjects: list[Subject], purposes: list[str], source: ConsentSource, now: datetime) -> None:
    for subject in subjects:
        set_consent(db, subject, dict.fromkeys(purposes, False), source=source, given_by=given_by_for(subject), now=now)


def _outcome(
    db: Session,
    settings: ConsentSettings,
    kind: InboundOutcomeType,
    event: InboundEvent,
    subjects: list[Subject],
    *,
    with_link: bool,
) -> InboundOutcome:
    url = preference_url(settings, issue_preference_link(db, subjects[0].phone_fingerprint)) if with_link else None
    return InboundOutcome(kind, event, tuple(s.id for s in subjects), url)


def _handle(
    db: Session, owner_id: uuid.UUID, event: InboundEvent, settings: ConsentSettings, now: datetime
) -> InboundOutcome:
    ignored = InboundOutcome(InboundOutcomeType.IGNORED, event)
    if event.phone is None:
        return ignored

    if event.type == InboundEventType.TEXT and event.text:
        click = CLICK_CODE.fullmatch(event.text.strip().upper())
        if click:
            subject = _complete_click(db, owner_id, event.phone, click.group(1), settings, now)
            if subject is None:
                return ignored
            return InboundOutcome(InboundOutcomeType.VERIFIED, event, (subject.id,))
        subjects = _subjects(db, owner_id, event.phone)
        if not subjects:
            return ignored
        words = normalise_words(event.text)
        if words in STOP_WORDS:
            _withdraw(db, subjects, purpose_codes(db), ConsentSource.WHATSAPP_KEYWORD, now)
            return _outcome(db, settings, InboundOutcomeType.WITHDRAWN, event, subjects, with_link=True)
        if words in START_WORDS:
            # Consent is given on the page, against a notice; START only brings the link.
            return _outcome(db, settings, InboundOutcomeType.SEND_PREFERENCES_LINK, event, subjects, with_link=True)
        return ignored

    if event.type == InboundEventType.BUTTON_REPLY and event.button_payload:
        subjects = _subjects(db, owner_id, event.phone)
        if not subjects:
            return ignored
        if event.button_payload == MANAGE_BUTTON:
            return _outcome(db, settings, InboundOutcomeType.SEND_PREFERENCES_LINK, event, subjects, with_link=True)
        if event.button_payload.startswith(STOP_BUTTON_PREFIX):
            purpose = event.button_payload.removeprefix(STOP_BUTTON_PREFIX)
            if purpose not in purpose_codes(db):
                return ignored
            _withdraw(db, subjects, [purpose], ConsentSource.WHATSAPP_BUTTON, now)
            return _outcome(db, settings, InboundOutcomeType.WITHDRAWN, event, subjects, with_link=True)
        return ignored

    if event.type == InboundEventType.ERROR and MARKETING_OPT_OUT_ERROR_CODES.intersection(event.error_codes):
        subjects = _subjects(db, owner_id, event.phone)
        marketing = [p.code for p in active_purposes(db) if p.category == TemplateCategory.MARKETING]
        if not subjects or not marketing:
            return ignored
        _withdraw(db, subjects, marketing, ConsentSource.META_OPT_OUT, now)
        return _outcome(db, settings, InboundOutcomeType.WITHDRAWN, event, subjects, with_link=False)

    return ignored


def handle_inbound(
    db: Session,
    *,
    owner_id: uuid.UUID,
    events: list[InboundEvent],
    settings: ConsentSettings,
    now: datetime | None = None,
) -> list[InboundOutcome]:
    """One outcome per event, in order. The caller commits."""
    now = now or datetime.now(UTC)
    return [_handle(db, owner_id, event, settings, now) for event in events]
