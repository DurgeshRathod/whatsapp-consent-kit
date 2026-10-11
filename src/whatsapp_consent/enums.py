"""Every closed set in the kit. Stored as text; migrations add CHECK constraints with the same values."""

from enum import StrEnum


class TemplateCategory(StrEnum):
    """Meta's WhatsApp template categories, with Meta's exact values."""

    MARKETING = "MARKETING"
    UTILITY = "UTILITY"
    AUTHENTICATION = "AUTHENTICATION"  # codes the person asked for: always allowed, never a purpose


class ConsentAction(StrEnum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


class ConsentSource(StrEnum):
    CONSENT_PAGE = "consent_page"
    PREFERENCES_PAGE = "preferences_page"
    OWNER_RECORDED = "owner_recorded"
    WHATSAPP_KEYWORD = "whatsapp_keyword"
    WHATSAPP_BUTTON = "whatsapp_button"
    META_OPT_OUT = "meta_opt_out"
    SYSTEM = "system"


class GivenBy(StrEnum):
    SELF = "self"
    GUARDIAN = "guardian"


class InviteKind(StrEnum):
    PERSONAL = "personal"
    PRACTICE = "practice"


class SubjectSource(StrEnum):
    PERSONAL_LINK = "personal_link"
    PRACTICE_LINK = "practice_link"
    WHATSAPP = "whatsapp"


class SubjectStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    SUSPENDED = "suspended"
    INVITED = "invited"
    UNVERIFIED = "unverified"
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class SendBlocker(StrEnum):
    NO_CONSENT = "no_consent"
    UNVERIFIED = "unverified"
    NEEDS_REVIEW = "needs_review"
    MINOR_SUSPENDED = "minor_suspended"


class VerificationPurpose(StrEnum):
    CONSENT = "consent"
    PREFERENCES = "preferences"
    DELETION = "deletion"


class VerificationMethod(StrEnum):
    AUTHENTICATION_TEMPLATE = "authentication_template"
    CLICK_TO_CHAT = "click_to_chat"
    FAKE = "fake"


class DataRequestSource(StrEnum):
    SUBJECT = "subject"
    OWNER = "owner"
    SYSTEM = "system"


class InboundEventType(StrEnum):
    TEXT = "text"
    BUTTON_REPLY = "button_reply"
    STATUS = "status"
    ERROR = "error"


class InboundOutcomeType(StrEnum):
    WITHDRAWN = "withdrawn"
    VERIFIED = "verified"
    SEND_PREFERENCES_LINK = "send_preferences_link"
    IGNORED = "ignored"


class Language(StrEnum):
    EN = "en"
    HI = "hi"
