from datetime import UTC

import pytest
from httpx import AsyncClient

from app.schemas.events import EventParticipant, EventStatus, RawEvent
from app.services.event_pipeline import process_events
from app.sources.base import WatchedArtistRef


@pytest.mark.asyncio
async def test_notifications_api(client: AsyncClient, session_factory, api_headers):
    from datetime import date, datetime, timedelta

    from app.matching.normalization import strict_normalize
    from app.models import ArtistCountry, WatchedArtist

    async with session_factory() as session:
        artist = WatchedArtist(
            canonical_name="Chelsea Wolfe",
            normalized_name=strict_normalize("Chelsea Wolfe"),
            match_mode="normal",
            enabled=True,
        )
        session.add(artist)
        await session.flush()
        session.add(ArtistCountry(artist_id=artist.id, country_code="PL"))
        await session.commit()

        ref = WatchedArtistRef(
            id=artist.id,
            canonical_name="Chelsea Wolfe",
            normalized_name="chelsea wolfe",
            aliases=[],
            normalized_aliases=[],
            countries=["PL"],
            match_mode="normal",
        )
        event = RawEvent(
            source="ticketmaster",
            source_event_id="api-test-1",
            source_url="https://example.com",
            event_name="Chelsea Wolfe",
            participants=[EventParticipant(name="Chelsea Wolfe", role="attraction")],
            local_date=date.today() + timedelta(days=30),
            city="Warszawa",
            venue_name="Progresja",
            country_code="PL",
            status=EventStatus.onsale,
            discovered_at=datetime.now(UTC),
        )
        await process_events(session, [event], [ref])

    pending = await client.get("/api/v1/notifications/pending", headers=api_headers)
    assert pending.status_code == 200
    items = pending.json()
    assert len(items) == 1
    assert items[0]["type"] == "NEW_EVENT"

    ack = await client.post(f"/api/v1/notifications/{items[0]['id']}/ack", headers=api_headers)
    assert ack.status_code == 200

    late_fail = await client.post(
        f"/api/v1/notifications/{items[0]['id']}/fail",
        headers=api_headers,
        json={"error": "late callback"},
    )
    assert late_fail.status_code == 200

    pending2 = await client.get("/api/v1/notifications/pending", headers=api_headers)
    assert pending2.json() == []
