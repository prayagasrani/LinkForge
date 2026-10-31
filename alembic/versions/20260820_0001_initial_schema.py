"""Create users, short URLs, and click events.

Revision ID: 20260820_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "short_urls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("short_code", sa.String(length=32), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("click_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_short_urls_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_short_urls")),
    )
    op.create_index("ix_short_urls_owner_created_at", "short_urls", ["owner_id", "created_at"])
    op.create_index(op.f("ix_short_urls_owner_id"), "short_urls", ["owner_id"])
    op.create_index(op.f("ix_short_urls_short_code"), "short_urls", ["short_code"], unique=True)

    op.create_table(
        "click_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("short_url_id", sa.Uuid(), nullable=False),
        sa.Column(
            "clicked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("referrer", sa.String(length=2048), nullable=True),
        sa.ForeignKeyConstraint(
            ["short_url_id"],
            ["short_urls.id"],
            name=op.f("fk_click_events_short_url_id_short_urls"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_click_events")),
    )
    op.create_index(op.f("ix_click_events_short_url_id"), "click_events", ["short_url_id"])
    op.create_index(
        "ix_click_events_url_clicked_at", "click_events", ["short_url_id", "clicked_at"]
    )


def downgrade() -> None:
    op.drop_table("click_events")
    op.drop_table("short_urls")
    op.drop_table("users")
