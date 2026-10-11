import os
import secrets

os.environ["CONSENT_ENVIRONMENT"] = "test"
os.environ["CONSENT_PUBLIC_BASE_URL"] = "http://testserver"
os.environ["CONSENT_FINGERPRINT_KEY"] = secrets.token_hex(32)
os.environ["CONSENT_SESSION_SECRET"] = secrets.token_hex(32)
os.environ["CONSENT_VERIFICATION_METHOD"] = "fake"
os.environ["CONSENT_WEB_SUBMISSIONS_PER_IP_HOUR"] = "100000"

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/postgres")
TEST_DB = f"wck_test_{secrets.token_hex(4)}"
TEST_URL = make_url(ADMIN_URL).set(database=TEST_DB).render_as_string(hide_password=False)
os.environ["CONSENT_DATABASE_URL"] = TEST_URL


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[str]:
    from whatsapp_consent.migrations import migrate

    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    try:
        migrate(TEST_URL)
        content_engine = create_engine(TEST_URL)
        with content_engine.begin() as conn:
            from sample_content import load_sample_content

            load_sample_content(conn)
        content_engine.dispose()
        yield TEST_URL
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def engine(database):
    eng = create_engine(database)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """A session for domain tests; everything is rolled back afterwards."""
    from sqlalchemy.orm import Session

    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()


import uuid  # noqa: E402


def random_phone() -> str:
    """A random Indian mobile in E.164, so tests never share per-number limits."""
    return f"+9198{secrets.randbelow(10**8):08d}"


@pytest.fixture
def owner_id():
    return uuid.uuid4()


@pytest.fixture
def make_subject(db, owner_id):
    from datetime import UTC, datetime

    from whatsapp_consent.enums import SubjectSource
    from whatsapp_consent.hashing import phone_fingerprint
    from whatsapp_consent.models import Subject

    key = os.environ["CONSENT_FINGERPRINT_KEY"]

    def make(*, owner=None, name="Asha", phone=None, verified=True, **fields):
        phone = phone or random_phone()
        subject = Subject(
            owner_id=owner or owner_id,
            display_name=name,
            phone_e164=phone,
            phone_fingerprint=phone_fingerprint(key, phone),
            source=fields.pop("source", SubjectSource.PERSONAL_LINK),
            phone_verified_at=datetime.now(UTC) if verified else None,
            **fields,
        )
        db.add(subject)
        db.flush()
        return subject

    return make


from contextlib import contextmanager  # noqa: E402


def local_phone() -> str:
    """A random Indian mobile the way people type it (no +91)."""
    return f"98{secrets.randbelow(10**8):08d}"


@contextmanager
def committed_session():
    """A session whose changes are committed, for setting up state the API will read."""
    import example_host

    with example_host.session() as db:
        yield db
        db.commit()


@pytest.fixture(scope="session")
def kit(database):
    import example_host

    example_host.configure_database(database)
    return example_host.build_kit()


@pytest.fixture
def client(kit):
    import example_host
    from fastapi.testclient import TestClient

    with TestClient(example_host.build_app(kit)) as test_client:
        yield test_client


@pytest.fixture
def owner():
    """Registers an owner with the example host; returns (owner_id, auth headers)."""
    import example_host

    from whatsapp_consent.enums import Language
    from whatsapp_consent.kit import OwnerInfo

    def make(display_name="Priya Nutrition", language="en", whatsapp_number=None):
        owner_id = uuid.uuid4()
        example_host.OWNERS[owner_id] = OwnerInfo(display_name, Language(language), whatsapp_number)
        return owner_id, {"X-Owner-Id": str(owner_id)}

    return make


@pytest.fixture
def relaxed_limits(monkeypatch):
    """For tests that verify the same number several times in a row."""
    from datetime import timedelta

    import whatsapp_consent.verification as verification

    monkeypatch.setattr(verification, "RESEND_AFTER", timedelta(0))
    monkeypatch.setattr(verification, "MAX_PER_HOUR", 100)
    monkeypatch.setattr(verification, "MAX_PER_DAY", 100)


@pytest.fixture
def click_client(database):
    """The example host with click-to-chat verification enabled."""
    import example_host
    from fastapi.testclient import TestClient

    from whatsapp_consent.config import ConsentSettings

    click_kit = example_host.build_kit(ConsentSettings(click_to_chat=True))  # pyright: ignore[reportCallIssue]
    with TestClient(example_host.build_app(click_kit)) as test_client:
        yield test_client
