import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import committed_session, local_phone, random_phone
from helpers import add_subject, adult, consent_and_verify, guardian, submit, text_of
from sqlalchemy import func, select, text


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def grant(db, subject, now=None, **wanted):
    from whatsapp_consent.enums import ConsentSource, GivenBy
    from whatsapp_consent.events import Evidence, set_consent

    return set_consent(
        db,
        subject,
        wanted,
        source=ConsentSource.CONSENT_PAGE,
        given_by=GivenBy.SELF,
        evidence=Evidence("10.0.0.1", "test-agent"),
        now=now,
    )


def test_erasure_removes_personal_data_and_keeps_anonymised_history(db, make_subject):
    from whatsapp_consent.enums import DataRequestSource, VerificationMethod, VerificationPurpose
    from whatsapp_consent.erasure import erase_subject
    from whatsapp_consent.models import ConsentEvent, ConsentEvidence, ConsentState, Subject, Verification

    subject = make_subject(name="Zoya")
    grant(db, subject, service=True, updates=True)
    subject_id, fingerprint = subject.id, subject.phone_fingerprint
    db.add(
        Verification(
            subject_id=subject_id,
            phone_e164=subject.phone_e164,
            purpose=VerificationPurpose.CONSENT,
            method=VerificationMethod.FAKE,
            code_hash="x",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            pending={"ip": "10.0.0.1", "user_agent": "test-agent"},
        )
    )
    db.flush()

    request = erase_subject(db, subject, source=DataRequestSource.SUBJECT)

    assert db.get(Subject, subject_id) is None
    assert (
        count(db, Verification, Verification.pending.is_not(None), Verification.phone_e164 == subject.phone_e164) == 0
    )
    assert (request.subject_id, request.phone_fingerprint, request.source) == (None, fingerprint, "subject")
    assert request.completed_at is not None
    events = list(db.scalars(select(ConsentEvent).where(ConsentEvent.phone_fingerprint == fingerprint)))
    assert sorted((e.purpose_code, e.action, e.source) for e in events) == [
        ("service", "granted", "consent_page"),
        ("service", "withdrawn", "preferences_page"),
        ("updates", "granted", "consent_page"),
        ("updates", "withdrawn", "preferences_page"),
    ]
    assert all(e.subject_id is None for e in events)
    assert count(db, ConsentEvidence, ConsentEvidence.event_id.in_([e.id for e in events])) == 0
    assert count(db, ConsentState, ConsentState.subject_id == subject_id) == 0


def test_the_host_hook_runs_in_the_same_transaction(db, make_subject):
    from whatsapp_consent.enums import DataRequestSource
    from whatsapp_consent.erasure import erase_subject
    from whatsapp_consent.models import Subject

    subject = make_subject()
    subject_id = subject.id
    seen = []

    def hook(session, owner_id, erased_id):
        seen.append((session is db, owner_id, erased_id))

    erase_subject(db, subject, source=DataRequestSource.OWNER, on_erased=hook)
    assert seen == [(True, subject.owner_id, subject_id)]

    other = make_subject()
    other_id = other.id

    def failing_hook(session, owner_id, erased_id):
        raise RuntimeError("host could not delete its data")

    # A savepoint stands in for the host's transaction: the hook's failure undoes the whole erasure.
    with pytest.raises(RuntimeError), db.begin_nested():
        erase_subject(db, other, source=DataRequestSource.OWNER, on_erased=failing_hook)
    assert db.get(Subject, other_id) is not None  # nothing was erased


