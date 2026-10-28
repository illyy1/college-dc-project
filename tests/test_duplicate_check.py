"""TDD specification for the duplicate check: before posting a fault as a
new GitHub issue, the system GETs the open issues for it, and posts nothing
if this computer's issue for the same fault is still open. The GET has its
own retry: a Timeout or ConnectionError is tried again, up to 3 attempts in
all, waiting 1 s and then 2 s.

What hardware_monitor/api_client.py must provide:
- find_open_issues(token, repo, labels, timeout=GITHUB_TIMEOUT_SECONDS,
                   max_attempts=GITHUB_MAX_ATTEMPTS)
    -> IssueSearchResult(ok, issues=(), error=None, attempts=1, category=None)
    One GET to {GITHUB_API_URL}/repos/{repo}/issues with params state=open,
    labels (comma-separated, GitHub matches issues with all of them) and
    per_page=100, and the same headers as create_issue. issues is a tuple
    of OpenIssue(number, url, title), pull requests left out. The reply must
    be 200 with a JSON list of issue objects, each with a positive number,
    an https html_url linking to that issue, and a title; anything else is a
    failure with its ErrorCategory. Never raises for API problems. Without
    a token nothing is sent (attempts 0, no category).

And services.report_fault():
- looks up the open issues labelled ["hardware-alert", alert_key] first;
- if one has exactly the title it would post (this computer, this fault),
  it posts nothing and asks Gemini nothing: FaultReport.already_open is
  True and .issue is that issue (ok, its number and url);
- if the lookup fails, it posts anyway (a duplicate is better than a lost
  alert) and FaultReport.lookup_error says why.

The dashboard prints which open issue it kept, and a failed lookup.

Only the external dependencies are mocked: requests.get and requests.post
(the `github` fixture: github.search is the GET), Gemini's post, and the
sleep between retries."""

import pytest
import requests

from fake_github import CREATED, created, open_issue, open_issues
from fake_http import NOT_JSON, api_response
from hardware_monitor import api_client, services
from hardware_monitor.api_client import GITHUB_API_URL, GITHUB_TIMEOUT_SECONDS
from hardware_monitor.config import COMPUTER_NAME

from conftest import GEMINI_KEY

LABELS = ["hardware-alert", "camera"]
# the title services.report_fault gives this computer's camera issue
CAMERA_TITLE = f"\U0001f6a8 [{COMPUTER_NAME}] Camera Disconnected"
TIMEOUTS_AND_CONNECTION_ERRORS = [
    requests.exceptions.ReadTimeout(),
    requests.exceptions.ConnectTimeout(),
    requests.exceptions.ConnectionError(),
]
FAILURE_IDS = ["read-timeout", "connect-timeout", "connection-error"]


def find(token="secret-token", **kwargs):
    return api_client.find_open_issues(token, "owner/repo", LABELS, **kwargs)


# --- the GET request ---

def test_the_lookup_is_one_get_for_the_open_issues_with_these_labels(github):
    find()

    (call,) = github.search.calls
    assert call.url == f"{GITHUB_API_URL}/repos/owner/repo/issues"
    assert call.params == {"state": "open", "labels": "hardware-alert,camera", "per_page": 100}
    assert call.headers["Authorization"] == "Bearer secret-token"
    assert call.timeout == GITHUB_TIMEOUT_SECONDS
    assert github.calls == []  # a lookup never posts


def test_the_open_issues_are_returned_in_order(github):
    github.search.reply = open_issues(open_issue(7, "first"), open_issue(9, "second"))
    result = find()

    assert result.ok and result.error is None and result.category is None
    assert result.issues == (api_client.OpenIssue(7, "https://github.com/owner/repo/issues/7", "first"),
                             api_client.OpenIssue(9, "https://github.com/owner/repo/issues/9", "second"))


def test_no_open_issues_is_a_success(github):
    result = find()
    assert result.ok and result.issues == ()


def test_pull_requests_are_left_out(github):
    pull = {**open_issue(8, "a pull request"), "pull_request": {"url": "https://api.github.com/..."}}
    github.search.reply = open_issues(open_issue(7, "an issue"), pull)
    assert [issue.number for issue in find().issues] == [7]


@pytest.mark.parametrize("token", [None, ""], ids=["no-token", "empty-token"])
def test_without_a_token_nothing_is_sent(github, token):
    result = find(token=token)
    assert not result.ok and result.attempts == 0 and result.category is None
    assert github.search.calls == []


# --- the retry: Timeout or ConnectionError, up to 3 attempts, waiting between tries ---

def test_three_attempts_waiting_1_then_2_seconds_by_default():
    assert api_client.GITHUB_MAX_ATTEMPTS == 3 and api_client.GITHUB_RETRY_BACKOFF_SECONDS == 1


@pytest.mark.parametrize("failure", TIMEOUTS_AND_CONNECTION_ERRORS, ids=FAILURE_IDS)
def test_recovers_after_one_failure(github, retry_waits, failure):
    github.search.reply = [failure, open_issues(open_issue(7, "camera"))]
    result = find()

    assert result.ok and result.attempts == 2 and [i.number for i in result.issues] == [7]
    assert retry_waits == [1]


@pytest.mark.parametrize("failure", TIMEOUTS_AND_CONNECTION_ERRORS, ids=FAILURE_IDS)
def test_recovers_on_the_third_and_last_attempt(github, retry_waits, failure):
    github.search.reply = [failure, failure, open_issues()]
    result = find()

    assert result.ok and result.attempts == 3
    assert retry_waits == [1, 2]


