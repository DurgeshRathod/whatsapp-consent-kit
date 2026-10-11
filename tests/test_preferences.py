from conftest import local_phone
from helpers import add_subject, adult, consent_and_verify, csrf_of, e164, guardian, practice_path, text_of


def last_code(phone_e164):
    from whatsapp_consent.senders import fake_sender

    return fake_sender.last_code(phone_e164)


def toggle(client, prefs, subject_id, purposes):
    page = client.get(prefs)
    data = {"csrf": csrf_of(page), "lang": "en", "purposes": purposes}
    return client.post(f"{prefs}/subjects/{subject_id}", data=data, follow_redirects=False)


def start_delete(client, prefs, target):
    page = client.get(prefs)
    res = client.post(
        f"{prefs}/delete", data={"csrf": csrf_of(page), "lang": "en", "target": target}, follow_redirects=False
    )
    assert res.status_code == 303, text_of(res)
    code_page = client.get(res.headers["location"])
    assert "Confirm deletion" in text_of(code_page)
    return code_page


def delete(client, prefs, target, phone_e164):
    code_page = start_delete(client, prefs, target)
    data = {"csrf": csrf_of(code_page), "lang": "en", "code": last_code(phone_e164)}
    return client.post(f"{prefs}/delete/confirm", data=data)


def test_preferences_show_and_change_consent(client, owner):
    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    prefs = consent_and_verify(client, path)
    page = text_of(client.get(prefs))
    assert "Your WhatsApp preferences" in page and "Priya Nutrition" in page and "Service messages" in page
    res = toggle(client, prefs, subject_id, ["service"])
    assert res.status_code == 303 and "saved=1" in res.headers["location"]
    assert "Saved." in text_of(client.get(res.headers["location"]))
    info = client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert info["consents"] == {"service": True, "updates": False}
    assert info["history"][-1]["source"] == "preferences_page"


def test_one_number_sees_every_owner(client, owner, relaxed_limits):
    _, first = owner(display_name="Priya Nutrition")
    _, second = owner(display_name="Ravi Fitness")
    phone = local_phone()
    _, path_a = add_subject(client, first, phone=phone)
    _, path_b = add_subject(client, second, phone=phone)
    consent_and_verify(client, path_a, data=adult(phone=phone))
    prefs = consent_and_verify(client, path_b, data=adult(phone=phone))
    page = text_of(client.get(prefs))
    assert "Priya Nutrition" in page and "Ravi Fitness" in page


def test_parent_sees_each_child(client, owner, relaxed_limits):
    _, headers = owner()
    path = practice_path(client, headers)
    phone = local_phone()
    consent_and_verify(client, path, who="child", data=guardian(phone=phone, child_name="Aarav"))
    prefs = consent_and_verify(client, path, who="child", data=guardian(phone=phone, child_name="Diya"))
    page = text_of(client.get(prefs))
    assert "For Aarav" in page and "For Diya" in page


def test_find_preferences_by_number(client, owner, relaxed_limits):
    _, headers = owner()
    _, path = add_subject(client, headers)
    data = adult()
    earlier = consent_and_verify(client, path, data=data)
    page = client.get("/p")
    res = client.post("/p", data={"csrf": csrf_of(page), "lang": "en", "phone": data["phone"]}, follow_redirects=False)
    assert res.status_code == 303
    code_page = client.get("/p/verify")
    assert "If this number is registered" in text_of(code_page)
    code = last_code(e164(data["phone"]))
    res = client.post(
        "/p/verify", data={"csrf": csrf_of(code_page), "lang": "en", "code": code}, follow_redirects=False
    )
    assert res.status_code == 303 and res.headers["location"].startswith("/p/")
    assert "Priya Nutrition" in text_of(client.get(res.headers["location"]))
    assert client.get(earlier).status_code == 200  # earlier links keep working


def test_looking_up_an_unknown_number_reveals_nothing(client):
    from whatsapp_consent.senders import fake_sender

    sent = len(fake_sender.sent)
    page = client.get("/p")
    res = client.post("/p", data={"csrf": csrf_of(page), "lang": "en", "phone": local_phone()}, follow_redirects=False)
    assert res.status_code == 303 and len(fake_sender.sent) == sent
    code_page = client.get("/p/verify")
    assert "If this number is registered" in text_of(code_page)
    res = client.post("/p/verify", data={"csrf": csrf_of(code_page), "lang": "en", "code": "123456"})
    assert res.status_code == 422 and "That code isn't right." in text_of(res)


