"""Initial schema for Milestone 1."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watched_artists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_name", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("match_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_watched_artists_normalized_name", "watched_artists", ["normalized_name"])

    op.create_table(
        "artist_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=512), nullable=False),
        sa.Column("normalized_alias", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(["artist_id"], ["watched_artists.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artist_aliases_normalized_alias", "artist_aliases", ["normalized_alias"])

    op.create_table(
        "artist_countries",
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.ForeignKeyConstraint(["artist_id"], ["watched_artists.id"]),
        sa.PrimaryKeyConstraint("artist_id", "country_code"),
    )

    op.create_table(
        "scan_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_countries", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "scan_source_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scan_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("events_found", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("http_requests", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["scan_id"], ["scan_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scan_source_runs_scan_id", "scan_source_runs", ["scan_id"])


def downgrade() -> None:
    op.drop_index("ix_scan_source_runs_scan_id", table_name="scan_source_runs")
    op.drop_table("scan_source_runs")
    op.drop_table("scan_runs")
    op.drop_table("artist_countries")
    op.drop_index("ix_artist_aliases_normalized_alias", table_name="artist_aliases")
    op.drop_table("artist_aliases")
    op.drop_index("ix_watched_artists_normalized_name", table_name="watched_artists")
    op.drop_table("watched_artists")
