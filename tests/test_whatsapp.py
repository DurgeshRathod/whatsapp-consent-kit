import hashlib
import hmac
import json
import uuid

import pytest
from conftest import committed_session, local_phone
from helpers import add_subject, adult, consent_and_verify, e164, guardian, practice_path, submit, text_of
from sqlalchemy import func, select, text


def payload(*messages, statuses=(), pnid="PNID-1"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "919800000001", "phone_number_id": pnid},
                            "messages": list(messages),
                            "statuses": list(statuses),
                        },
                    }
                ],
            }
        ],
    }


def text_message(phone_e164, body):
    return {
        "from": phone_e164.lstrip("+"),
        "id": "wamid.T",
        "timestamp": "1760000000",
        "type": "text",
        "text": {"body": body},
    }


def button_message(phone_e164, payload_value):
    return {
        "from": phone_e164.lstrip("+"),
        "id": "wamid.B",
        "timestamp": "1760000000",
        "type": "button",
        "button": {"payload": payload_value, "text": "Stop updates"},
    }


def failed_status(phone_e164, code):
    return {
        "id": "wamid.S",
        "status": "failed",
        "timestamp": "1760000000",
        "recipient_id": phone_e164.lstrip("+"),
        "errors": [{"code": code, "title": "Message failed"}],
    }


def inbound(kit, owner_id, body):
    from whatsapp_consent.meta import parse_webhook

    with committed_session() as db:
        return kit.handle_inbound(db, owner_id, parse_webhook(body))


def consents(client, headers, subject_id):
    return client.get(f"/subjects/{subject_id}", headers=headers).json()["consents"]


def verified_subject(client, headers, **body):
    """A subject who consented to both purposes and verified their number."""
    data = adult(phone=body.pop("phone", local_phone()))
    subject_id, path = add_subject(client, headers, phone=data["phone"], **body)
    consent_and_verify(client, path, data=data)
    return subject_id, e164(data["phone"])


# Meta helpers


def test_signature_check():
    from whatsapp_consent.meta import InvalidSignature, verify_signature

    body = b'{"object":"whatsapp_business_account"}'
    good = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    verify_signature(body, good, "app-secret")
    for header in (None, "", "sha256=00", good.replace("sha256=", "sha1=")):
        with pytest.raises(InvalidSignature):
            verify_signature(body, header, "app-secret")


def test_parse_webhook_events():
    from whatsapp_consent.enums import InboundEventType
    from whatsapp_consent.meta import parse_webhook

    interactive = {
        "from": "919811111111",
        "id": "wamid.I",
        "timestamp": "1760000000",
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": "whatsapp_consent:manage", "title": "Manage"}},
    }
    image = {"from": "919811111111", "id": "wamid.X", "timestamp": "1760000000", "type": "image", "image": {}}
    body = payload(
        text_message("+919811111111", "Hello"),
        button_message("+919811111111", "whatsapp_consent:stop:updates"),
        interactive,
        image,
        statuses=[
            failed_status("+919811111111", 131050),
            {"id": "wamid.D", "status": "delivered", "recipient_id": "919811111111"},
        ],
    )
    events = parse_webhook(body)
    assert [e.type for e in events] == [
        InboundEventType.TEXT,
        InboundEventType.BUTTON_REPLY,
        InboundEventType.BUTTON_REPLY,
        InboundEventType.ERROR,
        InboundEventType.STATUS,
    ]
    assert {e.phone for e in events} == {"+919811111111"}
    assert events[0].text == "Hello" and events[0].phone_number_id == "PNID-1"
    assert events[1].button_payload == "whatsapp_consent:stop:updates"
    assert events[2].button_payload == "whatsapp_consent:manage"
    assert events[3].error_codes == (131050,)
    assert parse_webhook({"entry": [{"changes": [{"field": "account_update", "value": {}}]}]}) == []


def test_kit_parse_webhook_checks_the_signature(kit, monkeypatch):
    from whatsapp_consent.meta import InvalidSignature

    monkeypatch.setattr(kit.settings, "meta_app_secret", "app-secret")
    raw = json.dumps(payload(text_message("+919811111111", "STOP"))).encode()
    signature = "sha256=" + hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()
    assert len(kit.parse_webhook(raw, signature)) == 1
    with pytest.raises(InvalidSignature):
        kit.parse_webhook(raw, "sha256=bad")