def test_audit_by_phone_still_works_after_erasure(db, make_subject, owner_id):
    from whatsapp_consent.audit import audit_phone
    from whatsapp_consent.config import ConsentSettings
    from whatsapp_consent.enums import DataRequestSource
    from whatsapp_consent.erasure import erase_subject

    phone = random_phone()
    subject = make_subject(phone=phone)
    grant(db, subject, updates=True)
    erase_subject(db, subject, source=DataRequestSource.SUBJECT)
    record = audit_phone(db, owner_id, phone, key=ConsentSettings().fingerprint_key)  # pyright: ignore[reportCallIssue]
    assert [(e.purpose, e.action, e.subject_id) for e in record.events] == [
        ("updates", "granted", None),
        ("updates", "withdrawn", None),
    ]
    assert [r.source for r in record.erasure_requests] == ["subject"]


def test_after_deletion_no_table_holds_the_persons_details(client, owner, engine):
    import example_host

    _, headers = owner()
    child, parent, phone = f"Zed{secrets.token_hex(3)}", f"Pia{secrets.token_hex(3)}", local_phone()
    subject_id, path = add_subject(client, headers, display_name=child, phone=phone, is_minor=True)
    consent_and_verify(client, path, who="child", data=guardian(child_name=child, guardian_name=parent, phone=phone))
    assert client.delete(f"/subjects/{subject_id}", headers=headers).status_code == 204
    assert example_host.ERASED[-1][1] == uuid.UUID(subject_id)

    with engine.connect() as conn:
        columns = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'whatsapp_consent' "
                "AND data_type IN ('text', 'character varying', 'USER-DEFINED', 'ARRAY', 'jsonb')"
            )
        ).all()
        for table, column in columns:
            for needle in (child, parent, phone, "+91" + phone):
                found = conn.execute(
                    text(f'SELECT count(*) FROM whatsapp_consent."{table}" WHERE "{column}"::text ILIKE :needle'),
                    {"needle": f"%{needle}%"},
                ).scalar()
                assert found == 0, f"{needle!r} left in {table}.{column}"


def test_only_the_owner_can_remove_their_subject(client, owner):
    _, mine = owner()
    _, theirs = owner()
    subject_id, _ = add_subject(client, mine)
    assert client.delete(f"/subjects/{subject_id}", headers=theirs).status_code == 404
    assert client.delete(f"/subjects/{subject_id}", headers=mine).status_code == 204
    assert client.get(f"/subjects/{subject_id}", headers=mine).status_code == 404
    assert client.get("/subjects/activity", headers=mine).json() == []  # the owner's own removals aren't listed


def test_activity_lists_subject_deletions_without_details(client, owner, kit):
    from whatsapp_consent.enums import DataRequestSource

    _, headers = owner()
    subject_id, _ = add_subject(client, headers)
    with committed_session() as db:
        kit.erase_subject(db, uuid.UUID(subject_id), DataRequestSource.SUBJECT)
    (item,) = client.get("/subjects/activity", headers=headers).json()
    assert item["kind"] == "subject_deleted_data" and set(item) == {"kind", "occurred_at"}


def test_code_page_after_the_owner_removed_the_subject(client, owner):
    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    assert submit(client, path, "me", adult()).status_code == 303
    client.delete(f"/subjects/{subject_id}", headers=headers)
    res = client.get("/c/verify")
    assert res.status_code == 410 and "isn't valid any more" in text_of(res)


def test_cleanup_removes_only_old_unverified_practice_signups(db, make_subject):
    from whatsapp_consent.enums import SubjectSource
    from whatsapp_consent.erasure import cleanup_unverified
    from whatsapp_consent.models import Subject

    now = datetime.now(UTC)
    old, young = now - timedelta(days=31), now - timedelta(days=29)
    subjects = {
        "old": make_subject(source=SubjectSource.PRACTICE_LINK, verified=False, needs_review=True, created_at=old),
        "recent": make_subject(source=SubjectSource.PRACTICE_LINK, verified=False, needs_review=True, created_at=young),
        "verified": make_subject(source=SubjectSource.PRACTICE_LINK, verified=True, created_at=old),
        "personal": make_subject(verified=False, created_at=old),
    }
    ids = {name: s.id for name, s in subjects.items()}
    assert cleanup_unverified(db, now=now) == 1
    assert db.get(Subject, ids["old"]) is None
    assert all(db.get(Subject, ids[name]) is not None for name in ("recent", "verified", "personal"))


