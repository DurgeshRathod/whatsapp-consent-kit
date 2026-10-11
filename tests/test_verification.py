import secrets
from datetime import UTC, datetime, timedelta

import pytest
from conftest import random_phone
from sqlalchemy import func, select

T0 = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)


@pytest.fixture
def key():
    from whatsapp_consent.config import ConsentSettings

    return ConsentSettings().fingerprint_key  # pyright: ignore[reportCallIssue]


def start(db, key, phone, at=T0, purpose="consent"):
    from whatsapp_consent.enums import Language, VerificationPurpose
    from whatsapp_consent.senders import fake_sender
    from whatsapp_consent.verification import start_code_verification

    return start_code_verification(
        db, phone=phone, purpose=VerificationPurpose(purpose), language=Language.EN, sender=fake_sender, key=key, now=at
    )


def check(db, key, verification, code, at, purpose="consent"):
    from whatsapp_consent.enums import VerificationPurpose
    from whatsapp_consent.verification import check_code

    return check_code(
        db, verification_id=verification.id, purpose=VerificationPurpose(purpose), code=code, key=key, now=at
    )


def reason_of(call) -> str:
    from whatsapp_consent.verification import VerificationError

    with pytest.raises(VerificationError) as error:
        call()
    return error.value.reason


def last_code(phone):
    from whatsapp_consent.senders import fake_sender

    return fake_sender.last_code(phone)


def test_a_code_is_sent_and_only_its_hash_is_stored(db, key):
    phone = random_phone()
    verification = start(db, key, phone)
    assert last_code(phone) not in verification.code_hash
    assert (verification.method, verification.expires_at) == ("fake", T0 + timedelta(minutes=10))


def test_the_right_code_verifies_once(db, key):
    phone = random_phone()
    verification = start(db, key, phone)
    code = last_code(phone)
    assert check(db, key, verification, code, T0 + timedelta(minutes=5)).verified_at == T0 + timedelta(minutes=5)
    assert reason_of(lambda: check(db, key, verification, code, T0 + timedelta(minutes=6))) == "not_found"


def test_wrong_codes_are_counted_and_lock_after_five(db, key):
    phone = random_phone()
    verification = start(db, key, phone)
    good = last_code(phone)
    bad = "000000" if good != "000000" else "111111"
    for attempt in range(1, 5):
        assert reason_of(lambda: check(db, key, verification, bad, T0)) == "wrong_code"
        assert verification.attempts == attempt
    assert reason_of(lambda: check(db, key, verification, bad, T0)) == "too_many_attempts"
    assert reason_of(lambda: check(db, key, verification, good, T0)) == "too_many_attempts"


def test_codes_expire_after_ten_minutes(db, key):
    phone = random_phone()
    verification = start(db, key, phone)
    late = T0 + timedelta(minutes=10, seconds=1)
    assert reason_of(lambda: check(db, key, verification, last_code(phone), late)) == "expired"


def test_a_code_only_works_for_its_purpose(db, key):
    phone = random_phone()
    verification = start(db, key, phone, purpose="consent")
    assert reason_of(lambda: check(db, key, verification, last_code(phone), T0, purpose="deletion")) == "not_found"


def test_resend_waits_a_minute_and_is_capped_per_hour(db, key):
    phone = random_phone()
    start(db, key, phone)
    assert reason_of(lambda: start(db, key, phone, T0 + timedelta(seconds=30))) == "too_soon"
    start(db, key, phone, T0 + timedelta(seconds=61))
    start(db, key, phone, T0 + timedelta(seconds=122))
    assert reason_of(lambda: start(db, key, phone, T0 + timedelta(seconds=183))) == "rate_limited"
    start(db, key, phone, T0 + timedelta(minutes=61))


def test_ten_codes_a_day_at_most(db, key):
    phone = random_phone()
    for i in range(10):
        start(db, key, phone, T0 + i * timedelta(minutes=61))
    assert reason_of(lambda: start(db, key, phone, T0 + 10 * timedelta(minutes=61))) == "rate_limited"
    start(db, key, phone, T0 + timedelta(hours=25))