@pytest.mark.parametrize("failure", TIMEOUTS_AND_CONNECTION_ERRORS, ids=FAILURE_IDS)
def test_gives_up_after_three_attempts(github, retry_waits, failure):
    github.search.reply = failure
    result = find()

    assert not result.ok and result.attempts == 3 and len(github.search.calls) == 3
    assert retry_waits == [1, 2]  # no wait after the last attempt
    assert result.error.startswith("Gave up after 3 attempt(s).")
    assert result.category is api_client.ErrorCategory.COMMUNICATION


@pytest.mark.parametrize("status", [401, 403, 404], ids=["bad-token", "forbidden", "no-repo"])
def test_permanent_http_errors_are_not_retried(github, retry_waits, status):
    github.search.reply = api_response(status, {"message": "nope"})
    result = find()

    assert not result.ok and result.attempts == 1 and retry_waits == []
    assert result.error == f"GitHub returned HTTP {status}: nope."
    assert result.category is api_client.ErrorCategory.HTTP


# --- the reply, checked like every other ---

@pytest.mark.parametrize("reply, expected", [
    (api_response(200, text="<html>not json</html>"), "INVALID_JSON"),
    (api_response(200, {"message": "an object, not a list"}), "VALIDATION"),
    (api_response(200, ["not an issue"]), "VALIDATION"),
    (open_issues({"html_url": "https://github.com/owner/repo/issues/7", "title": "t"}), "VALIDATION"),
    (open_issues(open_issue(0, "t")), "VALIDATION"),
    (open_issues(open_issue(7, "t", url="https://github.com/owner/repo/issues/8")), "VALIDATION"),
    (open_issues(open_issue(7, "t", url="http://github.com/owner/repo/issues/7")), "VALIDATION"),
    (open_issues({"number": 7, "html_url": "https://github.com/owner/repo/issues/7"}), "VALIDATION"),
    (open_issues(open_issue(7, None)), "VALIDATION"),
    (api_response(204, NOT_JSON), "HTTP"),
], ids=["html-body", "json-object", "item-not-an-object", "missing-number", "number-zero",
        "link-to-another-issue", "plain-http-link", "missing-title", "title-not-text", "204-instead-of-200"])
def test_replies_that_cannot_be_trusted_are_failures(github, retry_waits, reply, expected):
    github.search.reply = reply
    result = find()

    assert not result.ok and result.issues == ()
    assert result.category is api_client.ErrorCategory[expected]
    # GitHub would send the same reply again: no retry, so no "Gave up"
    assert result.attempts == 1 and len(github.search.calls) == 1 and retry_waits == []
    assert not result.error.startswith("Gave up")


# --- services.report_fault: no second issue for a fault already open ---

def report(desk, request=None, gemini_api_key=GEMINI_KEY):
    return services.report_fault(desk, "secret-token", gemini_api_key, "camera", "Camera Disconnected",
                                 "no frame", request)


def test_the_lookup_uses_the_alerts_labels(desk, github, gemini):
    report(desk)
    (call,) = github.search.calls
    assert call.params["labels"] == "hardware-alert,camera"


def test_an_open_issue_for_the_same_fault_is_not_posted_again(desk, alerts, github, gemini):
    request = desk.open_request_for_alert(alerts.camera(component_id="CAM-LOCAL", priority=4))
    github.search.reply = open_issues(open_issue(7, CAMERA_TITLE))
    outcome = report(desk, request)

    assert outcome.already_open
    assert outcome.issue.ok and outcome.issue.issue_number == 7
    assert outcome.issue.url == "https://github.com/owner/repo/issues/7"
    assert github.calls == [] and gemini.calls == []  # nothing posted, Gemini not asked
    assert request.priority == 4 and request.cause is None  # no triage applied


@pytest.mark.parametrize("title", [
    "\U0001f6a8 [ANOTHER-PC] Camera Disconnected",
    f"\U0001f6a8 [{COMPUTER_NAME}] Camera Frozen",
], ids=["another-computer", "another-fault"])
def test_an_open_issue_for_something_else_does_not_stop_the_post(desk, github, gemini, title):
    github.search.reply = open_issues(open_issue(7, title))
    outcome = report(desk)

    assert not outcome.already_open
    assert len(github.calls) == 1 and outcome.issue.issue_number == 1


def test_with_no_open_issue_the_fault_is_posted_as_before(desk, github, gemini):
    outcome = report(desk)

    assert not outcome.already_open and outcome.lookup_error is None
    assert outcome.triage is not None and len(github.calls) == 1


def test_a_failed_lookup_still_posts_the_alert(desk, github, gemini, retry_waits):
    github.search.reply = requests.exceptions.ConnectionError()
    outcome = report(desk)

    assert len(github.search.calls) == 3
    assert outcome.lookup_error.startswith("Gave up after 3 attempt(s).")
    assert not outcome.already_open and outcome.issue.ok and len(github.calls) == 1


# --- the dashboard ---

def test_dashboard_reports_the_issue_still_open(dash, github, logged):
    github.search.reply = open_issues(open_issue(7, CAMERA_TITLE))
    dash.app.send_github_alert("camera", "Camera Disconnected", "no frame")

    assert github.calls == []
    assert ("GitHub issue #7 for camera is still open (https://github.com/owner/repo/issues/7); "
            "not posting another.") in logged()


def test_dashboard_reports_a_failed_lookup_and_still_posts(dash, github, logged, retry_waits):
    github.search.reply = requests.exceptions.ReadTimeout()
    github.reply = created()
    dash.app.send_github_alert("camera", "Camera Disconnected", "no frame")

    output = logged()
    assert "Could not check GitHub for an open camera issue: Gave up after 3 attempt(s)." in output
    assert f"issue #{CREATED['number']}" in output
