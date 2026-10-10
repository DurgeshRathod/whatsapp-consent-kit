# WhatsApp Consent Kit: Design Spec

- **Date:** 2026-10-11
- **Status:** Approved in conversation (sections 1–3), awaiting written-spec review
- **Package:** `whatsapp-consent-kit` (import `whatsapp_consent`), repository `~/whatsapp-consent-kit`
- **First host:** marketing-saas (`~/marketing-saas`), whose original design is
  `docs/superpowers/specs/2026-10-11-clients-consent-design.md` in that repository. This spec supersedes its
  implementation sections; its product decisions (purposes, guardian flow, deletion rules) are carried over here.

## 1. Goal

A reusable Python package that gives any product with a WhatsApp Business number **lawful, provable, per-purpose
consent** for messaging people: collecting it, verifying the number, honouring opt-outs on WhatsApp, answering
"may I send this?", and erasing data on request. Built for India's DPDP Act and Meta's WhatsApp opt-in rules.

The kit stores **no health information** and **never stores inbound message text**.

## 2. Decisions

| # | Decision |
|---|---|
| K1 | A library first, embedded in each host (FastAPI + SQLAlchemy + Postgres). A standalone microservice later wraps the same package |
| K2 | Own git repository, semantic version tags; hosts pin a tag, develop against an editable local checkout |
| K3 | Generic terms: an **owner** (the business, e.g. a coach; DPDP data fiduciary) and a **subject** (the person consenting). The kit stores only an opaque `owner_id` from the host |
| K4 | WhatsApp-specific: purposes map to Meta template categories; verification by authentication template or click-to-chat; STOP/START keywords, buttons and Meta's opt-out signal; Meta webhook helpers |
| K5 | Closed sets are `StrEnum`s, stored as text with a generated CHECK constraint (not native Postgres enums), so a new value is a small migration |
| K6 | Purposes are host-defined (codes, labels in each language), each bound to one `TemplateCategory`. The send check works by purpose; a by-category shortcut works only when exactly one purpose maps to that category |
| K7 | The kit uses the **host's SQLAlchemy session**: kit and host changes commit together (erasure must remove host data in the same transaction). Its CLI and the future service build an engine from settings |
| K8 | Tables live in Postgres schema `whatsapp_consent`, with the kit's own Alembic history and version table. No foreign keys from the kit to host tables |
| K9 | Synchronous SQLAlchemy 2.x API. Async hosts call it via `AsyncSession.run_sync` |
| K10 | Postgres 14+, Python 3.12+. IDs are generated in Python (no `uuidv7()` dependency) |
| K11 | Public pages are server-rendered (Jinja2, no JavaScript), English and Hindi built in |
| K12 | The kit never sends WhatsApp messages. It needs a code **sender** from the host and returns **outcomes** (e.g. "send the preferences link") for the host to act on |
| K13 | v0.1: everything in this spec. v0.2: consent inside WhatsApp with WhatsApp Flows, same records |

Carried over from the marketing-saas design (product decisions):
- **Guardian flow:** for anyone under 18, the parent consents and receives the messages. Safeguards:
  - the owner can force guardian-only on personal links;
  - "for me" asks a neutral year-of-birth question, which is never stored;
  - the owner can mark a subject as a minor, which suspends them until a guardian consents.
- **Subject data:** name, WhatsApp number and birthday (day and month, optional). For minors, also the guardian's
  name and relationship. No free-text fields.
- **Erasure:** personal data is erased at once. Consent history, outbound send records and the request itself are
  kept anonymised under an HMAC phone fingerprint for `RETENTION_DAYS` (≥ 365, DPDP Rules 8(3)), then purged.
- **Erasure is per owner**, with a "delete from all" option.
- **Practice-link sign-ups** need the owner's review. Unverified ones are erased after 30 days.

**Legal review before launch:** host notice texts, the guardian declaration, the kit's built-in page texts (English
and Hindi), and the reading of Rule 8(3).

## 3. Enums (`whatsapp_consent.enums`)