def test_delete_from_one_owner_keeps_the_other(client, owner, relaxed_limits):
    _, first = owner(display_name="Priya Nutrition")
    _, second = owner(display_name="Ravi Fitness")
    phone = local_phone()
    id_a, path_a = add_subject(client, first, phone=phone)
    id_b, path_b = add_subject(client, second, phone=phone)
    consent_and_verify(client, path_a, data=adult(phone=phone))
    prefs = consent_and_verify(client, path_b, data=adult(phone=phone))
    res = delete(client, prefs, id_a, e164(phone))
    assert res.status_code == 200
    assert "Your data has been deleted" in text_of(res) and "365 days" in text_of(res)
    assert client.get(f"/subjects/{id_a}", headers=first).status_code == 404
    assert client.get(f"/subjects/{id_b}", headers=second).status_code == 200
    assert client.get(prefs).status_code == 200


def test_delete_everything_revokes_the_link(client, owner, relaxed_limits):
    _, first = owner()
    _, second = owner()
    phone = local_phone()
    id_a, path_a = add_subject(client, first, phone=phone)
    id_b, path_b = add_subject(client, second, phone=phone)
    consent_and_verify(client, path_a, data=adult(phone=phone))
    prefs = consent_and_verify(client, path_b, data=adult(phone=phone))
    assert delete(client, prefs, "all", e164(phone)).status_code == 200
    assert client.get(f"/subjects/{id_a}", headers=first).status_code == 404
    assert client.get(f"/subjects/{id_b}", headers=second).status_code == 404
    assert client.get(prefs).status_code == 410


def test_wrong_deletion_code_deletes_nothing(client, owner, relaxed_limits):
    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    data = adult()
    prefs = consent_and_verify(client, path, data=data)
    code_page = start_delete(client, prefs, subject_id)
    good = last_code(e164(data["phone"]))
    bad = "000000" if good != "000000" else "111111"
    res = client.post(f"{prefs}/delete/confirm", data={"csrf": csrf_of(code_page), "lang": "en", "code": bad})
    assert res.status_code == 422 and "That code isn't right." in text_of(res)
    assert client.get(f"/subjects/{subject_id}", headers=headers).status_code == 200


def test_another_numbers_subject_cannot_be_changed(client, owner, relaxed_limits):
    _, headers = owner()
    id_a, path_a = add_subject(client, headers)
    id_b, path_b = add_subject(client, headers, display_name="Other")
    prefs_a = consent_and_verify(client, path_a)
    consent_and_verify(client, path_b)
    assert toggle(client, prefs_a, id_b, []).status_code == 404
    assert client.get(f"/subjects/{id_b}", headers=headers).json()["consents"]["service"] is True


def test_unknown_preferences_link(client):
    assert client.get("/p/not-a-real-token").status_code == 410


def test_preferences_link_after_the_owner_removed_the_subject(client, owner):
    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    prefs = consent_and_verify(client, path)
    client.delete(f"/subjects/{subject_id}", headers=headers)
    res = client.get(prefs)
    assert res.status_code == 410 and "entering your number" in text_of(res)


def test_preferences_post_needs_csrf(client, owner):
    _, headers = owner()
    subject_id, path = add_subject(client, headers)
    prefs = consent_and_verify(client, path)
    res = client.post(f"{prefs}/subjects/{subject_id}", data={"csrf": "wrong", "lang": "en", "purposes": []})
    assert res.status_code == 403


# Security: cookies, links and targets are bound to the number that was actually verified.


def flow_cookie(data: dict) -> str:
    import os

    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(os.environ["CONSENT_SESSION_SECRET"], salt="whatsapp-consent-flow").dumps(data)


def fp(phone_e164: str) -> str:
    import os

    from whatsapp_consent.hashing import phone_fingerprint

    return phone_fingerprint(os.environ["CONSENT_FINGERPRINT_KEY"], phone_e164)


def verification_id(phone_e164: str, purpose: str) -> str:
    import example_host
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from whatsapp_consent.models import Verification

    with Session(example_host.session().get_bind()) as db:
        row = db.scalars(
            select(Verification)
            .where(Verification.phone_e164 == phone_e164, Verification.purpose == purpose)
            .order_by(Verification.created_at.desc())
        ).first()
        assert row is not None
        return str(row.id)


def start_lookup(client, phone_local):
    page = client.get("/p")
    res = client.post("/p", data={"csrf": csrf_of(page), "lang": "en", "phone": phone_local}, follow_redirects=False)
    assert res.status_code == 303
    return res


