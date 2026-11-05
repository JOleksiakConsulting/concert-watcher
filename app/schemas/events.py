from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class EventStatus(StrEnum):
    scheduled = "scheduled"
    onsale = "onsale"
    soldout = "soldout"
    cancelled = "cancelled"
    postponed = "postponed"
    rescheduled = "rescheduled"
    unknown = "unknown"


class EventParticipant(BaseModel):
    name: str
    role: Literal["headliner", "support", "lineup", "attraction", "unknown"] = "unknown"
    external_id: str | None = None


class RawEvent(BaseModel):
    source: str
    source_event_id: str | None = None
    source_url: str
    event_name: str
    participants: list[EventParticipant] = Field(default_factory=list)
    local_date: date | None = None
    local_time: time | None = None
    venue_name: str | None = None
    city: str | None = None
    country_code: str
    timezone: str | None = None
    status: EventStatus = EventStatus.unknown
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_data: dict = Field(default_factory=dict)


class SourceHealth(BaseModel):
    source: str
    healthy: bool
    message: str | None = None
