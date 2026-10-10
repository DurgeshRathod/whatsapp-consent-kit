"""Loading host content (purposes and notices) from the host's own data migrations.

Purposes are upserted by code. Notices are evidence of what a person was shown, so an existing
(version, language) is never changed: publish a new version instead.
"""

import json
import re
from typing import Any

from sqlalchemy import Connection, text

from whatsapp_consent.enums import Language, TemplateCategory

CODE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
CONSENTABLE = {TemplateCategory.MARKETING.value, TemplateCategory.UTILITY.value}


class InvalidLibraryContent(ValueError):
    pass


def _check_purpose(row: dict[str, Any]) -> None:
    code = row.get("code", "")
    if not CODE.fullmatch(code):
        raise InvalidLibraryContent(f"purpose code {code!r} must match {CODE.pattern}")
    if row.get("category") not in CONSENTABLE:
        raise InvalidLibraryContent(f"purpose {code}: category must be one of {sorted(CONSENTABLE)}")
    labels = row.get("labels") or {}
    for language in Language:
        label = labels.get(language.value)
        if not label:
            raise InvalidLibraryContent(f"purpose {code}: labels missing language {language.value}")
        for field in ("name", "description"):
            if not str(label.get(field, "")).strip():
                raise InvalidLibraryContent(f"purpose {code}: {language.value} {field} is empty")


def load_purposes(conn: Connection, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        _check_purpose(row)
    conn.execute(
        text(
            """
            INSERT INTO whatsapp_consent.purposes (code, category, labels, position)
            VALUES (:code, :category, CAST(:labels AS jsonb), :position)
            ON CONFLICT (code) DO UPDATE SET
              category = EXCLUDED.category, labels = EXCLUDED.labels, position = EXCLUDED.position
            WHERE (purposes.category, purposes.labels, purposes.position)
              IS DISTINCT FROM (EXCLUDED.category, EXCLUDED.labels, EXCLUDED.position)
            """
        ),
        [
            {"code": r["code"], "category": r["category"], "labels": json.dumps(r["labels"]), "position": r["position"]}
            for r in rows
        ],
    )


def load_notices(conn: Connection, rows: list[dict[str, Any]]) -> None:
    languages = {language.value for language in Language}
    for row in rows:
        if row.get("language") not in languages:
            raise InvalidLibraryContent(f"notice v{row.get('version')}: language must be one of {sorted(languages)}")
        if not all(str(row.get(field, "")).strip() for field in ("title", "summary", "body")):
            raise InvalidLibraryContent(
                f"notice v{row.get('version')} {row['language']}: title, summary and body are required"
            )
    conn.execute(
        text(
            """
            INSERT INTO whatsapp_consent.notices (version, language, title, summary, body, effective_from)
            VALUES (:version, :language, :title, :summary, :body, CAST(:effective_from AS date))
            ON CONFLICT (version, language) DO NOTHING
            """
        ),
        rows,
    )
