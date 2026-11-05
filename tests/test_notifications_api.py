from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.matching.normalization import strict_normalize
from app.models import ArtistCountry, NotificationOutbox, WatchedArtist
from app.schemas.events import EventParticipant, EventStatus, RawEvent
from app.services.event_pipeline import process_events
from app.sources.base import WatchedArtistRef

PENDING_URL = "/api/v1/notifications/pending"


@pytest.fixture
async def notification_id(session_factory) -> int:
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
        )
        result = await process_events(session, [event], [ref])
        assert result.notifications_created == 1

        return (await session.execute(select(NotificationOutbox.id))).scalar_one()


async def _outbox_row(session_factory, notification_id: int) -> NotificationOutbox:
    async with session_factory() as session:
        row = await session.get(NotificationOutbox, notification_id)
        assert row is not None
        return row


async def _pending_ids(client: AsyncClient, api_headers) -> list[int]:
    response = await client.get(PENDING_URL, headers=api_headers)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()]


async def _ack(client: AsyncClient, api_headers, notification_id: int):
    return await client.post(f"/api/v1/notifications/{notification_id}/ack", headers=api_headers)


async def _fail(client: AsyncClient, api_headers, notification_id: int, error: str):
    return await client.post(
        f"/api/v1/notifications/{notification_id}/fail",
        headers=api_headers,
        json={"error": error},
    )


async def test_pending_notification_exposes_event_payload(client, api_headers, notification_id):
    response = await client.get(PENDING_URL, headers=api_headers)

    assert response.status_code == 200, response.text
    [item] = response.json()
    assert item["id"] == notification_id
    assert item["type"] == "NEW_EVENT"
    assert item["artist"] == "Chelsea Wolfe"
    assert item["venue"] == "Progresja"
    assert item["country_code"] == "PL"


async def test_ack_removes_notification_from_pending(client, api_headers, session_factory, notification_id):
    response = await _ack(client, api_headers, notification_id)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "sent"}
    assert await _pending_ids(client, api_headers) == []
    row = await _outbox_row(session_factory, notification_id)
    assert row.status == "sent"
    assert row.sent_at is not None


async def test_repeated_ack_keeps_original_sent_at(client, api_headers, session_factory, notification_id):
    await _ack(client, api_headers, notification_id)
    first_sent_at = (await _outbox_row(session_factory, notification_id)).sent_at

    response = await _ack(client, api_headers, notification_id)

    assert response.status_code == 200, response.text
    assert (await _outbox_row(session_factory, notification_id)).sent_at == first_sent_at


async def test_fail_keeps_notification_pending_and_records_every_attempt(
    client, api_headers, session_factory, notification_id
):
    first = await _fail(client, api_headers, notification_id, "SMTP timeout")
    after_first = await _outbox_row(session_factory, notification_id)
    second = await _fail(client, api_headers, notification_id, "SMTP auth rejected")
    after_second = await _outbox_row(session_factory, notification_id)

    assert first.status_code == second.status_code == 200
    assert second.json() == {"status": "pending"}
    assert (after_first.attempts, after_first.last_error) == (1, "SMTP timeout")
    assert (after_second.attempts, after_second.last_error) == (2, "SMTP auth rejected")
    assert after_first.last_attempt_at is not None
    assert after_second.last_attempt_at >= after_first.last_attempt_at
    assert after_second.status == "pending"
    assert after_second.sent_at is None
    assert await _pending_ids(client, api_headers) == [notification_id]


async def test_late_fail_after_ack_does_not_resurrect_notification(
    client, api_headers, session_factory, notification_id
):
    await _ack(client, api_headers, notification_id)
    acked = await _outbox_row(session_factory, notification_id)

    response = await _fail(client, api_headers, notification_id, "late callback")

    assert response.status_code == 200, response.text
    row = await _outbox_row(session_factory, notification_id)
    assert row.status == "sent"
    assert row.sent_at == acked.sent_at
    assert row.attempts == 0
    assert row.last_error is None
    assert await _pending_ids(client, api_headers) == []


async def test_ack_after_failed_attempt_preserves_failure_history(
    client, api_headers, session_factory, notification_id
):
    await _fail(client, api_headers, notification_id, "SMTP timeout")

    response = await _ack(client, api_headers, notification_id)

    assert response.status_code == 200, response.text
    row = await _outbox_row(session_factory, notification_id)
    assert row.status == "sent"
    assert (row.attempts, row.last_error) == (1, "SMTP timeout")


@pytest.mark.parametrize("body", [{}, {"error": None}, {"message": "SMTP timeout"}])
async def test_fail_without_error_message_is_rejected(client, api_headers, session_factory, notification_id, body):
    response = await client.post(f"/api/v1/notifications/{notification_id}/fail", headers=api_headers, json=body)

    assert response.status_code == 422
    row = await _outbox_row(session_factory, notification_id)
    assert (row.status, row.attempts, row.last_error) == ("pending", 0, None)


@pytest.mark.parametrize("action", ["ack", "fail"])
async def test_unknown_notification_returns_404(client, api_headers, action):
    response = await client.post(
        f"/api/v1/notifications/999999/{action}",
        headers=api_headers,
        json={"error": "SMTP timeout"},
    )

    assert response.status_code == 404


@pytest.mark.parametrize("action", ["ack", "fail"])
async def test_state_changes_require_api_key(client, session_factory, notification_id, action):
    response = await client.post(f"/api/v1/notifications/{notification_id}/{action}", json={"error": "x"})

    assert response.status_code == 401
    row = await _outbox_row(session_factory, notification_id)
    assert (row.status, row.attempts) == ("pending", 0)
