"""Helpers for hosts' WhatsApp templates: an opt-out footer and quick-reply buttons the kit understands.

Add `opt_out_footer` and `stop_button` to every MARKETING template; when sending, pass `stop_button_parameter` for
the button's index so a tap withdraws that purpose.
"""

from typing import Any

from whatsapp_consent.enums import Language
from whatsapp_consent.inbound import MANAGE_BUTTON, STOP_BUTTON_PREFIX

OPT_OUT_FOOTERS = {
    Language.EN: "Reply STOP to stop these messages",
    Language.HI: "ये संदेश रोकने के लिए STOP भेजें",
}
STOP_BUTTON_TEXTS = {Language.EN: "Stop updates", Language.HI: "अपडेट बंद करें"}
MANAGE_BUTTON_TEXTS = {Language.EN: "Manage preferences", Language.HI: "पसंद बदलें"}


def opt_out_footer(language: Language) -> str:
    """Template footer text (Meta's limit is 60 characters)."""
    return OPT_OUT_FOOTERS[language]


def stop_button(language: Language) -> dict[str, Any]:
    """A quick-reply button for the template definition (Meta's limit is 25 characters)."""
    return {"type": "QUICK_REPLY", "text": STOP_BUTTON_TEXTS[language]}


def manage_button(language: Language) -> dict[str, Any]:
    return {"type": "QUICK_REPLY", "text": MANAGE_BUTTON_TEXTS[language]}


def _quick_reply_parameter(index: int, payload: str) -> dict[str, Any]:
    return {
        "type": "button",
        "sub_type": "quick_reply",
        "index": str(index),
        "parameters": [{"type": "payload", "payload": payload}],
    }


def stop_button_parameter(purpose: str, index: int) -> dict[str, Any]:
    """The send-time component that makes a tap on button `index` withdraw `purpose`."""
    return _quick_reply_parameter(index, STOP_BUTTON_PREFIX + purpose)


def manage_button_parameter(index: int) -> dict[str, Any]:
    return _quick_reply_parameter(index, MANAGE_BUTTON)
