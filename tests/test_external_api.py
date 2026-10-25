"""External API scenarios for the GitHub Issues client: success, HTTP errors,
timeouts and network failures, replies missing required fields, and the
retry mechanism.

Only the external dependencies are mocked: requests.post (with autospec, so
a call the real function would reject fails the test) and the sleep between
retries (see conftest.py and fake_github.py). create_issue itself always runs for real."""

import pytest
import requests

from fake_github import CREATED, NOT_JSON, api_response, created
from hardware_monitor import github_client
from hardware_monitor.github_client import (
    MAX_ATTEMPTS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    IssueResult,
    create_issue,
)

def send(token="secret-token", **kwargs):
    return create_issue(token, "owner/repo", "Camera down", "details", labels=["hardware-alert", "camera"], **kwargs)


# --- success ---

def test_success_returns_the_new_issue(github):
    github.reply = api_response(201, CREATED)

    assert send() == IssueResult(True, issue_number=42, url="https://github.com/owner/repo/issues/42")


def test_success_sends_the_expected_request(github):
    github.reply = api_response(201, CREATED)
    send()

    github.post.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/issues",
        json={"title": "Camera down", "body": "details", "labels": ["hardware-alert", "camera"]},
        headers={"Authorization": "Bearer secret-token", "Accept": "application/vnd.github.v3+json"},
        timeout=github_client.REQUEST_TIMEOUT_SECONDS,
    )


def test_extra_fields_in_the_reply_are_ignored(github):
    github.reply = api_response(201, {**CREATED, "state": "open", "labels": [], "user": {"login": "bot"}})
    assert send().ok


@pytest.mark.parametrize("token", [None, ""])
def test_missing_token_sends_nothing(github, token):
    result = send(token=token)

    assert not result.ok and "token missing" in result.error
    assert github.calls == []


# --- HTTP errors ---

@pytest.mark.parametrize("status, payload, expected", [
    (401, {"message": "Bad credentials"}, "HTTP 401: Bad credentials"),
    (403, {"message": "API rate limit exceeded"}, "HTTP 403: API rate limit exceeded"),
    (404, {"message": "Not Found"}, "HTTP 404: Not Found"),
    (410, {"message": "Issues are disabled for this repo"}, "HTTP 410: Issues are disabled"),
    (422, {"message": "Validation Failed", "errors": [{"field": "title"}]}, "HTTP 422: Validation Failed"),
    (500, NOT_JSON, "HTTP 500."),                 # HTML error page, no JSON message
    (502, {"error": "bad gateway"}, "HTTP 502."),  # JSON without a "message"
    (503, ["unexpected", "list"], "HTTP 503."),    # JSON that is not an object
], ids=["401-bad-credentials", "403-rate-limited", "404-not-found", "410-issues-disabled", "422-invalid",
        "500-html-page", "502-json-without-message", "503-json-list"])
def test_http_errors_are_reported_with_the_status(github, status, payload, expected):
    github.reply = api_response(status, payload, text="<html>error</html>")
    result = send()

    assert not result.ok
    assert expected in result.error
    assert result.issue_number is None and result.url is None


def test_a_success_code_other_than_201_is_not_trusted(github):
    github.reply = api_response(200, CREATED)  # GitHub answers 201 Created for a new issue
    result = send()
    assert not result.ok and "HTTP 200" in result.error


# --- timeouts and network failures ---

@pytest.mark.parametrize("timeout_error", [
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectTimeout("connect timed out"),
], ids=["read-timeout", "connect-timeout"])
def test_timeouts_are_reported(github, timeout_error):
    github.reply = timeout_error
    result = send()
    assert not result.ok and result.error.endswith(f"Last error: {TIMED_OUT}")


def test_custom_timeout_is_passed_on_and_reported(github):
    github.reply = requests.exceptions.ReadTimeout()
    result = send(timeout=0.5)

    assert all(call.timeout == 0.5 for call in github.calls)
    assert "within 0.5 s" in result.error


@pytest.mark.parametrize("network_error", [
    requests.exceptions.ConnectionError("Name or service not known"),
    requests.exceptions.SSLError("certificate verify failed"),
    requests.exceptions.TooManyRedirects("Exceeded 30 redirects"),
], ids=["connection-error", "ssl-error", "redirect-loop"])
def test_network_failures_are_reported_not_raised(github, network_error):
    github.reply = network_error
    result = send()
    assert not result.ok and f"Last error: Network error: {network_error}" in result.error


# --- replies missing required fields ---

