import os
import uuid
from itertools import product

import pytest
from conftest import random_phone
from sqlalchemy import select


def events_for(db, subject_id):
    from whatsapp_consent.models import ConsentEvent

    query = select(ConsentEvent).where(ConsentEvent.subject_id == subject_id).order_by(ConsentEvent.occurred_at)
    return list(db.scalars(query))


def grant(db, subject, **wanted):
    from whatsapp_consent.enums import ConsentSource, GivenBy
    from whatsapp_consent.events import set_consent

    return set_consent(db, subject, wanted, source=ConsentSource.CONSENT_PAGE, given_by=GivenBy.SELF)


def test_grant_and_withdraw_are_recorded(db, make_subject):
    from whatsapp_consent.enums import ConsentSource, GivenBy, Language
    from whatsapp_consent.events import current_consent, set_consent
    from whatsapp_consent.notices import latest_notice

    subject = make_subject()
    notice = latest_notice(db, Language.HI)
    set_consent(
        db,
        subject,
        {"service": True, "updates": False},
        source=ConsentSource.CONSENT_PAGE,
        given_by=GivenBy.SELF,
        notice=notice,
    )
    assert current_consent(db, subject.id) == {"service": True, "updates": False}
    set_consent(db, subject, {"service": False}, source=ConsentSource.PREFERENCES_PAGE, given_by=GivenBy.SELF)
    assert current_consent(db, subject.id) == {"service": False, "updates": False}

    events = events_for(db, subject.id)
    assert [(e.purpose_code, e.action, e.source, e.notice_version, e.notice_language) for e in events] == [
        ("service", "granted", "consent_page", 1, "hi"),
        ("service", "withdrawn", "preferences_page", None, None),
    ]
    assert all(e.verified_at_time and e.phone_fingerprint == subject.phone_fingerprint for e in events)
    assert all(e.owner_id == subject.owner_id for e in events)


def test_consent_page_reaffirms_but_other_sources_record_only_changes(db, make_subject):
    from whatsapp_consent.enums import ConsentSource, GivenBy
    from whatsapp_consent.events import set_consent

    subject = make_subject()
    grant(db, subject, service=True)
    grant(db, subject, service=True)
    set_consent(db, subject, {"service": True}, source=ConsentSource.PREFERENCES_PAGE, given_by=GivenBy.SELF)
    set_consent(db, subject, {"updates": False}, source=ConsentSource.OWNER_RECORDED, given_by=GivenBy.SELF)
    assert [e.source for e in events_for(db, subject.id)] == ["consent_page", "consent_page"]


def test_unknown_purpose_is_a_programming_error(db, make_subject):
    from whatsapp_consent.events import UnknownPurpose

    with pytest.raises(UnknownPurpose):
        grant(db, make_subject(), newsletter=True)


def test_evidence_is_kept_with_a_trimmed_browser_string(db, make_subject):
    from whatsapp_consent.enums import ConsentSource, GivenBy
    from whatsapp_consent.events import Evidence, set_consent
    from whatsapp_consent.models import ConsentEvidence

    subject = make_subject()
    (event,) = set_consent(
        db,
        subject,
        {"updates": True},
        source=ConsentSource.CONSENT_PAGE,
        given_by=GivenBy.GUARDIAN,
        evidence=Evidence("10.1.2.3", "x" * 900),
    )
    evidence = db.get(ConsentEvidence, event.id)
    assert (evidence.ip, len(evidence.user_agent), event.given_by) == ("10.1.2.3", 500, "guardian")


def test_latest_notice_per_language(db):
    from whatsapp_consent.enums import Language
    from whatsapp_consent.notices import latest_notice

    assert latest_notice(db, Language.HI).language == "hi"
    assert latest_notice(db, Language.EN).version == 1


@pytest.mark.parametrize("granted,verified,needs_review,suspended", list(product([True, False], repeat=4)))
def test_send_check(db, make_subject, granted, verified, needs_review, suspended):
    from whatsapp_consent.gate import can_send

    subject = make_subject(verified=verified, needs_review=needs_review, is_minor=suspended, minor_suspended=suspended)
    if granted:
        grant(db, subject, updates=True)
    assert can_send(db, subject.id, "updates") is (granted and verified and not needs_review and not suspended)
    assert can_send(db, subject.id, "service") is False


