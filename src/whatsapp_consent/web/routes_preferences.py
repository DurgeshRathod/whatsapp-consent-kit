"""Public preferences pages: /p (find by number), /p/verify, /p/{token} (manage, delete)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from whatsapp_consent.enums import (
    ConsentSource,
    DataRequestSource,
    GivenBy,
    Language,
    VerificationMethod,
    VerificationPurpose,
)
from whatsapp_consent.events import Evidence, active_purposes, current_consent, purpose_codes, set_consent
from whatsapp_consent.hashing import hash_code, new_code, phone_fingerprint
from whatsapp_consent.models import PreferenceLink, Subject, Verification
from whatsapp_consent.notices import latest_notice
from whatsapp_consent.phones import InvalidPhone, mask_phone, normalise_phone
from whatsapp_consent.preference_links import issue_preference_link, resolve_preference_link
from whatsapp_consent.preferences import preference_entries
from whatsapp_consent.ratelimit import allow
from whatsapp_consent.senders import VerificationSender
from whatsapp_consent.verification import (
    CODE_TTL,
    VerificationError,
    check_code,
    latest_pending,
    start_code_verification,
)
from whatsapp_consent.web.i18n import pick_lang, t
from whatsapp_consent.web.render import Pages
from whatsapp_consent.web.routes_consent import client_ip

if TYPE_CHECKING:
    from whatsapp_consent.kit import ConsentKit

HOUR = timedelta(hours=1)
FormStr = Annotated[str, Form()]


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


class _SilentSender:
    """Stands in for the real sender when the number isn't registered: same rows and limits, nothing sent."""

    def __init__(self, method: VerificationMethod) -> None:
        self.method = method

    def send_code(self, phone_e164: str, code: str, language: Language) -> None:
        return None


