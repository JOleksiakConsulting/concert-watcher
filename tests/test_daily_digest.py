import logging

import httpx
import pytest
import respx

from app.cli import daily_digest
from app.cli.daily_digest import DailyDigestResult, run_daily_digest
from app.core.config import settings
from app.services.digest import Digest, build_digest, send_email


@respx.mock
@pytest.mark.parametrize("status", ["degraded", "error", "skipped"])
async def test_source_status_is_reported_without_notifications(caplog, status):
    respx.post("http://test/api/v1/scan").respond(
        json={
            "sources": {
                "fource": {"status": status, "error": "unsupported_without_browser"},
            }
        }
    )
    respx.get("http://test/api/v1/notifications/pending?limit=200").respond(json=[])
    result = await run_daily_digest(
        api_base_url="http://test",
        api_key="test-key",
        country_codes=["PL"],
        sources=["fource"],
        dry_run=True,
    )
    if status == "skipped":
        assert result.status == "nothing_to_send"
        assert "fource" not in caplog.text
    else:
        assert result.status == "scan_degraded"
        assert "fource" in caplog.text
        assert "unsupported_without_browser" in caplog.text


def _notification(
    notification_id: int,
    notification_type: str = "NEW_EVENT",
) -> dict:
    return {
        "id": notification_id,
        "type": notification_type,
        "artist": "Chelsea Wolfe",
        "date": "2026-12-03",
        "time": "20:00:00",
        "city": "Warszawa",
        "venue": "Progresja",
        "country_code": "PL",
        "sources": [{"name": "Ticketmaster", "url": "https://example.com/event"}],
    }


def test_build_digest_contains_all_notifications_and_source_diagnostics():
    digest = build_digest(
        [
            _notification(1),
            _notification(2, "CANCELLED"),
        ],
        source_problems=[{"source": "eventim", "status": "degraded", "error": "parser unavailable"}],
    )

    assert digest.notification_ids == [1, 2]
    assert "NOWY KONCERT" in digest.text
    assert "ODWOŁANY KONCERT" in digest.text
    assert "Ticketmaster: https://example.com/event" in digest.text
    assert "eventim: degraded" in digest.text
    assert "Progresja" in digest.html


@pytest.mark.asyncio
@respx.mock
async def test_run_daily_digest_sends_before_acknowledging_notifications():
    respx.post("http://test/api/v1/scan").mock(
        return_value=httpx.Response(
            200,
            json={"sources": {"ticketmaster": {"status": "ok"}}},
        )
    )
    respx.get("http://test/api/v1/notifications/pending?limit=200").mock(
        return_value=httpx.Response(200, json=[_notification(42)])
    )
    ack_route = respx.post("http://test/api/v1/notifications/42/ack").mock(
        return_value=httpx.Response(200, json={"status": "sent"})
    )
    delivered: list[Digest] = []

    result = await run_daily_digest(
        api_base_url="http://test",
        api_key="test-key",
        country_codes=["PL"],
        sources=["ticketmaster"],
        send_email=lambda digest: delivered.append(digest),
    )

    assert result == DailyDigestResult(status="sent", notification_ids=[42])
    assert [digest.notification_ids for digest in delivered] == [[42]]
    assert ack_route.called


@pytest.mark.asyncio
@respx.mock
async def test_run_daily_digest_marks_notifications_failed_when_email_fails():
    respx.post("http://test/api/v1/scan").mock(return_value=httpx.Response(200, json={"sources": {}}))
    respx.get("http://test/api/v1/notifications/pending?limit=200").mock(
        return_value=httpx.Response(200, json=[_notification(7)])
    )
    fail_route = respx.post("http://test/api/v1/notifications/7/fail").mock(
        return_value=httpx.Response(200, json={"status": "pending"})
    )

    def fail_delivery(_digest: Digest) -> None:
        raise RuntimeError("SMTP password must not be logged")

    result = await run_daily_digest(
        api_base_url="http://test",
        api_key="test-key",
        country_codes=["PL"],
        sources=["ticketmaster"],
        send_email=fail_delivery,
    )

    assert result == DailyDigestResult(status="delivery_failed", notification_ids=[7])
    assert fail_route.called
    assert fail_route.calls[0].request.content == b'{"error":"Email delivery failed (RuntimeError)"}'


