import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import pytest
from conftest import committed_session, local_phone
from helpers import (
    add_subject,
    adult,
    consent_and_verify,
    csrf_of,
    e164,
    enter_code,
    guardian,
    path_of,
    practice_path,
    submit,
    text_of,
)
from sqlalchemy import select, text


def detail(client, headers, subject_id):
    return client.get(f"/subjects/{subject_id}", headers=headers).json()


def test_personal_link_full_flow(client, owner):
    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    start = text_of(client.get(path))
    assert "Priya Nutrition" in start and "Who is this for?" in start

    data = adult()
    submitted = submit(client, path, "me", data)
    assert submitted.status_code == 303 and submitted.headers["location"].startswith("/c/verify")
    assert "default-src 'none'" in submitted.headers["content-security-policy"]
    assert detail(client, headers, subject_id)["status"] == "invited"  # held until the number is verified

    verified = enter_code(client, e164(data["phone"]))
    assert verified.status_code == 303 and verified.headers["location"].startswith("/c/done")
    done = text_of(client.get("/c/done"))
    assert "You're all set" in done and "/p/" in done

    info = detail(client, headers, subject_id)
    assert (info["status"], info["verified"]) == ("active", True)
    assert info["consents"] == info["can_send"] == {"service": True, "updates": True}
    assert (info["birthday_day"], info["birthday_month"]) == (12, 3)
    assert {h["given_by"] for h in info["history"]} == {"self"}
    assert {h["notice_version"] for h in info["history"]} == {1}


def test_purposes_come_from_the_host(client, owner):
    _, headers = owner()
    _, path = add_subject(client, headers)
    page = text_of(client.get(path, params={"who": "me", "lang": "hi"}))
    assert "सेवा संदेश" in page and "अपडेट और ऑफ़र" in page
    assert 'value="service"' in page and 'value="updates"' in page


def test_personal_link_is_prefilled(client, owner):
    _, headers = owner()
    phone = local_phone()
    _, path = add_subject(client, headers, display_name="Ravi", phone=phone)
    page = text_of(client.get(path, params={"who": "me"}))
    assert 'value="Ravi"' in page and f'value="{phone[:5]} {phone[5:]}"' in page


def test_year_of_birth_is_not_stored(client, owner, engine):
    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    consent_and_verify(client, path, data=adult(birth_year="1987"))
    with engine.connect() as conn:
        row = (
            conn.execute(text("SELECT * FROM whatsapp_consent.subjects WHERE id = :id"), {"id": subject_id})
            .mappings()
            .one()
        )
    assert "1987" not in " ".join(str(value) for value in row.values())


def test_practice_link_signups_wait_for_review(client, owner):
    _, headers = owner()
    consent_and_verify(client, practice_path(client, headers), data=adult(name="Walk-in"))
    (signup,) = client.get("/subjects", params={"status": "needs_review"}, headers=headers).json()
    assert signup["display_name"] == "Walk-in" and signup["can_send"]["service"] is False
    approved = client.post(f"/subjects/{signup['id']}/approve", headers=headers).json()
    assert (approved["status"], approved["can_send"]["service"]) == ("active", True)


def test_practice_link_updates_a_subject_typed_differently(client, owner, relaxed_limits):
    _, headers = owner()
    path = practice_path(client, headers)
    phone = local_phone()
    consent_and_verify(client, path, data=adult(name="Neha", phone=phone))
    consent_and_verify(client, path, data=adult(name="neha", phone=f"+91 {phone[:5]}-{phone[5:]}"))
    assert len(client.get("/subjects", headers=headers).json()) == 1


def test_guardian_flow(client, owner):
    _, headers = owner()
    subject_id, path = add_subject(client, headers, display_name="Aarav", is_minor=True)
    first = text_of(client.get(path))
    assert "Your name (parent or guardian)" in first and "Who is this for?" not in first
    consent_and_verify(client, path, who="child")
    info = detail(client, headers, subject_id)
    assert (info["is_minor"], info["guardian_name"], info["guardian_relation"], info["status"]) == (
        True,
        "Meera",
        "Mother",
        "active",
    )
    assert {h["given_by"] for h in info["history"]} == {"guardian"}


