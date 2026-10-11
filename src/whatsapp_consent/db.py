"""The kit's SQLAlchemy base: every table lives in the `whatsapp_consent` schema."""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import MetaData, text
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.types import Uuid

SCHEMA = "whatsapp_consent"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class ConsentBase(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


def enum_column(enum_cls: type[StrEnum], **kwargs: Any) -> Any:
    """A text column holding a StrEnum. The CHECK constraint is written in the migration."""
    column_type = SAEnum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
    )
    return mapped_column(column_type, **kwargs)


def id_column() -> Any:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
