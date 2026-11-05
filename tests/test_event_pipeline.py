import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.matching.matcher import match_event_to_artists
from app.schemas.events import EventParticipant, EventStatus, RawEvent
from app.services.event_pipeline import process_events
from app.sources.base import WatchedArtistRef
from app.sources.ticketmaster import parse_ticketmaster_feed

FIXTURE = Path(__file__).parent / "fixtures" / "ticketmaster" / "events_pl.json"


def _future_date(days: int = 30) -> date:
    return date.today() + timedelta(days=days)


def _chelsea_artist() -> WatchedArtistRef:
    return WatchedArtistRef(
        id=1,
        canonical_name="Chelsea Wolfe",
        normalized_name="chelsea wolfe",
        aliases=["chelsea wolfe"],
        normalized_aliases=["chelsea wolfe"],
        countries=["PL"],
        match_mode="normal",
    )


def _health_artist() -> WatchedArtistRef:
    return WatchedArtistRef(
        id=2,
        canonical_name="HEALTH",
        normalized_name="health",
        aliases=["health"],
        normalized_aliases=["health"],
        countries=["PL"],
        match_mode="strict",
    )


@pytest.fixture
def tm_events():
    payload = json.loads(FIXTURE.read_text())
    return parse_ticketmaster_feed(payload, "PL")


def test_ticketmaster_fixture_parses(tm_events):
    assert len(tm_events) == 2
    assert tm_events[0].source_event_id == "tm-chelsea-wolfe-001"
    assert tm_events[0].participants[0].name == "Chelsea Wolfe"


def test_ticketmaster_parser_skips_malformed_items():
    events = parse_ticketmaster_feed(
        {
            "events": [
                None,
                {
                    "eventId": "tm-malformed-1",
                    "eventStartLocalDate": "not-a-date",
                    "eventStartLocalTime": 20,
                    "venue": [],
                    "attractions": [None, {"attraction": {"attractionName": "Chelsea Wolfe"}}],
                },
            ]
        },
        "PL",
    )

    assert len(events) == 1
    assert events[0].local_date is None
    assert events[0].local_time is None
    assert events[0].participants[0].name == "Chelsea Wolfe"


def test_raw_event_without_source_id_uses_stable_url_key():
    from app.services.event_pipeline import _source_event_id

    event = RawEvent(
        source="ticketmaster",
        source_url="https://example.com/events/1?tracking=ignored#details",
        event_name="Chelsea Wolfe",
        country_code="PL",
    )
    same_event = event.model_copy(update={"source_url": "https://EXAMPLE.com/events/1/"})

    assert event.source_event_id is None
    assert _source_event_id(event) == _source_event_id(same_event)


def test_raw_event_default_discovery_time_is_utc():
    event = RawEvent(
        source="ticketmaster",
        source_url="https://example.com/events/1",
        event_name="Chelsea Wolfe",
        country_code="PL",
    )

    assert event.discovered_at.tzinfo == UTC


def test_chelsea_wolfe_matches(tm_events):
    matches = match_event_to_artists(tm_events[0], [_chelsea_artist()])
    assert len(matches) == 1
    assert matches[0].match_method == "structured_exact"


def test_health_does_not_match_festival_title(tm_events):
    matches = match_event_to_artists(tm_events[1], [_health_artist()])
    assert matches == []


def test_alias_in_structured_participant_is_structured_exact():
    artist = _chelsea_artist()
    artist.aliases = ["CW"]
    artist.normalized_aliases = ["cw"]
    event = RawEvent(
        source="ticketmaster",
        source_event_id="tm-alias-1",
        source_url="https://example.com/alias",
        event_name="CW",
        participants=[EventParticipant(name="CW", role="attraction")],
        country_code="PL",
    )

    matches = match_event_to_artists(event, [artist])

    assert len(matches) == 1
    assert matches[0].confidence == 1.0
    assert matches[0].match_method == "structured_exact"


def test_strict_mode_disables_fuzzy_matching():
    event = RawEvent(
        source="ticketmaster",
        source_event_id="tm-fuzzy-1",
        source_url="https://example.com/fuzzy",
        event_name="Chelsea Wolf",
        participants=[EventParticipant(name="Chelsea Wolf", role="attraction")],
        country_code="PL",
    )
    normal_artist = _chelsea_artist()
    strict_artist = _chelsea_artist()
    strict_artist.match_mode = "strict"

    normal_matches = match_event_to_artists(event, [normal_artist])
    strict_matches = match_event_to_artists(event, [strict_artist])

    assert normal_matches[0].match_method == "fuzzy"
    assert strict_matches == []


