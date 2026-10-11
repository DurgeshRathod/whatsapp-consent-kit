"""Driving the public consent pages in tests, the way a browser would."""

import html
import re
from urllib.parse import urlparse

from conftest import local_phone

CSRF = re.compile(r'name="csrf" value="([^"]+)"')
MANAGE_LINK = re.compile(r'href="(http://testserver/p/[^"]+)"')


def text_of(response) -> str:
    """Page text with HTML entities decoded (Jinja escapes apostrophes)."""
    return html.unescape(response.text)


def csrf_of(response) -> str:
    match = CSRF.search(response.text)
    assert match, response.text
    return match.group(1)


def path_of(link: str) -> str:
    return urlparse(link).path


def e164(phone: str) -> str:
    from whatsapp_consent.phones import normalise_phone

    return normalise_phone(phone)


def add_subject(client, headers, **body) -> tuple[str, str]:
    """The owner adds a subject; returns (subject id, consent page path)."""
    res = client.post("/subjects", json={"display_name": "Asha", "phone": local_phone()} | body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["subject"]["id"], path_of(res.json()["invite"]["link"])


def practice_path(client, headers) -> str:
    return path_of(client.get("/subjects/practice-link", headers=headers).json()["link"])


def adult(**overrides) -> dict:
    data = {
        "name": "Asha",
        "phone": local_phone(),
        "birth_year": "1990",
        "birthday_day": "12",
        "birthday_month": "3",
        "purposes": ["service", "updates"],
    }
    return data | overrides


def guardian(**overrides) -> dict:
    data = {
        "guardian_name": "Meera",
        "guardian_relation": "Mother",
        "phone": local_phone(),
        "child_name": "Aarav",
        "declaration": "yes",
        "purposes": ["service", "updates"],
    }
    return data | overrides


def submit(client, path, who, data, lang="en"):
    page = client.get(path, params={"who": who, "lang": lang})
    form = {"csrf": csrf_of(page), "lang": lang, "who": who} | data
    return client.post(path, data=form, follow_redirects=False)


def enter_code(client, phone_e164, lang="en", code=None):
    from whatsapp_consent.senders import fake_sender

    page = client.get("/c/verify", params={"lang": lang})
    code = code or fake_sender.last_code(phone_e164)
    return client.post("/c/verify", data={"csrf": csrf_of(page), "lang": lang, "code": code}, follow_redirects=False)


def consent_and_verify(client, path, who="me", data=None, lang="en") -> str:
    """Completes the consent form and the code; returns the private preferences page path."""
    data = data or (adult() if who == "me" else guardian())
    submitted = submit(client, path, who, data, lang)
    assert submitted.status_code == 303, text_of(submitted)
    verified = enter_code(client, e164(data["phone"]), lang)
    assert verified.status_code == 303, text_of(verified)
    done = client.get("/c/done", params={"lang": lang})
    assert done.status_code == 200
    link = MANAGE_LINK.search(done.text)
    assert link, done.text
    return path_of(link.group(1))
