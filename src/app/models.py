"""SQLAlchemy ORM models. See docs/DECISIONS.md for the tsvector/search rationale."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Message(Base):
    """One turn of the conversation: a user message, an assistant reply, or a tool_result."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role"),
        Index("ix_messages_chat_id_id", "chat_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    text_preview: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status in ('open', 'done', 'cancelled')", name="ck_tasks_status"),
        Index("ix_tasks_tags", "tags", postgresql_using="gin"),
        Index("ix_tasks_search", "search", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    search: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('spanish', immutable_unaccent("
            "coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || "
            "coalesce(immutable_array_to_string(tags, ' '), '')))",
            persisted=True,
        ),
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # No ORM-level `onupdate`: that expires the attribute after every UPDATE and the
    # next read triggers a sync lazy-load, which raises MissingGreenlet under the
    # async engine. repositories.tasks.update_task() sets this explicitly instead.
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("chat_id", "name", name="uq_artifacts_chat_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    versions: Mapped[list[ArtifactVersion]] = relationship(
        back_populates="artifact", order_by="ArtifactVersion.version"
    )


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version", name="uq_artifact_versions_artifact_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    artifact: Mapped[Artifact] = relationship(back_populates="versions")
