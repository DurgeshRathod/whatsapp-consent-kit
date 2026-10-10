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