def test_message_helpers_fit_metas_limits():
    from whatsapp_consent.enums import Language
    from whatsapp_consent.messages import (
        manage_button,
        manage_button_parameter,
        opt_out_footer,
        stop_button,
        stop_button_parameter,
    )

    for language in Language:
        assert len(opt_out_footer(language)) <= 60
        assert "STOP" in opt_out_footer(language)
        assert len(stop_button(language)["text"]) <= 25 and len(manage_button(language)["text"]) <= 25
    parameter = stop_button_parameter("updates", 0)
    assert parameter["parameters"][0]["payload"] == "whatsapp_consent:stop:updates"
    assert manage_button_parameter(1)["index"] == "1"


# Handling inbound events


@pytest.mark.parametrize("word", ["STOP", "stop!", "Unsubscribe", "बंद करें", "रोकें"])
def test_stop_words_withdraw_everything(kit, client, owner, word):
    from whatsapp_consent.enums import InboundOutcomeType

    owner_id, headers = owner()
    subject_id, phone = verified_subject(client, headers)
    (outcome,) = inbound(kit, owner_id, payload(text_message(phone, word)))
    assert outcome.type == InboundOutcomeType.WITHDRAWN and outcome.subject_ids == (uuid.UUID(subject_id),)
    assert consents(client, headers, subject_id) == {"service": False, "updates": False}
    history = client.get(f"/subjects/{subject_id}", headers=headers).json()["history"]
    assert history[-1]["source"] == "whatsapp_keyword"
    assert (
        outcome.preferences_url
        and client.get(outcome.preferences_url.removeprefix("http://testserver")).status_code == 200
    )


def test_stop_from_a_parent_covers_every_child_but_only_this_owner(kit, client, owner, relaxed_limits):
    owner_id, headers = owner()
    _, other_headers = owner(display_name="Ravi Fitness")
    phone = local_phone()
    path = practice_path(client, headers)
    consent_and_verify(client, path, who="child", data=guardian(phone=phone, child_name="Aarav"))
    consent_and_verify(client, path, who="child", data=guardian(phone=phone, child_name="Diya"))
    other_id, other_path = add_subject(client, other_headers, phone=phone)
    consent_and_verify(client, other_path, data=adult(phone=phone))

    (outcome,) = inbound(kit, owner_id, payload(text_message(e164(phone), "STOP")))
    assert len(outcome.subject_ids) == 2
    for subject in client.get("/subjects", headers=headers).json():
        assert subject["consents"] == {"service": False, "updates": False}
    assert consents(client, other_headers, other_id) == {"service": True, "updates": True}


def test_start_sends_the_preferences_link_without_changing_consent(kit, client, owner):
    from whatsapp_consent.enums import InboundOutcomeType

    owner_id, headers = owner()
    subject_id, phone = verified_subject(client, headers)
    inbound(kit, owner_id, payload(text_message(phone, "STOP")))
    (outcome,) = inbound(kit, owner_id, payload(text_message(phone, "start")))
    assert outcome.type == InboundOutcomeType.SEND_PREFERENCES_LINK and outcome.preferences_url
    assert consents(client, headers, subject_id) == {"service": False, "updates": False}


def test_stop_button_withdraws_only_that_purpose(kit, client, owner):
    from whatsapp_consent.enums import InboundOutcomeType

    owner_id, headers = owner()
    subject_id, phone = verified_subject(client, headers)
    (outcome,) = inbound(kit, owner_id, payload(button_message(phone, "whatsapp_consent:stop:updates")))
    assert outcome.type == InboundOutcomeType.WITHDRAWN
    assert consents(client, headers, subject_id) == {"service": True, "updates": False}
    assert client.get(f"/subjects/{subject_id}", headers=headers).json()["history"][-1]["source"] == "whatsapp_button"
    (manage,) = inbound(kit, owner_id, payload(button_message(phone, "whatsapp_consent:manage")))
    assert manage.type == InboundOutcomeType.SEND_PREFERENCES_LINK
    (unknown,) = inbound(kit, owner_id, payload(button_message(phone, "whatsapp_consent:stop:newsletter")))
    assert unknown.type == InboundOutcomeType.IGNORED


def test_meta_opt_out_withdraws_marketing_only(kit, client, owner):
    from whatsapp_consent.inbound import MARKETING_OPT_OUT_ERROR_CODES

    owner_id, headers = owner()
    subject_id, phone = verified_subject(client, headers)
    code = next(iter(MARKETING_OPT_OUT_ERROR_CODES))
    inbound(kit, owner_id, payload(statuses=[failed_status(phone, code)]))
    assert consents(client, headers, subject_id) == {"service": True, "updates": False}
    assert client.get(f"/subjects/{subject_id}", headers=headers).json()["history"][-1]["source"] == "meta_opt_out"
    inbound(kit, owner_id, payload(statuses=[failed_status(phone, 131026)]))  # another failure: no change
    assert consents(client, headers, subject_id)["service"] is True