| Enum | Values |
|---|---|
| `TemplateCategory` | `MARKETING`, `UTILITY`, `AUTHENTICATION` (exactly Meta's values) |
| `ConsentAction` | `granted`, `withdrawn` |
| `ConsentSource` | `consent_page`, `preferences_page`, `owner_recorded`, `whatsapp_keyword`, `whatsapp_button`, `meta_opt_out`, `system` |
| `GivenBy` | `self`, `guardian` |
| `InviteKind` | `personal`, `practice` |
| `SubjectSource` | `personal_link`, `practice_link`, `whatsapp` |
| `SubjectStatus` | `needs_review`, `suspended`, `invited`, `unverified`, `active`, `withdrawn` |
| `SendBlocker` | `no_consent`, `unverified`, `needs_review`, `minor_suspended` |
| `VerificationPurpose` | `consent`, `preferences`, `deletion` |
| `VerificationMethod` | `authentication_template`, `click_to_chat`, `fake` |
| `DataRequestSource` | `subject`, `owner`, `system` |
| `InboundEventType` | `text`, `button_reply`, `status`, `error` |
| `InboundOutcomeType` | `withdrawn`, `verified`, `send_preferences_link`, `ignored` |
| `Language` | `en`, `hi` |

`AUTHENTICATION` messages (codes the person requested) are always allowed; no purpose may map to it.

## 4. Host integration

```python
kit = ConsentKit(
    settings=ConsentSettings(),             # CONSENT_* environment variables
    session_dependency=get_db,              # host's FastAPI dependency yielding a Session
    owner_dependency=current_owner,         # host dependency -> OwnerContext(owner_id, ...); raises 401/409 itself
    owner_lookup=lookup_owner,              # (db, owner_id) -> OwnerInfo | None
    sender=code_sender,                     # VerificationSender (authentication template); `fake` in development
    on_subject_erased=delete_host_data,     # optional (db, owner_id, subject_id) -> None, same transaction
)
app.include_router(kit.owner_router(), prefix="/clients")   # owner API (section 7)
app.include_router(kit.public_router())                     # /c, /p, /notice, /consent-static
```

- `OwnerContext(owner_id: UUID, info: OwnerInfo)` is what `owner_dependency` returns for the signed-in owner; it raises the host's own 401/409 when there is none.
- `OwnerInfo(display_name: str, language: Language, whatsapp_number: str | None)`. The page shows `display_name`
  as the business being opted in to. A `whatsapp_number` enables click-to-chat verification for that owner.
- Purposes and notices are host content, loaded by a **host** data migration:
  `whatsapp_consent.library.load_purposes(conn, rows)` and `load_notices(conn, rows)`.
- Migrations: the host runs `whatsapp-consent migrate` before its own migrations.

## 5. Python API (on `ConsentKit`, all taking the host `Session`)

| Call | Returns |
|---|---|
| `status(db, subject_id)` | `SubjectState`: per-purpose granted, verified, `SubjectStatus`, `SendBlocker`s per purpose, last change, notice version and language |
| `status_by_phone(db, owner_id, phone)` | `list[SubjectState]` (a parent may have several children); phone in any format |
| `can_send(db, subject_id, purpose)` | `bool`, the check before every message |
| `can_send_category(db, subject_id, TemplateCategory)` | `bool`; raises `AmbiguousCategory` unless exactly one purpose maps to it; `AUTHENTICATION` is always true |
| `sendable_subjects(db, owner_id, purpose)` | subject ids allowed right now, one query (campaign audience) |
| `audit(db, subject_id)` / `audit_by_phone(db, owner_id, phone)` | full consent history; by phone it still works after erasure, within retention |
| `handle_inbound(db, owner_id, events)` | `list[InboundOutcome]` (section 9) |
| `erase_subject(db, subject_id, source)`, `erase_owner(db, owner_id)` | erasure (section 11) |
| `run_maintenance(db)` | cleanup + purge counts |

## 6. Data model (schema `whatsapp_consent`)

| Table | Columns (main) | Rules |
|---|---|---|
| `subjects` | owner_id, display_name, phone_e164, phone_fingerprint, birthday_day, birthday_month, is_minor, guardian_name, guardian_relation, source, language, phone_verified_at, needs_review, minor_suspended | unique (owner_id, phone_e164, lower(display_name)); E.164 CHECK; birthday both-or-neither and valid; guardian fields both-or-neither and only on minors; minor_suspended only on minors |
| `invites` | owner_id, kind, subject_id, token_hash, minor_only, expires_at, used_at, revoked_at | personal ⇔ subject_id set and expires (7 days); one active practice invite per owner (partial unique); token stored as hash (practice token derived from the invite id, so it can be re-shown) |
| `purposes` | code, category, labels jsonb (per language: name, description), position, is_active | category never `AUTHENTICATION` |
| `notices` | version, language, title, summary, body, effective_from | unique (version, language); never edited |
| `consent_events` | owner_id, subject_id (nullable), phone_fingerprint, purpose_code, action, source, given_by, notice_version, notice_language, verified_at_time, occurred_at | append-only by trigger: only clearing subject_id, or deletion by the purge (transaction setting) |
| `consent_evidence` | event_id, ip, user_agent | deleted on erasure |
| `consent_state` | subject_id, purpose_code, granted, last_event_id | maintained by trigger on consent_events |
| `verifications` | subject_id, phone_e164, purpose, method, code_hash, attempts, expires_at, verified_at | per-number limits; deleted after 2 days |
| `preference_links` | phone_fingerprint, token_hash, expires_at (365 days), revoked_at | revoked when the number's last subject is erased |
| `data_requests` | owner_id, subject_id (nullable), phone_fingerprint, kind, source, requested_at, completed_at | survives erasure, anonymised |
| `rate_limit_events` | bucket, key_hash, created_at | IPs stored hashed; deleted after 2 days |

Fingerprints: `HMAC-SHA256(CONSENT_FINGERPRINT_KEY, "phone:" + E.164)`. Tokens: SHA-256 hashes. Codes: keyed hash
bound to purpose and phone. All enum columns: text + CHECK generated from the enum.

## 7. Owner API (`kit.owner_router()`, mounted by the host)

Fixed paths are declared before `/{subject_id}`.

| Endpoint | Does |
|---|---|
| `POST /` | add a subject (name, phone, birthday, under 18); returns subject + personal link + share message + `wa.me` URL |
| `GET /` | list, `?status=` (`SubjectStatus`) |
| `GET /status?phone=` | `status_by_phone` |
| `GET /sendable?purpose=` | count and ids |
| `GET /activity` | anonymised notices (deletions requested by subjects, stale sign-ups removed) |
| `GET /practice-link`, `POST /practice-link/rotate` | practice link + QR (SVG) |
| `GET /{id}` | status, blockers per purpose, history |
| `POST /{id}/invite`, `/{id}/approve`, `/{id}/withdraw`, `/{id}/mark-minor` | as in the original design |
| `DELETE /{id}` | erase (source `owner`) |

Share messages are built in English or Hindi from `OwnerInfo.language`.

## 8. Public pages (`kit.public_router()`)

As approved in the original design, generic in wording:
- `/c/{token}`: choose who, form, code entry, done.
- `/p`: find preferences by number (the same answer whether or not the number is registered).
- `/p/{token}`: per-owner toggles, delete one or all.
- `/notice/{version}`.

Page rules:
- Purpose labels come from the host's `purposes.labels`; the kit's own texts come from its English/Hindi strings.
- When click-to-chat is enabled for the owner, the code step offers **"Continue on WhatsApp"**: a `wa.me` link to the
  owner's number with `START-XXXXXX` pre-filled, alongside the typed-code option.
- Security: CSRF on every form, signed short-lived flow cookie, `noindex`, strict CSP, `Referrer-Policy:
  no-referrer`, no third-party assets, per-IP limits.

## 9. WhatsApp handling

**Meta helpers (`whatsapp_consent.meta`):**
- `verify_signature(raw_body, header, app_secret)` checks `X-Hub-Signature-256`.
- `parse_webhook(payload)` → `list[InboundEvent]`: phone_number_id, from (E.164), type, message id, timestamp, button
  payload, status, error code. Text bodies are kept only on the event object, in memory.

**`handle_inbound(db, owner_id, events)`:** the host maps `phone_number_id` to its owner and passes the events.
For each event, the subjects are found by (owner_id, sender phone):

| Event | Outcome |
|---|---|
| Text matching a **STOP word** (`STOP`, `UNSUBSCRIBE`, `STOP ALL`, `रोकें`, `बंद`, `बंद करें`; case-insensitive, punctuation ignored) | withdraw **every** purpose for those subjects (source `whatsapp_keyword`); outcome `withdrawn`, with the preferences link for the host's confirmation reply |
| Text `START` or `SUBSCRIBE` | no consent change (consent is given on the page, against a notice); outcome `send_preferences_link` |
| Text `START-XXXXXX` matching an open click-to-chat verification for that sender | verify the number (`consent` purpose); outcome `verified` |
| Button payload `whatsapp_consent:stop:<purpose>` | withdraw that purpose (source `whatsapp_button`) |
| Button payload `whatsapp_consent:manage` | outcome `send_preferences_link` |
| Error/status event carrying Meta's marketing opt-out signal | withdraw every `MARKETING` purpose (source `meta_opt_out`) |
| Anything else | `ignored`; text discarded |

The exact Meta error code(s) for the marketing opt-out signal are confirmed against Meta's documentation during
implementation (open item). They're kept in one constant.

**Message helpers (`whatsapp_consent.messages`):** opt-out footer text in each language (≤ 60 characters, Meta's
footer limit), and quick-reply button definitions with the payloads above, for hosts to add to their templates.

## 10. Verification

- `VerificationSender` protocol: `method` and `send_code(phone, code, language)`. The kit ships a `FakeSender` (logs
  codes) and is configured with the host's sender.
- `CONSENT_VERIFICATION_METHOD=fake` is refused when `CONSENT_ENVIRONMENT=production`.
- Typed codes: 6 digits, 10 minutes, 5 attempts, resend after 60 s, at most 3 per number per hour and 10 per day,
  and per-IP limits.
- Click-to-chat codes: `START-` + 6 characters (no 0/O/1/I), valid 30 minutes. They're verified only when the inbound
  sender's number equals the subject's number.

## 11. Erasure and retention

One routine, one transaction, for every trigger (subject request, owner removal, `erase_owner`, unverified cleanup):
1. Record `withdrawn` events for every granted purpose.
2. Call the host's `on_subject_erased(db, owner_id, subject_id)`.
3. Delete evidence, invites and verifications, then the subject (events and requests keep only the fingerprint).
4. Revoke preference links if the number has no subjects left.
5. Complete the data request.

`whatsapp-consent maintenance` (daily):
- erases unverified practice-link sign-ups older than 30 days;
- purges anonymised events and requests older than `CONSENT_RETENTION_DAYS`;
- deletes verifications and rate-limit rows older than 2 days, spent invites older than 30 days, and
  revoked or expired preference links.

## 12. Settings (`ConsentSettings`, prefix `CONSENT_`)

`DATABASE_URL` (falls back to `DATABASE_URL`, CLI only), `ENVIRONMENT`, `PUBLIC_BASE_URL`, `FINGERPRINT_KEY` (≥ 32,
long-lived), `SESSION_SECRET` (≥ 32), `VERIFICATION_METHOD` (`authentication_template` | `fake`),
`CLICK_TO_CHAT` (bool), `META_APP_SECRET` (optional, for signature checks), `RETENTION_DAYS` (≥ 365, default 365),
`WEB_SUBMISSIONS_PER_IP_HOUR` (default 20).

## 13. Repository, versions, tests

- **Layout:** `src/whatsapp_consent/` holds:
  - `kit.py`, `enums.py`, `config.py`, `models.py`
  - `phones.py`, `hashing.py`, `events.py`, `gate.py`, `status.py`, `subjects.py`, `invites.py`
  - `verification.py`, `senders.py`, `ratelimit.py`, `inbound.py`, `meta.py`, `messages.py`
  - `preferences.py`, `erasure.py`, `audit.py`, `library.py`, `cli.py`
  - `api/`, `web/`, `migrations/`

  Also `tests/`, `docs/`, `README.md` (quick start for a new host) and `CHANGELOG.md`.
- **Versions:** semantic version tags; v0.1.0 at the end of the first plan.
- **Tests:** pytest against a throwaway Postgres database per run, with a minimal example host app (fake owner
  dependency, sample purposes and notices) to exercise the routers and pages end to end. Coverage:
  - everything in the original testing section;
  - enum CHECK constraints;
  - STOP/START/buttons/click-to-chat;
  - signature checks and webhook parsing on recorded sample payloads;
  - status lookups and the audience query;
  - the send check by category;
  - the erasure hook in the same transaction;
  - a search of every table after erasure for leftover personal data;
  - English and Hindi text completeness.

## 14. marketing-saas as the first host

- Depends on the kit (git tag; editable local path in development).
- Host module `msaas_api/consent.py`:
  - owner = the signed-in coach who finished onboarding;
  - owner lookup = profile business name and language (`hi`/`hinglish` → `hi`);
  - sender = fake for now;
  - no erasure hook yet.
- Mounts the owner router at `/clients` and the public router.
- A host data migration loads its purposes (`service` → `UTILITY`, `updates` → `MARKETING`, labels in both
  languages) and its notice v1 (coach wording, English and Hindi).
- `make migrate` runs `whatsapp-consent migrate`, then its own migrations. The Task 1 branch
  (`feat/clients-consent`) is discarded; its code moves into the kit.
- The mobile Clients and Practice-link screens are built against this API (the practice link is at
  `/clients/practice-link`).

## 15. Open items

1. Confirm Meta's marketing opt-out error code(s) and the click-to-chat inbound format with Meta's docs and a test number.
2. Privacy lawyer review (see §2).
3. v0.2: WhatsApp Flows consent; Meta template helper for authentication codes (with the platform number, sub-project 2).
4. A native async API, if an async host needs it.
