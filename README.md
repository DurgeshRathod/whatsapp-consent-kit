# WhatsApp Consent Kit

Lawful, provable, per-purpose WhatsApp consent for FastAPI + SQLAlchemy + Postgres apps. Built for India's DPDP Act
and Meta's WhatsApp opt-in rules.

- Consent pages (English and Hindi), personal and practice links with QR codes, guardian consent for under-18s
- Number verification by a typed code (Meta authentication template) or click-to-chat (`START-XXXXXX`)
- STOP/START, opt-out buttons and Meta's marketing opt-out, from your webhook
- `can_send`, status by subject or phone, campaign audiences, a full audit history
- Preferences page per number, "delete my data", anonymised retention and daily maintenance

The kit stores no health data and never stores inbound message text. It never sends messages: you send codes through
your own sender and act on the outcomes it returns.

> **Status:** v0.1.0 is in development on the
> [`feat/v0.1.0`](https://github.com/DurgeshRathod/whatsapp-consent-kit/tree/feat/v0.1.0) branch. The design is in
> [`docs/specs`](docs/specs/2026-10-11-whatsapp-consent-kit-design.md) and the build plan in [`docs/plans`](docs/plans).

## How consent works

1. The owner (a coach, clinic, shop…) shares a **personal link** with one person, or a **practice link** / QR code
   with everyone.
2. The person picks each purpose separately (nothing is pre-ticked) against a versioned notice, in English or Hindi.
   A parent or guardian fills it in for a child.
3. Nothing is applied until the number is proven, by a typed code or by sending `START-XXXXXX` on WhatsApp. The
   submission is held on its verification row and applied exactly once.
4. Every grant and withdrawal is an append-only event with the notice version, language, source and evidence.
5. The person can manage or withdraw any time from a private preferences link, by replying STOP, or with an opt-out
   button. "Delete my data" erases them; an anonymised record (keyed fingerprint only) is kept for the retention
   period, then purged.

## Install

```bash
uv add "whatsapp-consent-kit @ git+https://github.com/DurgeshRathod/whatsapp-consent-kit.git@v0.1.0"
```

During development, point at a local checkout instead:

```toml
[tool.uv.sources]
whatsapp-consent-kit = { path = "../whatsapp-consent-kit", editable = true }
```

Requirements: Python ≥ 3.12, PostgreSQL ≥ 14, SQLAlchemy 2.x (sync sessions; async apps can use `run_sync`).

## Configure

| Variable | Meaning |
|---|---|
| `CONSENT_FINGERPRINT_KEY` | ≥ 32 chars, long-lived; keys phone fingerprints and codes |
| `CONSENT_SESSION_SECRET` | ≥ 32 chars; signs the page flow cookie |
| `CONSENT_PUBLIC_BASE_URL` | where the pages are served, e.g. `https://app.example.com` |
| `CONSENT_VERIFICATION_METHOD` | `authentication_template` (pass a sender) or `fake` (logs codes; refused in production) |
| `CONSENT_ENVIRONMENT` | `development`, `test` or `production` |
| `CONSENT_CLICK_TO_CHAT` | `true` to offer "Continue on WhatsApp" when the owner has a number |
| `CONSENT_META_APP_SECRET` | for webhook signature checks |
| `CONSENT_RETENTION_DAYS` | ≥ 365 (default 365); anonymised history is kept this long after erasure |
| `CONSENT_WEB_SUBMISSIONS_PER_IP_HOUR` | per-IP limit on page submissions (default 20) |
| `CONSENT_DATABASE_URL` | for the CLI only; falls back to `DATABASE_URL` |

## Database

The kit's tables live in the `whatsapp_consent` schema with their own migration history. Run its migrations before
your own:

```bash
uv run whatsapp-consent migrate && uv run alembic upgrade head
```

Load your purposes and notice from one of **your** data migrations:

```python
from whatsapp_consent.library import load_notices, load_purposes

def upgrade():
    conn = op.get_bind()
    load_purposes(conn, [
        {"code": "service", "category": "UTILITY", "position": 1,
         "labels": {"en": {"name": "Service messages", "description": "reminders and payment notices"},
                    "hi": {"name": "सेवा संदेश", "description": "रिमाइंडर और भुगतान की सूचनाएँ"}}},
        {"code": "updates", "category": "MARKETING", "position": 2, "labels": {...}},
    ])
    load_notices(conn, [{"version": 1, "language": "en", "effective_from": "2026-10-11",
                         "title": "...", "summary": "...", "body": "Paragraph one.\n\nParagraph two."},
                        {"version": 1, "language": "hi", ...}])
```

Notices are never edited: publish version 2 in a new migration.

## Mount

```python
from whatsapp_consent import ConsentKit, ConsentSettings, Language, OwnerContext, OwnerInfo

def current_owner(user = Depends(current_user)) -> OwnerContext:
    return OwnerContext(owner_id=user.id, info=OwnerInfo(user.business_name, Language.EN, user.whatsapp_number))

kit = ConsentKit(
    settings=ConsentSettings(),
    session_dependency=get_db,
    owner_dependency=current_owner,
    owner_lookup=lambda db, owner_id: ...,          # OwnerInfo for public pages
    sender=my_authentication_template_sender,       # VerificationSender
    on_subject_erased=delete_my_data_about_person,   # runs in the same transaction
)
app.include_router(kit.owner_router(prefix="/clients"))
app.include_router(kit.public_router())
```

A sender is any class with a `method` and `send_code`:

```python
from whatsapp_consent.enums import VerificationMethod
from whatsapp_consent.senders import VerificationSender

class QueuedTemplateSender:  # satisfies VerificationSender
    method = VerificationMethod.AUTHENTICATION_TEMPLATE

    def send_code(self, phone_e164: str, code: str, language: Language) -> None:
        queue.enqueue(send_authentication_template, phone_e164, code, language.value)  # returns at once
```

Your sender's `send_code` runs inside the request. Keep it fast (queue the WhatsApp call) so a page's response time
doesn't depend on whether a code was really sent: the preferences lookup gives the same answer for registered and
unregistered numbers, and timing is part of that answer.

## Ask

```python
kit.can_send(db, subject_id, "updates")
kit.can_send_category(db, subject_id, TemplateCategory.MARKETING)
kit.sendable_subjects(db, owner_id, "updates")
kit.status_by_phone(db, owner_id, "98765 43210")
kit.audit_by_phone(db, owner_id, "+919876543210")
```

## WhatsApp webhook

```python
from whatsapp_consent.meta import InvalidSignature, InvalidWebhookPayload


@app.post("/webhooks/whatsapp")
async def webhook(request: Request, db: Session = Depends(get_db)):
    try:
        events = kit.parse_webhook(await request.body(), request.headers.get("X-Hub-Signature-256"))
    except InvalidSignature:
        raise HTTPException(403)
    except InvalidWebhookPayload:
        raise HTTPException(400)
    by_number: dict[str, list] = {}
    for event in events:
        by_number.setdefault(event.phone_number_id, []).append(event)
    for phone_number_id, number_events in by_number.items():
        owner_id = owner_for_number(phone_number_id)   # your mapping; skip numbers you don't know
        for outcome in kit.handle_inbound(db, owner_id, number_events):  # one phone_number_id per call
            ...  # e.g. reply with outcome.preferences_url inside the 24-hour window
    db.commit()
```

Add `whatsapp_consent.messages.opt_out_footer(...)` and `stop_button(...)` to marketing templates. When you send
one, pass the matching payload parameter with the send, or a tap arrives as plain text and is ignored:

```python
from whatsapp_consent.messages import manage_button_parameter, stop_button_parameter

components = [
    # ...body parameters...
    stop_button_parameter("updates", index=0),   # the purpose this message was sent under
    manage_button_parameter(index=1),            # only if the template has a manage button
]
```

## Daily maintenance

Call `kit.run_maintenance(db)` from a scheduled job. It erases unverified practice-link sign-ups older than 30 days
(calling your `on_subject_erased` hook) and purges anonymised records past the retention period.

`uv run whatsapp-consent maintenance` runs only the purge, because a CLI can't call your hook. Hosts without an erasure
hook can add `--cleanup-unverified-without-host-hook`.

## Good to know

- Looking up a number on the preferences page counts toward that number's code limits (3 an hour, 10 a day), even
  when it isn't registered, so the answer looks the same either way.
- A minor's own number (no guardian on record) can withdraw consent but never grant it; only a guardian's verified
  consent can.
- Guardian consent is a declaration: a minor holding the guardian link could declare a guardian on their own
  number. Owners who mark minors should review guardian sign-ups.
- If a new notice version is published in only some languages, the other languages keep showing their latest
  version.

## Before launch

Have a privacy lawyer review your notice text (every language), the guardian declaration, the kit's page texts and
your retention period.

## Develop

```bash
uv sync && uv run pytest -q && uv run ruff check src tests && uv run pyright
```

Tests need a local Postgres (`TEST_ADMIN_DATABASE_URL`, default `postgresql+psycopg://postgres@localhost:5432/postgres`).