def two_numbers(client, owner):
    """Two verified subjects on different numbers; returns (ids, prefs paths, E.164 numbers)."""
    _, headers = owner()
    first, second = adult(), adult()
    id_a, path_a = add_subject(client, headers, phone=first["phone"])
    id_b, path_b = add_subject(client, headers, display_name="Other", phone=second["phone"])
    prefs_a = consent_and_verify(client, path_a, data=first)
    prefs_b = consent_and_verify(client, path_b, data=second)
    return headers, (id_a, id_b), (prefs_a, prefs_b), (e164(first["phone"]), e164(second["phone"]))


def exists(client, headers, subject_id) -> bool:
    return client.get(f"/subjects/{subject_id}", headers=headers).status_code == 200


def test_lookup_link_is_only_for_the_number_whose_code_was_entered(client, owner, relaxed_limits):
    _, ids, _, (phone_a, _) = two_numbers(client, owner)
    start_lookup(client, phone_a[3:])
    row_a, code = verification_id(phone_a, "preferences"), last_code(phone_a)
    page = client.get("/p/verify")
    client.cookies.set("wck_flow", flow_cookie({"pv": row_a, "l": "en"}))
    res = client.post("/p/verify", data={"csrf": csrf_of(page), "lang": "en", "code": code}, follow_redirects=False)
    assert res.status_code == 303
    body = text_of(client.get(res.headers["location"]))
    assert "Priya Nutrition" in body and "For " not in body and body.count("<section") == 1  # number A only


def test_lookup_cookie_pointing_at_a_deletion_code_gives_no_link(client, owner, relaxed_limits):
    _, ids, (prefs_a, _), (phone_a, _) = two_numbers(client, owner)
    start_delete(client, prefs_a, ids[0])
    row = verification_id(phone_a, "deletion")
    code = last_code(phone_a)
    page = client.get("/p")
    client.cookies.set("wck_flow", flow_cookie({"pv": row, "l": "en"}))
    res = client.post("/p/verify", data={"csrf": csrf_of(page), "lang": "en", "code": code}, follow_redirects=False)
    assert res.status_code == 422


def test_lookup_cookie_pointing_at_a_consent_row_gives_no_link(client, owner, relaxed_limits):
    _, headers = owner()
    data = adult()
    _, path = add_subject(client, headers, phone=data["phone"])
    assert submit_only(client, path, data).status_code == 303
    row = verification_id(e164(data["phone"]), "consent")
    code = last_code(e164(data["phone"]))
    page = client.get("/p")
    client.cookies.set("wck_flow", flow_cookie({"pv": row, "pf": fp(e164(data["phone"])), "l": "en"}))
    res = client.post("/p/verify", data={"csrf": csrf_of(page), "lang": "en", "code": code}, follow_redirects=False)
    assert res.status_code == 422


def submit_only(client, path, data):
    from helpers import submit

    return submit(client, path, "me", data)


def flow_of(response) -> dict:
    import os

    from itsdangerous import URLSafeTimedSerializer

    serializer = URLSafeTimedSerializer(os.environ["CONSENT_SESSION_SECRET"], salt="whatsapp-consent-flow")
    return serializer.loads(response.cookies.get("wck_flow"))


def shape(value):
    import re

    return re.sub(r"[0-9a-f]", "h", value) if isinstance(value, str) else type(value).__name__


def test_registered_and_unregistered_numbers_look_the_same(client, owner, relaxed_limits):
    import re

    from whatsapp_consent.senders import fake_sender

    _, headers = owner()
    data = adult()
    _, path = add_subject(client, headers, phone=data["phone"])
    consent_and_verify(client, path, data=data)

    def run(phone):
        start = start_lookup(client, phone)
        flow = flow_of(start)
        page = client.get(start.headers["location"])
        alerts = []
        for _ in range(5):
            page = client.get("/p/verify")
            res = client.post("/p/verify", data={"csrf": csrf_of(page), "lang": "en", "code": "000001"})
            found = re.search(r'role="alert">(.*?)</div>', text_of(res), re.S)
            assert found
            alerts.append((res.status_code, found.group(1)))
        quoted = re.sub(r'value="[^"]+"', "", text_of(page))
        return start.headers["location"], {k: shape(v) for k, v in flow.items()}, quoted, alerts

    registered = run(data["phone"])
    sent = len(fake_sender.sent)
    unknown = run(local_phone())
    assert len(fake_sender.sent) == sent  # nothing was sent for the unregistered number
    assert registered == unknown
    assert registered[1]["pv"] == "hhhhhhhh-hhhh-hhhh-hhhh-hhhhhhhhhhhh" and set(registered[1]) == {"pv", "l"}
    assert "isn't right" in registered[3][-1][1] or "Too many" in registered[3][-1][1]