def test_minor_only_link_refuses_the_adult_path(client, owner):
    _, headers = owner()
    _, path = add_subject(client, headers, is_minor=True)
    res = submit(client, path, "me", adult())
    assert res.status_code == 422 and "relationship to the child" in text_of(res)


def test_under_18_is_sent_to_the_guardian_form(client, owner):
    from whatsapp_consent.senders import fake_sender

    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    sent_before = len(fake_sender.sent)
    res = submit(client, path, "me", adult(birth_year=str(datetime.now().year - 15)))
    page = text_of(res)
    assert res.status_code == 200
    assert "a parent or guardian needs to fill this in" in page and 'name="guardian_name"' in page
    assert len(fake_sender.sent) == sent_before
    assert detail(client, headers, subject_id)["status"] == "invited"


def test_marking_a_minor_then_guardian_consent_lifts_the_suspension(client, owner, relaxed_limits):
    _, headers = owner()
    subject_id, path = add_subject(client, headers, display_name="Kabir")
    consent_and_verify(client, path)
    guardian_path = path_of(client.post(f"/subjects/{subject_id}/mark-minor", headers=headers).json()["link"])
    assert detail(client, headers, subject_id)["can_send"]["updates"] is False
    consent_and_verify(client, guardian_path, who="child", data=guardian(child_name="Kabir"))
    info = detail(client, headers, subject_id)
    assert (info["minor_suspended"], info["status"], info["can_send"]["updates"]) == (False, "active", True)


@pytest.mark.parametrize(
    "change,message",
    [
        ({"purposes": []}, "Choose at least one type of message."),
        ({"purposes": ["newsletter"]}, "Choose at least one type of message."),
        ({"phone": "12345"}, "Please enter a valid WhatsApp number."),
        ({"birthday_day": "31", "birthday_month": "2"}, "Please choose a valid birthday"),
        ({"name": "  "}, "Please enter your name."),
        ({"birth_year": "abc"}, "Please enter your year of birth."),
    ],
)
def test_form_errors(client, owner, change, message):
    _, headers = owner()
    _, path = add_subject(client, headers)
    res = submit(client, path, "me", adult(**change))
    assert res.status_code == 422 and message in text_of(res)


def test_child_form_needs_the_declaration(client, owner):
    _, headers = owner()
    _, path = add_subject(client, headers)
    res = submit(client, path, "child", guardian(declaration=""))
    assert res.status_code == 422
    assert "Please confirm you are the child's parent or guardian." in text_of(res)


def test_hindi_errors_and_steps_stay_in_hindi(client, owner):
    _, headers = owner()
    _, path = add_subject(client, headers)
    res = submit(client, path, "me", adult(purposes=[]), lang="hi")
    assert res.status_code == 422 and '<html lang="hi">' in res.text and "कम से कम एक" in text_of(res)
    assert submit(client, path, "me", adult(), lang="hi").status_code == 303
    page = client.get("/c/verify", params={"lang": "hi"})
    assert '<html lang="hi">' in page.text and "कोड डालें" in text_of(page)


def test_hindi_is_the_default_for_hindi_owners(client, owner):
    _, headers = owner(language="hi")
    _, path = add_subject(client, headers)
    assert '<html lang="hi">' in client.get(path).text


def test_completed_link_shows_already_confirmed(client, owner):
    _, headers = owner()
    _, path = add_subject(client, headers)
    consent_and_verify(client, path)
    assert "already confirmed" in text_of(client.get(path))
    assert client.post(path, data={"csrf": "x", "lang": "en", "who": "me"} | adult()).status_code == 403
    _, other_path = add_subject(client, headers)
    csrf = csrf_of(client.get(other_path, params={"who": "me"}))
    again = client.post(path, data={"csrf": csrf, "lang": "en", "who": "me"} | adult())
    assert "already confirmed" in text_of(again)


