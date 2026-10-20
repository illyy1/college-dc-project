"""External API scenarios for the GitHub Issues client: success, HTTP errors,
timeouts and network failures, replies missing required fields, and the
retry mechanism. requests.post is replaced by a fake in every test, so
nothing goes over the network, and retry waits are recorded instead of slept."""

from types import SimpleNamespace

import pytest
import requests

from hardware_monitor import dashboard, github_client
from hardware_monitor.dashboard import HardwareMonitorApp
from hardware_monitor.github_client import IssueResult, create_issue
from hardware_monitor.maintenance import MaintenanceDesk
from hardware_monitor.processing import Alert

NOT_JSON = object()


class FakeResponse:
    def __init__(self, status_code, payload=NOT_JSON, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is NOT_JSON:
            raise requests.exceptions.JSONDecodeError("Expecting value", self.text, 0)
        return self._payload


@pytest.fixture
def github(monkeypatch):
    """Replaces requests.post. Set .reply to a FakeResponse or an exception
    (returned on every attempt), or to a list of them (one per attempt)."""
    fake = SimpleNamespace(reply=None, calls=[])

    def post(url, **kwargs):
        fake.calls.append(SimpleNamespace(url=url, **kwargs))
        reply = fake.reply.pop(0) if isinstance(fake.reply, list) else fake.reply
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(github_client.requests, "post", post)
    return fake


@pytest.fixture(autouse=True)
def waits(monkeypatch):
    """Records the retry waits instead of sleeping through them."""
    recorded = []
    monkeypatch.setattr(github_client, "_sleep", recorded.append)
    return recorded


def send(token="secret-token", **kwargs):
    return create_issue(token, "owner/repo", "Camera down", "details", labels=["hardware-alert", "camera"], **kwargs)


CREATED = {"number": 42, "html_url": "https://github.com/owner/repo/issues/42", "title": "Camera down"}


# --- success ---

def test_success_returns_the_new_issue(github):
    github.reply = FakeResponse(201, CREATED)

    assert send() == IssueResult(True, issue_number=42, url="https://github.com/owner/repo/issues/42")


def test_success_sends_the_expected_request(github):
    github.reply = FakeResponse(201, CREATED)
    send()

    (call,) = github.calls
    assert call.url == "https://api.github.com/repos/owner/repo/issues"
    assert call.json == {"title": "Camera down", "body": "details", "labels": ["hardware-alert", "camera"]}
    assert call.headers["Authorization"] == "Bearer secret-token"
    assert call.timeout == github_client.REQUEST_TIMEOUT_SECONDS


def test_extra_fields_in_the_reply_are_ignored(github):
    github.reply = FakeResponse(201, {**CREATED, "state": "open", "labels": [], "user": {"login": "bot"}})
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
])
def test_http_errors_are_reported_with_the_status(github, status, payload, expected):
    github.reply = FakeResponse(status, payload, text="<html>error</html>")
    result = send()

    assert not result.ok
    assert expected in result.error
    assert result.issue_number is None and result.url is None


def test_a_success_code_other_than_201_is_not_trusted(github):
    github.reply = FakeResponse(200, CREATED)  # GitHub answers 201 Created for a new issue
    result = send()
    assert not result.ok and "HTTP 200" in result.error


# --- timeouts and network failures ---

@pytest.mark.parametrize("timeout_error", [
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectTimeout("connect timed out"),
])
def test_timeouts_are_reported(github, timeout_error):
    github.reply = timeout_error
    result = send()
    assert not result.ok and result.error.endswith("Last error: GitHub did not answer within 5 s.")


def test_custom_timeout_is_passed_on_and_reported(github):
    github.reply = requests.exceptions.ReadTimeout()
    result = send(timeout=0.5)

    assert all(call.timeout == 0.5 for call in github.calls)
    assert "within 0.5 s" in result.error


@pytest.mark.parametrize("network_error", [
    requests.exceptions.ConnectionError("Name or service not known"),
    requests.exceptions.SSLError("certificate verify failed"),
    requests.exceptions.TooManyRedirects("Exceeded 30 redirects"),
])
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
])
def test_replies_missing_required_fields_are_rejected(github, payload, problem):
    github.reply = FakeResponse(201, payload)
    result = send()

    assert not result.ok
    assert result.error == f"GitHub's reply is incomplete: {problem}."


