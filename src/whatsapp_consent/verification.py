"""Proving a person holds a WhatsApp number: a typed code we send, or a code they send us (click-to-chat)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatsapp_consent.enums import Language, VerificationMethod, VerificationPurpose
from whatsapp_consent.hashing import code_matches, hash_code, new_click_code, new_code
from whatsapp_consent.models import Verification
from whatsapp_consent.senders import DeliveryFailed, VerificationSender

CODE_TTL = timedelta(minutes=10)
CLICK_CODE_TTL = timedelta(minutes=30)
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=60)
MAX_PER_HOUR = 3
MAX_PER_DAY = 10
CLICK_PREFIX = "START-"
CLICK_HASH_PURPOSE = "click"

Reason = Literal[
    "too_soon", "rate_limited", "delivery_failed", "not_found", "expired", "too_many_attempts", "wrong_code"
]


class VerificationError(Exception):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


def start_code_verification(
    db: Session,
    *,
    phone: str,
    purpose: VerificationPurpose,
    language: Language,
    sender: VerificationSender,
    key: str,
    subject_id: uuid.UUID | None = None,
    pending: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Verification:
    """Sends a 6-digit code. Limits count only codes we sent (not click-to-chat codes)."""
    now = now or datetime.now(UTC)
    recent = db.scalars(
        select(Verification.created_at).where(
            Verification.phone_e164 == phone,
            Verification.method != VerificationMethod.CLICK_TO_CHAT,
            Verification.created_at > now - timedelta(days=1),
        )
    ).all()
    if any(created > now - RESEND_AFTER for created in recent):
        raise VerificationError("too_soon")
    if sum(created > now - timedelta(hours=1) for created in recent) >= MAX_PER_HOUR or len(recent) >= MAX_PER_DAY:
        raise VerificationError("rate_limited")

    code = new_code()
    try:
        sender.send_code(phone, code, language)
    except DeliveryFailed:
        raise VerificationError("delivery_failed") from None
    verification = Verification(
        subject_id=subject_id,
        phone_e164=phone,
        purpose=purpose,
        method=sender.method,
        code_hash=hash_code(key, purpose.value, phone, code),
        expires_at=now + CODE_TTL,
        pending=pending,
        created_at=now,
    )
    db.add(verification)
    db.flush()
    return verification


def start_click_verification(
    db: Session,
    *,
    phone: str,
    subject_id: uuid.UUID,
    key: str,
    pending: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[Verification, str]:
    """A code the person sends to the owner's number (`START-XXXXXX`). Nothing is sent by us."""
    now = now or datetime.now(UTC)
    code = new_click_code()
    verification = Verification(
        subject_id=subject_id,
        phone_e164=phone,
        purpose=VerificationPurpose.CONSENT,
        method=VerificationMethod.CLICK_TO_CHAT,
        code_hash=hash_code(key, CLICK_HASH_PURPOSE, phone, code),
        expires_at=now + CLICK_CODE_TTL,
        pending=pending,
        created_at=now,
    )
    db.add(verification)
    db.flush()
    return verification, CLICK_PREFIX + code


def check_code(
    db: Session,
    *,
    verification_id: uuid.UUID,
    purpose: VerificationPurpose,
    code: str,
    key: str,
    now: datetime | None = None,
) -> Verification:
    """Marks a typed code verified, or raises. A wrong code increments `attempts` first: commit after catching."""
    now = now or datetime.now(UTC)
    # Row lock: concurrent guesses must not share one attempts count.
    verification = db.get(Verification, verification_id, with_for_update=True)
    if (
        verification is None
        or verification.purpose != purpose
        or verification.method == VerificationMethod.CLICK_TO_CHAT
        or verification.verified_at is not None
    ):
        raise VerificationError("not_found")
    if verification.attempts >= MAX_ATTEMPTS:
        raise VerificationError("too_many_attempts")
    if now >= verification.expires_at:
        raise VerificationError("expired")
    if not code_matches(key, purpose.value, verification.phone_e164, code.strip(), verification.code_hash):
        verification.attempts += 1
        db.flush()
        raise VerificationError("too_many_attempts" if verification.attempts >= MAX_ATTEMPTS else "wrong_code")
    verification.verified_at = now
    db.flush()
    return verification


def latest_pending(
    db: Session,
    *,
    phone: str,
    purpose: VerificationPurpose,
    subject_id: uuid.UUID | None,
    now: datetime | None = None,
) -> Verification | None:
    """An unexpired, unused typed code for the same phone, purpose and subject (reused on a double submit)."""
    now = now or datetime.now(UTC)
    query = select(Verification).where(
        Verification.phone_e164 == phone,
        Verification.purpose == purpose,
        Verification.method != VerificationMethod.CLICK_TO_CHAT,
        Verification.verified_at.is_(None),
        Verification.expires_at > now,
        Verification.attempts < MAX_ATTEMPTS,
    )
    query = query.where(
        Verification.subject_id.is_(None) if subject_id is None else Verification.subject_id == subject_id
    )
    return db.scalar(query.order_by(Verification.created_at.desc()).limit(1))


def verify_click_code(
    db: Session, *, phone: str, code: str, key: str, now: datetime | None = None
) -> Verification | None:
    """Matches an inbound `START-XXXXXX` from `phone` to an open click-to-chat verification for that number."""
    now = now or datetime.now(UTC)
    candidates = db.scalars(
        select(Verification).where(
            Verification.phone_e164 == phone,
            Verification.method == VerificationMethod.CLICK_TO_CHAT,
            Verification.verified_at.is_(None),
            Verification.expires_at > now,
        )
    )
    for verification in candidates:
        if code_matches(key, CLICK_HASH_PURPOSE, phone, code.strip().upper(), verification.code_hash):
            verification.verified_at = now
            db.flush()
            return verification
    return None