def test_double_submit_reuses_the_pending_code(client, owner):
    _, headers = owner()
    _, path = add_subject(client, headers)
    data = adult()
    assert submit(client, path, "me", data).status_code == 303
    assert submit(client, path, "me", data).status_code == 303
    assert enter_code(client, e164(data["phone"])).status_code == 303


def test_replaced_expired_and_unknown_links_are_invalid(client, owner):
    from whatsapp_consent.hashing import hash_token
    from whatsapp_consent.models import Invite

    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    new_path = path_of(client.post(f"/subjects/{subject_id}/invite", headers=headers).json()["link"])
    assert client.get(path).status_code == 410
    token = new_path.rsplit("/", 1)[1]
    with committed_session() as db:
        invite = db.scalar(select(Invite).where(Invite.token_hash == hash_token(token)))
        assert invite is not None
        invite.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert client.get(new_path).status_code == 410
    assert client.get("/c/not-a-real-token").status_code == 410


def test_wrong_csrf_is_refused(client, owner):
    _, headers = owner()
    _, path = add_subject(client, headers)
    client.get(path, params={"who": "me"})
    assert client.post(path, data={"csrf": "wrong", "lang": "en", "who": "me"} | adult()).status_code == 403


def test_parent_with_two_children_gets_two_subjects(client, owner, relaxed_limits):
    _, headers = owner()
    path = practice_path(client, headers)
    phone = local_phone()
    consent_and_verify(client, path, who="child", data=guardian(phone=phone, child_name="Aarav"))
    consent_and_verify(client, path, who="child", data=guardian(phone=phone, child_name="Diya"))
    assert sorted(s["display_name"] for s in client.get("/subjects", headers=headers).json()) == ["Aarav", "Diya"]


def test_wrong_codes_then_lockout(client, owner):
    from whatsapp_consent.senders import fake_sender

    _, headers = owner()
    _, path = add_subject(client, headers)
    data = adult()
    submit(client, path, "me", data)
    phone = e164(data["phone"])
    good = fake_sender.last_code(phone)
    bad = "000000" if good != "000000" else "111111"
    for _ in range(4):
        res = enter_code(client, phone, code=bad)
        assert res.status_code == 422 and "That code isn't right." in text_of(res)
    assert "Too many wrong codes." in text_of(enter_code(client, phone, code=bad))
    assert enter_code(client, phone, code=good).status_code == 422


def test_resend_sends_a_working_code(client, owner, relaxed_limits):
    _, headers = owner()
    _, path = add_subject(client, headers)
    data = adult()
    submit(client, path, "me", data)
    page = client.get("/c/verify")
    res = client.post("/c/verify/resend", data={"csrf": csrf_of(page), "lang": "en"}, follow_redirects=False)
    assert res.status_code == 303 and "sent=1" in res.headers["location"]
    assert "A new code is on its way." in text_of(client.get(res.headers["location"]))
    assert enter_code(client, e164(data["phone"])).status_code == 303


def test_unreachable_number_keeps_nothing(client, owner):
    from whatsapp_consent.senders import fake_sender

    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    data = adult()
    fake_sender.fail_for.add(e164(data["phone"]))
    try:
        res = submit(client, path, "me", data)
    finally:
        fake_sender.fail_for.discard(e164(data["phone"]))
    assert res.status_code == 422 and "couldn't reach this number" in text_of(res)
    assert detail(client, headers, subject_id)["status"] == "invited"


def test_submissions_are_limited_per_ip(client, owner, kit, monkeypatch):
    _, headers = owner()
    _, path = add_subject(client, headers)
    monkeypatch.setattr(kit.settings, "web_submissions_per_ip_hour", 0)
    res = submit(client, path, "me", adult())
    assert res.status_code == 429 and "Too many attempts." in text_of(res)


