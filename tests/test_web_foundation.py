import html
import re
from string import Formatter
from typing import get_args

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