def test_click_to_chat_code_verifies_through_inbound(kit, click_client, owner):
    import re
    from urllib.parse import unquote

    from whatsapp_consent.enums import InboundOutcomeType

    owner_id, headers = owner(whatsapp_number="+919800000001")
    subject_id, path = add_subject(click_client, headers, is_minor=True)
    data = guardian()
    assert submit(click_client, path, "child", data).status_code == 303
    page = text_of(click_client.get("/c/verify"))
    match = re.search(r"text=(START-[^\"&]+)", page)
    assert match
    start_text = unquote(match[1])

    (wrong,) = inbound(kit, owner_id, payload(text_message("+919811110000", start_text)))
    assert wrong.type == InboundOutcomeType.IGNORED
    (outcome,) = inbound(kit, owner_id, payload(text_message(e164(data["phone"]), start_text.lower())))
    assert outcome.type == InboundOutcomeType.VERIFIED and outcome.subject_ids == (uuid.UUID(subject_id),)
    info = click_client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert (info["verified"], info["status"], info["can_send"]["service"]) == (True, "active", True)
    assert "already confirmed" in text_of(click_client.get(path))


def test_other_text_is_ignored_and_never_stored(kit, client, owner, engine):
    from whatsapp_consent.enums import InboundOutcomeType
    from whatsapp_consent.models import ConsentEvent

    owner_id, headers = owner()
    _, phone = verified_subject(client, headers)
    secret = "My blood sugar was 180 today"
    with committed_session() as db:
        before = db.scalar(select(func.count()).select_from(ConsentEvent))
    (outcome,) = inbound(kit, owner_id, payload(text_message(phone, secret)))
    assert outcome.type == InboundOutcomeType.IGNORED
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(ConsentEvent)).scalar() == before
        columns = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'whatsapp_consent' "
                "AND data_type IN ('text', 'character varying', 'jsonb')"
            )
        ).all()
        for table, column in columns:
            found = conn.execute(
                text(f'SELECT count(*) FROM whatsapp_consent."{table}" WHERE "{column}"::text ILIKE :n'),
                {"n": "%blood sugar%"},
            ).scalar()
            assert found == 0, f"{table}.{column}"


def test_events_from_unknown_or_erased_numbers_are_ignored(kit, client, owner):
    from whatsapp_consent.enums import InboundOutcomeType

    owner_id, headers = owner()
    subject_id, phone = verified_subject(client, headers)
    client.delete(f"/subjects/{subject_id}", headers=headers)
    outcomes = inbound(
        kit,
        owner_id,
        payload(
            text_message(phone, "STOP"),
            text_message("+919811112222", "STOP"),
            button_message(phone, "whatsapp_consent:manage"),
        ),
    )
    assert [o.type for o in outcomes] == [InboundOutcomeType.IGNORED] * 3


# Click-to-chat: held submissions, other owners, revoked invites, repeats


def held_click(click_client, headers, *, is_minor=False, who="me", data=None):
    import re
    from urllib.parse import unquote

    subject_id, path = add_subject(click_client, headers, is_minor=is_minor)
    data = data or (adult() if who == "me" else guardian())
    assert submit(click_client, path, who, data).status_code == 303
    page = text_of(click_client.get("/c/verify"))
    match = re.search(r"text=(START-[^\"&]+)", page)
    assert match
    start_text = unquote(match[1])
    return subject_id, path, data, start_text


def test_a_held_submission_is_applied_by_the_start_code(kit, click_client, owner):
    from whatsapp_consent.enums import InboundOutcomeType

    owner_id, headers = owner(whatsapp_number="+919800000001")
    subject_id, _, data, start_text = held_click(click_client, headers, data=adult(name="Ravi", purposes=["service"]))
    before = click_client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert before["display_name"] == "Asha" and before["consents"] == {"service": False, "updates": False}
    (outcome,) = inbound(kit, owner_id, payload(text_message(e164(data["phone"]), start_text)))
    assert outcome.type == InboundOutcomeType.VERIFIED
    info = click_client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert info["display_name"] == "Ravi" and info["verified"] is True
    assert info["consents"] == {"service": True, "updates": False}


