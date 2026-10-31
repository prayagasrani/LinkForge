"""Remove indexes duplicated by left-prefixed composite indexes.

Revision ID: 20260820_0002
Revises: 20260820_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260820_0002"
down_revision: str | None = "20260820_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_click_events_short_url_id", table_name="click_events")
    op.drop_index("ix_short_urls_owner_id", table_name="short_urls")


def downgrade() -> None:
    op.create_index("ix_short_urls_owner_id", "short_urls", ["owner_id"])
    op.create_index("ix_click_events_short_url_id", "click_events", ["short_url_id"])
