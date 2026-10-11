import re
import secrets
import uuid

import pytest
from conftest import TEST_URL
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

S = "whatsapp_consent"


@pytest.fixture(autouse=True)
def purposes(engine):
    # Host content is loaded by library loaders (Task 3); insert the minimum here so this file stands alone.
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {S}.purposes (code, category, labels, position) VALUES "
                "('service', 'UTILITY', '{}'::jsonb, 1), ('updates', 'MARKETING', '{}'::jsonb, 2) "
                "ON CONFLICT (code) DO NOTHING"
            )
        )


def subject(conn, owner_id=None, name="Asha", phone=None, **extra):
    phone = phone or f"+9198{secrets.randbelow(10**8):08d}"
    columns = {
        "owner_id": owner_id or uuid.uuid4(),
        "display_name": name,
        "phone_e164": phone,
        "phone_fingerprint": "fp-" + phone,
        "source": "personal_link",
    } | extra
    names = ", ".join(columns)
    values = ", ".join(f":{k}" for k in columns)
    return conn.execute(
        text(f"INSERT INTO {S}.subjects ({names}) VALUES ({values}) RETURNING id"), columns
    ).scalar_one()


def event(conn, owner_id, subject_id, action="granted", purpose="service"):
    return conn.execute(
        text(
            f"INSERT INTO {S}.consent_events (owner_id, subject_id, phone_fingerprint, purpose_code, action, source, "
            "given_by, verified_at_time) VALUES (:o, :s, 'fp', :p, :a, 'consent_page', 'self', false) RETURNING id"
        ),
        {"o": owner_id, "s": subject_id, "p": purpose, "a": action},
    ).scalar_one()


def state(conn, subject_id):
    rows = conn.execute(
        text(f"SELECT purpose_code, granted FROM {S}.consent_state WHERE subject_id = :s"), {"s": subject_id}
    )
    return dict(rows.all())


def test_tables_live_in_the_kit_schema_with_their_own_version_table(engine):
    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"), {"s": S}
            ).scalars()
        )
        public = set(
            conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            ).scalars()
        )
    assert {"subjects", "consent_events", "alembic_version"} <= tables
    assert public == set()


def test_models_match_the_migration(engine):
    import whatsapp_consent.models  # noqa: F401
    from whatsapp_consent.db import ConsentBase

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name <> 'alembic_version'"
            ),
            {"s": S},
        ).all()
    in_db = {(t, c) for t, c in rows}
    in_models = {(t.name, c.name) for t in ConsentBase.metadata.sorted_tables for c in t.columns}
    assert in_db == in_models


