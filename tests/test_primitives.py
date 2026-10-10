import re
from typing import Any

import pytest
from pydantic import ValidationError

KEY = "k" * 32


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "98765 43210",
        "098765-43210",
        "+91 98765 43210",
        "919876543210",
        "0091 9876543210",
        "(+91) 98765-43210",
    ],
)
def test_indian_formats_normalise_to_one_number(raw):
    from whatsapp_consent.phones import normalise_phone

    assert normalise_phone(raw) == "+919876543210"


def test_international_numbers_keep_their_country_code():
    from whatsapp_consent.phones import normalise_phone

    assert normalise_phone("+44 20 7946 0958") == "+442079460958"


@pytest.mark.parametrize(
    "raw", ["", "12345", "5876543210", "+91 12345 67890", "+0 1234567", "abcdefghij", "+91 98765 432101"]
)
def test_invalid_numbers_are_rejected(raw):
    from whatsapp_consent.phones import InvalidPhone, normalise_phone

    with pytest.raises(InvalidPhone):
        normalise_phone(raw)


def test_mask_and_local_display():
    from whatsapp_consent.phones import local_display, mask_phone

    assert mask_phone("+919876543210") == "+91 •••••••210"
    assert local_display("+919876543210") == "98765 43210"
    assert local_display("+442079460958") == "+442079460958"


def test_phone_fingerprint_is_keyed():
    from whatsapp_consent.hashing import phone_fingerprint

    fp = phone_fingerprint(KEY, "+919876543210")
    assert re.fullmatch(r"[0-9a-f]{64}", fp)
    assert fp == phone_fingerprint(KEY, "+919876543210")
    assert fp != phone_fingerprint("x" * 32, "+919876543210")


def test_tokens_are_random_and_hashed():
    from whatsapp_consent.hashing import hash_token, new_token

    raw, digest = new_token()
    assert raw != new_token()[0]
    assert len(raw) >= 43
    assert hash_token(raw) == digest


def test_derived_tokens_are_stable_and_separate_from_fingerprints():
    from whatsapp_consent.hashing import derive_token

    a = derive_token(KEY, "practice-invite", "abc")
    assert a == derive_token(KEY, "practice-invite", "abc")
    assert a != derive_token(KEY, "practice-invite", "abd")
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", a)


def test_codes_are_bound_to_purpose_and_phone():
    from whatsapp_consent.hashing import code_matches, hash_code, new_code

    code = new_code()
    assert re.fullmatch(r"[0-9]{6}", code)
    stored = hash_code(KEY, "consent", "+919876543210", code)
    assert code_matches(KEY, "consent", "+919876543210", code, stored)
    assert not code_matches(KEY, "deletion", "+919876543210", code, stored)
    assert not code_matches(KEY, "consent", "+919876543211", code, stored)


def test_click_codes_avoid_confusable_characters():
    from whatsapp_consent.hashing import new_click_code

    codes = {new_click_code() for _ in range(200)}
    assert all(re.fullmatch(r"[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}", c) for c in codes)
    assert len(codes) > 190


def test_template_categories_are_metas_values():
    from whatsapp_consent.enums import TemplateCategory

    assert [c.value for c in TemplateCategory] == ["MARKETING", "UTILITY", "AUTHENTICATION"]


def _settings(**overrides):
    from whatsapp_consent.config import ConsentSettings

    base: dict[str, Any] = {"fingerprint_key": "f" * 32, "session_secret": "s" * 32}
    return ConsentSettings(**(base | overrides))


def test_fake_sender_is_refused_in_production():
    with pytest.raises(ValidationError):
        _settings(environment="production", verification_method="fake")
    assert _settings(environment="production", verification_method="authentication_template")


def test_click_to_chat_is_not_a_sender_method():
    with pytest.raises(ValidationError):
        _settings(verification_method="click_to_chat")


def test_retention_cannot_go_below_a_year():
    with pytest.raises(ValidationError):
        _settings(retention_days=30)
    assert _settings(retention_days=730).retention_days == 730


def test_database_url_falls_back_to_database_url(monkeypatch):
    from whatsapp_consent.config import DatabaseUrlSettings

    monkeypatch.delenv("CONSENT_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://host/app")
    assert DatabaseUrlSettings().url() == "postgresql+psycopg://host/app"
    monkeypatch.setenv("CONSENT_DATABASE_URL", "postgresql+psycopg://host/consent")
    assert DatabaseUrlSettings().url() == "postgresql+psycopg://host/consent"