def test_another_owners_start_code_is_ignored(kit, click_client, owner):
    from whatsapp_consent.enums import InboundOutcomeType
    from whatsapp_consent.models import Verification

    _, headers = owner(whatsapp_number="+919800000001")
    other_id, _ = owner(display_name="Ravi Fitness")
    subject_id, _, data, start_text = held_click(click_client, headers)
    (outcome,) = inbound(kit, other_id, payload(text_message(e164(data["phone"]), start_text)))
    assert outcome.type == InboundOutcomeType.IGNORED
    info = click_client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert info["verified"] is False and info["consents"] == {"service": False, "updates": False}
    with committed_session() as db:
        row = db.scalar(select(Verification).where(Verification.subject_id == uuid.UUID(subject_id)))
        assert row is not None and row.verified_at is None and row.pending is not None


def test_an_invite_revoked_after_the_hold_is_ignored_and_nothing_is_kept(kit, click_client, owner):
    from whatsapp_consent.enums import InboundOutcomeType
    from whatsapp_consent.models import Verification

    owner_id, headers = owner(whatsapp_number="+919800000001")
    subject_id, _, data, start_text = held_click(click_client, headers)
    assert click_client.post(f"/subjects/{subject_id}/invite", headers=headers).status_code == 200  # revokes the old
    (outcome,) = inbound(kit, owner_id, payload(text_message(e164(data["phone"]), start_text)))
    assert outcome.type == InboundOutcomeType.IGNORED
    info = click_client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert info["verified"] is False and info["consents"] == {"service": False, "updates": False}
    assert info["display_name"] == "Asha"
    with committed_session() as db:
        row = db.scalar(select(Verification).where(Verification.subject_id == uuid.UUID(subject_id)))
        assert row is not None and row.verified_at is None and row.pending is not None


def test_the_same_start_text_twice_is_applied_once(kit, click_client, owner):
    from whatsapp_consent.enums import InboundOutcomeType

    owner_id, headers = owner(whatsapp_number="+919800000001")
    subject_id, _, data, start_text = held_click(click_client, headers)
    message = payload(text_message(e164(data["phone"]), start_text))
    (first,) = inbound(kit, owner_id, message)
    (second,) = inbound(kit, owner_id, message)
    assert (first.type, second.type) == (InboundOutcomeType.VERIFIED, InboundOutcomeType.IGNORED)
    history = click_client.get(f"/subjects/{subject_id}", headers=headers).json()["history"]
    assert len(history) == 2  # service + updates, once


# Minors


def minor_with_guardian(client, headers):
    phone = local_phone()
    subject_id, path = add_subject(client, headers, phone=phone, is_minor=True)
    consent_and_verify(client, path, who="child", data=guardian(phone=phone))
    return subject_id, e164(phone)


def test_a_minors_stop_withdraws_and_records_the_guardian(kit, client, owner):
    owner_id, headers = owner()
    subject_id, phone = minor_with_guardian(client, headers)
    inbound(kit, owner_id, payload(text_message(phone, "STOP")))
    info = client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert info["consents"] == {"service": False, "updates": False}
    assert info["history"][-1]["given_by"] == "guardian"


def test_a_suspended_minor_can_still_stop_and_is_recorded_as_self(kit, client, owner):
    owner_id, headers = owner()
    phone = local_phone()
    subject_id, _ = add_subject(client, headers, phone=phone, is_minor=True)
    with committed_session() as db:
        from whatsapp_consent.enums import ConsentSource, GivenBy
        from whatsapp_consent.events import set_consent
        from whatsapp_consent.models import Subject

        subject = db.get(Subject, uuid.UUID(subject_id))
        assert subject is not None
        set_consent(db, subject, {"updates": True}, source=ConsentSource.OWNER_RECORDED, given_by=GivenBy.SELF)
    inbound(kit, owner_id, payload(text_message(e164(phone), "STOP")))
    info = client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert info["consents"]["updates"] is False
    assert info["history"][-1]["given_by"] == "self"


def test_start_and_buttons_never_grant_consent_for_a_minor(kit, client, owner):
    owner_id, headers = owner()
    phone = local_phone()
    subject_id, _ = add_subject(client, headers, phone=phone, is_minor=True)
    for body in (
        payload(text_message(e164(phone), "START")),
        payload(button_message(e164(phone), "whatsapp_consent:manage")),
    ):
        (outcome,) = inbound(kit, owner_id, body)
        assert outcome.type.value == "send_preferences_link"
    info = client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert info["consents"] == {"service": False, "updates": False} and info["history"] == []


def test_invalid_signature_headers_never_raise_anything_else():
    from whatsapp_consent.meta import InvalidSignature, verify_signature

    for header in ("sha256=é\ud800", "sha256=" + "0" * 64, " ", "SHA256=abc"):
        with pytest.raises(InvalidSignature):
            verify_signature(b"{}", header, "app-secret")
