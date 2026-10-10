"""Initial schema: subjects, invites, purposes, notices, consent history, verification, preferences, erasure.

CHECK constraints list each enum's values literally. Adding an enum value needs a new migration that widens the
constraint (tests/test_zz_schema.py::test_check_constraints_match_the_enums enforces it).

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES_IN_DROP_ORDER = (
    "rate_limit_events",
    "data_requests",
    "preference_links",
    "verifications",
    "consent_state",
    "consent_evidence",
    "consent_events",
    "notices",
    "purposes",
    "invites",
    "subjects",
)
UPDATED_AT_TABLES = ("subjects", "invites", "purposes")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION whatsapp_consent.set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        r"""
        CREATE TABLE whatsapp_consent.subjects (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          owner_id uuid NOT NULL,
          display_name text NOT NULL,
          phone_e164 text NOT NULL,
          phone_fingerprint text NOT NULL,
          birthday_day smallint,
          birthday_month smallint,
          is_minor boolean NOT NULL DEFAULT false,
          guardian_name text,
          guardian_relation text,
          source varchar(32) NOT NULL,
          language varchar(32) NOT NULL DEFAULT 'en',
          phone_verified_at timestamptz,
          needs_review boolean NOT NULL DEFAULT false,
          minor_suspended boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_subjects_phone_e164 CHECK (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
          CONSTRAINT ck_subjects_source CHECK (source IN ('personal_link', 'practice_link', 'whatsapp')),
          CONSTRAINT ck_subjects_language CHECK (language IN ('en', 'hi')),
          CONSTRAINT ck_subjects_birthday CHECK (
            (birthday_day IS NULL) = (birthday_month IS NULL)
            AND (birthday_day IS NULL OR (birthday_day BETWEEN 1 AND 31 AND birthday_month BETWEEN 1 AND 12))
          ),
          CONSTRAINT ck_subjects_guardian CHECK (
            (guardian_name IS NULL) = (guardian_relation IS NULL) AND (guardian_name IS NULL OR is_minor)
          ),
          CONSTRAINT ck_subjects_minor_suspended CHECK (NOT minor_suspended OR is_minor)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_subjects_owner_phone_name "
        "ON whatsapp_consent.subjects (owner_id, phone_e164, lower(display_name))"
    )
    op.execute("CREATE INDEX ix_subjects_owner_id ON whatsapp_consent.subjects (owner_id)")
    op.execute("CREATE INDEX ix_subjects_phone_fingerprint ON whatsapp_consent.subjects (phone_fingerprint)")

    op.execute(
        """
        CREATE TABLE whatsapp_consent.invites (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          owner_id uuid NOT NULL,
          kind varchar(32) NOT NULL,
          subject_id uuid REFERENCES whatsapp_consent.subjects (id) ON DELETE CASCADE,
          token_hash text NOT NULL,
          minor_only boolean NOT NULL DEFAULT false,
          expires_at timestamptz,
          used_at timestamptz,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_invites_token_hash UNIQUE (token_hash),
          CONSTRAINT ck_invites_kind CHECK (kind IN ('personal', 'practice')),
          CONSTRAINT ck_invites_personal_has_subject CHECK ((kind = 'personal') = (subject_id IS NOT NULL)),
          CONSTRAINT ck_invites_personal_expires CHECK (kind = 'practice' OR expires_at IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX ix_invites_owner_id ON whatsapp_consent.invites (owner_id)")
    op.execute("CREATE INDEX ix_invites_subject_id ON whatsapp_consent.invites (subject_id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_invites_one_active_practice ON whatsapp_consent.invites (owner_id) "
        "WHERE kind = 'practice' AND revoked_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE whatsapp_consent.purposes (
          code text PRIMARY KEY,
          category varchar(32) NOT NULL,
          labels jsonb NOT NULL,
          position integer NOT NULL,
          is_active boolean NOT NULL DEFAULT true,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_purposes_category CHECK (category IN ('MARKETING', 'UTILITY')),
          CONSTRAINT ck_purposes_code CHECK (code ~ '^[a-z][a-z0-9_]{1,31}$')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE whatsapp_consent.notices (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          version integer NOT NULL,
          language varchar(32) NOT NULL,
          title text NOT NULL,
          summary text NOT NULL,
          body text NOT NULL,
          effective_from date NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_notices_version_language UNIQUE (version, language),
          CONSTRAINT ck_notices_language CHECK (language IN ('en', 'hi'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE whatsapp_consent.consent_events (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          owner_id uuid NOT NULL,
          subject_id uuid REFERENCES whatsapp_consent.subjects (id) ON DELETE SET NULL,
          phone_fingerprint text NOT NULL,
          purpose_code text NOT NULL REFERENCES whatsapp_consent.purposes (code),
          action varchar(32) NOT NULL,
          source varchar(32) NOT NULL,
          given_by varchar(32) NOT NULL,
          notice_version integer,
          notice_language varchar(32),
          verified_at_time boolean NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_consent_events_action CHECK (action IN ('granted', 'withdrawn')),
          CONSTRAINT ck_consent_events_source CHECK (
            source IN ('consent_page', 'preferences_page', 'owner_recorded', 'whatsapp_keyword', 'whatsapp_button',
                       'meta_opt_out', 'system')
          ),
          CONSTRAINT ck_consent_events_given_by CHECK (given_by IN ('self', 'guardian')),
          CONSTRAINT ck_consent_events_notice_language CHECK (notice_language IN ('en', 'hi')),
          CONSTRAINT ck_consent_events_notice CHECK ((notice_version IS NULL) = (notice_language IS NULL))
        )
        """
    )
    for column in ("owner_id", "subject_id", "phone_fingerprint", "occurred_at"):
        op.execute(f"CREATE INDEX ix_consent_events_{column} ON whatsapp_consent.consent_events ({column})")

    op.execute(
        """
        CREATE TABLE whatsapp_consent.consent_evidence (
          event_id uuid PRIMARY KEY REFERENCES whatsapp_consent.consent_events (id) ON DELETE CASCADE,
          ip text,
          user_agent text,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE whatsapp_consent.consent_state (
          subject_id uuid NOT NULL REFERENCES whatsapp_consent.subjects (id) ON DELETE CASCADE,
          purpose_code text NOT NULL REFERENCES whatsapp_consent.purposes (code),
          granted boolean NOT NULL,
          last_event_id uuid NOT NULL REFERENCES whatsapp_consent.consent_events (id),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT pk_consent_state PRIMARY KEY (subject_id, purpose_code)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE whatsapp_consent.verifications (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          subject_id uuid REFERENCES whatsapp_consent.subjects (id) ON DELETE CASCADE,
          phone_e164 text NOT NULL,
          purpose varchar(32) NOT NULL,
          method varchar(32) NOT NULL,
          code_hash text NOT NULL,
          attempts smallint NOT NULL DEFAULT 0,
          expires_at timestamptz NOT NULL,
          verified_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_verifications_purpose CHECK (purpose IN ('consent', 'preferences', 'deletion')),
          CONSTRAINT ck_verifications_method CHECK (method IN ('authentication_template', 'click_to_chat', 'fake'))
        )
        """
    )
    op.execute("CREATE INDEX ix_verifications_subject_id ON whatsapp_consent.verifications (subject_id)")
    op.execute("CREATE INDEX ix_verifications_phone_created ON whatsapp_consent.verifications (phone_e164, created_at)")

    op.execute(
        """
        CREATE TABLE whatsapp_consent.preference_links (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          phone_fingerprint text NOT NULL,
          token_hash text NOT NULL,
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_preference_links_token_hash UNIQUE (token_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_preference_links_phone_fingerprint ON whatsapp_consent.preference_links (phone_fingerprint)"
    )

    op.execute(
        """
        CREATE TABLE whatsapp_consent.data_requests (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          owner_id uuid NOT NULL,
          subject_id uuid REFERENCES whatsapp_consent.subjects (id) ON DELETE SET NULL,
          phone_fingerprint text NOT NULL,
          kind text NOT NULL DEFAULT 'erasure',
          source varchar(32) NOT NULL,
          requested_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          CONSTRAINT ck_data_requests_kind CHECK (kind IN ('erasure')),
          CONSTRAINT ck_data_requests_source CHECK (source IN ('subject', 'owner', 'system'))
        )
        """
    )
    op.execute("CREATE INDEX ix_data_requests_owner_id ON whatsapp_consent.data_requests (owner_id)")

    op.execute(
        """
        CREATE TABLE whatsapp_consent.rate_limit_events (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          bucket text NOT NULL,
          key_hash text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_rate_limit_events_lookup ON whatsapp_consent.rate_limit_events (bucket, key_hash, created_at)"
    )

    # Append-only history: the only changes allowed are clearing subject_id (erasure, via ON DELETE SET NULL) and
    # deletion by the retention purge, which sets whatsapp_consent.allow_purge for its transaction.
    op.execute(
        """
        CREATE FUNCTION whatsapp_consent.consent_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF current_setting('whatsapp_consent.allow_purge', true) = 'on' THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION 'consent_events is append-only';
          END IF;
          IF (NEW.subject_id IS NULL OR NEW.subject_id = OLD.subject_id)
             AND (to_jsonb(NEW) - 'subject_id') = (to_jsonb(OLD) - 'subject_id') THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'consent_events is append-only';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER consent_events_append_only BEFORE UPDATE OR DELETE ON whatsapp_consent.consent_events "
        "FOR EACH ROW EXECUTE FUNCTION whatsapp_consent.consent_events_guard()"
    )
    op.execute(
        """
        CREATE FUNCTION whatsapp_consent.consent_state_apply() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.subject_id IS NOT NULL THEN
            INSERT INTO whatsapp_consent.consent_state (subject_id, purpose_code, granted, last_event_id, updated_at)
            VALUES (NEW.subject_id, NEW.purpose_code, NEW.action = 'granted', NEW.id, NEW.occurred_at)
            ON CONFLICT (subject_id, purpose_code) DO UPDATE SET
              granted = EXCLUDED.granted, last_event_id = EXCLUDED.last_event_id, updated_at = EXCLUDED.updated_at;
          END IF;
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER consent_events_apply_state AFTER INSERT ON whatsapp_consent.consent_events "
        "FOR EACH ROW EXECUTE FUNCTION whatsapp_consent.consent_state_apply()"
    )
    for table in UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON whatsapp_consent.{table} "
            "FOR EACH ROW EXECUTE FUNCTION whatsapp_consent.set_updated_at()"
        )


def downgrade() -> None:
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE whatsapp_consent.{table}")
    for function in ("consent_state_apply", "consent_events_guard", "set_updated_at"):
        op.execute(f"DROP FUNCTION whatsapp_consent.{function}()")