@pytest.mark.parametrize("payload, error", [
    (NOT_JSON, "GitHub's reply was not valid JSON."),
    ([CREATED], "GitHub's reply was not a JSON object."),
    ("created", "GitHub's reply was not a JSON object."),
])
def test_replies_that_are_not_a_json_object_are_rejected(github, payload, error):
    github.reply = FakeResponse(201, payload, text="OK")
    assert send() == IssueResult(False, error=error)


# --- retry: recovering after a temporary failure ---

def created():
    return FakeResponse(201, CREATED)


@pytest.mark.parametrize("temporary_failure", [
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.ConnectionError("connection reset"),
    FakeResponse(429, {"message": "API rate limit exceeded"}),
    FakeResponse(500, NOT_JSON),
    FakeResponse(502, NOT_JSON),
    FakeResponse(503, {"message": "Service Unavailable"}),
    FakeResponse(504, NOT_JSON),
])
def test_recovers_after_one_temporary_failure(github, waits, temporary_failure):
    github.reply = [temporary_failure, created()]
    result = send()

    assert result == IssueResult(True, issue_number=42, url=CREATED["html_url"], attempts=2)
    assert len(github.calls) == 2
    assert waits == [1]


def test_recovers_on_the_last_allowed_attempt(github, waits):
    github.reply = [requests.exceptions.ConnectTimeout(), FakeResponse(502, NOT_JSON), created()]
    result = send()

    assert result.ok and result.attempts == 3
    assert waits == [1, 2]  # the wait doubles each time


def test_every_retry_sends_the_same_request(github):
    github.reply = [requests.exceptions.ReadTimeout(), FakeResponse(503, NOT_JSON), created()]
    send()

    first, *retries = github.calls
    for retry in retries:
        assert (retry.url, retry.json, retry.headers, retry.timeout) == \
               (first.url, first.json, first.headers, first.timeout)


def test_each_retry_is_reported_while_it_happens(github, capsys):
    github.reply = [FakeResponse(503, {"message": "Service Unavailable"}), created()]
    send()

    assert "GitHub attempt 1/3 failed (GitHub returned HTTP 503: Service Unavailable.) Retrying in 1 s." \
        in capsys.readouterr().out


# --- retry: stopping after the maximum number of attempts ---

def test_gives_up_after_the_maximum_attempts(github, waits):
    github.reply = requests.exceptions.ReadTimeout()
    result = send()

    assert not result.ok and result.attempts == 3
    assert len(github.calls) == 3
    assert waits == [1, 2]  # no pointless wait after the final attempt
    assert result.error == "Gave up after 3 attempt(s). Last error: GitHub did not answer within 5 s."


def test_never_tries_past_the_limit_even_if_the_next_try_would_work(github):
    github.reply = [FakeResponse(500, NOT_JSON)] * 3 + [created()]
    result = send()

    assert not result.ok
    assert len(github.calls) == 3 and len(github.reply) == 1  # the 4th reply was never requested


def test_reports_the_last_of_several_different_failures(github):
    github.reply = [requests.exceptions.ConnectionError("reset"), FakeResponse(500, NOT_JSON),
                    FakeResponse(503, {"message": "Service Unavailable"})]
    result = send()
    assert result.error == "Gave up after 3 attempt(s). Last error: GitHub returned HTTP 503: Service Unavailable."


@pytest.mark.parametrize("max_attempts, expected_waits", [
    (1, []),
    (2, [1]),
    (5, [1, 2, 4, 8]),
])
def test_the_attempt_limit_is_configurable(github, waits, max_attempts, expected_waits):
    github.reply = requests.exceptions.ConnectionError("down")
    result = send(max_attempts=max_attempts)

    assert len(github.calls) == result.attempts == max_attempts
    assert waits == expected_waits
    assert result.error.startswith(f"Gave up after {max_attempts} attempt(s).")


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_an_attempt_limit_below_one_is_rejected(github, max_attempts):
    with pytest.raises(ValueError, match="at least 1"):
        send(max_attempts=max_attempts)
    assert github.calls == []