def test_purge_removes_only_expired_anonymised_rows(db, make_subject):
    from whatsapp_consent.enums import DataRequestSource, VerificationMethod, VerificationPurpose
    from whatsapp_consent.erasure import erase_subject, purge
    from whatsapp_consent.models import ConsentEvent, DataRequest, RateLimitEvent, Verification

    now = datetime.now(UTC)
    long_ago = now - timedelta(days=400)

    expired = make_subject()
    expired_fp = expired.phone_fingerprint
    grant(db, expired, now=long_ago, service=True)
    erase_subject(db, expired, source=DataRequestSource.SUBJECT, now=long_ago)

    live = make_subject()
    live_id = live.id
    grant(db, live, now=long_ago, service=True)

    recent = make_subject()
    recent_fp = recent.phone_fingerprint
    grant(db, recent, now=now - timedelta(days=20), service=True)
    erase_subject(db, recent, source=DataRequestSource.SUBJECT, now=now - timedelta(days=10))

    old_phone = random_phone()
    db.add(
        Verification(
            phone_e164=old_phone,
            purpose=VerificationPurpose.CONSENT,
            method=VerificationMethod.FAKE,
            code_hash="x",
            expires_at=now - timedelta(days=3),
            created_at=now - timedelta(days=3),
        )
    )
    db.add(RateLimitEvent(bucket="test-purge", key_hash="x", created_at=now - timedelta(days=3)))
    db.flush()

    result = purge(db, retention_days=365, now=now)

    assert count(db, ConsentEvent, ConsentEvent.phone_fingerprint == expired_fp) == 0
    assert count(db, DataRequest, DataRequest.phone_fingerprint == expired_fp) == 0
    assert count(db, ConsentEvent, ConsentEvent.subject_id == live_id) == 1  # live subjects are never purged
    assert count(db, ConsentEvent, ConsentEvent.phone_fingerprint == recent_fp) == 2
    assert count(db, DataRequest, DataRequest.phone_fingerprint == recent_fp) == 1
    assert count(db, Verification, Verification.phone_e164 == old_phone) == 0
    assert count(db, RateLimitEvent, RateLimitEvent.bucket == "test-purge") == 0
    assert result.consent_events >= 2 and result.data_requests >= 1


def test_erasing_an_owner(db, make_subject, owner_id):
    from whatsapp_consent.erasure import erase_owner
    from whatsapp_consent.models import Subject

    make_subject()
    make_subject(name="Ravi")
    other = make_subject(owner=uuid.uuid4())
    other_id = other.id
    assert erase_owner(db, owner_id) == 2
    assert count(db, Subject, Subject.owner_id == owner_id) == 0
    assert db.get(Subject, other_id) is not None


def test_maintenance_command_only_purges_by_default(capsys):
    from whatsapp_consent.cli import main

    main(["maintenance"])
    out = json.loads(capsys.readouterr().out)
    assert {"consent_events", "data_requests", "preference_links"} <= set(out)
    assert "unverified_signups_removed" not in out
    main(["maintenance", "--cleanup-unverified-without-host-hook"])
    out = json.loads(capsys.readouterr().out)
    assert "unverified_signups_removed" in out