def test_deletion_cookie_for_another_numbers_row_deletes_nothing(client, owner, relaxed_limits):
    headers, (id_a, id_b), (prefs_a, prefs_b), (phone_a, _) = two_numbers(client, owner)
    start_delete(client, prefs_a, id_a)
    row_a, code = verification_id(phone_a, "deletion"), last_code(phone_a)
    page = client.get(prefs_b)
    token_b = prefs_b.rsplit("/", 1)[1]
    client.cookies.set("wck_flow", flow_cookie({"dv": row_a, "targets": [id_a, id_b], "l": "en"}))
    res = client.post(
        f"{prefs_b}/delete/confirm", data={"csrf": csrf_of(page), "lang": "en", "code": code}, follow_redirects=False
    )
    assert res.status_code != 200 and token_b
    assert exists(client, headers, id_a) and exists(client, headers, id_b)


def test_deletion_cookie_pointing_at_a_lookup_row_deletes_nothing(client, owner, relaxed_limits):
    headers, (id_a, _), (prefs_a, _), (phone_a, _) = two_numbers(client, owner)
    start_lookup(client, phone_a[3:])
    row, code = verification_id(phone_a, "preferences"), last_code(phone_a)
    page = client.get(prefs_a)
    client.cookies.set("wck_flow", flow_cookie({"dv": row, "targets": [id_a], "l": "en"}))
    res = client.post(f"{prefs_a}/delete/confirm", data={"csrf": csrf_of(page), "lang": "en", "code": code})
    assert res.status_code != 200
    assert exists(client, headers, id_a)


def test_deletion_targets_are_checked_against_the_verified_number(client, owner, relaxed_limits):
    headers, (id_a, id_b), (prefs_a, prefs_b), (_, phone_b) = two_numbers(client, owner)
    start_delete(client, prefs_b, id_b)
    row, code = verification_id(phone_b, "deletion"), last_code(phone_b)
    page = client.get(prefs_b)
    forged = {"dv": row, "targets": [id_b, id_a, "not-an-id"], "l": "en"}
    client.cookies.set("wck_flow", flow_cookie(forged))
    res = client.post(f"{prefs_b}/delete/confirm", data={"csrf": csrf_of(page), "lang": "en", "code": code})
    assert res.status_code == 200
    assert not exists(client, headers, id_b)
    assert exists(client, headers, id_a)


def test_a_used_deletion_code_cannot_be_replayed(client, owner, relaxed_limits):
    _, first = owner()
    _, second = owner()
    phone = local_phone()
    id_a, path_a = add_subject(client, first, phone=phone)
    id_b, path_b = add_subject(client, second, phone=phone)
    consent_and_verify(client, path_a, data=adult(phone=phone))
    prefs = consent_and_verify(client, path_b, data=adult(phone=phone))
    code_page = start_delete(client, prefs, id_a)
    row, code = verification_id(e164(phone), "deletion"), last_code(e164(phone))
    flow = flow_cookie({"dv": row, "pf": fp(e164(phone)), "targets": [id_a, id_b], "l": "en"})
    data = {"csrf": csrf_of(code_page), "lang": "en", "code": code}
    assert client.post(f"{prefs}/delete/confirm", data=data).status_code == 200
    client.cookies.set("wck_flow", flow)
    assert client.post(f"{prefs}/delete/confirm", data=data).status_code != 200
    assert exists(client, second, id_b)


def test_a_link_cannot_change_a_subject_on_another_number(client, owner, relaxed_limits):
    headers, (id_a, id_b), (prefs_a, _), _ = two_numbers(client, owner)
    for target in (id_b, "00000000-0000-0000-0000-000000000000"):
        assert toggle(client, prefs_a, target, []).status_code == 404
    info = client.get(f"/subjects/{id_b}", headers=headers).json()
    assert info["consents"]["service"] is True and len(info["history"]) == 2


