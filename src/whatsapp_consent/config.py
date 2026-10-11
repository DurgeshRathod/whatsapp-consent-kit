from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from whatsapp_consent.enums import VerificationMethod


class ConsentSettings(BaseSettings):
    """Kit configuration, read from CONSENT_* environment variables (and a local .env file)."""

    model_config = SettingsConfigDict(env_prefix="CONSENT_", env_file=".env", extra="ignore")

    environment: str = Field(default="development", pattern="^(development|test|production)$")
    # Only the CLI and a future standalone service use this; inside a host the kit uses the host's session.
    database_url: str | None = None
    # Base URL where the public pages are served; consent and preference links are built from it.
    public_base_url: str = "http://localhost:8000"
    # Keys phone fingerprints (kept after erasure), codes and derived tokens. Long-lived: replacing it makes
    # existing fingerprints and practice links unmatchable.
    fingerprint_key: str = Field(min_length=32)
    # Signs the short-lived cookie that carries a person through the public pages.
    session_secret: str = Field(min_length=32)
    # How typed codes are delivered: Meta authentication template (host's sender) or `fake` (logged; dev only).
    verification_method: VerificationMethod = VerificationMethod.FAKE
    # Offer "Continue on WhatsApp" (the person sends START-XXXXXX to the owner's number) when the owner has one.
    click_to_chat: bool = False
    # For checking X-Hub-Signature-256 on Meta webhooks (optional; hosts may check signatures themselves).
    meta_app_secret: str | None = None
    # Anonymised history is kept this long after erasure (DPDP Rules 8(3): at least a year).
    retention_days: int = Field(default=365, ge=365)
    # Public form submissions (consent, preference lookup, code resend) allowed per IP address per hour.
    web_submissions_per_ip_hour: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def _check_verification(self) -> "ConsentSettings":
        if self.verification_method == VerificationMethod.CLICK_TO_CHAT:
            raise ValueError("Click-to-chat is enabled with CONSENT_CLICK_TO_CHAT, not as the verification method")
        if self.environment == "production" and self.verification_method == VerificationMethod.FAKE:
            raise ValueError("CONSENT_VERIFICATION_METHOD=fake is not allowed in production")
        return self


class DatabaseUrlSettings(BaseSettings):
    """The CLI's database URL: CONSENT_DATABASE_URL, falling back to the host's DATABASE_URL."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    consent_database_url: str | None = None
    database_url: str | None = None

    def url(self) -> str:
        url = self.consent_database_url or self.database_url
        if not url:
            raise RuntimeError("Set CONSENT_DATABASE_URL or DATABASE_URL")
        return url
