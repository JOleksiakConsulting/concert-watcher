"""Milestone 2 migration: events, matching, canonical, outbox."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_events"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("event_name", sa.String(length=512), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=True),
        sa.Column("local_time", sa.Time(), nullable=True),
        sa.Column("venue_name", sa.String(length=512), nullable=True),
        sa.Column("normalized_venue", sa.String(length=512), nullable=True),
        sa.Column("city", sa.String(length=256), nullable=True),
        sa.Column("normalized_city", sa.String(length=256), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_event_id", name="uq_source_event"),
    )

    op.create_table(
        "event_artist_matches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_event_id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("match_method", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artist_id"], ["watched_artists.id"]),
        sa.ForeignKeyConstraint(["source_event_id"], ["source_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id", "artist_id", name="uq_event_artist_match"),
    )

    op.create_table(
        "canonical_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=True),
        sa.Column("local_time", sa.Time(), nullable=True),
        sa.Column("city", sa.String(length=256), nullable=True),
        sa.Column("normalized_city", sa.String(length=256), nullable=True),
        sa.Column("venue_name", sa.String(length=512), nullable=True),
        sa.Column("normalized_venue", sa.String(length=512), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artist_id"], ["watched_artists.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "canonical_event_sources",
        sa.Column("canonical_event_id", sa.Integer(), nullable=False),
        sa.Column("source_event_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["canonical_event_id"], ["canonical_events.id"]),
        sa.ForeignKeyConstraint(["source_event_id"], ["source_events.id"]),
        sa.PrimaryKeyConstraint("canonical_event_id", "source_event_id"),
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_event_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("notification_key", sa.String(length=256), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["canonical_event_id"], ["canonical_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_key"),
    )


def downgrade() -> None:
    op.drop_table("notification_outbox")
    op.drop_table("canonical_event_sources")
    op.drop_table("canonical_events")
    op.drop_table("event_artist_matches")
    op.drop_table("source_events")