@pytest.mark.asyncio
@respx.mock
async def test_run_daily_digest_does_not_send_when_pending_is_empty():
    respx.post("http://test/api/v1/scan").mock(return_value=httpx.Response(200, json={"sources": {}}))
    respx.get("http://test/api/v1/notifications/pending?limit=200").mock(return_value=httpx.Response(200, json=[]))
    send_email_called = False

    def unexpected_delivery(_digest: Digest) -> None:
        nonlocal send_email_called
        send_email_called = True

    result = await run_daily_digest(
        api_base_url="http://test",
        api_key="test-key",
        country_codes=["PL"],
        sources=["ticketmaster"],
        send_email=unexpected_delivery,
    )

    assert result == DailyDigestResult(status="nothing_to_send", notification_ids=[])
    assert not send_email_called


@pytest.mark.asyncio
@respx.mock
async def test_run_daily_digest_dry_run_does_not_send_ack_or_fail():
    scan_route = respx.post("http://test/api/v1/scan").mock(return_value=httpx.Response(200, json={"sources": {}}))
    pending_route = respx.get("http://test/api/v1/notifications/pending?limit=200").mock(
        return_value=httpx.Response(200, json=[_notification(11)])
    )
    ack_route = respx.post("http://test/api/v1/notifications/11/ack").mock(return_value=httpx.Response(200))
    fail_route = respx.post("http://test/api/v1/notifications/11/fail").mock(return_value=httpx.Response(200))
    delivered = False

    def unexpected_delivery(_digest: Digest) -> None:
        nonlocal delivered
        delivered = True

    result = await run_daily_digest(
        api_base_url="http://test",
        api_key="test-key",
        country_codes=["PL"],
        sources=["ticketmaster"],
        dry_run=True,
        send_email=unexpected_delivery,
    )

    assert result == DailyDigestResult(status="dry_run", notification_ids=[11])
    assert scan_route.called
    assert pending_route.called
    assert not delivered
    assert not ack_route.called
    assert not fail_route.called


@pytest.mark.asyncio
@respx.mock
async def test_run_daily_digest_returns_scan_in_progress_on_conflict():
    scan_route = respx.post("http://test/api/v1/scan").mock(
        return_value=httpx.Response(409, json={"detail": "Scan already in progress"})
    )
    pending_route = respx.get("http://test/api/v1/notifications/pending?limit=200").mock(
        return_value=httpx.Response(200, json=[_notification(12)])
    )

    result = await run_daily_digest(
        api_base_url="http://test",
        api_key="test-key",
        country_codes=["PL"],
        sources=["ticketmaster"],
    )

    assert result == DailyDigestResult(status="scan_in_progress", notification_ids=[])
    assert scan_route.call_count == 1
    assert not pending_route.called


@pytest.mark.parametrize("status_code", [429, 500, 503, 599])
@pytest.mark.asyncio
@respx.mock
async def test_run_daily_digest_retries_transient_http_status(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
):
    delays: list[int] = []

    async def fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr(daily_digest.asyncio, "sleep", fake_sleep)
    scan_route = respx.post("http://test/api/v1/scan").mock(
        side_effect=[
            httpx.Response(status_code, json={"detail": "temporary"}),
            httpx.Response(200, json={"sources": {}}),
        ]
    )
    respx.get("http://test/api/v1/notifications/pending?limit=200").mock(return_value=httpx.Response(200, json=[]))

    result = await run_daily_digest(
        api_base_url="http://test",
        api_key="test-key",
        country_codes=["PL"],
        sources=["ticketmaster"],
    )

    assert result == DailyDigestResult(status="nothing_to_send", notification_ids=[])
    assert scan_route.call_count == 2
    assert delays == [1]