def test_purge_keeps_events_while_the_erasure_request_is_within_retention(db, make_subject):
    from whatsapp_consent.enums import DataRequestSource
    from whatsapp_consent.erasure import erase_subject, purge
    from whatsapp_consent.models import ConsentEvent

    now = datetime.now(UTC)
    fresh, stale = make_subject(), make_subject()
    fresh_fp, stale_fp = fresh.phone_fingerprint, stale.phone_fingerprint
    for subject, erased_days_ago in ((fresh, 1), (stale, 366)):
        grant(db, subject, now=now - timedelta(days=400), service=True)
        erase_subject(db, subject, source=DataRequestSource.SUBJECT, now=now - timedelta(days=erased_days_ago))
    purge(db, retention_days=365, now=now)
    kept = list(db.scalars(select(ConsentEvent.action).where(ConsentEvent.phone_fingerprint == fresh_fp)))
    assert sorted(kept) == ["granted", "withdrawn"]
    assert count(db, ConsentEvent, ConsentEvent.phone_fingerprint == stale_fp) == 0


def test_cleanup_skips_signups_with_a_live_verification(db, make_subject):
    from whatsapp_consent.enums import SubjectSource, VerificationMethod, VerificationPurpose
    from whatsapp_consent.erasure import cleanup_unverified
    from whatsapp_consent.models import Subject, Verification

    now = datetime.now(UTC)
    subject = make_subject(
        source=SubjectSource.PRACTICE_LINK, verified=False, needs_review=True, created_at=now - timedelta(days=31)
    )
    subject_id = subject.id
    db.add(
        Verification(
            subject_id=subject_id,
            phone_e164=subject.phone_e164,
            purpose=VerificationPurpose.CONSENT,
            method=VerificationMethod.FAKE,
            code_hash="x",
            expires_at=now + timedelta(minutes=5),
        )
    )
    db.flush()
    assert cleanup_unverified(db, now=now) == 0
    assert db.get(Subject, subject_id) is not None
    assert cleanup_unverified(db, now=now + timedelta(minutes=6)) == 1


def test_erasure_keeps_phone_only_verifications_while_another_owner_has_the_number(db, make_subject):
    from whatsapp_consent.enums import DataRequestSource, VerificationMethod, VerificationPurpose
    from whatsapp_consent.erasure import erase_subject
    from whatsapp_consent.models import Verification

    phone = random_phone()
    mine, theirs = make_subject(phone=phone), make_subject(phone=phone, owner=uuid.uuid4())
    del theirs
    db.add(
        Verification(
            phone_e164=phone,
            purpose=VerificationPurpose.PREFERENCES,
            method=VerificationMethod.FAKE,
            code_hash="x",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    db.flush()
    erase_subject(db, mine, source=DataRequestSource.OWNER)
    assert count(db, Verification, Verification.phone_e164 == phone) == 1


def test_activity_is_per_owner(client, owner, kit):
    from whatsapp_consent.enums import DataRequestSource

    _, mine = owner()
    _, theirs = owner()
    mine_id, _ = add_subject(client, mine)
    add_subject(client, theirs)
    with committed_session() as db:
        kit.erase_subject(db, uuid.UUID(mine_id), DataRequestSource.SUBJECT)
    assert len(client.get("/subjects/activity", headers=mine).json()) == 1
    assert client.get("/subjects/activity", headers=theirs).json() == []


def test_erasure_removes_unverified_rows_held_for_the_number_under_another_subject(db, make_subject):
    from whatsapp_consent.enums import DataRequestSource, VerificationMethod, VerificationPurpose
    from whatsapp_consent.erasure import erase_subject
    from whatsapp_consent.models import Verification

    phone = random_phone()
    owner = uuid.uuid4()
    erased, other = make_subject(owner=owner, phone=phone, name="Y"), make_subject(owner=owner, name="X")
    db.add(
        Verification(
            subject_id=other.id,  # a personal link for X, where the person typed Y's number
            phone_e164=phone,
            purpose=VerificationPurpose.CONSENT,
            method=VerificationMethod.FAKE,
            code_hash="x",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            pending={"ip": "10.0.0.1", "user_agent": "test-agent", "submission": {"display_name": "X2"}},
        )
    )
    db.flush()
    erase_subject(db, erased, source=DataRequestSource.SUBJECT)
    assert count(db, Verification, Verification.phone_e164 == phone) == 0
