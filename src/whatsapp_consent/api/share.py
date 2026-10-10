"""Links and share messages an owner sends from their own WhatsApp."""

from urllib.parse import quote

import segno

from whatsapp_consent.api.schemas import ShareLinkOut
from whatsapp_consent.config import ConsentSettings
from whatsapp_consent.enums import Language
from whatsapp_consent.kit import OwnerInfo
from whatsapp_consent.models import Subject

SHARE_MESSAGES: dict[tuple[Language, bool], str] = {
    (Language.EN, False): "Hi {name}, please confirm you'd like WhatsApp updates from {business}: {link}",
    (Language.EN, True): "Hi, please confirm WhatsApp updates from {business} for {name}: {link}",
    (Language.HI, False): "नमस्ते {name}, कृपया पुष्टि करें कि आप {business} से WhatsApp पर अपडेट पाना चाहते हैं: {link}",
    (Language.HI, True): "नमस्ते, कृपया {name} के लिए {business} से WhatsApp अपडेट की पुष्टि करें: {link}",
}


def consent_link(settings: ConsentSettings, token: str) -> str:
    return f"{settings.public_base_url}/c/{token}"


def share_link(settings: ConsentSettings, info: OwnerInfo, subject: Subject, token: str) -> ShareLinkOut:
    link = consent_link(settings, token)
    template = SHARE_MESSAGES[(info.language, subject.is_minor)]
    message = template.format(name=subject.display_name, business=info.display_name, link=link)
    whatsapp_url = f"https://wa.me/{subject.phone_e164.lstrip('+')}?text={quote(message)}"
    return ShareLinkOut(link=link, share_message=message, whatsapp_url=whatsapp_url)


def qr_svg(link: str) -> str:
    return segno.make(link, error="m").svg_inline(scale=5, border=2, dark="#000000", light="#ffffff", omitsize=True)
