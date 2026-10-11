"""Public consent pages: /c/{token} (form), /c/verify (code or click-to-chat), /c/done."""

import uuid
from datetime import date, timedelta
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from whatsapp_consent.enums import Language, VerificationMethod, VerificationPurpose
from whatsapp_consent.events import Evidence, active_purposes, purpose_codes
from whatsapp_consent.hashing import phone_fingerprint
from whatsapp_consent.invites import resolve_invite
from whatsapp_consent.models import Invite, Subject, Verification
from whatsapp_consent.notices import latest_notice
from whatsapp_consent.phones import local_display, mask_phone
from whatsapp_consent.preference_links import issue_preference_link, preference_url
from whatsapp_consent.ratelimit import allow
from whatsapp_consent.submissions import (
    DuplicateSubject,
    InviteAlreadyUsed,
    MinorNeedsGuardian,
    complete_consent_verification,
    hold_consent,
    pending_payload,
)
from whatsapp_consent.verification import (
    VerificationError,
    check_code,
    latest_pending,
    start_click_verification,
    start_code_verification,
)
from whatsapp_consent.web.forms import empty_consent_values, parse_consent_form
from whatsapp_consent.web.i18n import MONTHS, pick_lang, t
from whatsapp_consent.web.render import Pages

if TYPE_CHECKING:
    from whatsapp_consent.kit import ConsentKit, OwnerInfo

HOUR = timedelta(hours=1)
FormStr = Annotated[str, Form()]


def client_ip(request: Request) -> str:
    # Behind a proxy, run the server with proxy headers enabled so this is the real client address.
    return request.client.host if request.client else "unknown"


