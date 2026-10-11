import copy

import pytest
from sample_content import NOTICES, PURPOSES
from sqlalchemy import text

S = "whatsapp_consent"


def test_sample_purposes_and_notices_are_loaded(engine):
    with engine.connect() as conn:
        purposes = conn.execute(text(f"SELECT code, category FROM {S}.purposes ORDER BY position")).all()
        notices = conn.execute(text(f"SELECT language FROM {S}.notices WHERE version = 1 ORDER BY language")).scalars()
        assert [tuple(p) for p in purposes] == [("service", "UTILITY"), ("updates", "MARKETING")]
        assert list(notices) == ["en", "hi"]


def test_loading_twice_changes_nothing(engine):
    from whatsapp_consent.library import load_notices, load_purposes

    query = text(f"SELECT code, labels, updated_at FROM {S}.purposes ORDER BY code")
    with engine.begin() as conn:
        before = conn.execute(query).all()
        load_purposes(conn, PURPOSES)
        load_notices(conn, NOTICES)
        assert conn.execute(query).all() == before


def test_notices_are_never_changed(engine):
    from whatsapp_consent.library import load_notices

    edited = copy.deepcopy(NOTICES)
    edited[0]["body"] = "Something else entirely."
    with engine.begin() as conn:
        load_notices(conn, edited)
        body = conn.execute(text(f"SELECT body FROM {S}.notices WHERE version = 1 AND language = 'en'")).scalar_one()
    assert body == NOTICES[0]["body"]


@pytest.mark.parametrize(
    "change,message",
    [
        ({"category": "AUTHENTICATION"}, "category"),
        ({"code": "Bad Code"}, "code"),
        ({"labels": {"en": {"name": "x", "description": "y"}}}, "hi"),
        ({"labels": {"en": {"name": "", "description": "y"}, "hi": {"name": "x", "description": "y"}}}, "name"),
    ],
)
def test_invalid_purposes_are_refused(engine, change, message):
    from whatsapp_consent.library import InvalidLibraryContent, load_purposes

    row = copy.deepcopy(PURPOSES[0]) | change
    with pytest.raises(InvalidLibraryContent, match=message), engine.begin() as conn:
        load_purposes(conn, [row])


def test_invalid_notices_are_refused(engine):
    from whatsapp_consent.library import InvalidLibraryContent, load_notices

    with pytest.raises(InvalidLibraryContent, match="language"), engine.begin() as conn:
        load_notices(conn, [NOTICES[0] | {"version": 99, "language": "fr"}])