def build(kit: "ConsentKit", pages: Pages) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    settings = kit.settings

    def link_invalid(request: Request, lang: Language) -> HTMLResponse:
        return pages.message(
            request,
            lang,
            title_key="link_invalid_title",
            body_key="prefs_link_invalid_body",
            status_code=410,
            link=f"/p?lang={lang.value}",
            link_key="lookup_title",
        )

    def not_found(request: Request, lang: Language) -> HTMLResponse:
        return pages.message(request, lang, title_key="not_found_title", body_key="not_found_body", status_code=404)

    def owned(db: Session, link: PreferenceLink, subject_id: uuid.UUID) -> Subject | None:
        """Only a verified subject of the link's own number may be changed through the link."""
        subject = db.get(Subject, subject_id)
        if subject is None or subject.phone_fingerprint != link.phone_fingerprint or subject.phone_verified_at is None:
            return None
        return subject

    def flow_row(db: Session, flow: dict, field: str, purpose: VerificationPurpose) -> Verification | None:
        """The flow's verification row, only if it is a phone-only row of this purpose (never a consent row)."""
        verification_id = _uuid(flow.get(field)) if flow.get(field) else None
        if verification_id is None:
            return None
        row = db.get(Verification, verification_id)
        if row is None or row.purpose != purpose or row.subject_id is not None:
            return None
        return row

    def deletion_row(db: Session, flow: dict, link: PreferenceLink) -> Verification | None:
        """The deletion code row, only if it was sent to the very number this link belongs to."""
        row = flow_row(db, flow, "dv", VerificationPurpose.DELETION)
        if row is None or phone_fingerprint(kit.key, row.phone_e164) != link.phone_fingerprint:
            return None
        return row

    def start_or_reuse(
        db: Session, phone: str, purpose: VerificationPurpose, language: Language, sender: VerificationSender
    ) -> str:
        """Starts a code; on any refusal reuses the number's pending code. Always returns the id of a real row."""
        try:
            return str(
                start_code_verification(
                    db, phone=phone, purpose=purpose, language=language, sender=sender, key=kit.key
                ).id
            )
        except VerificationError:
            pending = latest_pending(db, phone=phone, purpose=purpose, subject_id=None)
            if pending is not None:
                return str(pending.id)
            # Refused by the per-number send caps (e.g. a consent code went out a moment ago) with nothing to
            # reuse: a silent row, so the lockout after wrong guesses is the same as for any other number.
            silent = Verification(
                phone_e164=phone,
                purpose=purpose,
                method=sender.method,
                code_hash=hash_code(kit.key, purpose.value, phone, new_code()),
                expires_at=datetime.now(UTC) + CODE_TTL,
            )
            db.add(silent)
            db.flush()
            return str(silent.id)

    def lookup_form(
        request: Request, lang: Language, *, phone: str = "", errors: list[str] | None = None, status_code: int = 200
    ) -> HTMLResponse:
        return pages.form(
            request,
            "lookup.html",
            lang,
            status_code=status_code,
            title=t("lookup_title", lang),
            phone=phone,
            errors=errors or [],
        )

    def code_page(
        request: Request,
        lang: Language,
        *,
        title: str,
        intro: str,
        action: str,
        submit_label: str,
        submit_class: str | None = None,
        errors: list[str] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return pages.form(
            request,
            "code.html",
            lang,
            status_code=status_code,
            title=title,
            intro=intro,
            action=action,
            resend_action=None,
            submit_label=submit_label,
            submit_class=submit_class,
            errors=errors or [],
            sent=False,
            click=None,
        )

    def lookup_code(
        request: Request, lang: Language, errors: list[str] | None = None, status_code: int = 200
    ) -> HTMLResponse:
        return code_page(
            request,
            lang,
            title=t("lookup_title", lang),
            intro=t("lookup_sent", lang),
            action="/p/verify",
            submit_label=t("verify_submit", lang),
            errors=errors,
            status_code=status_code,
        )

    def deletion_code(
        request: Request,
        lang: Language,
        token: str,
        phone: str,
        errors: list[str] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return code_page(
            request,
            lang,
            title=t("delete_confirm_title", lang),
            intro=t("delete_confirm_intro", lang, phone=mask_phone(phone)),
            action=f"/p/{token}/delete/confirm",
            submit_label=t("delete_submit", lang),
            submit_class="danger",
            errors=errors,
            status_code=status_code,
        )

    def preferences(
        request: Request,
        db: Session,
        lang: Language,
        token: str,
        link: PreferenceLink,
        *,
        saved: bool = False,
        errors: list[str] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return pages.form(
            request,
            "preferences.html",
            lang,
            status_code=status_code,
            title=t("prefs_title", lang),
            token=token,
            entries=preference_entries(db, link.phone_fingerprint, kit.owner_lookup),
            purposes=active_purposes(db),
            saved=saved,
            errors=errors or [],
            notice=latest_notice(db, lang),
        )

    # Find preferences by number. The same answer whether or not the number is registered.

    @router.get("/p", response_class=HTMLResponse)
    def lookup_page(request: Request, lang: str | None = None) -> Response:
        return lookup_form(request, pick_lang(lang, Language.EN))

    @router.post("/p", response_class=HTMLResponse)
    def lookup_submit(
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
        phone: FormStr = "",
    ) -> Response:
        language = pick_lang(lang, Language.EN)
        if not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        try:
            phone_e164 = normalise_phone(phone)
        except InvalidPhone:
            return lookup_form(request, language, phone=phone, errors=["err_phone"], status_code=422)
        limit = settings.web_submissions_per_ip_hour
        allowed = allow(db, bucket="prefs_lookup", key=client_ip(request), limit=limit, window=HOUR, hash_key=kit.key)
        db.commit()
        if not allowed:
            return lookup_form(request, language, phone=phone, errors=["err_rate_limited"], status_code=429)

        # One code path for every number, registered or not, so nothing (cookie, attempts, limits, timing of
        # the answer) tells them apart. An unregistered number gets a real row with an unguessable code and a
        # sender that sends nothing. Sender latency is the host's responsibility: queue the send in its sender.
        registered = bool(preference_entries(db, phone_fingerprint(kit.key, phone_e164), kit.owner_lookup))
        sender = kit.sender if registered else _SilentSender(kit.sender.method)
        verification_id = start_or_reuse(db, phone_e164, VerificationPurpose.PREFERENCES, language, sender)
        db.commit()
        response = pages.redirect(f"/p/verify?lang={language.value}")
        pages.write_flow(response, {"pv": verification_id, "l": language.value})
        return response

    @router.get("/p/verify", response_class=HTMLResponse)
    def lookup_code_page(request: Request, lang: str | None = None) -> Response:
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or "pv" not in flow:
            return pages.session_expired(request, language)
        return lookup_code(request, language)

    @router.post("/p/verify", response_class=HTMLResponse)
    def lookup_code_submit(
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
        code: FormStr = "",
    ) -> Response:
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or "pv" not in flow or not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        row = flow_row(db, flow, "pv", VerificationPurpose.PREFERENCES)
        if row is None:  # a cookie that doesn't match a lookup row: same answer as a wrong code
            return lookup_code(request, language, errors=["err_wrong_code"], status_code=422)
        try:
            verification = check_code(
                db, verification_id=row.id, purpose=VerificationPurpose.PREFERENCES, code=code, key=kit.key
            )
        except VerificationError as error:
            db.commit()
            reason = "wrong_code" if error.reason == "not_found" else error.reason
            return lookup_code(request, language, errors=[f"err_{reason}"], status_code=422)
        token = issue_preference_link(db, phone_fingerprint(kit.key, verification.phone_e164))
        db.commit()
        response = pages.redirect(f"/p/{token}?lang={language.value}")
        pages.clear_flow(response)
        return response

    # Manage and delete, from a private link.

    @router.get("/p/{token}", response_class=HTMLResponse)
    def preferences_page(
        token: str,
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        lang: str | None = None,
        saved: int = 0,
    ) -> Response:
        link = resolve_preference_link(db, token)
        if link is None:
            return link_invalid(request, pick_lang(lang, Language.EN))
        entries = preference_entries(db, link.phone_fingerprint, kit.owner_lookup)
        fallback = entries[0].subject.language if entries else Language.EN
        return preferences(request, db, pick_lang(lang, fallback), token, link, saved=bool(saved))

    @router.post("/p/{token}/subjects/{subject_id}", response_class=HTMLResponse)
    def update_preferences(
        token: str,
        subject_id: uuid.UUID,
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
        purposes: Annotated[list[str], Form()] = [],  # noqa: B006 (FastAPI copies defaults)
    ) -> Response:
        language = pick_lang(lang, Language.EN)
        if not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        link = resolve_preference_link(db, token)
        if link is None:
            return link_invalid(request, language)
        subject = owned(db, link, subject_id)
        if subject is None:
            return not_found(request, language)
        has_guardian = subject.guardian_name is not None
        current = current_consent(db, subject.id)
        wanted = {code: code in purposes for code in purpose_codes(db)}
        # A minor's own number may only withdraw; granting needs the guardian's consent.
        own_number_minor = subject.is_minor and (not has_guardian or subject.minor_suspended)
        if own_number_minor and any(granted and not current.get(code) for code, granted in wanted.items()):
            return preferences(request, db, language, token, link, errors=["err_minor_needs_guardian"], status_code=422)
        set_consent(
            db,
            subject,
            wanted,
            source=ConsentSource.PREFERENCES_PAGE,
            given_by=GivenBy.GUARDIAN if has_guardian else GivenBy.SELF,
            notice=latest_notice(db, language),
            evidence=Evidence(client_ip(request), request.headers.get("user-agent")),
        )
        db.commit()
        return pages.redirect(f"/p/{token}?lang={language.value}&saved=1")

    @router.post("/p/{token}/delete", response_class=HTMLResponse)
    def start_deletion(
        token: str,
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
        target: FormStr = "",
    ) -> Response:
        language = pick_lang(lang, Language.EN)
        if not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        link = resolve_preference_link(db, token)
        if link is None:
            return link_invalid(request, language)
        limit = settings.web_submissions_per_ip_hour
        allowed = allow(db, bucket="prefs_delete", key=client_ip(request), limit=limit, window=HOUR, hash_key=kit.key)
        db.commit()
        if not allowed:
            return preferences(request, db, language, token, link, errors=["err_rate_limited"], status_code=429)
        if target == "all":
            # The code proves control of the number: everything held for it goes, verified or not.
            targets = list(db.scalars(select(Subject).where(Subject.phone_fingerprint == link.phone_fingerprint)))
        else:
            verified = [e.subject for e in preference_entries(db, link.phone_fingerprint, kit.owner_lookup)]
            targets = [s for s in verified if str(s.id) == target]
        if not targets:
            return not_found(request, language)
        phone = targets[0].phone_e164
        try:
            verification = start_code_verification(
                db, phone=phone, purpose=VerificationPurpose.DELETION, language=language, sender=kit.sender, key=kit.key
            )
        except VerificationError as error:
            verification = latest_pending(db, phone=phone, purpose=VerificationPurpose.DELETION, subject_id=None)
            if verification is None:
                db.commit()
                return preferences(request, db, language, token, link, errors=[f"err_{error.reason}"], status_code=429)
        db.commit()
        response = pages.redirect(f"/p/{token}/delete?lang={language.value}")
        pages.write_flow(
            response,
            {
                "dv": str(verification.id),
                "all": target == "all",
                "targets": [str(s.id) for s in targets],
                "l": language.value,
            },
        )
        return response

    @router.get("/p/{token}/delete", response_class=HTMLResponse)
    def deletion_code_page(
        token: str, request: Request, db: Annotated[Session, Depends(kit.session_dependency)], lang: str | None = None
    ) -> Response:
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or "dv" not in flow:
            return pages.session_expired(request, language)
        link = resolve_preference_link(db, token)
        if link is None:
            return link_invalid(request, language)
        verification = deletion_row(db, flow, link)
        if verification is None:
            return pages.session_expired(request, language)
        return deletion_code(request, language, token, verification.phone_e164)

    @router.post("/p/{token}/delete/confirm", response_class=HTMLResponse)
    def confirm_deletion(
        token: str,
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
        code: FormStr = "",
    ) -> Response:
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or "dv" not in flow or not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        link = resolve_preference_link(db, token)
        if link is None:
            return link_invalid(request, language)
        fingerprint = link.phone_fingerprint
        verification = deletion_row(db, flow, link)
        if verification is None:
            return pages.session_expired(request, language)
        phone = verification.phone_e164
        try:
            check_code(
                db, verification_id=verification.id, purpose=VerificationPurpose.DELETION, code=code, key=kit.key
            )
        except VerificationError as error:
            db.commit()
            return deletion_code(request, language, token, phone, errors=[f"err_{error.reason}"], status_code=422)
        # Never trust ids from the cookie: re-derive from the subjects of the number that was just verified.
        if flow.get("all") is True:
            current = list(db.scalars(select(Subject.id).where(Subject.phone_fingerprint == fingerprint)))
        else:
            wanted = {str(i) for i in flow.get("targets", []) if isinstance(i, str)}
            verified = [e.subject.id for e in preference_entries(db, fingerprint, kit.owner_lookup)]
            current = [i for i in verified if str(i) in wanted]
        for subject_id in current:
            kit.erase_subject(db, subject_id, DataRequestSource.SUBJECT)
        db.commit()
        response = pages.message(
            request, language, title_key="deleted_title", body_key="deleted_body", days=settings.retention_days
        )
        pages.clear_flow(response)
        return response

    return router