def test_send_blockers_explain_why(make_subject):
    from whatsapp_consent.enums import SendBlocker
    from whatsapp_consent.gate import send_blockers

    subject = make_subject(verified=False, needs_review=True)
    assert send_blockers(subject, granted=False) == [
        SendBlocker.NO_CONSENT,
        SendBlocker.UNVERIFIED,
        SendBlocker.NEEDS_REVIEW,
    ]


def test_send_check_by_category(db, make_subject):
    from whatsapp_consent.enums import TemplateCategory
    from whatsapp_consent.gate import can_send_category

    subject = make_subject()
    grant(db, subject, updates=True)
    assert can_send_category(db, subject.id, TemplateCategory.MARKETING) is True
    assert can_send_category(db, subject.id, TemplateCategory.UTILITY) is False
    assert can_send_category(db, subject.id, TemplateCategory.AUTHENTICATION) is True


def test_category_check_refuses_when_two_purposes_share_it(db, make_subject):
    from whatsapp_consent.enums import TemplateCategory
    from whatsapp_consent.gate import AmbiguousCategory, can_send_category
    from whatsapp_consent.models import Purpose

    db.add(Purpose(code="tips", category=TemplateCategory.MARKETING, labels={}, position=3))
    db.flush()
    with pytest.raises(AmbiguousCategory):
        can_send_category(db, make_subject().id, TemplateCategory.MARKETING)


def test_unknown_subject_cannot_be_messaged(db):
    from whatsapp_consent.enums import TemplateCategory
    from whatsapp_consent.gate import can_send, can_send_category

    assert can_send(db, uuid.uuid4(), "service") is False
    assert can_send_category(db, uuid.uuid4(), TemplateCategory.AUTHENTICATION) is False


def test_sendable_subjects_matches_the_send_check(db, make_subject, owner_id):
    from whatsapp_consent.gate import can_send, sendable_subjects

    subjects = [
        make_subject(),
        make_subject(verified=False),
        make_subject(needs_review=True),
        make_subject(is_minor=True, minor_suspended=True),
        make_subject(),
    ]
    for subject in subjects[:4]:
        grant(db, subject, updates=True)
    make_subject(owner=uuid.uuid4())  # another owner's subject
    expected = [s.id for s in subjects if can_send(db, s.id, "updates")]
    assert sendable_subjects(db, owner_id, "updates") == expected == [subjects[0].id]


def test_status_explains_each_subject(db, make_subject, owner_id):
    from whatsapp_consent.enums import SendBlocker, SubjectStatus
    from whatsapp_consent.status import state_of

    invited = make_subject(verified=False)
    assert state_of(db, invited).status == SubjectStatus.INVITED

    subject = make_subject(verified=False)
    grant(db, subject, updates=True)
    state = state_of(db, subject)
    assert (state.status, state.consents) == (SubjectStatus.UNVERIFIED, {"service": False, "updates": True})
    assert state.blockers == {
        "service": [SendBlocker.NO_CONSENT, SendBlocker.UNVERIFIED],
        "updates": [SendBlocker.UNVERIFIED],
    }
    assert (state.notice_version, state.last_changed_at is not None) == (None, True)
    assert state.can_send("updates") is False


def test_status_by_phone_accepts_any_format_and_returns_siblings(db, make_subject, owner_id):
    from whatsapp_consent.status import status_by_phone

    key = os.environ["CONSENT_FINGERPRINT_KEY"]
    make_subject(name="Aarav", phone="+919812345678")
    make_subject(name="Diya", phone="+919812345678")
    make_subject(owner=uuid.uuid4(), name="Other", phone="+919812345678")
    names = sorted(s.display_name for s in status_by_phone(db, owner_id, "098123 45678", key=key))
    assert names == ["Aarav", "Diya"]
    assert status_by_phone(db, owner_id, random_phone(), key=key) == []


def test_audit_lists_the_history(db, make_subject):
    from whatsapp_consent.audit import audit_subject
    from whatsapp_consent.enums import ConsentSource, GivenBy
    from whatsapp_consent.events import set_consent

    subject = make_subject()
    grant(db, subject, service=True, updates=True)
    set_consent(db, subject, {"updates": False}, source=ConsentSource.OWNER_RECORDED, given_by=GivenBy.SELF)
    record = audit_subject(db, subject.id)
    assert record is not None
    assert record.owner_id == subject.owner_id
    assert [(e.purpose, e.action) for e in record.events][-1] == ("updates", "withdrawn")
    assert len(record.events) == 3
    assert audit_subject(db, uuid.uuid4()) is None
