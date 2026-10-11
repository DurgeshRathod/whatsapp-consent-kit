from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from whatsapp_consent.enums import Language
from whatsapp_consent.models import Notice
from whatsapp_consent.web.i18n import pick_lang
from whatsapp_consent.web.render import STATIC_DIR, Pages, with_headers

if TYPE_CHECKING:
    from whatsapp_consent.kit import ConsentKit


def build(kit: "ConsentKit", pages: Pages) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    stylesheet = (STATIC_DIR / "consent.css").read_text(encoding="utf-8")

    @router.get("/consent-static/consent.css")
    def css() -> Response:
        return with_headers(Response(stylesheet, media_type="text/css"))

    @router.get("/notice/{version}", response_class=HTMLResponse)
    def notice_page(
        version: str,
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        lang: str | None = None,
    ) -> HTMLResponse:
        language = pick_lang(lang, Language.EN)
        notice = None
        # Anything that is not a short run of ASCII digits is simply an unknown notice.
        if version.isascii() and version.isdigit() and len(version) <= 6:
            notice = db.scalar(select(Notice).where(Notice.version == int(version), Notice.language == language))
        if notice is None:
            return pages.message(
                request, language, title_key="not_found_title", body_key="not_found_body", status_code=404
            )
        return pages.render(
            request, "notice.html", language, title=notice.title, notice=notice, paragraphs=notice.body.split("\n\n")
        )

    return router