def test_a_link_cannot_change_an_unverified_subject_with_the_same_number(client, owner, relaxed_limits):
    _, first = owner()
    _, second = owner()
    phone = local_phone()
    _, path_a = add_subject(client, first, phone=phone)
    id_b, _ = add_subject(client, second, phone=phone)  # never verified
    prefs = consent_and_verify(client, path_a, data=adult(phone=phone))
    assert toggle(client, prefs, id_b, ["service"]).status_code == 404
    assert client.get(f"/subjects/{id_b}", headers=second).json()["history"] == []
    page = client.get(prefs)
    res = client.post(f"{prefs}/delete", data={"csrf": csrf_of(page), "lang": "en", "target": id_b})
    assert res.status_code == 404


def test_every_preferences_post_needs_csrf(client, owner, relaxed_limits):
    _, (id_a, _), (prefs_a, _), (phone_a, _) = two_numbers(client, owner)
    start_delete(client, prefs_a, id_a)
    for path in ("/p", "/p/verify", f"{prefs_a}/subjects/{id_a}", f"{prefs_a}/delete", f"{prefs_a}/delete/confirm"):
        res = client.post(
            path, data={"csrf": "wrong", "lang": "en", "phone": phone_a, "code": "123456", "target": id_a}
        )
        assert res.status_code == 403, path


def test_lookup_after_the_hourly_cap_reuses_the_pending_code_for_both_kinds(client, owner, monkeypatch):
    from datetime import timedelta

    import whatsapp_consent.verification as v

    monkeypatch.setattr(v, "RESEND_AFTER", timedelta(0))
    monkeypatch.setattr(v, "MAX_PER_DAY", 100)
    monkeypatch.setattr(v, "MAX_PER_HOUR", 1)
    _, headers = owner()
    data = adult()
    _, path = add_subject(client, headers, phone=data["phone"])
    monkeypatch.setattr(v, "MAX_PER_HOUR", 100)
    consent_and_verify(client, path, data=data)
    monkeypatch.setattr(v, "MAX_PER_HOUR", 2)
    first = flow_of(start_lookup(client, data["phone"]))["pv"]
    code = last_code(e164(data["phone"]))
    second = flow_of(start_lookup(client, data["phone"]))["pv"]  # capped: reuses the pending code
    assert first == second
    page = client.get("/p/verify")
    res = client.post("/p/verify", data={"csrf": csrf_of(page), "lang": "en", "code": code}, follow_redirects=False)
    assert res.status_code == 303


def make_minor(client, owner):
    _, headers = owner()
    data = adult()
    subject_id, path = add_subject(client, headers, phone=data["phone"])
    prefs = consent_and_verify(client, path, data=data)
    assert client.post(f"/subjects/{subject_id}/mark-minor", headers=headers).status_code == 200
    return headers, subject_id, prefs


def test_a_minors_own_number_can_withdraw_but_not_grant(client, owner, relaxed_limits):
    headers, subject_id, prefs = make_minor(client, owner)
    res = toggle(client, prefs, subject_id, ["service"])
    assert res.status_code == 303
    info = client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert info["history"][-1]["given_by"] == "self" and info["history"][-1]["action"] == "withdrawn"
    before = len(info["history"])
    res = toggle(client, prefs, subject_id, ["service", "updates"])
    assert res.status_code == 422 and "guardian" in text_of(res).lower()
    after = client.get(f"/subjects/{subject_id}", headers=headers).json()
    assert len(after["history"]) == before and after["consents"] == info["consents"]


def test_delete_all_removes_unverified_subjects_of_the_number_too(client, owner, relaxed_limits):
    _, first = owner()
    _, second = owner()
    phone = local_phone()
    id_a, path_a = add_subject(client, first, phone=phone)
    id_b, _ = add_subject(client, second, phone=phone)  # never verified
    prefs = consent_and_verify(client, path_a, data=adult(phone=phone))
    assert delete(client, prefs, "all", e164(phone)).status_code == 200
    assert not exists(client, first, id_a) and not exists(client, second, id_b)


def test_starting_deletion_is_rate_limited_per_ip(client, kit, owner, relaxed_limits, monkeypatch):
    from whatsapp_consent.senders import fake_sender

    _, (id_a, _), (prefs_a, _), _ = two_numbers(client, owner)
    page = client.get(prefs_a)
    monkeypatch.setattr(kit.settings, "web_submissions_per_ip_hour", 0)
    sent = len(fake_sender.sent)
    res = client.post(f"{prefs_a}/delete", data={"csrf": csrf_of(page), "lang": "en", "target": id_a})
    assert res.status_code == 429 and len(fake_sender.sent) == sent
