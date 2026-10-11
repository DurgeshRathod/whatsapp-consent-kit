# Changelog

## v0.1.0 (unreleased)

First release.

- Schema `whatsapp_consent` with its own migrations; enums stored as text with CHECK constraints; append-only consent
  events enforced by trigger.
- Host content loaders for purposes (bound to Meta template categories) and versioned notices.
- Consent pages in English and Hindi: personal and practice links, QR codes, guardian flow, under-18 check.
- Submissions are held on their verification row and applied exactly once, only after the number is proven.
- Verification by typed code (authentication template sender) and click-to-chat (`START-XXXXXX`).
- Owner API: subjects, status by phone, sendable audience, practice link, activity, removal.
- Python API: `status`, `status_by_phone`, `can_send`, `can_send_category`, `sendable_subjects`, `audit`.
- WhatsApp: webhook signature checks and parsing; STOP/START (Unicode-aware), buttons, click-to-chat codes, Meta
  marketing opt-out; `InvalidSignature` and `InvalidWebhookPayload`.
- Preferences pages that answer the same for registered and unregistered numbers; delete my data.
- Erasure with a host hook in the same transaction; anonymised history kept for the retention period after erasure;
  `kit.run_maintenance` and `whatsapp-consent maintenance` (purge only unless told otherwise).
