import gzip
import json
from datetime import UTC, date, datetime, time
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.events import EventParticipant, EventStatus, RawEvent, SourceHealth
from app.sources.base import SourceAdapter, SourceDegradedError, WatchedArtistRef

TM_FEED_URL = "https://app.ticketmaster.com/discovery-feed/v2/events.json"

_STATUS_MAP = {
    "onsale": EventStatus.onsale,
    "offsale": EventStatus.soldout,
    "cancelled": EventStatus.cancelled,
    "postponed": EventStatus.postponed,
    "rescheduled": EventStatus.rescheduled,
    "scheduled": EventStatus.scheduled,
}


def _parse_time(value: str | None) -> time | None:
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _parse_date(value: str | None) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def parse_ticketmaster_event(item: dict[str, Any], country_code: str) -> RawEvent | None:
    if not isinstance(item, dict):
        return None

    event_id = item.get("eventId")
    if not event_id:
        return None

    venue = item.get("venue")
    if not isinstance(venue, dict):
        venue = {}

    attractions = item.get("attractions")
    if not isinstance(attractions, list):
        attractions = []

    participants: list[EventParticipant] = []

    for entry in attractions:
        if not isinstance(entry, dict):
            continue
        attraction = entry.get("attraction") or entry
        if not isinstance(attraction, dict):
            continue
        name = attraction.get("attractionName") or attraction.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        participants.append(
            EventParticipant(
                name=name,
                role="attraction",
                external_id=attraction.get("attractionId"),
            )
        )

    status_value = item.get("eventStatus")
    status_raw = status_value.strip().lower() if isinstance(status_value, str) else "unknown"
    status = _STATUS_MAP.get(status_raw, EventStatus.unknown)

    source_url = item.get("primaryEventUrl")
    event_name = item.get("eventName")
    if not isinstance(source_url, str):
        source_url = ""
    if not isinstance(event_name, str) or not event_name.strip():
        event_name = "Unknown event"

    venue_name = venue.get("venueName")
    city = venue.get("venueCity")
    venue_country = venue.get("venueCountryCode")
    timezone = venue.get("venueTimezone")
    if not isinstance(venue_name, str):
        venue_name = None
    if not isinstance(city, str):
        city = None
    if not isinstance(venue_country, str) or not venue_country.strip():
        venue_country = country_code
    if not isinstance(timezone, str):
        timezone = None

    return RawEvent(
        source="ticketmaster",
        source_event_id=str(event_id),
        source_url=source_url,
        event_name=event_name,
        participants=participants,
        local_date=_parse_date(item.get("eventStartLocalDate")),
        local_time=_parse_time(item.get("eventStartLocalTime")),
        venue_name=venue_name,
        city=city,
        country_code=venue_country.upper(),
        timezone=timezone,
        status=status,
        discovered_at=datetime.now(UTC),
        raw_data=item,
    )


def load_feed_payload(response: httpx.Response) -> dict[str, Any]:
    content = response.content
    if content.startswith(b"\x1f\x8b"):
        content = gzip.decompress(content)
    payload = json.loads(content)
    return payload if isinstance(payload, dict) else {}


def parse_ticketmaster_feed(payload: dict[str, Any], country_code: str) -> list[RawEvent]:
    if not isinstance(payload, dict):
        return []

    items = payload.get("events")
    if not isinstance(items, list):
        return []

    events: list[RawEvent] = []
    for item in items:
        parsed = parse_ticketmaster_event(item, country_code)
        if parsed is not None:
            events.append(parsed)
    return events


class TicketmasterAdapter(SourceAdapter):
    name = "ticketmaster"

    def __init__(self, *, enabled: bool = True, client: httpx.AsyncClient | None = None) -> None:
        self._enabled = enabled
        self._client = client
        self.last_http_requests = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def fetch_events(
        self,
        country_code: str,
        watched_artists: list[WatchedArtistRef],
    ) -> list[RawEvent]:
        del watched_artists
        if not settings.ticketmaster_api_key:
            raise SourceDegradedError("TICKETMASTER_API_KEY not configured")

        params = {
            "apikey": settings.ticketmaster_api_key,
            "countryCode": country_code.upper(),
        }
        self.last_http_requests += 1

        request_kwargs = {"params": params, "follow_redirects": True}
        if self._client is not None:
            response = await self._client.get(TM_FEED_URL, **request_kwargs)
        else:
            async with httpx.AsyncClient(timeout=settings.ticketmaster_http_timeout_seconds) as client:
                response = await client.get(TM_FEED_URL, **request_kwargs)

        response.raise_for_status()
        return parse_ticketmaster_feed(load_feed_payload(response), country_code)

    async def healthcheck(self) -> SourceHealth:
        if not self._enabled:
            return SourceHealth(source=self.name, healthy=False, message="disabled")
        if not settings.ticketmaster_api_key:
            return SourceHealth(source=self.name, healthy=False, message="TICKETMASTER_API_KEY not configured")
        return SourceHealth(source=self.name, healthy=True)