@pytest.mark.parametrize("payload, problem", [
    ({}, "missing 'number', missing 'html_url'"),
    ({"html_url": CREATED["html_url"]}, "missing 'number'"),
    ({"number": 42}, "missing 'html_url'"),
    ({"number": None, "html_url": CREATED["html_url"]}, "missing 'number'"),
    ({"number": "42", "html_url": CREATED["html_url"]}, "'number' should be int, got '42'"),
    ({"number": True, "html_url": CREATED["html_url"]}, "'number' should be int, got True"),
    ({"number": 42, "html_url": 12345}, "'html_url' should be str, got 12345"),
], ids=["no-fields", "missing-number", "missing-url", "null-number", "number-as-text", "number-as-bool",
        "url-as-number"])
def test_replies_missing_required_fields_are_rejected(github, payload, problem):
    github.reply = api_response(201, payload)
    result = send()

    assert not result.ok
    assert result.error == f"GitHub's reply is incomplete: {problem}."


@pytest.mark.parametrize("payload, error", [
    (NOT_JSON, "GitHub's reply was not valid JSON."),
    ([CREATED], "GitHub's reply was not a JSON object."),
    ("created", "GitHub's reply was not a JSON object."),
], ids=["not-json", "json-list", "json-string"])
def test_replies_that_are_not_a_json_object_are_rejected(github, payload, error):
    github.reply = api_response(201, payload, text="OK")
    assert send() == IssueResult(False, error=error)


# --- retry: recovering after a temporary failure ---

def expected_waits(attempts):
    """The retry_waits between `attempts` attempts: the backoff, doubling each time."""
    return [RETRY_BACKOFF_SECONDS * 2 ** n for n in range(attempts - 1)]


TIMED_OUT = f"GitHub did not answer within {REQUEST_TIMEOUT_SECONDS} s."


def test_the_default_retry_policy():
    # the one place the defaults are pinned; every other test reads them from github_client
    assert (MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS, REQUEST_TIMEOUT_SECONDS) == (3, 1, 5)


@pytest.mark.parametrize("temporary_failure", [
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectionError("connection reset"),
    api_response(429, {"message": "API rate limit exceeded"}),
    api_response(500, NOT_JSON),
    api_response(502, NOT_JSON),
    api_response(503, {"message": "Service Unavailable"}),
    api_response(504, NOT_JSON),
], ids=["read-timeout", "connection-reset", "429-rate-limited", "500", "502", "503", "504"])
def test_recovers_after_one_temporary_failure(github, retry_waits, temporary_failure):
    github.reply = [temporary_failure, created()]
    result = send()

    assert result == IssueResult(True, issue_number=42, url=CREATED["html_url"], attempts=2)
    assert len(github.calls) == 2
    assert retry_waits == expected_waits(2)


def test_recovers_on_the_last_allowed_attempt(github, retry_waits):
    failures = [requests.exceptions.ConnectTimeout(), api_response(502, NOT_JSON)] * MAX_ATTEMPTS
    github.reply = failures[:MAX_ATTEMPTS - 1] + [created()]
    result = send()

    assert result.ok and result.attempts == MAX_ATTEMPTS
    assert retry_waits == expected_waits(MAX_ATTEMPTS)  # the wait doubles each time


def test_every_retry_sends_the_same_request(github):
    github.reply = [requests.exceptions.ReadTimeout(), api_response(503, NOT_JSON), created()]
    send()

    first, *retries = github.calls
    for retry in retries:
        assert (retry.url, retry.json, retry.headers, retry.timeout) == \
               (first.url, first.json, first.headers, first.timeout)


def test_each_retry_is_reported_while_it_happens(github, capsys):
    github.reply = [api_response(503, {"message": "Service Unavailable"}), created()]
    send()

    assert (f"GitHub attempt 1/{MAX_ATTEMPTS} failed (GitHub returned HTTP 503: Service Unavailable.) "
            f"Retrying in {RETRY_BACKOFF_SECONDS} s.") in capsys.readouterr().out


# --- retry: stopping after the maximum number of attempts ---

def test_gives_up_after_the_maximum_attempts(github, retry_waits):
    github.reply = requests.exceptions.ReadTimeout()
    result = send()

    assert not result.ok and result.attempts == MAX_ATTEMPTS
    assert len(github.calls) == MAX_ATTEMPTS
    assert retry_waits == expected_waits(MAX_ATTEMPTS)  # no pointless wait after the final attempt
    assert result.error == f"Gave up after {MAX_ATTEMPTS} attempt(s). Last error: {TIMED_OUT}"


def test_never_tries_past_the_limit_even_if_the_next_try_would_work(github):
    github.reply = [api_response(500, NOT_JSON)] * MAX_ATTEMPTS + [created()]
    result = send()

    assert not result.ok
    assert len(github.calls) == MAX_ATTEMPTS and len(github.reply) == 1  # the next reply was never requested


