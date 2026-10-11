import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from whatsapp_consent.enums import (
    ConsentAction,
    ConsentSource,
    GivenBy,
    Language,
    SendBlocker,
    SubjectSource,
    SubjectStatus,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class NewSubjectIn(BaseModel):
    display_name: Name
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=24)]
    birthday_day: int | None = Field(default=None, ge=1, le=31)
    birthday_month: int | None = Field(default=None, ge=1, le=12)
    is_minor: bool = False

    @model_validator(mode="after")
    def _birthday_is_a_real_day(self) -> "NewSubjectIn":
        if (self.birthday_day is None) != (self.birthday_month is None):
            raise ValueError("Give both the day and the month of the birthday, or neither.")
        if self.birthday_day is not None and self.birthday_month is not None:
            date(2024, self.birthday_month, self.birthday_day)  # a leap year, so 29 February is allowed
        return self


class ShareLinkOut(BaseModel):
    link: str
    share_message: str
    whatsapp_url: str


class SubjectOut(BaseModel):
    id: uuid.UUID
    display_name: str
    phone: str
    birthday_day: int | None
    birthday_month: int | None
    is_minor: bool
    guardian_name: str | None
    guardian_relation: str | None
    source: SubjectSource
    status: SubjectStatus
    verified: bool
    needs_review: bool
    minor_suspended: bool
    consents: dict[str, bool]
    can_send: dict[str, bool]
    blockers: dict[str, list[SendBlocker]]
    created_at: datetime


class HistoryOut(BaseModel):
    purpose: str
    action: ConsentAction
    source: ConsentSource
    given_by: GivenBy
    notice_version: int | None
    notice_language: Language | None
    verified_at_time: bool
    occurred_at: datetime


class SubjectDetailOut(SubjectOut):
    history: list[HistoryOut]


class NewSubjectOut(BaseModel):
    subject: SubjectOut
    invite: ShareLinkOut


class WithdrawIn(BaseModel):
    purpose: str


class PracticeLinkOut(BaseModel):
    link: str
    qr_svg: str


class SendableOut(BaseModel):
    purpose: str
    count: int
    subject_ids: list[uuid.UUID]


class ActivityOut(BaseModel):
    kind: Literal["subject_deleted_data", "unverified_signup_removed"]
    occurred_at: datetime
