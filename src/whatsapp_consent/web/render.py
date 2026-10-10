"""Rendering public pages with the kit's privacy headers, CSRF protection and the signed flow cookie."""

import hmac
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from jinja2 import Environment, FileSystemLoader, select_autoescape

from whatsapp_consent.config import ConsentSettings
from whatsapp_consent.enums import Language
from whatsapp_consent.web.i18n import other_lang, pick_lang, t

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

CSRF_COOKIE = "wck_csrf"
FLOW_COOKIE = "wck_flow"
FLOW_MAX_AGE = 30 * 60

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Robots-Tag": "noindex, nofollow",
    # Links carry private tokens; never leak them to other sites.
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True
)


def with_headers(response: Response) -> Response:
    response.headers.update(SECURITY_HEADERS)
    return response


class Pages:
    """Everything a public route needs to answer, bound to one kit's settings."""

    def __init__(self, settings: ConsentSettings) -> None:
        self.settings = settings
        self._secure = settings.public_base_url.startswith("https://")
        self._flow = URLSafeTimedSerializer(settings.session_secret, salt="whatsapp-consent-flow")

    def render(
        self, request: Request, template: str, lang: Language, *, status_code: int = 200, **context: Any
    ) -> HTMLResponse:
        other = other_lang(lang)
        switch_url = None
        if request.method == "GET":
            params = dict(request.query_params) | {"lang": other.value}
            switch_url = f"{request.url.path}?{urlencode(params)}"
        page = _env.get_template(template).render(
            lang=lang,
            t=lambda key, **fmt: t(key, lang, **fmt),
            other_lang=other,
            other_lang_name=t("lang_name", other),
            lang_switch_url=switch_url,
            **context,
        )
        response = HTMLResponse(page, status_code=status_code)
        with_headers(response)
        return response

    def form(
        self, request: Request, template: str, lang: Language, *, status_code: int = 200, **context: Any
    ) -> HTMLResponse:
        """A page with a form: carries a CSRF token in a hidden field and in a cookie (double submit)."""
        token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
        response = self.render(request, template, lang, status_code=status_code, csrf=token, **context)
        response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", secure=self._secure, path="/")
        return response

    def csrf_ok(self, request: Request, submitted: str) -> bool:
        cookie = request.cookies.get(CSRF_COOKIE)
        if not cookie or not submitted:
            return False
        return hmac.compare_digest(cookie, submitted)

    def message(
        self,
        request: Request,
        lang: Language,
        *,
        title_key: str,
        body_key: str | None = None,
        status_code: int = 200,
        link: str | None = None,
        link_key: str | None = None,
        **fmt: Any,
    ) -> HTMLResponse:
        return self.render(
            request,
            "message.html",
            lang,
            status_code=status_code,
            title=t(title_key, lang, **fmt),
            body=t(body_key, lang, **fmt) if body_key else None,
            link=link,
            link_text=t(link_key, lang) if link_key else None,
        )

    def invalid_link(self, request: Request, lang: Language) -> HTMLResponse:
        return self.message(
            request, lang, title_key="link_invalid_title", body_key="link_invalid_body", status_code=410
        )

    def session_expired(self, request: Request, lang: Language) -> HTMLResponse:
        return self.message(request, lang, title_key="session_expired", status_code=403)

    def redirect(self, url: str) -> RedirectResponse:
        response = RedirectResponse(url, status_code=303)
        with_headers(response)
        return response

    def write_flow(self, response: Response, data: dict[str, Any]) -> None:
        """Carries a person between steps (which code, which subject) without server-side sessions."""
        response.set_cookie(
            FLOW_COOKIE,
            self._flow.dumps(data),
            max_age=FLOW_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=self._secure,
            path="/",
        )

    def read_flow(self, request: Request) -> dict[str, Any] | None:
        raw = request.cookies.get(FLOW_COOKIE)
        if not raw:
            return None
        try:
            return self._flow.loads(raw, max_age=FLOW_MAX_AGE)
        except BadSignature:  # includes SignatureExpired
            return None

    def clear_flow(self, response: Response) -> None:
        response.delete_cookie(FLOW_COOKIE, path="/")

    def flow_lang(self, value: str | None, flow: dict[str, Any] | None) -> Language:
        return pick_lang(value, pick_lang(flow.get("l") if flow else None, Language.EN))