@pytest.mark.asyncio
@respx.mock
async def test_run_daily_digest_retries_request_error(
    monkeypatch: pytest.MonkeyPatch,
):
    delays: list[int] = []

    async def fake_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr(daily_digest.asyncio, "sleep", fake_sleep)
    scan_route = respx.post("http://test/api/v1/scan").mock(
        side_effect=[
            httpx.ConnectError("temporary connection failure"),
            httpx.Response(200, json={"sources": {}}),
        ]
    )
    respx.get("http://test/api/v1/notifications/pending?limit=200").mock(return_value=httpx.Response(200, json=[]))

    result = await run_daily_digest(
        api_base_url="http://test",
        api_key="test-key",
        country_codes=["PL"],
        sources=["ticketmaster"],
    )

    assert result == DailyDigestResult(status="nothing_to_send", notification_ids=[])
    assert scan_route.call_count == 2
    assert delays == [1]


def test_main_rejects_conflicting_smtp_flags_before_api_request(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "watcher@example.com")
    monkeypatch.setattr(settings, "smtp_to", "me@example.com")
    monkeypatch.setattr(settings, "smtp_use_tls", True)
    monkeypatch.setattr(settings, "smtp_use_ssl", True)
    called = False

    async def unexpected_run(**_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(daily_digest, "run_daily_digest", unexpected_run)

    result = daily_digest.main([])

    captured = capsys.readouterr()
    assert result == 2
    assert not called
    assert "SMTP_USE_TLS and SMTP_USE_SSL cannot both be enabled" in captured.err


@pytest.mark.asyncio
@respx.mock
async def test_run_daily_digest_logs_partial_ack_state(
    caplog: pytest.LogCaptureFixture,
):
    respx.post("http://test/api/v1/scan").mock(return_value=httpx.Response(200, json={"sources": {}}))
    respx.get("http://test/api/v1/notifications/pending?limit=200").mock(
        return_value=httpx.Response(200, json=[_notification(21), _notification(22)])
    )
    respx.post("http://test/api/v1/notifications/21/ack").mock(
        return_value=httpx.Response(200, json={"status": "sent"})
    )
    respx.post("http://test/api/v1/notifications/22/ack").mock(
        return_value=httpx.Response(400, json={"detail": "cannot acknowledge"})
    )
    caplog.set_level(logging.INFO, logger="app.cli.daily_digest")

    with pytest.raises(httpx.HTTPStatusError):
        await run_daily_digest(
            api_base_url="http://test",
            api_key="test-key",
            country_codes=["PL"],
            sources=["ticketmaster"],
            send_email=lambda _digest: None,
        )

    acknowledged = [record for record in caplog.records if getattr(record, "notification_id", None) == 21]
    failures = [record for record in caplog.records if hasattr(record, "acknowledged_notification_ids")]
    assert acknowledged
    assert failures
    assert failures[-1].acknowledged_notification_ids == [21]
    assert failures[-1].pending_notification_ids == [22]


def test_send_email_uses_text_and_html_parts(monkeypatch):
    sent_messages = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int):
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self):
            return None

        def login(self, username: str, password: str):
            assert username == "user"
            assert password == "password"

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr("app.services.digest.smtplib.SMTP", FakeSMTP)

    send_email(
        build_digest([_notification(1)]),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="password",
        smtp_from="watcher@example.com",
        smtp_to=["me@example.com"],
        smtp_use_tls=True,
        smtp_use_ssl=False,
    )

    assert len(sent_messages) == 1
    assert sent_messages[0]["Subject"].startswith("[Concert Watcher]")
    assert sent_messages[0].get_body(preferencelist=("plain",)).get_content()
    assert sent_messages[0].get_body(preferencelist=("html",)).get_content()
