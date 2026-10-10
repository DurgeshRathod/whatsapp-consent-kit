import os
import uuid
from urllib.parse import urlparse

import pytest
from conftest import committed_session, local_phone


def add(client, headers, **body):
    payload = {"display_name": "Asha", "phone": local_phone()} | body
    res = client.post("/subjects", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def token_of(link: str) -> str:
    return urlparse(link).path.rsplit("/", 1)[1]


def grant_committed(subject_id: str, **wanted):
    from whatsapp_consent.enums import ConsentSource, GivenBy
    from whatsapp_consent.events import set_consent
    from whatsapp_consent.models import Subject

    with committed_session() as db:
        subject = db.get(Subject, uuid.UUID(subject_id))
        assert subject is not None
        set_consent(db, subject, wanted, source=ConsentSource.CONSENT_PAGE, given_by=GivenBy.SELF)


def test_requests_without_an_owner_are_refused(client):
    assert client.get("/subjects").status_code == 401


def test_add_subject_returns_a_share_link(client, owner):
    _, headers = owner()
    created = add(client, headers, phone="98765 43210", birthday_day=12, birthday_month=3)
    subject = created["subject"]
    assert (subject["phone"], subject["status"]) == ("+919876543210", "invited")
    assert subject["can_send"] == {"service": False, "updates": False}
    assert subject["blockers"]["updates"] == ["no_consent", "unverified"]
    assert (subject["birthday_day"], subject["birthday_month"]) == (12, 3)
    invite = created["invite"]
    assert invite["link"].startswith("http://testserver/c/")
    assert invite["whatsapp_url"].startswith("https://wa.me/919876543210?text=")
    assert "Priya Nutrition" in invite["share_message"] and invite["link"] in invite["share_message"]


def test_share_message_follows_the_owner_language(client, owner):
    _, headers = owner(language="hi")
    assert "नमस्ते" in add(client, headers)["invite"]["share_message"]


def test_minor_share_message_is_addressed_to_the_parent(client, owner):
    _, headers = owner()
    created = add(client, headers, display_name="Aarav", is_minor=True)
    message = created["invite"]["share_message"]
    assert message.startswith("Hi, please confirm WhatsApp updates from Priya Nutrition for Aarav")


@pytest.mark.parametrize(
    "body", [{"phone": "12345"}, {"birthday_day": 12}, {"birthday_day": 30, "birthday_month": 2}, {"display_name": " "}]
)
def test_add_subject_validation(client, owner, body):
    _, headers = owner()
    res = client.post("/subjects", json={"display_name": "Asha", "phone": local_phone()} | body, headers=headers)
    assert res.status_code == 422


def test_same_name_and_number_twice_is_a_conflict(client, owner):
    _, headers = owner()
    phone = local_phone()
    add(client, headers, display_name="Asha", phone=phone)
    res = client.post("/subjects", json={"display_name": "ASHA", "phone": "+91" + phone}, headers=headers)
    assert res.status_code == 409


def test_subjects_are_private_to_their_owner(client, owner):
    _, mine = owner()
    _, theirs = owner()
    subject_id = add(client, mine)["subject"]["id"]
    assert client.get(f"/subjects/{subject_id}", headers=theirs).status_code == 404
    for action in ("invite", "approve", "mark-minor"):
        assert client.post(f"/subjects/{subject_id}/{action}", headers=theirs).status_code == 404
    res = client.post(f"/subjects/{subject_id}/withdraw", json={"purpose": "service"}, headers=theirs)
    assert res.status_code == 404
    assert client.get("/subjects", headers=theirs).json() == []


def test_a_new_link_replaces_the_old_one(client, owner):
    from whatsapp_consent.invites import resolve_invite

    _, headers = owner()
    created = add(client, headers)
    old = token_of(created["invite"]["link"])
    new = token_of(client.post(f"/subjects/{created['subject']['id']}/invite", headers=headers).json()["link"])
    with committed_session() as db:
        assert resolve_invite(db, old) is None
        assert resolve_invite(db, new) is not None


def test_withdraw_records_an_owner_event(client, owner):
    _, headers = owner()
    subject_id = add(client, headers)["subject"]["id"]
    grant_committed(subject_id, service=True, updates=True)
    res = client.post(f"/subjects/{subject_id}/withdraw", json={"purpose": "updates"}, headers=headers)
    assert res.json()["consents"] == {"service": True, "updates": False}
    last = client.get(f"/subjects/{subject_id}", headers=headers).json()["history"][-1]
    assert (last["purpose"], last["action"], last["source"]) == ("updates", "withdrawn", "owner_recorded")
    unknown = client.post(f"/subjects/{subject_id}/withdraw", json={"purpose": "newsletter"}, headers=headers)
    assert unknown.status_code == 422


def test_mark_minor_suspends_and_returns_a_guardian_link(client, owner):
    from whatsapp_consent.invites import resolve_invite

    _, headers = owner()
    subject_id = add(client, headers, display_name="Aarav")["subject"]["id"]
    link = client.post(f"/subjects/{subject_id}/mark-minor", headers=headers).json()
    detail = client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert (detail["is_minor"], detail["minor_suspended"], detail["status"]) == (True, True, "suspended")
    with committed_session() as db:
        invite = resolve_invite(db, token_of(link["link"]))
        assert invite is not None and invite.minor_only is True


def test_review_and_status_filters(client, owner):
    from whatsapp_consent.enums import SubjectSource
    from whatsapp_consent.hashing import phone_fingerprint
    from whatsapp_consent.models import Subject

    owner_id, headers = owner()
    invited_id = add(client, headers)["subject"]["id"]
    phone = "+91" + local_phone()
    with committed_session() as db:
        db.add(
            Subject(
                owner_id=owner_id,
                display_name="Walk-in",
                phone_e164=phone,
                phone_fingerprint=phone_fingerprint(os.environ["CONSENT_FINGERPRINT_KEY"], phone),
                source=SubjectSource.PRACTICE_LINK,
                needs_review=True,
            )
        )
    review = client.get("/subjects", params={"status": "needs_review"}, headers=headers).json()
    assert [s["display_name"] for s in review] == ["Walk-in"]
    invited = client.get("/subjects", params={"status": "invited"}, headers=headers).json()
    assert [s["id"] for s in invited] == [invited_id]
    approved = client.post(f"/subjects/{review[0]['id']}/approve", headers=headers).json()
    assert (approved["needs_review"], approved["status"]) == (False, "invited")
    assert client.get("/subjects", params={"status": "nonsense"}, headers=headers).status_code == 422


def test_status_by_phone_and_sendable(client, owner):
    from whatsapp_consent.models import Subject

    _, headers = owner()
    phone = local_phone()
    first = add(client, headers, display_name="Aarav", phone=phone)["subject"]["id"]
    add(client, headers, display_name="Diya", phone=phone)
    grant_committed(first, updates=True)
    with committed_session() as db:
        from datetime import UTC, datetime

        subject = db.get(Subject, uuid.UUID(first))
        assert subject is not None
        subject.phone_verified_at = datetime.now(UTC)

    found = client.get("/subjects/status", params={"phone": f"+91 {phone[:5]} {phone[5:]}"}, headers=headers).json()
    assert sorted(s["display_name"] for s in found) == ["Aarav", "Diya"]
    assert client.get("/subjects/status", params={"phone": "123"}, headers=headers).status_code == 422

    sendable = client.get("/subjects/sendable", params={"purpose": "updates"}, headers=headers).json()
    assert sendable == {"purpose": "updates", "count": 1, "subject_ids": [first]}
    assert client.get("/subjects/sendable", params={"purpose": "nope"}, headers=headers).status_code == 422


def test_practice_link_is_stable_until_rotated(client, owner):
    from whatsapp_consent.invites import resolve_invite

    _, headers = owner()
    first = client.get("/subjects/practice-link", headers=headers).json()
    assert first == client.get("/subjects/practice-link", headers=headers).json()
    assert first["link"].startswith("http://testserver/c/")
    assert first["qr_svg"].startswith("<svg")
    rotated = client.post("/subjects/practice-link/rotate", headers=headers).json()
    assert rotated["link"] != first["link"]
    with committed_session() as db:
        assert resolve_invite(db, token_of(first["link"])) is None
        active = resolve_invite(db, token_of(rotated["link"]))
        assert active is not None and active.kind == "practice"


def test_kit_python_api(kit, client, owner):
    owner_id, headers = owner()
    subject_id = add(client, headers)["subject"]["id"]
    with committed_session() as db:
        state = kit.status(db, uuid.UUID(subject_id))
        assert state.status == "invited"
        assert kit.can_send(db, uuid.UUID(subject_id), "service") is False
        assert kit.sendable_subjects(db, owner_id, "service") == []
        assert kit.audit(db, uuid.UUID(subject_id)).events == []