def test_click_to_chat_verification(click_client, owner):
    from whatsapp_consent.config import ConsentSettings
    from whatsapp_consent.submissions import complete_consent_verification
    from whatsapp_consent.verification import verify_click_code

    _, headers = owner(whatsapp_number="+919800000001")
    subject_id, path = add_subject(click_client, headers)
    data = adult()
    assert submit(click_client, path, "me", data).status_code == 303
    page = text_of(click_client.get("/c/verify"))
    assert "Continue on WhatsApp" in page
    match = re.search(r'href="https://wa\.me/919800000001\?text=([^"]+)"', page)
    assert match
    start_text = unquote(match.group(1))
    assert re.fullmatch(r"START-[A-HJ-NP-Z2-9]{6}", start_text)

    check = click_client.post(
        "/c/verify/check", data={"csrf": csrf_of(click_client.get("/c/verify")), "lang": "en"}, follow_redirects=False
    )
    assert check.status_code == 422 and "haven't received your WhatsApp message" in text_of(check)

    # What the inbound handler (Task 11) does when the message arrives from that number:
    with committed_session() as db:
        key = ConsentSettings().fingerprint_key  # pyright: ignore[reportCallIssue]
        verification = verify_click_code(db, phone=e164(data["phone"]), code=start_text.removeprefix("START-"), key=key)
        assert verification is not None
        complete_consent_verification(db, verification)

    check = click_client.post(
        "/c/verify/check", data={"csrf": csrf_of(click_client.get("/c/verify")), "lang": "en"}, follow_redirects=False
    )
    assert check.status_code == 303 and check.headers["location"].startswith("/c/done")
    assert click_client.get(f"/subjects/{subject_id}", headers=headers).json()["status"] == "active"


def test_click_to_chat_is_off_without_an_owner_number(click_client, owner):
    _, headers = owner()
    _, path = add_subject(click_client, headers)
    submit(click_client, path, "me", adult())
    assert "Continue on WhatsApp" not in text_of(click_client.get("/c/verify"))


# Fix round 1: a submission is held on its verification row and applied only when the number is proven.


def post_check(client):
    page = client.get("/c/verify")
    return client.post("/c/verify/check", data={"csrf": csrf_of(page), "lang": "en"}, follow_redirects=False)


def preference_links_for(phone_e164) -> int:
    from whatsapp_consent.config import ConsentSettings
    from whatsapp_consent.hashing import phone_fingerprint
    from whatsapp_consent.models import PreferenceLink

    key = ConsentSettings().fingerprint_key  # pyright: ignore[reportCallIssue]
    with committed_session() as db:
        rows = db.scalars(
            select(PreferenceLink).where(PreferenceLink.phone_fingerprint == phone_fingerprint(key, phone_e164))
        )
        return len(rows.all())


def inbound_click(start_text, phone_e164):
    """What the inbound handler (Task 11) does when START-XXXXXX arrives from that number."""
    from whatsapp_consent.config import ConsentSettings
    from whatsapp_consent.submissions import complete_consent_verification
    from whatsapp_consent.verification import verify_click_code

    with committed_session() as db:
        key = ConsentSettings().fingerprint_key  # pyright: ignore[reportCallIssue]
        verification = verify_click_code(db, phone=phone_e164, code=start_text.removeprefix("START-"), key=key)
        assert verification is not None
        complete_consent_verification(db, verification)


def start_text_of(client) -> str:
    match = re.search(r'href="https://wa\.me/\d+\?text=([^"]+)"', client.get("/c/verify").text)
    assert match
    return unquote(match.group(1))


