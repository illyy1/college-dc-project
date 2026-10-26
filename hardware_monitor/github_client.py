"""GitHub Issues client the dashboard uses to report hardware faults. Kept
apart from the Tkinter code so the API handling (success, HTTP errors,
timeouts, malformed replies, retries) can be tested without a window or a
network."""

from dataclasses import dataclass

import requests

from .api_calls import json_object, request_failure, retry_api_call, status_failure

GITHUB_API_URL = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 5
# fields of GitHub's "issue created" reply the dashboard relies on, with their types
REQUIRED_RESPONSE_FIELDS = {"number": int, "html_url": str}

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1  # waits 1 s, then 2 s, 4 s, ... between attempts


@dataclass
class IssueResult:
    ok: bool
    issue_number: int | None = None
    url: str | None = None
    error: str | None = None
    attempts: int = 1


def create_issue(token, repo, title, body, labels=(), timeout=REQUEST_TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS):
    """Opens a GitHub issue, retrying temporary failures (timeouts, connection
    errors, a reply cut off mid-way, HTTP 429/5xx) up to max_attempts times with
    a growing wait. Permanent failures (bad token, missing repo, invalid request,
    a bad URL or header, a redirect loop) and
    incomplete replies are not retried: the first would fail again, and an
    incomplete 201 reply means the issue was already created.

    A timeout can hide a request GitHub did receive, so a retry after one may
    occasionally post a duplicate issue; that is preferred to losing the alert.

    Never raises for API problems: every failure comes back as
    IssueResult(ok=False, error=...), so a failed alert can't crash the
    monitoring thread that sent it."""
    if not token:
        return IssueResult(False, error="GitHub token missing or decryption failed.", attempts=0)

    return retry_api_call(lambda: _try_once(token, repo, title, body, labels, timeout),
                          max_attempts=max_attempts, backoff_seconds=RETRY_BACKOFF_SECONDS, label="GitHub")


def _try_once(token, repo, title, body, labels, timeout):
    """One POST. Returns (IssueResult, whether the failure is worth retrying)."""
    try:
        response = requests.post(
            f"{GITHUB_API_URL}/repos/{repo}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        error, retryable = request_failure(e, "GitHub", timeout)
        return IssueResult(False, error=error), retryable

    failure = status_failure(response, "GitHub", 201, _api_message)  # a new issue is 201 Created
    if failure:
        error, retryable = failure
        return IssueResult(False, error=error), retryable
    return _parse_created_issue(response), False


def _api_message(response):
    """GitHub explains errors in a JSON "message" field; returns ': <message>' or ''."""
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):  # not JSON, or JSON that is not an object
        return ""
    return f": {message}" if message else ""


def _parse_created_issue(response):
    data, error = json_object(response, "GitHub")
    if error:
        return IssueResult(False, error=error)

    problems = []
    for field, expected in REQUIRED_RESPONSE_FIELDS.items():
        value = data.get(field)
        if value is None:
            problems.append(f"missing '{field}'")
        elif isinstance(value, bool) or not isinstance(value, expected):
            problems.append(f"'{field}' should be {expected.__name__}, got {value!r}")
    if problems:
        return IssueResult(False, error=f"GitHub's reply is incomplete: {', '.join(problems)}.")

    problems = _impossible_values(data["number"], data["html_url"])
    if problems:
        return IssueResult(False, error=f"GitHub's reply is invalid: {', '.join(problems)}.")
    return IssueResult(True, issue_number=data["number"], url=data["html_url"])


def _impossible_values(number, url):
    """What is wrong with a reply's values, once their types are right: the
    dashboard prints the number and link, so both must point at the new issue."""
    problems = []
    if number <= 0:
        problems.append(f"'number' should be positive, got {number}")
    if not url.strip():
        problems.append("'html_url' is empty")
    elif not url.startswith("https://"):
        problems.append("'html_url' should be an https link")
    elif not url.rstrip("/").endswith(f"/issues/{number}"):
        problems.append(f"'html_url' doesn't link to issue #{number}")
    return problems
