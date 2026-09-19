"""initial schema: messages, tasks, artifacts, artifact_versions

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-19 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `unaccent` is STABLE, not IMMUTABLE, so it can't be used directly inside a
    # GENERATED column. `immutable_unaccent` is the standard wrapper for this
    # (documented on the PostgreSQL wiki) — it lets tasks.search fold accents so
    # "renovacion" matches "renovación".
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(
        "CREATE OR REPLACE FUNCTION immutable_unaccent(text) "
        "RETURNS text AS $$ SELECT unaccent('unaccent', $1) $$ "
        "LANGUAGE sql IMMUTABLE PARALLEL SAFE"
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("text_preview", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role"),
        sa.UniqueConstraint("telegram_update_id", name="uq_messages_telegram_update_id"),
    )
    op.create_index("ix_messages_chat_id", "messages", ["chat_id"])
    op.create_index("ix_messages_chat_id_id", "messages", ["chat_id", "id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "search",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('spanish', immutable_unaccent("
                "coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || "
                "coalesce(array_to_string(tags, ' '), '')))",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('open', 'done', 'cancelled')", name="ck_tasks_status"),
    )
    op.create_index("ix_tasks_chat_id", "tasks", ["chat_id"])
    op.create_index("ix_tasks_tags", "tasks", ["tags"], postgresql_using="gin")
    op.create_index("ix_tasks_search", "tasks", ["search"], postgresql_using="gin")

    op.create_table(
        "artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("chat_id", "name", name="uq_artifacts_chat_name"),
    )
    op.create_index("ix_artifacts_chat_id", "artifacts", ["chat_id"])

    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("artifact_id", "version", name="uq_artifact_versions_artifact_version"),
    )
    op.create_index("ix_artifact_versions_artifact_id", "artifact_versions", ["artifact_id"])


def downgrade() -> None:
    op.drop_table("artifact_versions")
    op.drop_table("artifacts")
    op.drop_index("ix_tasks_search", table_name="tasks")
    op.drop_index("ix_tasks_tags", table_name="tasks")
    op.drop_index("ix_tasks_chat_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_messages_chat_id_id", table_name="messages")
    op.drop_index("ix_messages_chat_id", table_name="messages")
    op.drop_table("messages")

    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
