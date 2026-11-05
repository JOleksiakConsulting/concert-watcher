import asyncio
from datetime import date, timedelta

import httpx
import pytest

from app.schemas.events import EventParticipant, EventStatus, RawEvent
from app.schemas.scan import ScanRequest
from app.services.scan_service import ScanInProgressError, _run_adapter, run_scan
from app.sources.stub import StubSourceAdapter


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_artists_crud(client, api_headers):
    create = await client.post(
        "/api/v1/artists",
        headers=api_headers,
        json={
            "canonical_name": "Chelsea Wolfe",
            "aliases": ["CHELSEA WOLFE"],
            "countries": ["PL"],
            "match_mode": "normal",
        },
    )
    assert create.status_code == 201
    artist = create.json()
    assert artist["canonical_name"] == "Chelsea Wolfe"
    assert artist["enabled"] is True

    listing = await client.get("/api/v1/artists", headers=api_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    get_one = await client.get(f"/api/v1/artists/{artist['id']}", headers=api_headers)
    assert get_one.status_code == 200

    deleted = await client.delete(f"/api/v1/artists/{artist['id']}", headers=api_headers)
    assert deleted.status_code == 200
    assert deleted.json()["enabled"] is False


@pytest.mark.asyncio
async def test_api_key_required(client):
    response = await client.get("/api/v1/artists")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_artist_input_is_normalized_and_validated(client, api_headers):
    valid = await client.post(
        "/api/v1/artists",
        headers=api_headers,
        json={
            "canonical_name": "  Chelsea Wolfe  ",
            "aliases": ["CW", "cw"],
            "countries": ["pl", "PL"],
        },
    )

    assert valid.status_code == 201
    assert valid.json()["canonical_name"] == "Chelsea Wolfe"
    assert valid.json()["aliases"] == ["CW"]
    assert valid.json()["countries"] == ["PL"]

    blank_name = await client.post(
        "/api/v1/artists",
        headers=api_headers,
        json={"canonical_name": "   ", "countries": ["PL"]},
    )
    invalid_country = await client.post(
        "/api/v1/artists",
        headers=api_headers,
        json={"canonical_name": "Amenra", "countries": ["POL"]},
    )

    assert blank_name.status_code == 422
    assert invalid_country.status_code == 422


@pytest.mark.asyncio
async def test_scan(client, api_headers, monkeypatch):
    monkeypatch.setattr(
        "app.services.scan_service.build_adapters",
        lambda: {name: StubSourceAdapter(name) for name in ["ticketmaster", "knockout"]},
    )
    response = await client.post(
        "/api/v1/scan",
        headers=api_headers,
        json={"country_codes": ["PL"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "scan_id" in data
    assert "ticketmaster" in data["sources"]
    assert data["sources"]["knockout"]["status"] == "degraded"
    assert data["matched_events"] == 0


@pytest.mark.asyncio
async def test_scan_rejects_unknown_or_empty_sources(client, api_headers):
    unknown = await client.post(
        "/api/v1/scan",
        headers=api_headers,
        json={"sources": ["ticketmaser"]},
    )
    empty = await client.post(
        "/api/v1/scan",
        headers=api_headers,
        json={"sources": []},
    )

    assert unknown.status_code == 422
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_scan_end_to_end(client, api_headers, monkeypatch):
    created_artist = await client.post(
        "/api/v1/artists",
        headers=api_headers,
        json={"canonical_name": "Chelsea Wolfe", "countries": ["PL"]},
    )
    assert created_artist.status_code == 201

    event = RawEvent(
        source="ticketmaster",
        source_event_id="tm-api-e2e",
        source_url="https://example.test/tm-api-e2e",
        event_name="Chelsea Wolfe",
        participants=[EventParticipant(name="Chelsea Wolfe", role="attraction")],
        local_date=date.today() + timedelta(days=30),
        venue_name="Progresja",
        city="Warszawa",
        country_code="PL",
        status=EventStatus.onsale,
    )

    class FixedAdapter(StubSourceAdapter):
        async def fetch_events(self, country_code, watched_artists):
            return [event]

    class FailingAdapter(StubSourceAdapter):
        async def fetch_events(self, country_code, watched_artists):
            raise httpx.ReadTimeout("source timed out")

    monkeypatch.setattr(
        "app.services.scan_service.build_adapters",
        lambda: {
            "ticketmaster": FixedAdapter("ticketmaster"),
            "knockout": FailingAdapter("knockout"),
        },
    )

    first = await client.post(
        "/api/v1/scan",
        headers=api_headers,
        json={
            "country_codes": ["pl", "PL"],
            "sources": ["ticketmaster", "knockout"],
        },
    )
    pending = await client.get("/api/v1/notifications/pending", headers=api_headers)
    second = await client.post(
        "/api/v1/scan",
        headers=api_headers,
        json={"country_codes": ["PL"], "sources": ["ticketmaster", "knockout"]},
    )

    assert first.status_code == 200
    assert first.json()["new_events"] == 1
    assert first.json()["notifications_created"] == 1
    assert first.json()["sources"]["ticketmaster"]["status"] == "ok"
    assert first.json()["sources"]["knockout"]["status"] == "error"
    assert len(pending.json()) == 1
    assert second.json()["new_events"] == 0
    assert second.json()["notifications_created"] == 0


@pytest.mark.asyncio
async def test_scan_lock(session_factory):
    class SlowAdapter(StubSourceAdapter):
        async def fetch_events(self, country_code, watched_artists):
            await asyncio.sleep(0.2)
            return []

    async with session_factory() as session:
        adapters = {"slow": SlowAdapter("slow")}

        task = asyncio.create_task(run_scan(session, ScanRequest(), adapters=adapters))
        await asyncio.sleep(0.05)

        with pytest.raises(ScanInProgressError):
            async with session_factory() as other_session:
                await run_scan(other_session, ScanRequest(), adapters=adapters)

        await task


@pytest.mark.asyncio
async def test_adapter_http_error_does_not_expose_request_credentials():
    secret = "ticketmaster-secret-key"

    class FailingAdapter(StubSourceAdapter):
        async def fetch_events(self, country_code, watched_artists):
            request = httpx.Request(
                "GET",
                "https://example.test/events",
                params={"apikey": secret, "countryCode": country_code},
            )
            response = httpx.Response(401, request=request)
            response.raise_for_status()

    result, _, _, _ = await _run_adapter(FailingAdapter("failing"), ["PL"], [])

    assert result.status == "error"
    assert result.error == "HTTP 401 returned by source"
    assert secret not in result.error
