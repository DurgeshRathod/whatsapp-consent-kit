"""Phone numbers as WhatsApp sees them: E.164 (`+919876543210`)."""

import re

E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
INDIAN_MOBILE = re.compile(r"^\+91[6-9][0-9]{9}$")


class InvalidPhone(ValueError):
    pass


def normalise_phone(raw: str) -> str:
    """Accepts the ways people type Indian mobiles (spaces, dashes, 0 or 91 prefixes) and international +numbers."""
    digits = re.sub(r"[\s\-().]", "", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+"):
        candidate = digits
    elif re.fullmatch(r"0?[6-9][0-9]{9}", digits):
        candidate = "+91" + digits[-10:]
    elif re.fullmatch(r"91[6-9][0-9]{9}", digits):
        candidate = "+" + digits
    else:
        raise InvalidPhone(raw)
    if not E164.fullmatch(candidate):
        raise InvalidPhone(raw)
    if candidate.startswith("+91") and not INDIAN_MOBILE.fullmatch(candidate):
        raise InvalidPhone(raw)
    return candidate


def mask_phone(phone_e164: str) -> str:
    """`+919876543210` -> `+91 •••••••210`: enough to recognise, not enough to read out."""
    return f"{phone_e164[:3]} {'•' * (len(phone_e164) - 6)}{phone_e164[-3:]}"


def local_display(phone_e164: str) -> str:
    """How an Indian number is usually written (`98765 43210`); other numbers unchanged."""
    if INDIAN_MOBILE.fullmatch(phone_e164):
        return f"{phone_e164[3:8]} {phone_e164[8:]}"
    return phone_e164