@pytest.mark.asyncio
async def test_e2e_pipeline_creates_notification(session_factory, tm_events):
    async with session_factory() as session:
        from app.matching.normalization import strict_normalize
        from app.models import ArtistAlias, ArtistCountry, WatchedArtist

        artist = WatchedArtist(
            canonical_name="Chelsea Wolfe",
            normalized_name=strict_normalize("Chelsea Wolfe"),
            match_mode="normal",
            enabled=True,
        )
        session.add(artist)
        await session.flush()
        session.add(ArtistAlias(artist_id=artist.id, alias="CHELSEA WOLFE", normalized_alias="chelsea wolfe"))
        session.add(ArtistCountry(artist_id=artist.id, country_code="PL"))
        await session.commit()

        refs = [
            WatchedArtistRef(
                id=artist.id,
                canonical_name=artist.canonical_name,
                normalized_name=artist.normalized_name,
                aliases=["CHELSEA WOLFE"],
                normalized_aliases=["chelsea wolfe"],
                countries=["PL"],
                match_mode="normal",
            )
        ]

        future_event = tm_events[0].model_copy(update={"local_date": _future_date()})

        result = await process_events(session, [future_event], refs)
        assert result.matched_events == 1
        assert result.new_events == 1
        assert result.notifications_created == 1

        result2 = await process_events(session, [future_event], refs)
        assert result2.notifications_created == 0


@pytest.mark.asyncio
async def test_deduplication_merges_sources(session_factory):
    from app.matching.normalization import strict_normalize
    from app.models import ArtistCountry, WatchedArtist

    future = _future_date()
    artist_ref = WatchedArtistRef(
        id=1,
        canonical_name="Chelsea Wolfe",
        normalized_name="chelsea wolfe",
        aliases=[],
        normalized_aliases=[],
        countries=["PL"],
        match_mode="normal",
    )

    tm_event = RawEvent(
        source="ticketmaster",
        source_event_id="tm-1",
        source_url="https://tm.example/1",
        event_name="Chelsea Wolfe",
        participants=[EventParticipant(name="Chelsea Wolfe", role="attraction")],
        local_date=future,
        local_time=time(20, 0),
        venue_name="Progresja",
        city="Warszawa",
        country_code="PL",
        timezone="Europe/Warsaw",
        status=EventStatus.onsale,
        discovered_at=datetime.now(UTC),
    )
    ko_event = RawEvent(
        source="knockout",
        source_event_id="ko-1",
        source_url="https://knockout.example/1",
        event_name="CHELSEA WOLFE",
        participants=[EventParticipant(name="CHELSEA WOLFE", role="headliner")],
        local_date=future,
        local_time=time(20, 0),
        venue_name="Klub Progresja",
        city="Warszawa",
        country_code="PL",
        timezone="Europe/Warsaw",
        status=EventStatus.onsale,
        discovered_at=datetime.now(UTC),
    )

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
        artist_ref.id = artist.id

        r1 = await process_events(session, [tm_event], [artist_ref])
        assert r1.notifications_created == 1

        r2 = await process_events(session, [ko_event], [artist_ref])
        assert r2.notifications_created == 0

        from sqlalchemy import select

        from app.models import CanonicalEvent, NotificationOutbox

        canonical_events = (await session.execute(select(CanonicalEvent))).scalars().all()
        notification = (await session.execute(select(NotificationOutbox))).scalar_one()
        notification_sources = json.loads(notification.payload_json)["sources"]

        assert len(canonical_events) == 1
        assert {source["name"] for source in notification_sources} == {
            "ticketmaster",
            "knockout",
        }


def test_deduplication_keeps_distinct_venues_separate():
    from app.deduplication.dedupe import venues_compatible
    from app.matching.normalization import normalize_venue

    assert not venues_compatible(
        "Progresja",
        normalize_venue("Progresja"),
        "Hydrozagadka",
        normalize_venue("Hydrozagadka"),
    )


