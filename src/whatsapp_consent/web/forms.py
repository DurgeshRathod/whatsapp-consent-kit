"""Validating the consent form. Returns message keys for errors; never raises."""

from dataclasses import dataclass, field
from datetime import date

from whatsapp_consent.enums import Language
from whatsapp_consent.phones import InvalidPhone, normalise_phone
from whatsapp_consent.submissions import ConsentSubmission

NAME_MAX = 80
RELATION_MAX = 40
ADULT_AGE = 18
OLDEST = 120


@dataclass
class ParsedConsent:
    submission: ConsentSubmission | None
    errors: list[str] = field(default_factory=list)
    switch_to_child: bool = False


def empty_consent_values() -> dict:
    return {
        "name": "",
        "phone": "",
        "birth_year": "",
        "birthday_day": "",
        "birthday_month": "",
        "guardian_name": "",
        "guardian_relation": "",
        "child_name": "",
        "declaration": "",
        "purposes": [],
    }


def _int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


def _birthday(day_text: str, month_text: str) -> tuple[int | None, int | None, bool]:
    if not day_text.strip() and not month_text.strip():
        return None, None, True
    day, month = _int(day_text), _int(month_text)
    if day is None or month is None:
        return None, None, False
    try:
        date(2024, month, day)  # a leap year, so 29 February is allowed
    except ValueError:
        return None, None, False
    return day, month, True


def parse_consent_form(
    *,
    who: str,
    name: str,
    phone: str,
    birth_year: str,
    birthday_day: str,
    birthday_month: str,
    guardian_name: str,
    guardian_relation: str,
    child_name: str,
    declaration: str,
    purposes: list[str],
    allowed_purposes: list[str],
    language: Language,
    current_year: int,
) -> ParsedConsent:
    errors: list[str] = []
    guardian: str | None = None
    relation: str | None = None
    if who == "me":
        year = _int(birth_year)
        plausible = year is not None and current_year - OLDEST <= year <= current_year
        # Year only: someone turning 18 this year counts as an adult. The year is never stored.
        if plausible and year is not None and current_year - year < ADULT_AGE:
            return ParsedConsent(None, switch_to_child=True)
        display_name = name.strip()[:NAME_MAX]
        if not display_name:
            errors.append("err_name")
        if not plausible:
            errors.append("err_birth_year")
    else:
        display_name = child_name.strip()[:NAME_MAX]
        guardian = guardian_name.strip()[:NAME_MAX] or None
        relation = guardian_relation.strip()[:RELATION_MAX] or None
        if not guardian or not relation:
            errors.append("err_guardian")
        if not display_name:
            errors.append("err_child_name")
        if declaration != "yes":
            errors.append("err_declaration")

    phone_e164: str | None = None
    try:
        phone_e164 = normalise_phone(phone)
    except InvalidPhone:
        errors.append("err_phone")
    day, month, birthday_ok = _birthday(birthday_day, birthday_month)
    if not birthday_ok:
        errors.append("err_birthday")
    chosen = frozenset(p for p in purposes if p in allowed_purposes)
    if not chosen:
        errors.append("err_purposes")

    if errors or phone_e164 is None:
        return ParsedConsent(None, errors)
    return ParsedConsent(
        ConsentSubmission(
            for_child=who == "child",
            display_name=display_name,
            phone=phone_e164,
            birthday_day=day,
            birthday_month=month,
            guardian_name=guardian,
            guardian_relation=relation,
            purposes=chosen,
            language=language,
        )
    )