def test_models_match_the_migrated_schema_for_autogenerate(engine):
    """A future `alembic revision --autogenerate` must propose nothing for an unchanged model."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import whatsapp_consent.models  # noqa: F401
    from whatsapp_consent.db import ConsentBase

    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "version_table_schema": S,
                "include_name": lambda name, type_, parent: (name in (None, S)) if type_ == "schema" else True,
            },
        )
        assert compare_metadata(context, ConsentBase.metadata) == []


ENUM_COLUMNS = {
    ("subjects", "source"): "SubjectSource",
    ("subjects", "language"): "Language",
    ("invites", "kind"): "InviteKind",
    ("purposes", "category"): "TemplateCategory",
    ("notices", "language"): "Language",
    ("consent_events", "action"): "ConsentAction",
    ("consent_events", "source"): "ConsentSource",
    ("consent_events", "given_by"): "GivenBy",
    ("consent_events", "notice_language"): "Language",
    ("verifications", "purpose"): "VerificationPurpose",
    ("verifications", "method"): "VerificationMethod",
    ("data_requests", "source"): "DataRequestSource",
}


def test_check_constraints_match_the_enums(engine):
    """Adding an enum value needs a migration that widens the CHECK constraint; this test says so."""
    from whatsapp_consent import enums

    with engine.connect() as conn:
        for (table, column), enum_name in ENUM_COLUMNS.items():
            definition = conn.execute(
                text(
                    "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                    "JOIN pg_namespace n ON n.oid = c.connamespace "
                    "WHERE n.nspname = :s AND c.conname = :name"
                ),
                {"s": S, "name": f"ck_{table}_{column}"},
            ).scalar_one()
            allowed = set(re.findall(r"'([^']+)'::", definition))
            expected = {m.value for m in getattr(enums, enum_name)}
            if enum_name == "TemplateCategory":
                expected.discard("AUTHENTICATION")  # never a purpose
            assert allowed == expected, (table, column, allowed, expected)


def test_consent_state_follows_events(engine):
    owner = uuid.uuid4()
    with engine.begin() as conn:
        sid = subject(conn, owner)
        event(conn, owner, sid, "granted", "service")
        event(conn, owner, sid, "granted", "updates")
        assert state(conn, sid) == {"service": True, "updates": True}
        event(conn, owner, sid, "withdrawn", "updates")
        assert state(conn, sid) == {"service": True, "updates": False}


def test_consent_events_are_append_only(engine):
    owner = uuid.uuid4()
    with engine.begin() as conn:
        ev = event(conn, owner, subject(conn, owner))
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text(f"UPDATE {S}.consent_events SET action = 'withdrawn' WHERE id = :id"), {"id": ev})
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {S}.consent_events WHERE id = :id"), {"id": ev})


def test_deleting_a_subject_keeps_its_anonymised_history(engine):
    owner = uuid.uuid4()
    with engine.begin() as conn:
        sid = subject(conn, owner)
        ev = event(conn, owner, sid)
        conn.execute(text(f"DELETE FROM {S}.subjects WHERE id = :id"), {"id": sid})
        row = conn.execute(
            text(f"SELECT subject_id, phone_fingerprint FROM {S}.consent_events WHERE id = :id"), {"id": ev}
        ).one()
        assert (row.subject_id, row.phone_fingerprint) == (None, "fp")
        assert state(conn, sid) == {}


def test_purge_setting_allows_deleting_events(engine):
    owner = uuid.uuid4()
    with engine.begin() as conn:
        sid = subject(conn, owner)
        ev = event(conn, owner, sid)
        conn.execute(text(f"DELETE FROM {S}.subjects WHERE id = :id"), {"id": sid})
        conn.execute(text("SELECT set_config('whatsapp_consent.allow_purge', 'on', true)"))
        conn.execute(text(f"DELETE FROM {S}.consent_events WHERE id = :id"), {"id": ev})


def test_one_subject_per_owner_phone_and_name_ignoring_case(engine):
    owner = uuid.uuid4()
    with engine.begin() as conn:
        subject(conn, owner, name="Asha", phone="+919811111111")
        subject(conn, owner, name="Ravi", phone="+919811111111")  # a sibling on the same parent's number
    with pytest.raises(IntegrityError), engine.begin() as conn:
        subject(conn, owner, name="ASHA", phone="+919811111111")


@pytest.mark.parametrize(
    "extra",
    [
        {"guardian_name": "Meera", "guardian_relation": "Mother"},
        {"is_minor": True, "guardian_name": "Meera"},
        {"birthday_day": 12},
        {"birthday_day": 32, "birthday_month": 1},
        {"minor_suspended": True},
        {"phone_e164": "9876543210"},
        {"source": "import"},
        {"language": "fr"},
    ],
)
def test_subject_checks(engine, extra):
    with pytest.raises(IntegrityError), engine.begin() as conn:
        subject(conn, **extra)


def test_purposes_cannot_be_authentication(engine):
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {S}.purposes (code, category, labels, position) "
                "VALUES ('codes', 'AUTHENTICATION', '{}'::jsonb, 9)"
            )
        )


def test_one_active_practice_link_per_owner(engine):
    owner = uuid.uuid4()
    insert = text(f"INSERT INTO {S}.invites (owner_id, kind, token_hash) VALUES (:o, 'practice', :t)")
    with engine.begin() as conn:
        conn.execute(insert, {"o": owner, "t": secrets.token_hex(8)})
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"o": owner, "t": secrets.token_hex(8)})


def test_downgrade_to_base_and_upgrade_again():
    from whatsapp_consent.migrations import downgrade, migrate

    downgrade(TEST_URL, "base")
    migrate(TEST_URL)
    eng = create_engine(TEST_URL)
    with eng.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {S}.subjects")).scalar() == 0
    eng.dispose()


def test_cli_migrate_is_idempotent(capsys):
    from whatsapp_consent.cli import main

    main(["migrate"])
    assert "whatsapp_consent" in capsys.readouterr().out
