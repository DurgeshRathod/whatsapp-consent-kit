"""ConsentKit: a host's configuration, and the kit's public Python API and routers."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from sqlalchemy.orm import Session

from whatsapp_consent.audit import AuditRecord, audit_phone, audit_subject
from whatsapp_consent.config import ConsentSettings
from whatsapp_consent.enums import Language, TemplateCategory
from whatsapp_consent.gate import can_send, can_send_category, sendable_subjects
from whatsapp_consent.models import Subject
from whatsapp_consent.status import SubjectState, state_of, status_by_phone


@dataclass(frozen=True)
class OwnerInfo:
    """What the public pages and share messages say about an owner."""

    display_name: str  # the business named in every opt-in (a Meta requirement)
    language: Language = Language.EN
    whatsapp_number: str | None = None  # E.164; enables click-to-chat verification for this owner


@dataclass(frozen=True)
class OwnerContext:
    """What the host's owner dependency returns for the signed-in owner."""

    owner_id: uuid.UUID
    info: OwnerInfo


OwnerLookup = Callable[[Session, uuid.UUID], OwnerInfo | None]
ErasureHook = Callable[[Session, uuid.UUID, uuid.UUID], None]


class ConsentKit:
    def __init__(
        self,
        *,
        settings: ConsentSettings,
        session_dependency: Callable[..., Any],
        owner_dependency: Callable[..., Any],
        owner_lookup: OwnerLookup,
        sender: Any = None,
        on_subject_erased: ErasureHook | None = None,
    ) -> None:
        """
        session_dependency: the host's FastAPI dependency yielding a SQLAlchemy Session.
        owner_dependency: the host's FastAPI dependency returning OwnerContext (raising its own 401/409).
        owner_lookup: (db, owner_id) -> OwnerInfo | None, for public pages.
        sender: a VerificationSender for authentication-template codes (not needed with the fake method).
        on_subject_erased: (db, owner_id, subject_id) -> None; delete the host's data about the person,
        in the same transaction.
        """
        self.settings = settings
        self.session_dependency = session_dependency
        self.owner_dependency = owner_dependency
        self.owner_lookup = owner_lookup
        self.sender = sender
        self.on_subject_erased = on_subject_erased

    @property
    def key(self) -> str:
        return self.settings.fingerprint_key

    # Questions a host asks

    def status(self, db: Session, subject_id: uuid.UUID) -> SubjectState | None:
        subject = db.get(Subject, subject_id)
        return state_of(db, subject) if subject else None

    def status_by_phone(self, db: Session, owner_id: uuid.UUID, phone: str) -> list[SubjectState]:
        return status_by_phone(db, owner_id, phone, key=self.key)

    def can_send(self, db: Session, subject_id: uuid.UUID, purpose: str) -> bool:
        return can_send(db, subject_id, purpose)

    def can_send_category(self, db: Session, subject_id: uuid.UUID, category: TemplateCategory) -> bool:
        return can_send_category(db, subject_id, category)

    def sendable_subjects(self, db: Session, owner_id: uuid.UUID, purpose: str) -> list[uuid.UUID]:
        return sendable_subjects(db, owner_id, purpose)

    def audit(self, db: Session, subject_id: uuid.UUID) -> AuditRecord | None:
        return audit_subject(db, subject_id)

    def audit_by_phone(self, db: Session, owner_id: uuid.UUID, phone: str) -> AuditRecord:
        return audit_phone(db, owner_id, phone, key=self.key)

    # Routers

    def owner_router(self, prefix: str = "/subjects") -> APIRouter:
        from whatsapp_consent.api.router import build_owner_router

        return build_owner_router(self, prefix)
