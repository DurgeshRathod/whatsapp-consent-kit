import html
import re
import time
from string import Formatter
from types import SimpleNamespace
from typing import get_args

import itsdangerous.timed
import pytest
from starlette.requests import Request
from starlette.responses import Response

from whatsapp_consent.enums import Language
from whatsapp_consent.web.render import CSRF_COOKIE, FLOW_COOKIE, FLOW_MAX_AGE, Pages

DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def test_notice_page_in_english_and_hindi(client):
    en = client.get("/notice/1")
    assert en.status_code == 200
    assert '<html lang="en">' in en.text
    assert "privacy notice" in html.unescape(en.text)
    hi = client.get("/notice/1", params={"lang": "hi"})
    assert '<html lang="hi">' in hi.text and "गोपनीयता सूचना" in hi.text


def test_unknown_language_falls_back_to_english(client):
    assert '<html lang="en">' in client.get("/notice/1", params={"lang": "fr"}).text


def test_unknown_notice_is_a_friendly_404(client):
    res = client.get("/notice/999")
    assert res.status_code == 404 and "<h1>" in res.text


def test_public_pages_send_privacy_headers(client):
    for path in ("/notice/1", "/notice/999", "/consent-static/consent.css"):
        headers = client.get(path).headers
        assert "default-src 'none'" in headers["content-security-policy"], path
        assert headers["x-robots-tag"] == "noindex, nofollow"
        assert headers["referrer-policy"] == "no-referrer"


def test_owner_api_is_not_affected(client, owner):
    _, headers = owner()
    assert "content-security-policy" not in client.get("/subjects", headers=headers).headers


def test_language_switch_link_keeps_the_page(client):
    assert 'href="/notice/1?lang=hi"' in client.get("/notice/1", params={"lang": "en"}).text


def test_stylesheet_is_served(client):
    res = client.get("/consent-static/consent.css")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/css")


def test_every_string_exists_in_every_language():
    from whatsapp_consent.enums import Language
    from whatsapp_consent.web.i18n import MONTHS, STRINGS

    for key, value in STRINGS.items():
        assert set(value) == {lang.value for lang in Language}, key
        assert all(text.strip() for text in value.values()), key
        assert DEVANAGARI.search(value["hi"]), key
    assert all(len(MONTHS[lang]) == 12 for lang in Language)


def test_placeholders_match_between_languages():
    from whatsapp_consent.web.i18n import STRINGS

    for key, value in STRINGS.items():
        fields = {lang: {f for _, f, _, _ in Formatter().parse(text) if f} for lang, text in value.items()}
        assert fields["en"] == fields["hi"], key


def test_templates_use_known_strings_and_no_external_assets():
    from whatsapp_consent.web.i18n import STRINGS
    from whatsapp_consent.web.render import TEMPLATES_DIR

    templates = list(TEMPLATES_DIR.glob("*.html"))
    assert templates
    for path in templates:
        source = path.read_text()
        for key in re.findall(r"t\('([a-z0-9_]+)'", source):
            assert key in STRINGS, f"{path.name}: {key}"
        for banned in ("<script", "http://", "https://", "style="):
            assert banned not in source, f"{path.name}: {banned}"


def test_every_verification_error_has_a_message():
    from whatsapp_consent.verification import Reason
    from whatsapp_consent.web.i18n import STRINGS

    for reason in get_args(Reason):
        assert f"err_{reason}" in STRINGS, reason


def _request(cookies: dict[str, str] | None = None, method: str = "GET") -> Request:
    headers = []
    if cookies:
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _set_cookie(response: Response, name: str) -> str:
    for header in response.headers.getlist("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(f"no {name} cookie was set")


def _cookie_value(header: str) -> str:
    return header.split(";")[0].split("=", 1)[1]


@pytest.fixture
def pages(kit):
    return Pages(kit.settings)


def test_csrf_accepts_only_the_matching_token(pages):
    assert pages.csrf_ok(_request({CSRF_COOKIE: "tok"}), "tok") is True
    assert pages.csrf_ok(_request({CSRF_COOKIE: "tok"}), "other") is False
    assert pages.csrf_ok(_request(), "tok") is False
    assert pages.csrf_ok(_request({CSRF_COOKIE: "tok"}), "") is False


def test_csrf_with_non_ascii_submission_is_refused_without_error(pages):
    assert pages.csrf_ok(_request({CSRF_COOKIE: "tok"}), "tökén") is False


def test_flow_round_trips_through_the_cookie(pages):
    response = Response()
    pages.write_flow(response, {"l": "hi", "s": "abc"})
    request = _request({FLOW_COOKIE: _cookie_value(_set_cookie(response, FLOW_COOKIE))})
    assert pages.read_flow(request) == {"l": "hi", "s": "abc"}


def test_tampered_flow_cookie_reads_as_none(pages):
    response = Response()
    pages.write_flow(response, {"l": "en"})
    value = _cookie_value(_set_cookie(response, FLOW_COOKIE))
    tampered = value[:-1] + ("A" if value[-1] != "A" else "B")
    assert pages.read_flow(_request({FLOW_COOKIE: tampered})) is None


def test_expired_flow_cookie_reads_as_none(pages, monkeypatch):
    response = Response()
    pages.write_flow(response, {"l": "en"})
    value = _cookie_value(_set_cookie(response, FLOW_COOKIE))
    later = time.time() + FLOW_MAX_AGE + 60
    monkeypatch.setattr(itsdangerous.timed, "time", SimpleNamespace(time=lambda: later))
    assert pages.read_flow(_request({FLOW_COOKIE: value})) is None


def test_form_and_flow_cookies_are_httponly(pages):
    form = pages.form(_request(), "message.html", Language.EN, title="x")
    assert "httponly" in _set_cookie(form, CSRF_COOKIE).lower()
    response = Response()
    pages.write_flow(response, {"l": "en"})
    assert "httponly" in _set_cookie(response, FLOW_COOKIE).lower()


def test_cookies_are_secure_only_on_https(kit):
    https = Pages(kit.settings.model_copy(update={"public_base_url": "https://consent.example"}))
    flow = Response()
    https.write_flow(flow, {"l": "en"})
    assert "; secure" in _set_cookie(flow, FLOW_COOKIE).lower()
    form = https.form(_request(), "message.html", Language.EN, title="x")
    assert "; secure" in _set_cookie(form, CSRF_COOKIE).lower()

    http_flow = Response()
    Pages(kit.settings).write_flow(http_flow, {"l": "en"})
    assert "; secure" not in _set_cookie(http_flow, FLOW_COOKIE).lower()


@pytest.mark.parametrize("path", ["/notice/abc", "/notice/99999999999", "/notice/%D9%A1", "/notice/0"])
def test_malformed_notice_versions_are_friendly_404(client, path):
    res = client.get(path)
    assert res.status_code == 404 and "<h1>" in res.text
