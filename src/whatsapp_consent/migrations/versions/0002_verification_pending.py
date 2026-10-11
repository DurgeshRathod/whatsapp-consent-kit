"""Hold a consent page submission on its verification row until the number is proven.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE whatsapp_consent.verifications ADD COLUMN pending jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE whatsapp_consent.verifications DROP COLUMN pending")
