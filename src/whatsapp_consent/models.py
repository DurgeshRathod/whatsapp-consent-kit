"""Tables in the `whatsapp_consent` schema."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from whatsapp_consent.db import SCHEMA, ConsentBase, enum_column, id_column
from whatsapp_consent.enums import (
    ConsentAction,
    ConsentSource,
    DataRequestSource,
    GivenBy,
    InviteKind,
    Language,
    SubjectSource,
    TemplateCategory,
    VerificationMethod,
    VerificationPurpose,
)


def _fk(table_column: str, **kwargs: Any) -> ForeignKey:
    return ForeignKey(f"{SCHEMA}.{table_column}", **kwargs)


def _timestamp(**kwargs: Any) -> Any:
    return mapped_column(DateTime(timezone=True), **kwargs)


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Subject(ConsentBase):
    """A person an owner may message. For a child, the phone is the parent's and display_name the child's."""

    __tablename__ = "subjects"

    __table_args__ = (
        Index("uq_subjects_owner_phone_name", "owner_id", "phone_e164", text("lower(display_name)"), unique=True),
    )

    id: Mapped[uuid.UUID] = id_column()
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    display_name: Mapped[str] = mapped_column(Text)
    phone_e164: Mapped[str] = mapped_column(Text)
    phone_fingerprint: Mapped[str] = mapped_column(Text, index=True)
    birthday_day: Mapped[int | None] = mapped_column(SmallInteger)
    birthday_month: Mapped[int | None] = mapped_column(SmallInteger)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    guardian_name: Mapped[str | None] = mapped_column(Text)
    guardian_relation: Mapped[str | None] = mapped_column(Text)
    source: Mapped[SubjectSource] = enum_column(SubjectSource)
    language: Mapped[Language] = enum_column(Language, default=Language.EN, server_default="en")
    phone_verified_at: Mapped[datetime | None] = _timestamp()
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    minor_suspended: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Invite(ConsentBase):
    """A consent link: personal (one subject, single use, expires) or practice (reusable, one active per owner)."""

    __tablename__ = "invites"

    __table_args__ = (
        Index(
            "uq_invites_one_active_practice",
            "owner_id",
            unique=True,
            postgresql_where=text("kind = 'practice' AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = id_column()
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    kind: Mapped[InviteKind] = enum_column(InviteKind)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(_fk("subjects.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    minor_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    expires_at: Mapped[datetime | None] = _timestamp()
    used_at: Mapped[datetime | None] = _timestamp()
    revoked_at: Mapped[datetime | None] = _timestamp()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Purpose(ConsentBase):
    """Host content: something a person can agree to, bound to one Meta template category."""

    __tablename__ = "purposes"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[TemplateCategory] = enum_column(TemplateCategory)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {"en": {"name": ..., "description": ...}, "hi": {...}}
    position: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Notice(ConsentBase):
    """Host content: the notice a person was shown. Never edited; a change is a new version."""

    __tablename__ = "notices"

    __table_args__ = (UniqueConstraint("version", "language", name="uq_notices_version_language"),)

    id: Mapped[uuid.UUID] = id_column()
    version: Mapped[int] = mapped_column(Integer)
    language: Mapped[Language] = enum_column(Language)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = _now()


class ConsentEvent(ConsentBase):
    """Append-only consent history (trigger-enforced). Survives erasure through the phone fingerprint."""

    __tablename__ = "consent_events"

    id: Mapped[uuid.UUID] = id_column()
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(_fk("subjects.id", ondelete="SET NULL"), index=True)
    phone_fingerprint: Mapped[str] = mapped_column(Text, index=True)
    purpose_code: Mapped[str] = mapped_column(_fk("purposes.code"))
    action: Mapped[ConsentAction] = enum_column(ConsentAction)
    source: Mapped[ConsentSource] = enum_column(ConsentSource)
    given_by: Mapped[GivenBy] = enum_column(GivenBy)
    notice_version: Mapped[int | None] = mapped_column(Integer)
    notice_language: Mapped[Language | None] = enum_column(Language, nullable=True)
    verified_at_time: Mapped[bool] = mapped_column(Boolean)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ConsentEvidence(ConsentBase):
    """Device evidence for one event. Personal data: deleted on erasure."""

    __tablename__ = "consent_evidence"

    event_id: Mapped[uuid.UUID] = mapped_column(_fk("consent_events.id", ondelete="CASCADE"), primary_key=True)
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class ConsentState(ConsentBase):
    """Current consent per subject and purpose, maintained by a trigger on consent_events."""

    __tablename__ = "consent_state"

    subject_id: Mapped[uuid.UUID] = mapped_column(_fk("subjects.id", ondelete="CASCADE"), primary_key=True)
    purpose_code: Mapped[str] = mapped_column(_fk("purposes.code"), primary_key=True)
    granted: Mapped[bool] = mapped_column(Boolean)
    last_event_id: Mapped[uuid.UUID] = mapped_column(_fk("consent_events.id"))
    updated_at: Mapped[datetime] = _now()


class Verification(ConsentBase):
    __tablename__ = "verifications"

    __table_args__ = (Index("ix_verifications_phone_created", "phone_e164", "created_at"),)

    id: Mapped[uuid.UUID] = id_column()
    subject_id: Mapped[uuid.UUID | None] = mapped_column(_fk("subjects.id", ondelete="CASCADE"), index=True)
    phone_e164: Mapped[str] = mapped_column(Text)
    purpose: Mapped[VerificationPurpose] = enum_column(VerificationPurpose)
    method: Mapped[VerificationMethod] = enum_column(VerificationMethod)
    code_hash: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    expires_at: Mapped[datetime] = _timestamp()
    verified_at: Mapped[datetime | None] = _timestamp()
    # A consent page submission held until this verification succeeds (see submissions.hold_consent).
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))  # None is SQL NULL
    # The `hold_id` of the held submission this row's completion applied (None if it applied nothing).
    applied_hold_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = _now()


class PreferenceLink(ConsentBase):
    """A private link to a verified number's preferences page."""

    __tablename__ = "preference_links"

    id: Mapped[uuid.UUID] = id_column()
    phone_fingerprint: Mapped[str] = mapped_column(Text, index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime] = _timestamp()
    revoked_at: Mapped[datetime | None] = _timestamp()
    created_at: Mapped[datetime] = _now()


class DataRequest(ConsentBase):
    """An erasure request. Survives the erasure, linked only by the phone fingerprint."""

    __tablename__ = "data_requests"

    id: Mapped[uuid.UUID] = id_column()
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(_fk("subjects.id", ondelete="SET NULL"))
    phone_fingerprint: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="erasure", server_default="erasure")
    source: Mapped[DataRequestSource] = enum_column(DataRequestSource)
    requested_at: Mapped[datetime] = _now()
    completed_at: Mapped[datetime | None] = _timestamp()


class RateLimitEvent(ConsentBase):
    __tablename__ = "rate_limit_events"

    __table_args__ = (Index("ix_rate_limit_events_lookup", "bucket", "key_hash", "created_at"),)

    id: Mapped[uuid.UUID] = id_column()
    bucket: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
