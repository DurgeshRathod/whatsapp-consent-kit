"""The owner API, built for one ConsentKit. Fixed paths are declared before /{subject_id}."""

import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from whatsapp_consent.api.schemas import (
    ActivityOut,
    NewSubjectIn,
    NewSubjectOut,
    PracticeLinkOut,
    SendableOut,
    ShareLinkOut,
    SubjectDetailOut,
    SubjectOut,
    WithdrawIn,
)
from whatsapp_consent.api.share import consent_link, qr_svg, share_link
from whatsapp_consent.api.views import subject_detail_out, subject_out, subjects_out
from whatsapp_consent.enums import ConsentSource, DataRequestSource, SubjectStatus
from whatsapp_consent.events import given_by_for, purpose_codes, set_consent
from whatsapp_consent.gate import sendable_subjects
from whatsapp_consent.invites import get_practice_invite, issue_personal_invite, rotate_practice_invite
from whatsapp_consent.models import DataRequest, Subject
from whatsapp_consent.phones import InvalidPhone, normalise_phone
from whatsapp_consent.status import subjects_by_phone
from whatsapp_consent.subjects import DuplicateSubject, create_subject

if TYPE_CHECKING:
    from whatsapp_consent.kit import ConsentKit, OwnerContext


def build_owner_router(kit: "ConsentKit", prefix: str) -> APIRouter:
    from whatsapp_consent.kit import OwnerContext

    router = APIRouter(prefix=prefix, tags=["whatsapp-consent"])

    def own(db: Session, owner: "OwnerContext", subject_id: uuid.UUID) -> Subject:
        subject = db.get(Subject, subject_id)
        if subject is None or subject.owner_id != owner.owner_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Subject not found.")
        return subject

    def require_purpose(db: Session, purpose: str) -> None:
        if purpose not in purpose_codes(db):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown purpose.")

    def practice_out(token: str) -> PracticeLinkOut:
        link = consent_link(kit.settings, token)
        return PracticeLinkOut(link=link, qr_svg=qr_svg(link))

    @router.post("", status_code=status.HTTP_201_CREATED)
    def add_subject(
        body: NewSubjectIn,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> NewSubjectOut:
        try:
            phone = normalise_phone(body.phone)
        except InvalidPhone:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "That doesn't look like a WhatsApp number."
            ) from None
        try:
            subject = create_subject(
                db,
                owner_id=owner.owner_id,
                display_name=body.display_name,
                phone_e164=phone,
                birthday_day=body.birthday_day,
                birthday_month=body.birthday_month,
                is_minor=body.is_minor,
                language=owner.info.language,
                key=kit.key,
            )
        except DuplicateSubject:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "There is already someone with this name and number."
            ) from None
        issued = issue_personal_invite(db, subject, minor_only=body.is_minor)
        db.commit()
        return NewSubjectOut(
            subject=subject_out(db, subject), invite=share_link(kit.settings, owner.info, subject, issued.token)
        )

    @router.get("")
    def list_subjects(
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
        status_filter: Annotated[SubjectStatus | None, Query(alias="status")] = None,
    ) -> list[SubjectOut]:
        subjects = list(
            db.scalars(select(Subject).where(Subject.owner_id == owner.owner_id).order_by(Subject.created_at.desc()))
        )
        return [s for s in subjects_out(db, subjects) if status_filter is None or s.status == status_filter]

    @router.get("/status")
    def status_by_phone(
        phone: str,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> list[SubjectOut]:
        try:
            subjects = subjects_by_phone(db, owner.owner_id, phone, key=kit.key)
        except InvalidPhone:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "That doesn't look like a WhatsApp number."
            ) from None
        return subjects_out(db, subjects)

    @router.get("/sendable")
    def sendable(
        purpose: str,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> SendableOut:
        require_purpose(db, purpose)
        ids = sendable_subjects(db, owner.owner_id, purpose)
        return SendableOut(purpose=purpose, count=len(ids), subject_ids=ids)

    @router.get("/practice-link")
    def get_practice_link(
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> PracticeLinkOut:
        issued = get_practice_invite(db, owner.owner_id, key=kit.key)
        db.commit()
        return practice_out(issued.token)

    @router.post("/practice-link/rotate")
    def rotate_practice_link(
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> PracticeLinkOut:
        """Replaces the link (e.g. if it is abused); the old link and QR code stop working."""
        issued = rotate_practice_invite(db, owner.owner_id, key=kit.key)
        db.commit()
        return practice_out(issued.token)

    @router.get("/activity")
    def activity(
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> list[ActivityOut]:
        """Things that happened without the owner: deletions requested by subjects, stale sign-ups removed."""
        requests = db.scalars(
            select(DataRequest)
            .where(
                DataRequest.owner_id == owner.owner_id,
                DataRequest.source.in_((DataRequestSource.SUBJECT, DataRequestSource.SYSTEM)),
                DataRequest.completed_at.is_not(None),
            )
            .order_by(DataRequest.completed_at.desc())
            .limit(50)
        )
        return [
            ActivityOut(
                kind="subject_deleted_data" if r.source == DataRequestSource.SUBJECT else "unverified_signup_removed",
                occurred_at=r.completed_at,  # pyright: ignore[reportArgumentType]
            )
            for r in requests
        ]

    @router.get("/{subject_id}")
    def get_subject(
        subject_id: uuid.UUID,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> SubjectDetailOut:
        return subject_detail_out(db, own(db, owner, subject_id))

    @router.post("/{subject_id}/invite")
    def new_invite(
        subject_id: uuid.UUID,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> ShareLinkOut:
        subject = own(db, owner, subject_id)
        issued = issue_personal_invite(db, subject, minor_only=subject.is_minor)
        db.commit()
        return share_link(kit.settings, owner.info, subject, issued.token)

    @router.post("/{subject_id}/approve")
    def approve(
        subject_id: uuid.UUID,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> SubjectOut:
        subject = own(db, owner, subject_id)
        subject.needs_review = False
        db.commit()
        return subject_out(db, subject)

    @router.post("/{subject_id}/withdraw")
    def withdraw(
        subject_id: uuid.UUID,
        body: WithdrawIn,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> SubjectOut:
        subject = own(db, owner, subject_id)
        require_purpose(db, body.purpose)
        set_consent(
            db, subject, {body.purpose: False}, source=ConsentSource.OWNER_RECORDED, given_by=given_by_for(subject)
        )
        db.commit()
        return subject_out(db, subject)

    @router.post("/{subject_id}/mark-minor")
    def mark_minor(
        subject_id: uuid.UUID,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> ShareLinkOut:
        """Suspends the subject until a parent or guardian consents through the returned link."""
        subject = own(db, owner, subject_id)
        subject.is_minor = True
        subject.minor_suspended = True
        issued = issue_personal_invite(db, subject, minor_only=True)
        db.commit()
        return share_link(kit.settings, owner.info, subject, issued.token)

    @router.delete("/{subject_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove(
        subject_id: uuid.UUID,
        owner: Annotated[OwnerContext, Depends(kit.owner_dependency)],
        db: Annotated[Session, Depends(kit.session_dependency)],
    ) -> None:
        """Erases the subject: personal data now, anonymised evidence after the retention period."""
        own(db, owner, subject_id)
        kit.erase_subject(db, subject_id, DataRequestSource.OWNER)
        db.commit()

    return router
