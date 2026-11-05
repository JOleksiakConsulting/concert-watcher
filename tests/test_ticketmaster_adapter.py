import gzip
import json

import httpx
import pytest

from app.core.config import settings
from app.sources.ticketmaster import TM_FEED_URL, TicketmasterAdapter

FEED = {
    "events": [
        {
            "eventId": "tm-puscifer-001",
            "eventName": "Puscifer",
            "eventStatus": "onsale",
            "eventStartLocalDate": "2026-11-20",
            "eventStartLocalTime": "20:00:00",
            "primaryEventUrl": "https://www.ticketmaster.pl/event/puscifer",
            "venue": {
                "venueName": "Atlas Arena",
                "venueCity": "Łódź",
                "venueCountryCode": "PL",
                "venueTimezone": "Europe/Warsaw",
            },
            "attractions": [{"attraction": {"attractionName": "Puscifer"}}],
        }
    ]
}


def _gzip_feed() -> bytes:
    return gzip.compress(json.dumps(FEED).encode("utf-8"))


@pytest.mark.asyncio
async def test_fetch_events_follows_redirect_to_gzip_feed(monkeypatch):
    monkeypatch.setattr(settings, "ticketmaster_api_key", "test-key")
    gzip_url = "https://s3.amazonaws.com/feeds/EVENTS_RAW-PL.json.gz"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(TM_FEED_URL):
            return httpx.Response(303, headers={"location": gzip_url})
        if str(request.url) == gzip_url:
            return httpx.Response(
                200,
                content=_gzip_feed(),
                headers={"content-type": "application/gzip"},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TicketmasterAdapter(client=client)
        events = await adapter.fetch_events("PL", [])

    assert len(events) == 1
    assert events[0].source_event_id == "tm-puscifer-001"
    assert events[0].event_name == "Puscifer"
    assert adapter.last_http_requests == 1


@pytest.mark.asyncio
async def test_fetch_events_parses_uncompressed_json(monkeypatch):
    monkeypatch.setattr(settings, "ticketmaster_api_key", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FEED)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TicketmasterAdapter(client=client)
        events = await adapter.fetch_events("PL", [])

    assert [event.source_event_id for event in events] == ["tm-puscifer-001"]


@pytest.mark.parametrize("status_code", [429, 500])
@pytest.mark.asyncio
async def test_fetch_events_raises_for_http_error(monkeypatch, status_code):
    monkeypatch.setattr(settings, "ticketmaster_api_key", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TicketmasterAdapter(client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.fetch_events("PL", [])


@pytest.mark.asyncio
async def test_fetch_events_raises_for_malformed_json(monkeypatch):
    monkeypatch.setattr(settings, "ticketmaster_api_key", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not-json")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = TicketmasterAdapter(client=client)
        with pytest.raises(json.JSONDecodeError):
            await adapter.fetch_events("PL", [])


@pytest.mark.asyncio
async def test_fetch_events_uses_dedicated_timeout(monkeypatch):
    monkeypatch.setattr(settings, "ticketmaster_api_key", "test-key")
    monkeypatch.setattr(settings, "ticketmaster_http_timeout_seconds", 120)
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *, timeout: object):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url: str, **_kwargs):
            return httpx.Response(
                200,
                json=FEED,
                request=httpx.Request("GET", TM_FEED_URL),
            )

    monkeypatch.setattr("app.sources.ticketmaster.httpx.AsyncClient", FakeClient)

    adapter = TicketmasterAdapter()
    events = await adapter.fetch_events("PL", [])

    assert len(events) == 1
    assert captured["timeout"] == 120