def build(kit: "ConsentKit", pages: Pages) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    settings = kit.settings

    def context(db: Session, token: str) -> "tuple[Invite, OwnerInfo] | None":
        invite = resolve_invite(db, token)
        if invite is None:
            return None
        info = kit.owner_lookup(db, invite.owner_id)
        return (invite, info) if info is not None else None

    def already_done(request: Request, lang: Language) -> HTMLResponse:
        return pages.message(
            request,
            lang,
            title_key="already_done_title",
            body_key="already_done_body",
            link=f"/p?lang={lang.value}",
            link_key="open_preferences",
        )

    def prefill(db: Session, invite: Invite, who: str) -> dict:
        values = empty_consent_values()
        subject = db.get(Subject, invite.subject_id) if invite.subject_id else None
        if subject is None:
            return values
        phone = local_display(subject.phone_e164)
        if who == "me":
            values |= {"name": subject.display_name, "phone": phone}
        else:
            values |= {
                "child_name": subject.display_name,
                "phone": phone,
                "guardian_name": subject.guardian_name or "",
                "guardian_relation": subject.guardian_relation or "",
            }
        if subject.birthday_day is not None:
            values |= {"birthday_day": str(subject.birthday_day), "birthday_month": str(subject.birthday_month)}
        return values

    def consent_form(
        request: Request,
        db: Session,
        lang: Language,
        invite: Invite,
        info: "OwnerInfo",
        token: str,
        who: str,
        values: dict,
        errors: list[str],
        status_code: int = 200,
    ) -> HTMLResponse:
        return pages.form(
            request,
            "consent_form.html",
            lang,
            status_code=status_code,
            business=info.display_name,
            token=token,
            who=who,
            values=values,
            errors=errors,
            purposes=active_purposes(db),
            notice=latest_notice(db, lang),
            months=MONTHS[lang],
            current_year=date.today().year,
            minor_only=invite.minor_only,
        )

    def code_page(
        request: Request,
        db: Session,
        lang: Language,
        flow: dict,
        *,
        errors: list[str] | None = None,
        sent: bool = False,
        status_code: int = 200,
    ) -> HTMLResponse:
        verification = db.get(Verification, uuid.UUID(flow["v"])) if flow.get("v") else None
        subject = db.get(Subject, uuid.UUID(flow["s"]))
        if subject is None:
            return pages.invalid_link(request, lang)
        info = kit.owner_lookup(db, subject.owner_id)
        click = None
        if flow.get("c") and info and info.whatsapp_number:
            number = info.whatsapp_number.lstrip("+")
            click = {"url": f"https://wa.me/{number}?text={quote(flow['c'])}", "check_action": "/c/verify/check"}
        phone = verification.phone_e164 if verification else subject.phone_e164
        return pages.form(
            request,
            "code.html",
            lang,
            status_code=status_code,
            title=t("verify_title", lang),
            intro=t("verify_intro", lang, phone=mask_phone(phone)),
            action="/c/verify",
            resend_action="/c/verify/resend",
            submit_label=t("verify_submit", lang),
            errors=errors or [],
            sent=sent,
            click=click,
            business=info.display_name if info else "",
        )

    def flow_row(db: Session, flow: dict, field: str) -> Verification | None:
        """The flow's verification row, only if it is a consent verification for the flow's subject."""
        if not flow.get(field) or not flow.get("s"):
            return None
        row = db.get(Verification, uuid.UUID(flow[field]))
        if row is None or row.purpose != VerificationPurpose.CONSENT or row.subject_id != uuid.UUID(flow["s"]):
            return None
        return row

    def finish(db: Session, subject: Subject, verification: Verification, lang: Language) -> Response:
        """The number is proven and the held submission applied: hand out the preferences link for that number."""
        token = issue_preference_link(db, phone_fingerprint(kit.key, verification.phone_e164))
        info = kit.owner_lookup(db, subject.owner_id)
        db.commit()
        response = pages.redirect(f"/c/done?lang={lang.value}")
        pages.write_flow(response, {"done": True, "pt": token, "b": info.display_name if info else "", "l": lang.value})
        return response

    # Fixed paths first: /c/verify and /c/done must not be read as tokens.

    @router.get("/c/verify", response_class=HTMLResponse)
    def verify_page(
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        lang: str | None = None,
        sent: int = 0,
    ) -> Response:
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or "s" not in flow:
            return pages.session_expired(request, language)
        if (flow.get("v") and flow_row(db, flow, "v") is None) or (flow.get("cv") and flow_row(db, flow, "cv") is None):
            return pages.invalid_link(request, language)  # e.g. the owner erased the subject meanwhile
        return code_page(request, db, language, flow, sent=bool(sent))

    @router.post("/c/verify", response_class=HTMLResponse)
    def verify_submit(
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
        code: FormStr = "",
    ) -> Response:
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or not flow.get("v") or not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        if flow_row(db, flow, "v") is None:
            return pages.invalid_link(request, language)
        try:
            verification = check_code(
                db, verification_id=uuid.UUID(flow["v"]), purpose=VerificationPurpose.CONSENT, code=code, key=kit.key
            )
        except VerificationError as error:
            db.commit()  # keep the attempt count
            return code_page(request, db, language, flow, errors=[f"err_{error.reason}"], status_code=422)
        try:
            subject = complete_consent_verification(db, verification)
        except InviteAlreadyUsed:
            db.rollback()
            return already_done(request, language)
        except LookupError:
            db.rollback()
            return pages.invalid_link(request, language)
        except IntegrityError:
            db.rollback()
            return code_page(request, db, language, flow, errors=["err_duplicate"], status_code=409)
        return finish(db, subject, verification, language)

    @router.post("/c/verify/check", response_class=HTMLResponse)
    def click_check(
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
    ) -> Response:
        """After sending START-XXXXXX on WhatsApp: the inbound handler has verified the number, or not yet."""
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or "s" not in flow or not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        # Only the click-to-chat row this flow started counts; never the subject's verified state.
        verification = flow_row(db, flow, "cv") if settings.click_to_chat else None
        subject = db.get(Subject, uuid.UUID(flow["s"]))
        if verification is None or verification.method != VerificationMethod.CLICK_TO_CHAT or subject is None:
            return pages.invalid_link(request, language)
        if verification.verified_at is None or verification.pending is not None:
            # Not received yet (the inbound handler verifies the row and applies the held submission).
            return code_page(request, db, language, flow, errors=["err_not_received_yet"], status_code=422)
        return finish(db, subject, verification, language)

    @router.post("/c/verify/resend", response_class=HTMLResponse)
    def resend(
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
    ) -> Response:
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or not flow.get("v") or not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        previous = flow_row(db, flow, "v")
        if previous is None:
            return pages.invalid_link(request, language)
        limit = settings.web_submissions_per_ip_hour
        if not allow(db, bucket="code_resend", key=client_ip(request), limit=limit, window=HOUR, hash_key=kit.key):
            db.commit()
            return code_page(request, db, language, flow, errors=["err_rate_limited"], status_code=429)
        try:
            fresh = start_code_verification(
                db,
                phone=previous.phone_e164,
                purpose=VerificationPurpose.CONSENT,
                language=language,
                sender=kit.sender,
                key=kit.key,
                subject_id=previous.subject_id,
                pending=previous.pending,
            )
        except VerificationError as error:
            db.commit()
            return code_page(request, db, language, flow, errors=[f"err_{error.reason}"], status_code=429)
        db.commit()
        response = pages.redirect(f"/c/verify?lang={language.value}&sent=1")
        pages.write_flow(response, flow | {"v": str(fresh.id)})
        return response

    @router.get("/c/done", response_class=HTMLResponse)
    def done_page(request: Request, lang: str | None = None) -> Response:
        flow = pages.read_flow(request)
        language = pages.flow_lang(lang, flow)
        if not flow or not flow.get("done"):
            return pages.session_expired(request, language)
        return pages.render(
            request, "done.html", language, business=flow["b"], manage_url=preference_url(settings, flow["pt"])
        )

    @router.get("/c/{token}", response_class=HTMLResponse)
    def consent_page(
        token: str,
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        lang: str | None = None,
        who: str | None = None,
    ) -> Response:
        found = context(db, token)
        if found is None:
            return pages.invalid_link(request, pick_lang(lang, Language.EN))
        invite, info = found
        language = pick_lang(lang, info.language)
        if invite.used_at is not None:
            return already_done(request, language)
        if invite.minor_only:
            who = "child"
        if who not in ("me", "child"):
            return pages.render(request, "consent_choose.html", language, business=info.display_name, token=token)
        return consent_form(request, db, language, invite, info, token, who, prefill(db, invite, who), [])

    @router.post("/c/{token}", response_class=HTMLResponse)
    def consent_submit(
        token: str,
        request: Request,
        db: Annotated[Session, Depends(kit.session_dependency)],
        csrf: FormStr = "",
        lang: FormStr = "",
        who: FormStr = "me",
        name: FormStr = "",
        phone: FormStr = "",
        birth_year: FormStr = "",
        birthday_day: FormStr = "",
        birthday_month: FormStr = "",
        guardian_name: FormStr = "",
        guardian_relation: FormStr = "",
        child_name: FormStr = "",
        declaration: FormStr = "",
        purposes: Annotated[list[str], Form()] = [],  # noqa: B006 (FastAPI copies defaults)
    ) -> Response:
        language = pick_lang(lang, Language.EN)
        found = context(db, token)
        if found is None:
            return pages.invalid_link(request, language)
        invite, info = found
        if not pages.csrf_ok(request, csrf):
            return pages.session_expired(request, language)
        if invite.used_at is not None:
            return already_done(request, language)

        who = "child" if invite.minor_only or who == "child" else "me"
        values = {
            "name": name,
            "phone": phone,
            "birth_year": birth_year,
            "birthday_day": birthday_day,
            "birthday_month": birthday_month,
            "guardian_name": guardian_name,
            "guardian_relation": guardian_relation,
            "child_name": child_name,
            "declaration": declaration,
            "purposes": purposes,
        }

        def again(errors: list[str], status_code: int = 422, as_who: str = who) -> HTMLResponse:
            return consent_form(request, db, language, invite, info, token, as_who, values, errors, status_code)

        limit = settings.web_submissions_per_ip_hour
        allowed = allow(db, bucket="consent_submit", key=client_ip(request), limit=limit, window=HOUR, hash_key=kit.key)
        db.commit()  # the hit counts even if the form has errors
        if not allowed:
            return again(["err_rate_limited"], 429)

        parsed = parse_consent_form(
            who=who, allowed_purposes=purpose_codes(db), language=language, current_year=date.today().year, **values
        )
        if parsed.switch_to_child:
            values["child_name"] = values["child_name"] or name
            return again(["err_under_18"], 200, "child")
        if parsed.errors or parsed.submission is None:
            return again(parsed.errors)

        submission = parsed.submission
        notice = latest_notice(db, language)
        evidence = Evidence(client_ip(request), request.headers.get("user-agent"))
        try:
            subject = hold_consent(
                db, invite=invite, submission=submission, notice=notice, evidence=evidence, key=kit.key
            )
        except MinorNeedsGuardian:
            db.rollback()
            values["child_name"] = values["child_name"] or name
            return again(["err_minor_needs_guardian"], 200, "child")
        except (DuplicateSubject, IntegrityError):
            db.rollback()
            return again(["err_duplicate"], 409)
        except LookupError:
            db.rollback()
            return pages.invalid_link(request, language)
        held = pending_payload(invite=invite, submission=submission, notice=notice, evidence=evidence, key=kit.key)

        try:
            verification = start_code_verification(
                db,
                phone=submission.phone,
                purpose=VerificationPurpose.CONSENT,
                language=language,
                sender=kit.sender,
                key=kit.key,
                subject_id=subject.id,
                pending=held,
            )
        except VerificationError as error:
            reused = None
            if error.reason == "too_soon":
                reused = latest_pending(
                    db, phone=submission.phone, purpose=VerificationPurpose.CONSENT, subject_id=subject.id
                )
            if reused is None:
                db.rollback()  # nothing is kept for a number we can't verify right now
                return again([f"err_{error.reason}"], 429 if error.reason in ("too_soon", "rate_limited") else 422)
            reused.pending = held  # the latest submission is the one the code will confirm
            verification = reused

        flow = {"v": str(verification.id), "s": str(subject.id), "l": language.value}
        if settings.click_to_chat and info.whatsapp_number:
            click, click_text = start_click_verification(
                db, phone=submission.phone, subject_id=subject.id, key=kit.key, pending=held
            )
            flow |= {"cv": str(click.id), "c": click_text}
        db.commit()
        response = pages.redirect(f"/c/verify?lang={language.value}")
        pages.write_flow(response, flow)
        return response

    return router
