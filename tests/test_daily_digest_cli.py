import httpx
import pytest

from app.cli import daily_digest
from app.cli.daily_digest import DailyDigestResult
from app.core.config import settings


class FakeRunDailyDigest:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result = DailyDigestResult(status="sent", notification_ids=[1])
        self.error: Exception | None = None

    async def __call__(self, **kwargs) -> DailyDigestResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def run_digest(monkeypatch) -> FakeRunDailyDigest:
    fake = FakeRunDailyDigest()
    monkeypatch.setattr(daily_digest, "run_daily_digest", fake)
    return fake


@pytest.fixture
def smtp_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "watcher@example.com")
    monkeypatch.setattr(settings, "smtp_to", "me@example.com")
    monkeypatch.setattr(settings, "smtp_use_tls", True)
    monkeypatch.setattr(settings, "smtp_use_ssl", False)


@pytest.fixture
def smtp_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_from", "")
    monkeypatch.setattr(settings, "smtp_to", "")


@pytest.mark.usefixtures("smtp_configured")
@pytest.mark.parametrize(
    ("argv", "result", "exit_code", "stream", "message"),
    [
        pytest.param(
            [],
            DailyDigestResult(status="sent", notification_ids=[1, 2]),
            0,
            "out",
            "Digest sent and 2 notification(s) acknowledged.",
            id="sent",
        ),
        pytest.param(
            [],
            DailyDigestResult(status="nothing_to_send", notification_ids=[]),
            0,
            "out",
            "No pending notifications; delivery skipped.",
            id="nothing-to-send",
        ),
        pytest.param(
            ["--dry-run"],
            DailyDigestResult(status="dry_run", notification_ids=[1, 2, 3]),
            0,
            "out",
            "Dry run: 3 notification(s), nothing acknowledged.",
            id="dry-run",
        ),
        pytest.param(
            [],
            DailyDigestResult(status="delivery_failed", notification_ids=[1, 2]),
            1,
            "err",
            "Email delivery failed; 2 notification(s) left pending.",
            id="delivery-failed",
        ),
        pytest.param(
            [],
            DailyDigestResult(status="scan_degraded", notification_ids=[]),
            1,
            "err",
            "Scan incomplete: source errors reported; no pending notifications.",
            id="scan-degraded",
        ),
        pytest.param(
            [],
            DailyDigestResult(status="scan_in_progress", notification_ids=[]),
            2,
            "err",
            "Scan already in progress; no notifications were changed.",
            id="scan-in-progress",
        ),
    ],
)
def test_exit_code_and_output_stream_reflect_digest_status(
    run_digest, capsys, argv, result, exit_code, stream, message
):
    run_digest.result = result

    assert daily_digest.main(argv) == exit_code

    captured = capsys.readouterr()
    expected, other = (captured.out, captured.err) if stream == "out" else (captured.err, captured.out)
    assert expected.strip() == message
    assert other == ""


@pytest.mark.usefixtures("smtp_configured")
@pytest.mark.parametrize(
    ("error", "message"),
    [
        pytest.param(
            httpx.ReadTimeout("timed out"),
            "no API response within DIGEST_HTTP_TIMEOUT_SECONDS; the scan may still be running.",
            id="read-timeout",
        ),
        pytest.param(
            httpx.ConnectError("connection refused"),
            "ConnectError: connection refused",
            id="api-unreachable",
        ),
        pytest.param(
            httpx.HTTPStatusError(
                "Server error '500 Internal Server Error'",
                request=httpx.Request("POST", "http://test/api/v1/scan"),
                response=httpx.Response(500),
            ),
            "HTTPStatusError: Server error '500 Internal Server Error'",
            id="api-http-error",
        ),
        pytest.param(
            ValueError("Pending notifications response must be a JSON list"),
            "ValueError: Pending notifications response must be a JSON list",
            id="malformed-api-response",
        ),
        pytest.param(OSError("Network is unreachable"), "OSError: Network is unreachable", id="os-error"),
    ],
)
def test_expected_failures_exit_1_with_diagnostic_on_stderr(run_digest, capsys, error, message):
    run_digest.error = error

    assert daily_digest.main([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Daily digest failed: ")
    assert message in captured.err
    assert settings.app_api_key not in captured.err


def test_unexpected_exception_is_not_swallowed(run_digest, smtp_configured):
    run_digest.error = RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        daily_digest.main([])


@pytest.mark.usefixtures("smtp_missing")
def test_missing_smtp_configuration_exits_2_before_any_api_call(run_digest, capsys):
    assert daily_digest.main([]) == 2

    captured = capsys.readouterr()
    assert run_digest.calls == []
    assert captured.out == ""
    assert "SMTP_HOST, SMTP_FROM and SMTP_TO" in captured.err


@pytest.mark.usefixtures("smtp_missing")
def test_dry_run_does_not_require_smtp_and_never_sends(run_digest):
    run_digest.result = DailyDigestResult(status="dry_run", notification_ids=[1])

    assert daily_digest.main(["--dry-run"]) == 0

    [call] = run_digest.calls
    assert call["dry_run"] is True
    assert call["send_email"] is None
    assert callable(call["on_digest"])


@pytest.mark.usefixtures("smtp_configured")
def test_cli_arguments_and_settings_are_passed_to_digest_run(run_digest, monkeypatch):
    monkeypatch.setattr(settings, "digest_api_url", "http://watcher.internal:8182")
    monkeypatch.setattr(settings, "digest_limit", 37)

    daily_digest.main(["--countries", " pl, CZ ,", "--sources", "ticketmaster,, fource "])

    [call] = run_digest.calls
    assert call["api_base_url"] == "http://watcher.internal:8182"
    assert call["api_key"] == settings.app_api_key
    assert call["country_codes"] == ["pl", "CZ"]
    assert call["sources"] == ["ticketmaster", "fource"]
    assert call["limit"] == 37
    assert call["dry_run"] is False
    assert call["send_email"] is daily_digest._smtp_delivery
    assert call["on_digest"] is None
