from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WatchedArtist(Base):
    __tablename__ = "watched_artists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    match_mode: Mapped[str] = mapped_column(String(32), default="normal", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    aliases: Mapped[list["ArtistAlias"]] = relationship(back_populates="artist", cascade="all, delete-orphan")
    countries: Mapped[list["ArtistCountry"]] = relationship(back_populates="artist", cascade="all, delete-orphan")


class ArtistAlias(Base):
    __tablename__ = "artist_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("watched_artists.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(512), nullable=False, index=True)

    artist: Mapped[WatchedArtist] = relationship(back_populates="aliases")


class ArtistCountry(Base):
    __tablename__ = "artist_countries"

    artist_id: Mapped[int] = mapped_column(ForeignKey("watched_artists.id"), primary_key=True, nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), primary_key=True, nullable=False)

    artist: Mapped[WatchedArtist] = relationship(back_populates="countries")


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_countries: Mapped[str] = mapped_column(Text, nullable=False)


class ScanSourceRun(Base):
    __tablename__ = "scan_source_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    events_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    http_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceEvent(Base):
    __tablename__ = "source_events"
    __table_args__ = (UniqueConstraint("source", "source_event_id", name="uq_source_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    event_name: Mapped[str] = mapped_column(String(512), nullable=False)
    local_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    local_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    venue_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    normalized_venue: Mapped[str | None] = mapped_column(String(512), nullable=True)
    city: Mapped[str | None] = mapped_column(String(256), nullable=True)
    normalized_city: Mapped[str | None] = mapped_column(String(256), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class EventArtistMatch(Base):
    __tablename__ = "event_artist_matches"
    __table_args__ = (UniqueConstraint("source_event_id", "artist_id", name="uq_event_artist_match"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("source_events.id"), nullable=False)
    artist_id: Mapped[int] = mapped_column(ForeignKey("watched_artists.id"), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    match_method: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CanonicalEvent(Base):
    __tablename__ = "canonical_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("watched_artists.id"), nullable=False)
    local_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    local_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    city: Mapped[str | None] = mapped_column(String(256), nullable=True)
    normalized_city: Mapped[str | None] = mapped_column(String(256), nullable=True)
    venue_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    normalized_venue: Mapped[str | None] = mapped_column(String(512), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CanonicalEventSource(Base):
    __tablename__ = "canonical_event_sources"

    canonical_event_id: Mapped[int] = mapped_column(ForeignKey("canonical_events.id"), primary_key=True, nullable=False)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("source_events.id"), primary_key=True, nullable=False)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_event_id: Mapped[int] = mapped_column(ForeignKey("canonical_events.id"), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    notification_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
