"""WhatsApp Consent Kit: lawful, provable, per-purpose WhatsApp consent for FastAPI + SQLAlchemy + Postgres apps."""

from whatsapp_consent.config import ConsentSettings
from whatsapp_consent.enums import Language, TemplateCategory
from whatsapp_consent.kit import ConsentKit, OwnerContext, OwnerInfo

__version__ = "0.1.0"
__all__ = ["ConsentKit", "ConsentSettings", "Language", "OwnerContext", "OwnerInfo", "TemplateCategory"]
