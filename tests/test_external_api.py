"""External API scenarios for the GitHub Issues client: success, HTTP errors,
timeouts and network failures, and replies missing required fields.
requests.post is replaced by a fake in every test, so nothing goes over the
network."""

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
    """Replaces requests.post; set .reply to a FakeResponse or an exception."""
    fake = SimpleNamespace(reply=None, calls=[])

    def post(url, **kwargs):
        fake.calls.append(SimpleNamespace(url=url, **kwargs))
        if isinstance(fake.reply, Exception):
            raise fake.reply
        return fake.reply

    monkeypatch.setattr(github_client.requests, "post", post)
    return fake


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
    assert not result.ok and result.error == "GitHub did not answer within 5 s."


def test_custom_timeout_is_passed_on_and_reported(github):
    github.reply = requests.exceptions.ReadTimeout()
    result = send(timeout=0.5)

    assert github.calls[0].timeout == 0.5
    assert "within 0.5 s" in result.error


@pytest.mark.parametrize("network_error", [
    requests.exceptions.ConnectionError("Name or service not known"),
    requests.exceptions.SSLError("certificate verify failed"),
    requests.exceptions.TooManyRedirects("Exceeded 30 redirects"),
])
def test_network_failures_are_reported_not_raised(github, network_error):
    github.reply = network_error
    result = send()
    assert not result.ok and result.error.startswith("Network error:") and str(network_error) in result.error


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


def test_dashboard_without_token_does_not_call_github(app, github, capsys):
    app.github_token = None
    app.send_github_alert("camera", "Camera Disconnected", "no frame")

    assert github.calls == []
    assert "token missing" in capsys.readouterr().out