def test_reports_the_last_of_several_different_failures(github):
    earlier = [requests.exceptions.ConnectionError("reset"), api_response(500, NOT_JSON)] * MAX_ATTEMPTS
    github.reply = earlier[:MAX_ATTEMPTS - 1] + [api_response(503, {"message": "Service Unavailable"})]
    result = send()
    assert result.error == (f"Gave up after {MAX_ATTEMPTS} attempt(s). "
                            f"Last error: GitHub returned HTTP 503: Service Unavailable.")


@pytest.mark.parametrize("max_attempts", [1, 2, 5], ids=["1-attempt", "2-attempts", "5-attempts"])
def test_the_attempt_limit_is_configurable(github, retry_waits, max_attempts):
    github.reply = requests.exceptions.ConnectionError("down")
    result = send(max_attempts=max_attempts)

    assert len(github.calls) == result.attempts == max_attempts
    assert retry_waits == expected_waits(max_attempts)
    assert result.error.startswith(f"Gave up after {max_attempts} attempt(s).")


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_an_attempt_limit_below_one_is_rejected(github, max_attempts):
    with pytest.raises(ValueError, match="at least 1"):
        send(max_attempts=max_attempts)
    assert github.calls == []


# --- retry: permanent failures are not retried ---

@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_permanent_http_errors_stop_immediately(github, retry_waits, status):
    github.reply = api_response(status, {"message": "nope"})
    result = send()

    assert not result.ok and result.attempts == 1
    assert len(github.calls) == 1 and retry_waits == []
    assert result.error == f"GitHub returned HTTP {status}: nope."  # no "Gave up": it was not a retry problem


def test_an_incomplete_reply_is_not_retried(github, retry_waits):
    # 201 means the issue exists; posting again would create a duplicate
    github.reply = api_response(201, {"number": 42})
    result = send()
    assert not result.ok and len(github.calls) == 1 and retry_waits == []


def test_a_permanent_error_after_a_temporary_one_stops_the_retries(github):
    github.reply = [api_response(503, NOT_JSON), api_response(401, {"message": "Bad credentials"}), created()]
    result = send()

    assert result.attempts == 2 and len(github.calls) == 2
    assert result.error == "GitHub returned HTTP 401: Bad credentials."


def test_no_token_means_no_attempts(github, retry_waits):
    result = send(token=None)
    assert result.attempts == 0 and github.calls == [] and retry_waits == []


# --- the dashboard's use of the client (the shared `dash` fixture) ---

def test_dashboard_posts_the_call_and_technician(dash, github, capsys, alerts):
    github.reply = api_response(201, CREATED)
    request = dash.app.maintenance_desk.open_request_for_alert(
        alerts.make(component_id="CAM-LOCAL", workstation_id="PC", message="no frame"))
    technician = dash.app.maintenance_desk.technician_for(request)

    dash.app.send_github_alert("camera", "Camera Disconnected", "no frame", request)

    (call,) = github.calls
    assert call.json["title"].endswith("Camera Disconnected")
    assert call.json["labels"] == ["hardware-alert", "camera"]
    assert f"`{request.request_id}`" in call.json["body"] and technician.name in call.json["body"]
    assert "issue #42 (https://github.com/owner/repo/issues/42)" in capsys.readouterr().out


@pytest.mark.parametrize("reply, message", [
    (api_response(401, {"message": "Bad credentials"}), "HTTP 401: Bad credentials"),
    (requests.exceptions.ReadTimeout(), TIMED_OUT),
    (api_response(201, {"number": 42}), "missing 'html_url'"),
], ids=["http-error", "timeout", "incomplete-reply"])
def test_dashboard_reports_failures_without_crashing(dash, github, capsys, reply, message):
    github.reply = reply
    dash.app.send_github_alert("mic_conn", "Microphone Disconnected", "unplugged")

    output = capsys.readouterr().out
    assert "GitHub alert for mic_conn not sent:" in output and message in output


def test_dashboard_reports_a_recovered_alert(dash, github, capsys):
    github.reply = [requests.exceptions.ReadTimeout(), created()]
    dash.app.send_github_alert("camera", "Camera Disconnected", "no frame")
    assert "Alert posted to GitHub for camera after 2 attempts: issue #42" in capsys.readouterr().out


def test_dashboard_reports_when_retries_are_exhausted(dash, github, capsys):
    github.reply = api_response(503, NOT_JSON)
    dash.app.send_github_alert("camera", "Camera Disconnected", "no frame")

    assert len(github.calls) == MAX_ATTEMPTS
    assert f"GitHub alert for camera not sent: Gave up after {MAX_ATTEMPTS} attempt(s)." in capsys.readouterr().out


def test_dashboard_without_token_does_not_call_github(dash, github, capsys):
    dash.app.github_token = None
    dash.app.send_github_alert("camera", "Camera Disconnected", "no frame")

    assert github.calls == []
    assert "token missing" in capsys.readouterr().out