def test_click_check_without_click_to_chat_does_not_finish(client, owner, relaxed_limits):
    _, headers = owner()
    data = adult(purposes=["service"])
    subject_id, path = add_subject(client, headers, phone=data["phone"])
    consent_and_verify(client, path, data=data)
    before = detail(client, headers, subject_id)
    links = preference_links_for(e164(data["phone"]))
    new_path = path_of(client.post(f"/subjects/{subject_id}/invite", headers=headers).json()["link"])
    client.cookies.clear()
    assert (
        submit(client, new_path, "me", data | {"name": "Someone Else", "purposes": ["service", "updates"]}).status_code
        == 303
    )
    res = post_check(client)
    assert not (res.status_code == 303 and res.headers["location"].startswith("/c/done"))
    after = detail(client, headers, subject_id)
    assert (after["display_name"], after["can_send"], after["consents"]) == (
        before["display_name"],
        before["can_send"],
        before["consents"],
    )
    assert preference_links_for(e164(data["phone"])) == links


def test_guardian_link_cannot_lift_a_suspension_without_a_code(client, owner, relaxed_limits):
    _, headers = owner()
    data = adult(name="Kabir")
    subject_id, path = add_subject(client, headers, display_name="Kabir", phone=data["phone"])
    consent_and_verify(client, path, data=data)
    guardian_path = path_of(client.post(f"/subjects/{subject_id}/mark-minor", headers=headers).json()["link"])
    stranger = guardian(child_name="Kabir", phone=data["phone"], guardian_name="Stranger")
    assert submit(client, guardian_path, "child", stranger).status_code == 303
    res = post_check(client)
    assert not (res.status_code == 303 and res.headers["location"].startswith("/c/done"))
    info = detail(client, headers, subject_id)
    assert (info["minor_suspended"], info["guardian_name"]) == (True, None)


def test_practice_link_cannot_take_over_a_verified_subject(client, owner, relaxed_limits):
    _, headers = owner()
    data = adult(name="Asha", purposes=["service"])
    subject_id, path = add_subject(client, headers, phone=data["phone"])
    consent_and_verify(client, path, data=data)
    links = preference_links_for(e164(data["phone"]))
    client.cookies.clear()
    practice = practice_path(client, headers)
    assert submit(client, practice, "me", adult(name="asha", phone=data["phone"])).status_code == 303
    res = post_check(client)
    assert not (res.status_code == 303 and res.headers["location"].startswith("/c/done"))
    assert "/p/" not in text_of(client.get("/c/done"))
    assert preference_links_for(e164(data["phone"])) == links
    assert detail(client, headers, subject_id)["can_send"]["updates"] is False


def test_replayed_flow_cookie_verifies_only_its_own_submission(client, owner, relaxed_limits):
    from whatsapp_consent.models import Subject
    from whatsapp_consent.web.render import FLOW_COOKIE

    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    mine, other = local_phone(), local_phone()
    assert submit(client, path, "me", adult(name="Mine", phone=mine, purposes=["service"])).status_code == 303
    flow_a = client.cookies.get(FLOW_COOKIE)
    assert submit(client, path, "me", adult(name="Other", phone=other)).status_code == 303
    flow_b = client.cookies.get(FLOW_COOKIE)
    client.cookies.set(FLOW_COOKIE, flow_a)
    assert enter_code(client, e164(mine)).status_code == 303
    with committed_session() as db:
        subject = db.get(Subject, uuid.UUID(subject_id))
        assert subject is not None
        assert (subject.phone_e164, subject.display_name) == (e164(mine), "Mine")
        assert subject.phone_verified_at is not None
    # B's held submission is never applied, even with B's own code.
    client.cookies.set(FLOW_COOKIE, flow_b)
    res = enter_code(client, e164(other))
    assert not (res.status_code == 303 and res.headers["location"].startswith("/c/done"))
    info = detail(client, headers, subject_id)
    assert (info["display_name"], info["consents"]) == ("Mine", {"service": True, "updates": False})