def test_failed_delivery_saves_nothing(db, key):
    from whatsapp_consent.models import Verification
    from whatsapp_consent.senders import fake_sender

    phone = random_phone()
    fake_sender.fail_for.add(phone)
    try:
        assert reason_of(lambda: start(db, key, phone)) == "delivery_failed"
    finally:
        fake_sender.fail_for.discard(phone)
    assert db.scalar(select(func.count()).select_from(Verification).where(Verification.phone_e164 == phone)) == 0


def test_latest_pending_finds_an_unexpired_code(db, key):
    from whatsapp_consent.enums import VerificationPurpose
    from whatsapp_consent.verification import latest_pending

    phone = random_phone()
    verification = start(db, key, phone)
    found = latest_pending(
        db, phone=phone, purpose=VerificationPurpose.CONSENT, subject_id=None, now=T0 + timedelta(minutes=1)
    )
    assert found is not None and found.id == verification.id
    later = T0 + timedelta(minutes=11)
    assert latest_pending(db, phone=phone, purpose=VerificationPurpose.CONSENT, subject_id=None, now=later) is None


def test_click_to_chat_code_verifies_from_the_right_number_only(db, key, make_subject):
    from whatsapp_consent.verification import start_click_verification, verify_click_code

    subject = make_subject(verified=False)
    verification, text = start_click_verification(db, phone=subject.phone_e164, subject_id=subject.id, key=key, now=T0)
    assert text.startswith("START-") and len(text) == 12
    code = text.removeprefix("START-")
    assert verify_click_code(db, phone=random_phone(), code=code, key=key, now=T0) is None
    assert verify_click_code(db, phone=subject.phone_e164, code="ZZZZZZ", key=key, now=T0) is None
    late = T0 + timedelta(minutes=31)
    assert verify_click_code(db, phone=subject.phone_e164, code=code, key=key, now=late) is None
    found = verify_click_code(db, phone=subject.phone_e164, code=code, key=key, now=T0 + timedelta(minutes=5))
    assert found is not None and found.id == verification.id and found.verified_at is not None
    assert verify_click_code(db, phone=subject.phone_e164, code=code, key=key, now=T0) is None  # used once


def test_click_to_chat_codes_do_not_count_towards_send_limits(db, key, make_subject):
    from whatsapp_consent.verification import start_click_verification

    subject = make_subject(verified=False)
    for i in range(5):
        start_click_verification(
            db, phone=subject.phone_e164, subject_id=subject.id, key=key, now=T0 + timedelta(seconds=i)
        )
    start(db, key, subject.phone_e164, T0 + timedelta(seconds=10))  # not "too_soon"


def test_rate_limiter_counts_per_key_within_the_window(db, key):
    from whatsapp_consent.models import RateLimitEvent
    from whatsapp_consent.ratelimit import allow

    ip = f"10.9.{secrets.randbelow(250)}.{secrets.randbelow(250)}-{secrets.token_hex(3)}"
    hour = timedelta(hours=1)
    attempts = [allow(db, bucket="test", key=ip, limit=2, window=hour, hash_key=key, now=T0) for _ in range(3)]
    assert attempts == [True, True, False]
    assert allow(db, bucket="test", key=ip, limit=2, window=hour, hash_key=key, now=T0 + timedelta(minutes=61))
    from whatsapp_consent.hashing import keyed_hash

    expected = keyed_hash(key, "ratelimit:test", ip)
    stored = db.scalars(select(RateLimitEvent.key_hash).where(RateLimitEvent.bucket == "test")).all()
    assert stored.count(expected) == 3
    assert ip not in stored


def test_sender_resolution():
    from whatsapp_consent.config import ConsentSettings
    from whatsapp_consent.senders import SenderNotConfigured, fake_sender, resolve_sender

    fake = ConsentSettings(verification_method="fake")  # pyright: ignore[reportCallIssue]
    real = ConsentSettings(verification_method="authentication_template")  # pyright: ignore[reportCallIssue]
    assert resolve_sender(fake, None) is fake_sender
    with pytest.raises(SenderNotConfigured):
        resolve_sender(real, None)
    assert resolve_sender(real, fake_sender) is fake_sender
