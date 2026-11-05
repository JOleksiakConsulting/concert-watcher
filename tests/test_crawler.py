from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.core.config import settings
from app.sources.base import SourceDegradedError
from app.sources.crawler import PublicCrawler

ORIGIN = "https://promoter.test"


@pytest.fixture
def no_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("app.sources.crawler.asyncio.sleep", sleep)
    monkeypatch.setattr(settings, "crawler_min_delay_seconds", 0)
    return sleep


@respx.mock
async def test_robots_denial_prevents_event_request(no_sleep):
    respx.get(ORIGIN + "/robots.txt").respond(text="User-agent: *\nDisallow: /events")
    page = respx.get(ORIGIN + "/events").respond(text="should not be read")
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceDegradedError, match="robots_disallowed"):
            await PublicCrawler(ORIGIN, client).get_html(ORIGIN + "/events")
    assert not page.called


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_access_denied_is_not_retried(status, no_sleep):
    respx.get(ORIGIN + "/robots.txt").respond(status_code=404)
    page = respx.get(ORIGIN + "/events").respond(status_code=status)
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceDegradedError, match="unsupported_without_browser"):
            await PublicCrawler(ORIGIN, client).get_html(ORIGIN + "/events")
    assert page.call_count == 1


@respx.mock
@pytest.mark.parametrize("target", ["http://127.0.0.1/secret", "https://other.test/events", "/private"])
async def test_redirect_target_is_checked_before_fetch(target, no_sleep):
    respx.get(ORIGIN + "/robots.txt").respond(text="User-agent: *\nDisallow: /private")
    respx.get(ORIGIN + "/events").respond(status_code=302, headers={"Location": target})
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceDegradedError):
            await PublicCrawler(ORIGIN, client).get_html(ORIGIN + "/events")
    assert len(respx.calls) == 2


@respx.mock
async def test_retry_after_and_crawl_delay_are_respected(no_sleep):
    respx.get(ORIGIN + "/robots.txt").respond(text="User-agent: *\nCrawl-delay: 3\nAllow: /")
    page = respx.get(ORIGIN + "/events").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, text="events"),
        ]
    )
    async with httpx.AsyncClient() as client:
        crawler = PublicCrawler(ORIGIN, client)
        assert await crawler.get_html(ORIGIN + "/events") == "events"
    assert page.call_count == 2
    assert any(call.args[0] == 7 for call in no_sleep.call_args_list)
    assert any(2.5 < call.args[0] <= 3 for call in no_sleep.call_args_list)
    assert page.calls[0].request.headers["User-Agent"] == settings.crawler_user_agent


@respx.mock
async def test_unavailable_robots_prevents_crawling(no_sleep):
    robots = respx.get(ORIGIN + "/robots.txt").respond(status_code=503)
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await PublicCrawler(ORIGIN, client).get_html(ORIGIN + "/events")
    assert robots.call_count == 3
    assert len(respx.calls) == 3


@respx.mock
async def test_long_retry_after_stops_until_next_scan(no_sleep):
    respx.get(ORIGIN + "/robots.txt").respond(status_code=404)
    page = respx.get(ORIGIN + "/events").respond(status_code=429, headers={"Retry-After": "3600"})
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceDegradedError, match="retry_after_too_long"):
            await PublicCrawler(ORIGIN, client).get_html(ORIGIN + "/events")
    assert page.call_count == 1
