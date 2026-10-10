"""How typed verification codes reach a phone. The kit never sends anything else."""

import logging
from typing import Protocol

from whatsapp_consent.config import ConsentSettings
from whatsapp_consent.enums import Language, VerificationMethod

log = logging.getLogger("whatsapp_consent.verification")


class DeliveryFailed(Exception):
    """The number couldn't be reached (e.g. it isn't on WhatsApp)."""


class SenderNotConfigured(RuntimeError):
    pass


class VerificationSender(Protocol):
    method: VerificationMethod

    def send_code(self, phone_e164: str, code: str, language: Language) -> None: ...


class FakeSender:
    """Development and test sender: writes the code to the log instead of sending it."""

    method = VerificationMethod.FAKE

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Language]] = []
        self.fail_for: set[str] = set()

    def send_code(self, phone_e164: str, code: str, language: Language) -> None:
        if phone_e164 in self.fail_for:
            raise DeliveryFailed(phone_e164)
        self.sent.append((phone_e164, code, language))
        log.warning("[fake-whatsapp] verification code %s for %s", code, phone_e164)

    def last_code(self, phone_e164: str) -> str:
        return next(code for phone, code, _ in reversed(self.sent) if phone == phone_e164)


fake_sender = FakeSender()


def resolve_sender(settings: ConsentSettings, sender: VerificationSender | None) -> VerificationSender:
    if settings.verification_method == VerificationMethod.FAKE:
        return fake_sender
    if sender is None:
        raise SenderNotConfigured("CONSENT_VERIFICATION_METHOD=authentication_template needs a sender from the host")
    return sender