def test_verified_subject_changes_wait_for_the_code(client, owner, relaxed_limits):
    _, headers = owner()
    data = adult(name="Neha", purposes=["service"])
    subject_id, path = add_subject(client, headers, display_name="Neha", phone=data["phone"])
    consent_and_verify(client, path, data=data)
    before = detail(client, headers, subject_id)
    assert before["can_send"]["updates"] is False
    client.cookies.clear()
    changed = data | {"name": "neha", "birthday_day": "1", "birthday_month": "7", "purposes": ["service", "updates"]}
    assert submit(client, practice_path(client, headers), "me", changed).status_code == 303
    held = detail(client, headers, subject_id)
    assert held["can_send"]["updates"] is False
    assert (held["display_name"], held["birthday_day"], held["birthday_month"]) == ("Neha", 12, 3)
    assert enter_code(client, e164(data["phone"])).status_code == 303
    after = detail(client, headers, subject_id)
    assert after["can_send"]["updates"] is True
    assert (after["display_name"], after["birthday_day"], after["birthday_month"]) == ("neha", 1, 7)


def test_adult_form_is_refused_for_a_recorded_minor(client, owner):
    from whatsapp_consent.models import Verification
    from whatsapp_consent.senders import fake_sender

    _, headers = owner()
    phone = local_phone()
    subject_id, _ = add_subject(client, headers, display_name="Kabir", phone=phone, is_minor=True)
    sent_before = len(fake_sender.sent)
    res = submit(client, practice_path(client, headers), "me", adult(name="Kabir", phone=phone))
    page = text_of(res)
    assert res.status_code == 200 and "This person is recorded as under 18." in page
    assert 'name="guardian_name"' in page
    assert len(fake_sender.sent) == sent_before
    with committed_session() as db:
        held = db.scalars(select(Verification).where(Verification.subject_id == uuid.UUID(subject_id))).all()
        assert held == []


def test_child_submission_on_a_verified_adult_waits_for_the_code(client, owner, relaxed_limits):
    _, headers = owner()
    data = adult(name="Kabir", purposes=["service"])
    subject_id, path = add_subject(client, headers, display_name="Kabir", phone=data["phone"])
    consent_and_verify(client, path, data=data)
    before = detail(client, headers, subject_id)
    new_path = path_of(client.post(f"/subjects/{subject_id}/invite", headers=headers).json()["link"])
    child = guardian(child_name="Kabir", phone=data["phone"])
    assert submit(client, new_path, "child", child).status_code == 303
    held = detail(client, headers, subject_id)
    assert held["can_send"] == before["can_send"] and held["is_minor"] is False
    assert enter_code(client, e164(data["phone"])).status_code == 303
    info = detail(client, headers, subject_id)
    assert (info["is_minor"], info["guardian_name"], info["minor_suspended"]) == (True, "Meera", False)
    assert info["can_send"]["updates"] is True


def test_click_to_chat_end_to_end_applies_once(click_client, owner):
    from whatsapp_consent.models import Verification

    _, headers = owner(whatsapp_number="+919800000001")
    subject_id, path = add_subject(click_client, headers)
    data = adult(name="Ravi", birthday_day="5", birthday_month="9")
    assert submit(click_client, path, "me", data).status_code == 303
    assert detail(click_client, headers, subject_id)["status"] == "invited"
    inbound_click(start_text_of(click_client), e164(data["phone"]))
    res = post_check(click_client)
    assert res.status_code == 303 and res.headers["location"].startswith("/c/done")
    assert "/p/" in text_of(click_client.get("/c/done"))
    info = detail(click_client, headers, subject_id)
    assert (info["status"], info["display_name"], info["birthday_day"]) == ("active", "Ravi", 5)
    assert len(info["history"]) == 2
    with committed_session() as db:
        rows = db.scalars(select(Verification).where(Verification.subject_id == uuid.UUID(subject_id))).all()
        assert all(row.pending is None for row in rows)
