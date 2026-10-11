"""Meta WhatsApp Cloud API webhooks: signature checks and parsing into simple events.

Message text is kept only on the in-memory event, for keyword matching. Never store it.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from whatsapp_consent.enums import InboundEventType


class InvalidSignature(Exception):
    pass


class InvalidWebhookPayload(Exception):
    """A body that passed the signature check but is not a webhook payload we can read."""


def verify_signature(raw_body: bytes, header: str | None, app_secret: str) -> None:
    """Checks `X-Hub-Signature-256` (`sha256=<hex HMAC of the raw body with the app secret>`)."""
    expected = ("sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()).encode()
    try:
        given = header.encode("utf-8") if isinstance(header, str) else b""
    except UnicodeError:
        given = b""
    if not given or not hmac.compare_digest(given, expected):
        raise InvalidSignature("webhook signature does not match")


@dataclass(frozen=True)
class InboundEvent:
    type: InboundEventType
    phone_number_id: str  # the business number that received it (the host maps it to an owner)
    phone: str | None  # E.164 of the person: the sender, or the recipient for statuses
    message_id: str | None
    timestamp: datetime | None
    text: str | None = None
    button_payload: str | None = None
    status: str | None = None
    error_codes: tuple[int, ...] = ()


def _e164(wa_id: Any) -> str | None:
    digits = "".join(ch for ch in str(wa_id or "") if ch.isdigit())
    return "+" + digits if digits else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None


def parse_webhook(payload: Any) -> list[InboundEvent]:
    """Raises InvalidWebhookPayload for anything that is not a well-formed webhook body."""
    try:
        return _parse(payload)
    except (AttributeError, TypeError, ValueError, KeyError, RecursionError) as error:
        raise InvalidWebhookPayload("malformed webhook payload") from error


def _parse(payload: Any) -> list[InboundEvent]:
    if not isinstance(payload, dict):
        raise TypeError("webhook payload must be an object")
    events: list[InboundEvent] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            number_id = str(value.get("metadata", {}).get("phone_number_id", ""))
            for message in value.get("messages", []):
                common = {
                    "phone_number_id": number_id,
                    "phone": _e164(message.get("from")),
                    "message_id": message.get("id"),
                    "timestamp": _timestamp(message.get("timestamp")),
                }
                kind = message.get("type")
                if kind == "text":
                    events.append(
                        InboundEvent(InboundEventType.TEXT, text=_string(message.get("text", {}).get("body")), **common)
                    )
                elif kind == "button":
                    payload_value = _string(message.get("button", {}).get("payload"))
                    events.append(InboundEvent(InboundEventType.BUTTON_REPLY, button_payload=payload_value, **common))
                elif kind == "interactive":
                    interactive = message.get("interactive", {})
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    events.append(
                        InboundEvent(InboundEventType.BUTTON_REPLY, button_payload=_string(reply.get("id")), **common)
                    )
            for status in value.get("statuses", []):
                codes = tuple(int(error["code"]) for error in status.get("errors", []) if "code" in error)
                events.append(
                    InboundEvent(
                        InboundEventType.ERROR if codes else InboundEventType.STATUS,
                        phone_number_id=number_id,
                        phone=_e164(status.get("recipient_id")),
                        message_id=status.get("id"),
                        timestamp=_timestamp(status.get("timestamp")),
                        status=_string(status.get("status")),
                        error_codes=codes,
                    )
                )
    return events