# --- retry: permanent failures are not retried ---

@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_permanent_http_errors_stop_immediately(github, waits, status):
    github.reply = FakeResponse(status, {"message": "nope"})
    result = send()

    assert not result.ok and result.attempts == 1
    assert len(github.calls) == 1 and waits == []
    assert result.error == f"GitHub returned HTTP {status}: nope."  # no "Gave up": it was not a retry problem


def test_an_incomplete_reply_is_not_retried(github, waits):
    # 201 means the issue exists; posting again would create a duplicate
    github.reply = FakeResponse(201, {"number": 42})
    result = send()
    assert not result.ok and len(github.calls) == 1 and waits == []


def test_a_permanent_error_after_a_temporary_one_stops_the_retries(github):
    github.reply = [FakeResponse(503, NOT_JSON), FakeResponse(401, {"message": "Bad credentials"}), created()]
    result = send()

    assert result.attempts == 2 and len(github.calls) == 2
    assert result.error == "GitHub returned HTTP 401: Bad credentials."


def test_no_token_means_no_attempts(github, waits):
    result = send(token=None)
    assert result.attempts == 0 and github.calls == [] and waits == []


# --- the dashboard's use of the client ---

class RunNow:
    """Stands in for threading.Thread so the dashboard's background post runs inline."""

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def app(monkeypatch, technicians, tmp_path):
    """A dashboard without its Tk window: only what send_github_alert uses."""
    monkeypatch.setattr(dashboard, "threading", SimpleNamespace(Thread=RunNow))
    app = HardwareMonitorApp.__new__(HardwareMonitorApp)
    app.github_token = "secret-token"
    app.maintenance_desk = MaintenanceDesk(technicians, log_path=str(tmp_path / "log.jsonl"), report_csv_path=None)
    return app


def test_dashboard_posts_the_call_and_technician(app, github, capsys):
    github.reply = FakeResponse(201, CREATED)
    request = app.maintenance_desk.open_request_for_alert(
        Alert("CAM-LOCAL", "PC", "CAMERA_FAILURE", "no frame", priority=1))
    technician = app.maintenance_desk.technician_for(request)

    app.send_github_alert("camera", "Camera Disconnected", "no frame", request)

    (call,) = github.calls
    assert call.json["title"].endswith("Camera Disconnected")
    assert call.json["labels"] == ["hardware-alert", "camera"]
    assert f"`{request.request_id}`" in call.json["body"] and technician.name in call.json["body"]
    assert "issue #42 (https://github.com/owner/repo/issues/42)" in capsys.readouterr().out


@pytest.mark.parametrize("reply, message", [
    (FakeResponse(401, {"message": "Bad credentials"}), "HTTP 401: Bad credentials"),
    (requests.exceptions.ReadTimeout(), "did not answer within 5 s"),
    (FakeResponse(201, {"number": 42}), "missing 'html_url'"),
])
def test_dashboard_reports_failures_without_crashing(app, github, capsys, reply, message):
    github.reply = reply
    app.send_github_alert("mic_conn", "Microphone Disconnected", "unplugged")

    output = capsys.readouterr().out
    assert "GitHub alert for mic_conn not sent:" in output and message in output


def test_dashboard_reports_a_recovered_alert(app, github, capsys):
    github.reply = [requests.exceptions.ReadTimeout(), created()]
    app.send_github_alert("camera", "Camera Disconnected", "no frame")
    assert "Alert posted to GitHub for camera after 2 attempts: issue #42" in capsys.readouterr().out


def test_dashboard_reports_when_retries_are_exhausted(app, github, capsys):
    github.reply = FakeResponse(503, NOT_JSON)
    app.send_github_alert("camera", "Camera Disconnected", "no frame")

    assert len(github.calls) == 3
    assert "GitHub alert for camera not sent: Gave up after 3 attempt(s)." in capsys.readouterr().out


def test_dashboard_without_token_does_not_call_github(app, github, capsys):
    app.github_token = None
    app.send_github_alert("camera", "Camera Disconnected", "no frame")

    assert github.calls == []
    assert "token missing" in capsys.readouterr().out