@pytest.mark.asyncio
async def test_new_source_cancellation_creates_notification(session_factory):
    from sqlalchemy import select

    from app.matching.normalization import strict_normalize
    from app.models import ArtistCountry, NotificationOutbox, WatchedArtist

    artist_ref = WatchedArtistRef(
        id=1,
        canonical_name="Chelsea Wolfe",
        normalized_name="chelsea wolfe",
        aliases=[],
        normalized_aliases=[],
        countries=["PL"],
        match_mode="normal",
    )
    common = {
        "event_name": "Chelsea Wolfe",
        "participants": [EventParticipant(name="Chelsea Wolfe", role="attraction")],
        "local_date": _future_date(),
        "venue_name": "Progresja",
        "city": "Warszawa",
        "country_code": "PL",
    }
    onsale = RawEvent(
        source="ticketmaster",
        source_event_id="tm-cross-source-cancel",
        source_url="https://tm.example/cross-source-cancel",
        status=EventStatus.onsale,
        **common,
    )
    cancelled = RawEvent(
        source="knockout",
        source_event_id="ko-cross-source-cancel",
        source_url="https://knockout.example/cross-source-cancel",
        status=EventStatus.cancelled,
        **common,
    )

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
        artist_ref.id = artist.id

        await process_events(session, [onsale], [artist_ref])
        result = await process_events(session, [cancelled], [artist_ref])
        notification_types = (await session.execute(select(NotificationOutbox.notification_type))).scalars().all()

        assert result.notifications_created == 1
        assert notification_types == ["NEW_EVENT", "CANCELLED"]


@pytest.mark.asyncio
async def test_cancellation_notification(session_factory):
    from app.matching.normalization import strict_normalize
    from app.models import ArtistCountry, WatchedArtist

    future = _future_date()
    artist_ref = WatchedArtistRef(
        id=1,
        canonical_name="Chelsea Wolfe",
        normalized_name="chelsea wolfe",
        aliases=[],
        normalized_aliases=[],
        countries=["PL"],
        match_mode="normal",
    )

    onsale = RawEvent(
        source="ticketmaster",
        source_event_id="tm-cancel-1",
        source_url="https://tm.example/cancel",
        event_name="Chelsea Wolfe",
        participants=[EventParticipant(name="Chelsea Wolfe", role="attraction")],
        local_date=future,
        venue_name="Progresja",
        city="Warszawa",
        country_code="PL",
        status=EventStatus.onsale,
        discovered_at=datetime.now(UTC),
    )
    cancelled = onsale.model_copy(update={"status": EventStatus.cancelled})

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
        artist_ref.id = artist.id

        await process_events(session, [onsale], [artist_ref])
        result = await process_events(session, [cancelled], [artist_ref])
        assert result.notifications_created == 1


@pytest.mark.asyncio
async def test_date_and_venue_change_updates_existing_canonical(session_factory):
    from app.matching.normalization import strict_normalize
    from app.models import ArtistCountry, WatchedArtist

    artist_ref = WatchedArtistRef(
        id=1,
        canonical_name="Chelsea Wolfe",
        normalized_name="chelsea wolfe",
        aliases=[],
        normalized_aliases=[],
        countries=["PL"],
        match_mode="normal",
    )
    initial_date = _future_date()
    changed_date = initial_date + timedelta(days=1)
    initial = RawEvent(
        source="ticketmaster",
        source_event_id="tm-change-1",
        source_url="https://example.com/change",
        event_name="Chelsea Wolfe",
        participants=[EventParticipant(name="Chelsea Wolfe", role="attraction")],
        local_date=initial_date,
        venue_name="Progresja",
        city="Warszawa",
        country_code="PL",
        status=EventStatus.onsale,
    )
    changed = initial.model_copy(
        update={
            "local_date": changed_date,
            "venue_name": "Hydrozagadka",
            "source_url": "https://example.com/change-updated",
            "timezone": "Europe/Warsaw",
        }
    )

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
        artist_ref.id = artist.id

        first = await process_events(session, [initial], [artist_ref])
        second = await process_events(session, [changed], [artist_ref])

        assert first.new_events == 1
        assert second.new_events == 0
        assert second.notifications_created == 2

        from sqlalchemy import select

        from app.models import CanonicalEvent, NotificationOutbox, SourceEvent

        canonical_events = (await session.execute(select(CanonicalEvent))).scalars().all()
        notifications = (await session.execute(select(NotificationOutbox))).scalars().all()

        assert len(canonical_events) == 1
        assert canonical_events[0].local_date == changed_date
        assert canonical_events[0].venue_name == "Hydrozagadka"
        source_event = (await session.execute(select(SourceEvent))).scalar_one()
        assert source_event.source_url == "https://example.com/change-updated"
        assert source_event.timezone == "Europe/Warsaw"
        assert {notification.notification_type for notification in notifications} == {
            "NEW_EVENT",
            "DATE_CHANGED",
            "VENUE_CHANGED",
        }
