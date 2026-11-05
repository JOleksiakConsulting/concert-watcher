import os
from datetime import date, time, timedelta
from pathlib import Path

import pytest
import respx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.matching.matcher import match_event_to_artists
from app.schemas.events import EventStatus
from app.services.scan_service import _run_adapter
from app.sources.base import SourceDegradedError, WatchedArtistRef
from app.sources.promoters import (
    FOURCE_URL,
    KNOCKOUT_URL,
    FourceAdapter,
    KnockoutAdapter,
    parse_fource_listing,
    parse_knockout_detail,
    parse_knockout_listing,
)

FIXTURES = Path(__file__).parent / "fixtures" / "promoters"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def artist(name="KORVEN"):
    return WatchedArtistRef(
        id=1,
        canonical_name=name,
        normalized_name=name.casefold(),
        aliases=[],
        normalized_aliases=[],
        countries=["PL"],
        match_mode="strict",
    )


def test_knockout_listing_and_detail_preserve_support_and_start_time():
    events, next_page = parse_knockout_listing(fixture("knockout-listing.html"))
    assert len(events) == 32
    assert next_page == KNOCKOUT_URL + "?page=2"
    event = next(e for e in events if e.source_event_id == "1076")
    detail = parse_knockout_detail(fixture("knockout-detail.html"), event)
    assert [(p.name, p.role) for p in detail.participants] == [
        ("GLASSWIRE", "headliner"),
        ("KORVEN", "support"),
        ("HOLLOW OATH", "support"),
    ]
    assert detail.local_date == date(2026, 9, 23)
    assert detail.local_time == time(19, 0)  # Start, not door opening (18:00).
    assert detail.venue_name == "PreZero Arena Gliwice"
    assert detail.city == "Gliwice"
    assert match_event_to_artists(detail, [artist()])


def test_fource_deduplicates_responsive_cards_and_preserves_names_and_statuses():
    events = parse_fource_listing(fixture("fource-listing.html"))
    assert len(events) == 42
    by_name = {e.event_name: e for e in events}
    assert by_name["GL/SS"].participants[0].name == "GL/SS"
    assert by_name["Distant Satellites"].participants[1].name == "Zuzanna Wiśniał"
    assert len(by_name["HEXXA"].participants) == 1  # Tour title is not a support.
    assert by_name["Marlow Fay"].status == EventStatus.cancelled
    assert by_name["Lil Chirp"].status == EventStatus.soldout
    assert by_name["Bring Back"].venue_name == "Letnia Scena Progresji"
    assert by_name["Bring Back"].local_time is None
    assert len([e for e in events if e.event_name == "Lounge Theory"]) == 3


@pytest.mark.parametrize("parser", [parse_knockout_listing, parse_fource_listing])
def test_changed_dom_is_degraded_not_successfully_empty(parser):
    with pytest.raises(SourceDegradedError, match="structure_changed"):
        parser("<html><body>Please enable JavaScript</body></html>")


def test_fource_does_not_infer_foreign_country_from_polish_domain():
    html = fixture("fource-listing.html").replace("Warszawa", "Berlin")
    events = parse_fource_listing(html)
    assert all(e.country_code == "" for e in events if e.city == "Berlin")


@pytest.mark.parametrize("date_range", ["8–12. 9. 26", "8. 9. 26 – 12. 9. 26"])
def test_aggregated_fource_date_is_rejected(date_range):
    html = fixture("fource-listing.html").replace("8. 9. 26", date_range)
    with pytest.raises(SourceDegradedError, match="ambiguous_date"):
        parse_fource_listing(html)


