"""A minimal host app wired the way a real project wires the kit. The tests drive it over HTTP."""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from whatsapp_consent.config import ConsentSettings
from whatsapp_consent.kit import ConsentKit, OwnerContext, OwnerInfo

OWNERS: dict[uuid.UUID, OwnerInfo] = {}
ERASED: list[tuple[uuid.UUID, uuid.UUID]] = []  # (owner_id, subject_id) received by the erasure hook

_sessionmaker: sessionmaker[Session] | None = None


def configure_database(url: str) -> None:
    global _sessionmaker
    _sessionmaker = sessionmaker(create_engine(url), expire_on_commit=False)


def session() -> Session:
    assert _sessionmaker is not None, "call configure_database first"
    return _sessionmaker()


def get_db() -> Iterator[Session]:
    with session() as db:
        yield db


def current_owner(x_owner_id: Annotated[str | None, Header()] = None) -> OwnerContext:
    """The host's own sign-in, reduced to a header for tests."""
    try:
        owner_id = uuid.UUID(x_owner_id or "")
    except ValueError:
        raise HTTPException(401, "Not signed in") from None
    info = OWNERS.get(owner_id)
    if info is None:
        raise HTTPException(401, "Not signed in")
    return OwnerContext(owner_id=owner_id, info=info)


def lookup_owner(db: Session, owner_id: uuid.UUID) -> OwnerInfo | None:
    return OWNERS.get(owner_id)


def record_erasure(db: Session, owner_id: uuid.UUID, subject_id: uuid.UUID) -> None:
    ERASED.append((owner_id, subject_id))


def build_kit(settings: ConsentSettings | None = None) -> ConsentKit:
    return ConsentKit(
        settings=settings or ConsentSettings(),  # pyright: ignore[reportCallIssue]
        session_dependency=get_db,
        owner_dependency=current_owner,
        owner_lookup=lookup_owner,
        on_subject_erased=record_erasure,
    )


def build_app(kit: ConsentKit) -> FastAPI:
    app = FastAPI()
    app.include_router(kit.owner_router(prefix="/subjects"))
    app.include_router(kit.public_router())
    return app
