from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.matching.normalization import strict_normalize
from app.models import ArtistAlias, ArtistCountry
from app.schemas.events import EventParticipant, EventStatus, RawEvent
from app.sources.stub import StubSourceAdapter

ARTISTS_URL = "/api/v1/artists"


async def _create_artist(client, api_headers, **overrides) -> dict:
    body = {
        "canonical_name": "Chelsea Wolfe",
        "aliases": [],
        "countries": ["PL"],
        "match_mode": "normal",
    } | overrides
    response = await client.post(ARTISTS_URL, headers=api_headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _patch_artist(client, api_headers, artist_id: int, body: dict):
    return await client.patch(f"{ARTISTS_URL}/{artist_id}", headers=api_headers, json=body)


async def _get_artist(client, api_headers, artist_id: int) -> dict:
    response = await client.get(f"{ARTISTS_URL}/{artist_id}", headers=api_headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def source_events(monkeypatch) -> list[RawEvent]:
    events: list[RawEvent] = []

    class FixedAdapter(StubSourceAdapter):
        async def fetch_events(self, country_code, watched_artists):
            return [event for event in events if event.country_code == country_code]

    monkeypatch.setattr(
        "app.services.scan_service.build_adapters",
        lambda: {"ticketmaster": FixedAdapter("ticketmaster")},
    )
    return events


def _event(participant: str, country_code: str = "PL") -> RawEvent:
    return RawEvent(
        source="ticketmaster",
        source_event_id=f"tm-{country_code}-{participant}",
        source_url=f"https://example.test/{country_code}/{participant}",
        event_name=participant,
        participants=[EventParticipant(name=participant, role="attraction")],
        local_date=date.today() + timedelta(days=30),
        venue_name="Progresja",
        city="Warszawa",
        country_code=country_code,
        status=EventStatus.onsale,
    )


async def _scan(client, api_headers) -> dict:
    response = await client.post(
        "/api/v1/scan",
        headers=api_headers,
        json={"country_codes": ["PL", "CZ"], "sources": ["ticketmaster"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sources"]["ticketmaster"]["status"] == "ok"
    return response.json()


async def test_patch_replaces_aliases_and_countries_without_orphan_rows(client, api_headers, session_factory):
    artist = await _create_artist(client, api_headers, aliases=["CW", "Wolfe"], countries=["PL"])

    response = await _patch_artist(
        client,
        api_headers,
        artist["id"],
        {"aliases": ["Chelsea W.", "chelsea w."], "countries": ["pl", "CZ"]},
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["aliases"] == ["Chelsea W."]
    assert sorted(updated["countries"]) == ["CZ", "PL"]

    persisted = await _get_artist(client, api_headers, artist["id"])
    assert persisted["aliases"] == updated["aliases"]
    assert sorted(persisted["countries"]) == ["CZ", "PL"]

    async with session_factory() as session:
        alias_rows = (await session.execute(select(ArtistAlias.alias, ArtistAlias.normalized_alias))).all()
        country_rows = (await session.execute(select(ArtistCountry.country_code))).scalars().all()
    assert alias_rows == [("Chelsea W.", strict_normalize("Chelsea W."))]
    assert sorted(country_rows) == ["CZ", "PL"]


async def test_patch_leaves_omitted_fields_untouched(client, api_headers):
    artist = await _create_artist(client, api_headers, aliases=["CW"], countries=["PL", "CZ"])

    response = await _patch_artist(client, api_headers, artist["id"], {"match_mode": "strict"})

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["match_mode"] == "strict"
    assert updated["canonical_name"] == artist["canonical_name"]
    assert updated["aliases"] == artist["aliases"]
    assert sorted(updated["countries"]) == sorted(artist["countries"])
    assert updated["enabled"] is True


async def test_patch_with_empty_alias_list_removes_all_aliases(client, api_headers, session_factory):
    artist = await _create_artist(client, api_headers, aliases=["CW", "Wolfe"])

    response = await _patch_artist(client, api_headers, artist["id"], {"aliases": []})

    assert response.status_code == 200, response.text
    assert response.json()["aliases"] == []
    async with session_factory() as session:
        assert (await session.execute(select(ArtistAlias))).scalars().all() == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"countries": []}, id="empty-countries"),
        pytest.param({"countries": ["PL", "POL"]}, id="invalid-country"),
        pytest.param({"canonical_name": "   "}, id="blank-name"),
        pytest.param({"aliases": ["Wolfe", " "]}, id="blank-alias"),
        pytest.param({"match_mode": "loose"}, id="unknown-match-mode"),
        pytest.param({"enabled": "maybe"}, id="non-boolean-enabled"),
    ],
)
async def test_invalid_patch_is_rejected_without_partial_changes(client, api_headers, body):
    artist = await _create_artist(client, api_headers, aliases=["CW"], countries=["PL"])

    response = await _patch_artist(client, api_headers, artist["id"], {"match_mode": "strict", **body})

    assert response.status_code == 422
    assert await _get_artist(client, api_headers, artist["id"]) == artist


async def test_patch_unknown_artist_returns_404(client, api_headers):
    response = await _patch_artist(client, api_headers, 999_999, {"match_mode": "strict"})

    assert response.status_code == 404


async def test_patch_requires_api_key(client, api_headers):
    artist = await _create_artist(client, api_headers)

    response = await client.patch(f"{ARTISTS_URL}/{artist['id']}", json={"enabled": False})

    assert response.status_code == 401
    assert (await _get_artist(client, api_headers, artist["id"]))["enabled"] is True


@pytest.mark.parametrize(
    ("initial", "patch", "event"),
    [
        pytest.param({}, {"aliases": ["CW"]}, _event("CW"), id="added-alias"),
        pytest.param(
            {"canonical_name": "Lingua Ignota", "match_mode": "strict"},
            {"canonical_name": "Reverend Kristin Michael Hayter"},
            _event("Reverend Kristin Michael Hayter"),
            id="renamed-artist",
        ),
        pytest.param({}, {"countries": ["PL", "CZ"]}, _event("Chelsea Wolfe", "CZ"), id="added-country"),
    ],
)
async def test_patch_applies_to_event_already_seen_by_previous_scan(
    client, api_headers, source_events, initial, patch, event
):
    artist = await _create_artist(client, api_headers, **initial)
    source_events.append(event)

    before = await _scan(client, api_headers)
    assert before["matched_events"] == 0
    assert before["notifications_created"] == 0

    response = await _patch_artist(client, api_headers, artist["id"], patch)
    assert response.status_code == 200, response.text

    after = await _scan(client, api_headers)
    pending = await client.get("/api/v1/notifications/pending", headers=api_headers)

    assert after["matched_events"] == 1
    assert after["notifications_created"] == 1
    assert [(item["type"], item["artist"]) for item in pending.json()] == [
        ("NEW_EVENT", response.json()["canonical_name"]),
    ]


async def test_reenabling_deleted_artist_resumes_matching(client, api_headers, source_events):
    artist = await _create_artist(client, api_headers)
    source_events.append(_event("Chelsea Wolfe"))
    deleted = await client.delete(f"{ARTISTS_URL}/{artist['id']}", headers=api_headers)
    assert deleted.status_code == 200, deleted.text

    while_disabled = await _scan(client, api_headers)
    assert while_disabled["matched_events"] == 0

    response = await _patch_artist(client, api_headers, artist["id"], {"enabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True

    after = await _scan(client, api_headers)
    assert after["matched_events"] == 1
    assert after["notifications_created"] == 1