@respx.mock
async def test_knockout_pagination_and_selective_detail_fetch(monkeypatch):
    monkeypatch.setattr(settings, "crawler_min_delay_seconds", 0)
    respx.get("https://knockoutprod.net/robots.txt").respond(text=fixture("knockout-robots.txt"))
    # A different occurrence on page 2, with no next link.
    soup = BeautifulSoup(fixture("knockout-listing.html"), "lxml")
    card = soup.select_one(".concerts-list__item")
    card["href"] = KNOCKOUT_URL + "/detail/99999"
    respx.get(KNOCKOUT_URL + "?page=2").respond(text=str(card))
    respx.get(KNOCKOUT_URL).respond(text=fixture("knockout-listing.html"))
    detail = respx.get(KNOCKOUT_URL + "/detail/1076").respond(text=fixture("knockout-detail.html"))
    adapter = KnockoutAdapter()
    events = await adapter.fetch_events("PL", [artist()])
    assert len(events) == 33
    assert adapter.last_http_requests == 4  # robots, two listings, matching detail.
    assert detail.call_count == 1
    assert next(e for e in events if e.source_event_id == "1076").venue_name == "PreZero Arena Gliwice"


@respx.mock
async def test_knockout_pagination_loop_is_degraded(monkeypatch):
    monkeypatch.setattr(settings, "crawler_min_delay_seconds", 0)
    respx.get("https://knockoutprod.net/robots.txt").respond(text="User-agent: *\nAllow: /")
    respx.get(KNOCKOUT_URL, params={}).respond(text=fixture("knockout-listing.html"))
    respx.get(KNOCKOUT_URL + "?page=2").respond(text=fixture("knockout-listing.html"))
    with pytest.raises(SourceDegradedError, match="pagination_loop"):
        await KnockoutAdapter().fetch_events("PL", [])


@respx.mock
@pytest.mark.parametrize("adapter_type", [KnockoutAdapter, FourceAdapter])
async def test_disabled_or_unsupported_country_does_not_make_requests(adapter_type):
    assert await adapter_type(enabled=False).fetch_events("PL", []) == []
    assert await adapter_type().fetch_events("DE", []) == []
    assert len(respx.calls) == 0


@respx.mock
async def test_fource_api_scan_to_outbox_is_idempotent(client, api_headers, monkeypatch):
    monkeypatch.setattr(settings, "crawler_min_delay_seconds", 0)
    monkeypatch.setattr("app.services.scan_service.build_adapters", lambda: {"fource": FourceAdapter()})
    soup = BeautifulSoup(fixture("fource-listing.html"), "lxml")
    card = soup.select_one("a.events__item")
    future = date.today() + timedelta(days=30)
    card.find_all("span", recursive=False)[
        -1
    ].string = f"{future.day}. {future.month}. {future:%y} • Hydrozagadka, Warszawa"
    respx.get("https://fource.pl/robots.txt").respond(text=fixture("fource-robots.txt"))
    respx.get(FOURCE_URL).respond(text=str(card))
    created = await client.post(
        "/api/v1/artists",
        headers=api_headers,
        json={
            "canonical_name": "Zuzanna Wiśniał",
            "countries": ["PL"],
        },
    )
    assert created.status_code == 201
    first = await client.post("/api/v1/scan", headers=api_headers, json={"sources": ["fource"]})
    second = await client.post("/api/v1/scan", headers=api_headers, json={"sources": ["fource"]})
    pending = await client.get("/api/v1/notifications/pending", headers=api_headers)
    assert first.json()["notifications_created"] == 1
    assert second.json()["notifications_created"] == 0
    assert pending.json()[0]["artist"] == "Zuzanna Wiśniał"
    assert pending.json()[0]["venue"] == "Hydrozagadka"


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="opt-in public HTTP smoke test")
@pytest.mark.parametrize("adapter_type", [KnockoutAdapter, FourceAdapter])
async def test_promoter_live_smoke(adapter_type):
    result, _, requests, events = await _run_adapter(adapter_type(), ["PL"], [artist()])
    assert requests >= 2
    assert result.status == "ok", result.error
    assert events
    assert all(event.country_code == "PL" for event in events)
